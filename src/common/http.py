"""统一 HTTP 客户端：超时 + 指数退避重试 + 明确错误类型。

传输层通过 ``transport`` 注入，测试用假实现替换真实网络，避免用例依赖外网。
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 单次响应体上限：超出即报错，避免无界读入内存

PUBLIC_SCHEMES = frozenset({"http", "https"})
BLOCKED_HOSTNAMES = frozenset(
    {"localhost", "localhost.localdomain", "ip6-localhost", "metadata.google.internal"}
)

Transport = Callable[[str, str, Mapping[str, str], bytes | None, float], tuple[int, bytes]]
IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class HttpError(RuntimeError):
    """HTTP 调用失败（已重试仍失败，或响应不可解析）。"""

    def __init__(
        self,
        message: str,
        *,
        url: str = "",
        status: int | None = None,
        body: str = "",
        blocked: bool = False,
    ):
        super().__init__(message)
        self.url = url
        self.status = status
        self.body = body
        self.blocked = blocked


def _is_blocked_address(address: IpAddress) -> bool:
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def static_url_problem(url: str) -> str:
    """无网络的静态校验：仅允许 http/https，且拒绝本机/私网/literal 地址。

    只做字面量判断（不解析 DNS），供采集链路在「决定要不要探测」时复用同一套
    地址策略；真正的解析结果校验由 :func:`url_target_problem` 与连接时的
    IP 固定逻辑负责。返回空串表示静态层面可访问，否则返回中文原因。
    """
    try:
        parts = urlsplit(str(url or "").strip())
    except ValueError:
        return "链接格式非法"
    scheme = (parts.scheme or "").lower()
    if scheme not in PUBLIC_SCHEMES:
        return f"仅允许 http/https（收到 {scheme or '无 scheme'}）"
    host = (parts.hostname or "").strip().lower()
    if not host:
        return "缺少主机名"
    if host in BLOCKED_HOSTNAMES or host.endswith(".localhost"):
        return "本机/内部域名不在允许范围"
    try:
        literal: IpAddress | None = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and _is_blocked_address(literal):
        return "私网/链路本地地址不在允许范围"
    return ""


def url_target_problem(url: str, *, resolve: Callable[..., Any] | None = None) -> str:
    """连接前校验目标：只允许 http/https，且**域名解析结果**必须是公网地址。

    仅检查字面 IP 不足以防止 SSRF（``evil.example`` 可解析到 ``127.0.0.1``）；
    这里在每次真正发起连接（含重定向）前解析域名并逐个校验，命中私网/本机/
    链路本地/保留地址即拒绝。返回空串表示可连接，否则返回中文原因。
    """
    resolver = resolve or socket.getaddrinfo
    problem = static_url_problem(url)
    if problem:
        return problem
    parts = urlsplit(str(url or "").strip())
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").strip().lower()
    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError:
        return "端口非法"
    try:
        infos = resolver(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        return f"域名解析失败（{exc}）"
    addresses = []
    for info in infos or []:
        sockaddr = info[4] if len(info) > 4 else None
        if sockaddr:
            addresses.append(str(sockaddr[0]).split("%", 1)[0])
    if not addresses:
        return "域名未解析到任何地址"
    for raw in addresses:
        try:
            resolved = ipaddress.ip_address(raw)
        except ValueError:
            return f"域名解析结果不可信（{raw}）"
        if _is_blocked_address(resolved):
            return f"域名解析到非公网地址（{raw}）"
    return ""


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """跟随重定向前重新校验目标，避免用一次公网 302 绕过首次校验。"""

    def __init__(self, *, resolve: Callable[..., Any] | None = None) -> None:
        super().__init__()
        self._resolve = resolve

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001 - 协议签名
        problem = url_target_problem(newurl, resolve=self._resolve)
        if problem:
            raise HttpError(f"拒绝跟随重定向：{problem}", url=newurl, blocked=True)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _public_socket_addresses(host: str, port: int) -> list[tuple[int, int, int, Any]]:
    """解析域名并逐个校验；返回可直接连接的公网地址列表。

    校验与返回在同一处完成，调用方**只连接这里返回的地址**，因此不会在
    「校验用解析」与「建连用解析」之间留下 DNS 重绑定窗口。
    """
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise HttpError(f"域名解析失败（{exc}）", blocked=True) from exc
    addresses: list[tuple[int, int, int, Any]] = []
    for info in infos or []:
        family, socktype, proto, _, sockaddr = info
        raw = str(sockaddr[0]).split("%", 1)[0]
        try:
            resolved = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise HttpError(f"域名解析结果不可信（{raw}）", blocked=True) from exc
        if _is_blocked_address(resolved):
            raise HttpError(f"域名解析到非公网地址（{raw}）", blocked=True)
        addresses.append((family, socktype, proto, sockaddr))
    if not addresses:
        raise HttpError("域名未解析到任何地址", blocked=True)
    return addresses


def _connect_pinned(
    host: str,
    port: int,
    timeout: float | None,
    source_address: tuple[str, int] | None,
) -> socket.socket:
    family, socktype, proto, sockaddr = _public_socket_addresses(host, port)[0]
    sock = socket.socket(family, socktype, proto)
    try:
        if timeout is not None:
            sock.settimeout(timeout)
        if source_address:
            sock.bind(source_address)
        sock.connect(sockaddr)
    except BaseException:
        sock.close()
        raise
    return sock


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """连接已校验的公网 IP，而不是让系统再次解析主机名。"""

    def connect(self) -> None:
        self.sock = _connect_pinned(self.host, self.port, self.timeout, self.source_address)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """同上，并在固定 IP 上完成 TLS 握手（SNI 仍使用原始主机名）。"""

    def connect(self) -> None:
        sock = _connect_pinned(self.host, self.port, self.timeout, self.source_address)
        server_hostname = getattr(self, "_tunnel_host", None) or self.host
        self.sock = self._context.wrap_socket(sock, server_hostname=server_hostname)


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):  # noqa: ANN001 - 协议签名
        return self.do_open(_PinnedHTTPConnection, req)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):  # noqa: ANN001 - 协议签名
        return self.do_open(_PinnedHTTPSConnection, req, context=self._context)


_OPENER = urllib.request.build_opener(
    SafeRedirectHandler(),
    _PinnedHTTPHandler,
    _PinnedHTTPSHandler,
)


def urllib_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    data: bytes | None,
    timeout: float,
) -> tuple[int, bytes]:
    """最小传输实现（stdlib），返回 (status, body)。"""
    problem = url_target_problem(url)
    if problem:
        raise HttpError(f"拒绝访问：{problem}", url=url, blocked=True)
    request = urllib.request.Request(url, data=data, method=method)
    for key, value in headers.items():
        request.add_header(key, value)
    try:
        with _OPENER.open(request, timeout=timeout) as response:  # noqa: S310 - 仅 http(s)
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise HttpError(
                    f"响应体超过 {MAX_RESPONSE_BYTES} 字节上限，已中止（防止无界读入）",
                    url=url,
                )
            return int(response.status), raw
    except urllib.error.HTTPError as exc:  # 4xx/5xx 也要读 body 便于诊断，但同样限长
        raw = exc.read(MAX_RESPONSE_BYTES + 1)
        return int(exc.code), raw[:MAX_RESPONSE_BYTES]


def _parse_body(status: int, body: bytes, url: str) -> Any:
    text = body.decode("utf-8", errors="replace")
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise HttpError(
            f"响应不是合法 JSON（status={status}）",
            url=url,
            status=status,
            body=text[:500],
        ) from exc


class HttpClient:
    """带重试的 JSON 客户端。4xx（非 408/425/429）不重试，5xx/网络异常按 1s/2s/4s 退避。"""

    def __init__(
        self,
        transport: Transport = urllib_transport,
        *,
        timeout: float = 15.0,
        retries: int = 3,
        backoff_s: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._transport = transport
        self.timeout = timeout
        self.retries = max(0, retries)
        self.backoff_s = backoff_s
        self._sleep = sleep

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        payload: Any = None,
        expect_json: bool = True,
    ) -> Any:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        merged: dict[str, str] = {"Accept": "application/json"}
        if body is not None:
            merged["Content-Type"] = "application/json"
        merged.update(dict(headers or {}))

        last_error: str = ""
        for attempt in range(self.retries + 1):
            try:
                status, raw = self._transport(method, url, merged, body, self.timeout)
            except (urllib.error.URLError, OSError) as exc:  # 网络层异常
                last_error = f"网络异常: {exc}"
                if attempt < self.retries:
                    self._sleep(self.backoff_s * (2**attempt))
                    continue
                raise HttpError(last_error, url=url) from exc

            if status in RETRY_STATUS and attempt < self.retries:
                last_error = f"status={status}"
                self._sleep(self.backoff_s * (2**attempt))
                continue
            if status >= 400:
                raise HttpError(
                    f"HTTP {status}",
                    url=url,
                    status=status,
                    body=raw.decode("utf-8", errors="replace")[:500],
                )
            if not expect_json:
                return raw
            return _parse_body(status, raw, url)

        raise HttpError(f"重试耗尽: {last_error}", url=url)

    def get_json(self, url: str, headers: Mapping[str, str] | None = None) -> Any:
        return self.request("GET", url, headers=headers)

    def post_json(self, url: str, payload: Any, headers: Mapping[str, str] | None = None) -> Any:
        return self.request("POST", url, headers=headers, payload=payload)

    def get_text(self, url: str, headers: Mapping[str, str] | None = None) -> str:
        raw = self.request("GET", url, headers=headers, expect_json=False)
        return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
