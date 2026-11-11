"""吃瓜指数：0-100 的可解释评分。

指数是**确定性**的（只看公开互动指标与发布时间），因此 LLM 不可用、或 LLM 给出的分数
异常时都能稳定产出；LLM 可以在报告里补充「人话理由」，但不能改写数值。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from ..common.models import Post, utc_now

LEVELS: tuple[tuple[int, str], ...] = ((85, "沸腾"), (65, "热"), (40, "温"), (0, "微"))
LEVEL_EMOJI = {"沸腾": "🍉🍉🍉", "热": "🍉🍉", "温": "🍉", "微": "🍃"}


@dataclass(frozen=True)
class MelonScore:
    score: int
    level: str
    emoji: str
    reasons: tuple[str, ...]
    breakdown: dict[str, int]

    def headline(self) -> str:
        return f"{self.score}/100（{self.level}）{self.emoji}"


def level_for(score: int) -> str:
    for threshold, name in LEVELS:
        if score >= threshold:
            return name
    return "微"


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def freshness(post: Post, *, now: datetime | None = None) -> float:
    """新鲜度：12 小时内满分，72 小时外归零（线性衰减）。"""
    moment = now or utc_now()
    hours = max(0.0, (moment - post.created_at).total_seconds() / 3600.0)
    if hours <= 12:
        return 1.0
    if hours >= 72:
        return 0.0
    return 1.0 - (hours - 12) / 60.0


def melon_index(post: Post, *, now: datetime | None = None) -> MelonScore:
    """四分量加权：传播面 35 / 讨论热度 30 / 争议度 25 / 新鲜度 10。"""
    metrics = post.metrics
    reach_c = _clamp01(math.log10(1 + max(0, metrics.views)) / 6.0)
    heat_c = _clamp01(math.log10(1 + max(0, metrics.engagement)) / math.log10(1 + 5000))
    ratio = (metrics.replies + 1.5 * metrics.quotes) / max(metrics.likes, 1)
    contro_c = _clamp01(ratio / 0.5)
    fresh_c = freshness(post, now=now)

    breakdown = {
        "传播面": int(round(reach_c * 35)),
        "讨论热度": int(round(heat_c * 30)),
        "争议度": int(round(contro_c * 25)),
        "新鲜度": int(round(fresh_c * 10)),
    }
    score = max(0, min(100, sum(breakdown.values())))
    level = level_for(score)
    reasons = (
        f"传播面 {breakdown['传播面']}/35（阅读 {metrics.views}）",
        f"讨论热度 {breakdown['讨论热度']}/30（加权互动 {metrics.engagement}）",
        f"争议度 {breakdown['争议度']}/25（回复 {metrics.replies} + 引用 {metrics.quotes} 对点赞 {metrics.likes}）",
        f"新鲜度 {breakdown['新鲜度']}/10",
    )
    return MelonScore(
        score=score,
        level=level,
        emoji=LEVEL_EMOJI[level],
        reasons=reasons,
        breakdown=breakdown,
    )


def coerce_melon_score(value: object) -> int | None:
    """把 LLM 返回的分数收敛到合法区间；非法值返回 None 以便回退确定性评分。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = int(round(float(value)))
    elif isinstance(value, str) and value.strip().rstrip("%").isdigit():
        number = int(value.strip().rstrip("%"))
    else:
        return None
    return max(0, min(100, number))
