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
