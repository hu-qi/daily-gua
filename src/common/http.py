"""统一 HTTP 客户端：超时 + 指数退避重试 + 明确错误类型。

传输层通过 ``transport`` 注入，测试用假实现替换真实网络，避免用例依赖外网。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 单次响应体上限：超出即报错，避免无界读入内存

Transport = Callable[[str, str, Mapping[str, str], bytes | None, float], tuple[int, bytes]]


class HttpError(RuntimeError):
    """HTTP 调用失败（已重试仍失败，或响应不可解析）。"""

    def __init__(self, message: str, *, url: str = "", status: int | None = None, body: str = ""):
        super().__init__(message)
        self.url = url
        self.status = status
        self.body = body


def urllib_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    data: bytes | None,
    timeout: float,
) -> tuple[int, bytes]:
    """最小传输实现（stdlib），返回 (status, body)。"""
    request = urllib.request.Request(url, data=data, method=method)
    for key, value in headers.items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - 仅 http(s)
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
