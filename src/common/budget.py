"""token 预算与截断（不依赖具体 tokenizer，按中英文混合估算，宁多不少）。"""

from __future__ import annotations

TRUNCATION_MARKER = "\n…[已按 token 预算截断]"


class BudgetExceeded(RuntimeError):
    """超出预算时抛出，由调用方降级。"""


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数：CJK 字符约 1 token/字，其余约 4 字符/token。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u3400" <= ch <= "\u9fff")
    rest = len(text) - cjk
    return cjk + (rest + 3) // 4


def truncate_to_tokens(text: str, max_tokens: int, *, marker: str = TRUNCATION_MARKER) -> str:
    """按预算截断文本，保留头部（信息密度更高）并追加截断标记。"""
    if max_tokens <= 0:
        return ""
    if estimate_tokens(text) <= max_tokens:
        return text
    marker_tokens = estimate_tokens(marker)
    room = max_tokens - marker_tokens
    if room <= 0:
        return marker.strip()
    # 逐步缩小保留比例，保证不超预算
    kept = text
    ratio = 0.8
    while kept and estimate_tokens(kept + marker) > max_tokens:
        kept = text[: max(1, int(len(text) * ratio))]
        ratio *= 0.8
    return kept + marker


class TokenBudget:
    """累计预算：prompt 估算 + 预留输出，超出即拒绝（调用方降级为链接汇总）。"""

    def __init__(self, limit: int) -> None:
        if limit <= 0:
            raise ValueError("limit 必须为正数")
        self.limit = limit
        self.used = 0

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def can_afford(self, tokens: int) -> bool:
        return tokens <= self.remaining

    def spend(self, tokens: int) -> None:
        if tokens < 0:
            raise ValueError("tokens 不能为负")
        if not self.can_afford(tokens):
            raise BudgetExceeded(f"token 预算不足：需要 {tokens}，剩余 {self.remaining}")
        self.used += tokens

    def reserve(self, *, prompt_text: str, max_output_tokens: int) -> None:
        """按「prompt 估算 + 预留输出」整体扣费。"""
        self.spend(estimate_tokens(prompt_text) + max_output_tokens)
