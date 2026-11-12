"""common 层：脱敏、HTTP 重试、token 预算、模型工具、隐私打码。"""

from __future__ import annotations

import logging

import pytest
from src.common.budget import BudgetExceeded, TokenBudget, estimate_tokens, truncate_to_tokens
from src.common.http import HttpClient, HttpError
from src.common.models import Metrics, Post, canonical_url, handle_from_url, status_id_from_url
from src.common.privacy import mask_personal
from src.common.redaction import RedactingFilter, Redactor, default_redactor
from tests.conftest import FakeTransport, json_route


def test_redactor_masks_known_secret_values():
    secret = "sk-live-abcdef1234567890"
    redactor = Redactor([secret])
    text = redactor.redact(f"call failed with api key {secret} inside body")
    assert secret not in text
    assert "***" in text


def test_redactor_masks_bearer_and_query_tokens():
    redactor = Redactor()
    masked = redactor.redact("Authorization: Bearer abcdefghijklmnop &access_token=qwertyuiop123")
    assert "abcdefghijklmnop" not in masked
    assert "qwertyuiop123" not in masked
    assert masked.count("***") >= 2


def test_default_redactor_collects_env_secrets(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "llm-secret-value-42")
    redactor = default_redactor()
    assert "llm-secret-value-42" not in redactor.redact("key=llm-secret-value-42")


def test_logging_filter_never_emits_secret(monkeypatch, caplog):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret_token_value")
    logger = logging.getLogger("test.redaction")
    logger.addFilter(RedactingFilter(default_redactor()))
    with caplog.at_level(logging.INFO, logger="test.redaction"):
        logger.info("token is %s", "ghp_secret_token_value")
    assert "ghp_secret_token_value" not in caplog.text
    assert "***" in caplog.text


def test_http_retries_then_succeeds():
    transport = FakeTransport(
        routes={"https://api.example.com/ok": json_route({"hello": "world"})}, failures=2
    )
    client = HttpClient(transport=transport, retries=3, backoff_s=0, sleep=lambda _: None)
    data = client.get_json("https://api.example.com/ok")
    assert data == {"hello": "world"}
    assert transport.call_count == 3


def test_http_raises_after_retries_exhausted():
    transport = FakeTransport(failures=5)
    client = HttpClient(transport=transport, retries=1, backoff_s=0, sleep=lambda _: None)
    with pytest.raises(HttpError):
        client.get_json("https://api.example.com/x")
    assert transport.call_count == 2


def test_http_does_not_retry_client_errors():
    transport = FakeTransport(routes={"https://api.example.com": (403, b'{"message":"nope"}')})
    client = HttpClient(transport=transport, retries=3, backoff_s=0, sleep=lambda _: None)
    with pytest.raises(HttpError) as excinfo:
        client.get_json("https://api.example.com/nope")
    assert excinfo.value.status == 403
    assert transport.call_count == 1


def test_http_reports_invalid_json():
    transport = FakeTransport(routes={"https://api.example.com": (200, b"not-json")})
    client = HttpClient(transport=transport, retries=0, sleep=lambda _: None)
    with pytest.raises(HttpError):
        client.get_json("https://api.example.com/bad")


def test_estimate_tokens_handles_cjk_and_latin():
    assert estimate_tokens("") == 0
    chinese = estimate_tokens("每日一瓜")
    assert chinese >= 4
    assert estimate_tokens("abcdefgh") == 2


def test_truncate_to_tokens_respects_budget():
    text = "瓜" * 500
    truncated = truncate_to_tokens(text, 50)
    assert estimate_tokens(truncated) <= 50 + estimate_tokens("…[已按 token 预算截断]")
    assert "截断" in truncated


def test_token_budget_rejects_overspending():
    budget = TokenBudget(10)
    budget.spend(4)
    assert budget.remaining == 6
    with pytest.raises(BudgetExceeded):
        budget.spend(10)


def test_canonical_url_strips_tracking_params():
    assert canonical_url("https://x.com/criscxuan/status/1?utm_source=a&s=20") == (
        "https://x.com/criscxuan/status/1"
    )


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://x.com/criscxuan/status/2120336410989433287", "2120336410989433287"),
        ("https://twitter.com/i/status/123456789", "123456789"),
        ("https://example.com/status/1", None),
    ],
)
def test_status_id_from_url(url, expected):
    assert status_id_from_url(url) == expected


def test_handle_from_url():
    assert handle_from_url("https://x.com/criscxuan/status/1") == "criscxuan"
    assert handle_from_url("https://x.com/i/status/1") is None


def test_post_key_prefers_status_id():
    post = Post(
        id="1",
        url="https://x.com/a/status/999",
        author="a",
        text="t",
        created_at=__import__("datetime").datetime(2026, 11, 11, tzinfo=__import__("datetime").timezone.utc),
    )
    assert post.key == "x:999"


def test_metrics_delta_is_non_negative():
    before = Metrics(likes=10, replies=2)
    after = Metrics(likes=5, replies=6)
    delta = after.delta(before)
    assert delta.likes == 0
    assert delta.replies == 4


def test_mask_personal_redacts_contacts():
    masked = mask_personal("联系 13812345678 或 a@b.com，身份证 110101199003071234")
    assert "13812345678" not in masked
    assert "a@b.com" not in masked
    assert "110101199003071234" not in masked
    assert "138****5678" in masked

def test_transport_rejects_oversized_response(monkeypatch):
    """响应体有上限：超出即报错，绝不无界读入内存。"""
    from src.common import http as http_module

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):  # noqa: ANN001 - 上下文协议
            return False

        def read(self, n=-1):  # noqa: ANN001
            return b"x" * (n if n and n > 0 else 1024)

    monkeypatch.setattr(http_module, "url_target_problem", lambda *a, **k: "")
    monkeypatch.setattr(http_module._OPENER, "open", lambda *a, **k: FakeResponse())
    with pytest.raises(HttpError) as excinfo:
        http_module.urllib_transport("GET", "https://example.com/huge", {}, None, 5)
    assert "上限" in str(excinfo.value)


def test_transport_returns_body_under_limit(monkeypatch):
    from src.common import http as http_module

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):  # noqa: ANN001 - 上下文协议
            return False

        def read(self, n=-1):  # noqa: ANN001
            return b'{"ok":true}'

    monkeypatch.setattr(http_module, "url_target_problem", lambda *a, **k: "")
    monkeypatch.setattr(http_module._OPENER, "open", lambda *a, **k: FakeResponse())
    status, body = http_module.urllib_transport("GET", "https://example.com/x", {}, None, 5)
    assert status == 200 and body == b'{"ok":true}'



def _resolver(mapping: dict[str, str]):
    import socket

    def resolve(host, port, **kwargs):  # noqa: ANN001 - 模拟 getaddrinfo
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (mapping[host], port))]

    return resolve


def test_url_target_problem_rejects_domain_resolving_to_private_ip():
    """仅校验字面 IP 会漏掉「公网域名解析到 127.0.0.1」的 SSRF。"""
    from src.common import http as http_module

    assert "非公网" in http_module.url_target_problem(
        "https://evil.example/x", resolve=_resolver({"evil.example": "127.0.0.1"})
    )
    assert "非公网" in http_module.url_target_problem(
        "https://rebind.example/x", resolve=_resolver({"rebind.example": "169.254.169.254"})
    )
    assert (
        http_module.url_target_problem(
            "https://ok.example/x", resolve=_resolver({"ok.example": "93.184.216.34"})
        )
        == ""
    )


def test_url_target_problem_rejects_non_http_and_literal_private():
    from src.common import http as http_module

    assert http_module.url_target_problem("file:///etc/passwd") != ""
    assert http_module.url_target_problem("http://127.0.0.1/admin") != ""
    assert http_module.url_target_problem("http://[::1]/") != ""


def test_urllib_transport_blocks_private_dns_before_connecting(monkeypatch):
    import socket

    from src.common import http as http_module

    def fake_getaddrinfo(host, port, **kwargs):  # noqa: ANN001 - 模拟解析
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))]

    called = {"open": False}

    def fail_open(*args, **kwargs):  # noqa: ANN001
        called["open"] = True
        raise AssertionError("不应发起连接")

    monkeypatch.setattr(http_module.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(http_module._OPENER, "open", fail_open)
    with pytest.raises(HttpError) as excinfo:
        http_module.urllib_transport("GET", "https://evil.example/x", {}, None, 5)
    assert excinfo.value.blocked is True
    assert called["open"] is False


def test_load_settings_falls_back_on_invalid_numeric_env():
    """仓库 Variables 填错数值不应让出报流水线崩溃。"""
    from src.common.config import load_settings

    settings = load_settings(
        {"HTTP_TIMEOUT_S": "abc", "HTTP_RETRIES": "", "LLM_MAX_TOKENS": "12x"}
    )
    assert settings.http_timeout_s == 15.0
    assert settings.http_retries == 3
    assert settings.llm_max_tokens == 4096


def test_public_socket_addresses_rejects_private_resolution(monkeypatch):
    """固定连接用的解析函数必须自己拒绝私网地址（而非依赖前置校验）。"""
    import socket

    from src.common import http as http_module

    def fake_getaddrinfo(host, port, **kwargs):  # noqa: ANN001
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", port))]

    monkeypatch.setattr(http_module.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(HttpError) as excinfo:
        http_module._public_socket_addresses("evil.example", 443)
    assert excinfo.value.blocked is True


def test_redirect_handler_revalidates_each_hop():
    import urllib.request

    from src.common import http as http_module

    handler = http_module.SafeRedirectHandler(
        resolve=_resolver({"ok.example": "93.184.216.34", "evil.example": "127.0.0.1"})
    )
    request = urllib.request.Request("https://ok.example/")
    with pytest.raises(HttpError) as excinfo:
        handler.redirect_request(request, None, 302, "", {}, "https://evil.example/x")
    assert excinfo.value.blocked is True
