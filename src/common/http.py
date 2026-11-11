"""统一 HTTP 客户端：超时 + 指数退避重试 + 明确错误类型。

传输层通过 ``transport`` 注入，测试用假实现替换真实网络，避免用例依赖外网。
"""

from __future__ import annotations

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


def url_target_problem(url: str, *, resolve: Callable[..., Any] | None = None) -> str:
    """连接前校验目标：只允许 http/https，且**域名解析结果**必须是公网地址。

    仅检查字面 IP 不足以防止 SSRF（``evil.example`` 可解析到 ``127.0.0.1``）；
    这里在每次真正发起连接（含重定向）前解析域名并逐个校验，命中私网/本机/
    链路本地/保留地址即拒绝。返回空串表示可连接，否则返回中文原因。
    """
    resolver = resolve or socket.getaddrinfo
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
    if literal is not None:
        return "私网/链路本地地址不在允许范围" if _is_blocked_address(literal) else ""
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


_OPENER = urllib.request.build_opener(SafeRedirectHandler())


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
    except urllib.error.HTTPError as exc:  # 4xx/5xx 也要读 body 便于诊断
        return int(exc.code), exc.read()


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
