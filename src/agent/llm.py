"""LLM 客户端（OpenAI 兼容 ``/chat/completions``）。

- 超时与重试复用 ``HttpClient``；
- 每次调用先按「prompt 估算 + 预留输出」扣 token 预算，超预算直接抛错（上层降级）；
- key 只存在于请求头，任何日志都不打印请求体。
"""

from __future__ import annotations

from typing import Any

from ..common.budget import TokenBudget, estimate_tokens
from ..common.config import Settings
from ..common.http import HttpClient, HttpError
from ..common.redaction import get_logger

LOGGER = get_logger(__name__)


class LLMError(RuntimeError):
    """LLM 不可用 / 返回不可用内容。"""


class LLMClient:
    def __init__(
        self,
        settings: Settings,
        http: HttpClient | None = None,
        *,
        budget: TokenBudget | None = None,
    ) -> None:
        self._settings = settings
        self._http = http or HttpClient(
            timeout=settings.llm_timeout_s, retries=settings.http_retries
        )
        self._budget = budget or TokenBudget(settings.token_budget)

    @property
    def available(self) -> bool:
        return self._settings.llm_configured

    @property
    def model(self) -> str:
        return self._settings.llm_model

    def _endpoint(self) -> str:
        base = (self._settings.llm_base_url or "").rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        if not self.available:
            raise LLMError("LLM 未配置（缺少 LLM_BASE_URL / LLM_API_KEY）")
        limit = max_tokens or self._settings.llm_max_tokens
        prompt_text = "\n".join(str(m.get("content") or "") for m in messages)
        try:
            self._budget.reserve(prompt_text=prompt_text, max_output_tokens=limit)
        except Exception as exc:  # BudgetExceeded
            raise LLMError(f"token 预算不足，降级处理: {exc}") from exc

        payload: dict[str, Any] = {
            "model": self._settings.llm_model,
            "messages": messages,
            "max_tokens": limit,
            "temperature": self._settings.llm_temperature
            if temperature is None
            else temperature,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self._settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        try:
            data = self._http.post_json(self._endpoint(), payload, headers)
        except HttpError as exc:
            raise LLMError(f"LLM 调用失败: {exc}") from exc

        content = _extract_content(data)
        if not content.strip():
            raise LLMError("LLM 返回空内容")
        LOGGER.info(
            "LLM 调用完成：model=%s prompt≈%d tokens，输出≈%d tokens",
            self._settings.llm_model,
            estimate_tokens(prompt_text),
            estimate_tokens(content),
        )
        return content


def _extract_content(data: Any) -> str:
    if not isinstance(data, dict):
        raise LLMError("LLM 响应不是 JSON 对象")
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMError("LLM 响应缺少 choices")
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") or {}
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):  # 少数网关返回分段 content
            parts = [
                str(seg.get("text") or "")
                for seg in content
                if isinstance(seg, dict) and seg.get("text")
            ]
            return "".join(parts)
    text = first.get("text")
    return str(text) if isinstance(text, str) else ""
