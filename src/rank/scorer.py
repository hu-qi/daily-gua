"""瓜候选打分：互动增速、争议度（回复+引用/点赞）、大号参与度、传播面。

三个正交信号合成 0-100 分；每个分量都有上限归一化，避免单条爆帖把分布拉爆。
所有函数为纯函数，除 ``now`` 外不读时钟，便于固定时钟测试。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from ..common.models import Metrics, Post, canonical_url, utc_now
from .snapshots import PostSnapshot, hours_between


@dataclass(frozen=True)
class RankConfig:
    w_velocity: float = 0.45
    w_controversy: float = 0.30
    w_influencer: float = 0.15
    w_reach: float = 0.10
    controversy_target: float = 0.5
    velocity_ceiling_per_hour: float = 200.0
    influencer_log_ceiling: float = 6.0
    reach_log_ceiling: float = 6.0
    min_engagement: int = 0

    @property
    def weight_sum(self) -> float:
        total = self.w_velocity + self.w_controversy + self.w_influencer + self.w_reach
        return total if total > 0 else 1.0


@dataclass(frozen=True)
class ScoreBreakdown:
    post: Post
    total: float
    velocity: float
    controversy: float
    influencer: float
    reach: float
    growth_per_hour: float
    growth_basis: str
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def components(self) -> dict[str, float]:
        return {
            "velocity": round(self.velocity, 4),
            "controversy": round(self.controversy, 4),
            "influencer": round(self.influencer, 4),
            "reach": round(self.reach, 4),
        }


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def growth_per_hour(post: Post, snapshot: PostSnapshot | None, *, now: datetime) -> tuple[float, str]:
    """每小时间加权互动增量。

    - 有上一份快照：差分 / 间隔小时（真实增速）；
    - 无快照：本次互动 / 发布至今小时（平均增速，作为代理指标）。
    """
    if snapshot is not None:
        hours = hours_between(snapshot.captured_at, now)
        if hours > 0:
            delta = post.metrics.delta(snapshot.metrics).engagement
            return delta / hours, "snapshot_diff"
    hours_alive = hours_between(post.created_at, now)
    if hours_alive <= 0:
        return float(post.metrics.engagement), "fallback_total"
    return post.metrics.engagement / hours_alive, "fallback_avg"


def controversy_score(metrics: Metrics, cfg: RankConfig) -> float:
    """争议度 =（回复 + 1.5×引用）/ 点赞，归一到 ``controversy_target``。

    点赞多但无人辩驳的是「热帖」，回复引用相对点赞高才更像「瓜」。
    """
    ratio = (metrics.replies + 1.5 * metrics.quotes) / max(metrics.likes, 1)
    return _clamp(ratio / cfg.controversy_target if cfg.controversy_target > 0 else 0.0)


def influencer_score(post: Post, cfg: RankConfig) -> float:
    """大号参与度代理：作者粉丝量的对数归一（粉丝越多，传播势能越大）。"""
    followers = max(0, post.author_followers)
    if followers <= 0:
        return 0.0
    ceiling = max(1.0, cfg.influencer_log_ceiling)
    return _clamp(math.log10(1 + followers) / ceiling)


def reach_score(metrics: Metrics, cfg: RankConfig) -> float:
    ceiling = max(1.0, cfg.reach_log_ceiling)
    return _clamp(math.log10(1 + max(0, metrics.views)) / ceiling)


def score_post(
    post: Post,
    *,
    snapshot: PostSnapshot | None = None,
    now: datetime | None = None,
    cfg: RankConfig | None = None,
) -> ScoreBreakdown:
    config = cfg or RankConfig()
    moment = now or utc_now()
    gph, basis = growth_per_hour(post, snapshot, now=moment)
    velocity_c = _clamp(
        math.log1p(max(0.0, gph)) / math.log1p(config.velocity_ceiling_per_hour)
    )
    controversy_c = controversy_score(post.metrics, config)
    influencer_c = influencer_score(post, config)
    reach_c = reach_score(post.metrics, config)
    total = (
        100.0
        * (
            config.w_velocity * velocity_c
            + config.w_controversy * controversy_c
            + config.w_influencer * influencer_c
            + config.w_reach * reach_c
        )
        / config.weight_sum
    )
    reasons = _reasons(post, gph, basis, controversy_c, influencer_c, reach_c)
    return ScoreBreakdown(
        post=post,
        total=round(total, 2),
        velocity=velocity_c,
        controversy=controversy_c,
        influencer=influencer_c,
        reach=reach_c,
        growth_per_hour=round(gph, 2),
        growth_basis=basis,
        reasons=reasons,
    )


def _reasons(
    post: Post,
    gph: float,
    basis: str,
    controversy: float,
    influencer: float,
    reach: float,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if gph >= 50:
        reasons.append(f"互动增速约 {gph:.0f}/小时，属当天高速升温")
    elif gph >= 10:
        reasons.append(f"互动增速约 {gph:.0f}/小时，持续被讨论")
    if controversy >= 0.6:
        reasons.append("回复与引用相对点赞占比高，争议强于共鸣")
    if influencer >= 0.6:
        reasons.append(f"发帖人粉丝量约 {post.author_followers}，属大号量级")
    if reach >= 0.6:
        reasons.append(f"阅读量约 {post.metrics.views}，传播面广")
    if basis == "fallback_avg":
        reasons.append("缺少历史快照，增速按发布至今均值估算")
    if not reasons:
        reasons.append("互动量级一般，作为候选保留")
    return tuple(reasons)


@dataclass
class RankedResult:
    """``top1`` 为当日主角，``top3`` 为除主角外的三条备选（不足则少）。"""

    top1: ScoreBreakdown | None
    top3: list[ScoreBreakdown]
    scored: list[ScoreBreakdown]

    @property
    def candidates(self) -> list[ScoreBreakdown]:
        return list(self.top3)


def _eligible(post: Post, cfg: RankConfig) -> bool:
    text = " ".join(post.text.split())
    if not text:
        return False
    return post.metrics.engagement >= cfg.min_engagement


def rank_posts(
    posts: list[Post],
    *,
    snapshots: dict[str, PostSnapshot] | None = None,
    now: datetime | None = None,
    cfg: RankConfig | None = None,
    top_n: int = 3,
    exclude_urls: set[str] | tuple[str, ...] | list[str] | None = None,
) -> RankedResult:
    """按分数降序，返回 Top1 与 Top3 备选（同键去重，保留高分者）。

    ``exclude_urls`` 为已发布 URL 集合（按 ``canonical_url`` 规范化比较），
    打分前剔除，纯函数、默认不排除。
    """
    config = cfg or RankConfig()
    moment = now or utc_now()
    snapshot_map = snapshots or {}
    excluded = {canonical_url(url) for url in exclude_urls or ()}
    seen: dict[str, ScoreBreakdown] = {}
    for post in posts:
        if not _eligible(post, config):
            continue
        if post.url and canonical_url(post.url) in excluded:
            continue
        item = score_post(post, snapshot=snapshot_map.get(post.key), now=moment, cfg=config)
        current = seen.get(post.key)
        if current is None or item.total > current.total:
            seen[post.key] = item
    scored = sorted(seen.values(), key=lambda s: (-s.total, s.post.key))
    top1 = scored[0] if scored else None
    return RankedResult(
        top1=top1,
        top3=scored[1 : top_n + 1],
        scored=scored,
    )
