"""指标快照：用「上次采集 vs 本次采集」的差分计算互动增速。

快照以 JSON 文件形式随仓库提交（``state/snapshots.json``），无需数据库；
缺失上一份快照时，rank 层退化为「发布至今平均每小时互动」的代理增速。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..common.models import Metrics, Post, utc_now

SNAPSHOT_VERSION = 1
MAX_AGE_DAYS = 30


@dataclass(frozen=True)
class PostSnapshot:
    key: str
    url: str
    author: str
    captured_at: datetime
    metrics: Metrics

    def to_dict(self) -> dict[str, object]:
        return {
            "url": self.url,
            "author": self.author,
            "captured_at": self.captured_at.isoformat(),
            "metrics": self.metrics.to_dict(),
        }

    @classmethod
    def from_dict(cls, key: str, raw: object) -> PostSnapshot | None:
        if not isinstance(raw, dict):
            return None
        stamp = raw.get("captured_at")
        try:
            captured_at = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        if captured_at.tzinfo is None:
            captured_at = captured_at.replace(tzinfo=UTC)
        return cls(
            key=key,
            url=str(raw.get("url") or ""),
            author=str(raw.get("author") or ""),
            captured_at=captured_at,
            metrics=Metrics.from_dict(raw.get("metrics")),
        )


def load_snapshots(path: str | Path) -> dict[str, PostSnapshot]:
    """读取快照；文件缺失/损坏时返回空字典（不阻断出报）。"""
    file = Path(path)
    if not file.exists():
        return {}
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    posts = data.get("posts") if isinstance(data, dict) else None
    if not isinstance(posts, dict):
        return {}
    snapshots: dict[str, PostSnapshot] = {}
    for key, raw in posts.items():
        snapshot = PostSnapshot.from_dict(str(key), raw)
        if snapshot is not None:
            snapshots[snapshot.key] = snapshot
    return snapshots


def save_snapshots(path: str | Path, posts: list[Post], *, now: datetime | None = None) -> None:
    """整体覆盖写入本次采集快照，并淘汰过期条目。"""
    moment = now or utc_now()
    entries: dict[str, dict[str, object]] = {}
    for post in posts:
        entries[post.key] = PostSnapshot(
            key=post.key,
            url=post.url,
            author=post.author,
            captured_at=moment,
            metrics=post.metrics,
        ).to_dict()
    payload = {"version": SNAPSHOT_VERSION, "captured_at": moment.isoformat(), "posts": entries}
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_suffix(file.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(file)


def prune_snapshots(
    snapshots: dict[str, PostSnapshot], *, now: datetime | None = None
) -> dict[str, PostSnapshot]:
    moment = now or utc_now()
    cutoff = moment - timedelta(days=MAX_AGE_DAYS)
    return {key: snap for key, snap in snapshots.items() if snap.captured_at >= cutoff}


def hours_between(earlier: datetime, later: datetime) -> float:
    delta = (later - earlier).total_seconds() / 3600.0
    return max(delta, 0.0)
