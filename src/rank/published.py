"""已发布主瓜集合：避免同一 URL 反复成为当日主角。

与 ``snapshots.json``（指标时序）语义不同，已发布状态独立存放在
``state/published.json``。出报后按规范化 URL 幂等追加，保留最近 N 条。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..common.models import canonical_url, utc_now

PUBLISHED_VERSION = 1
MAX_PUBLISHED = 90


@dataclass(frozen=True)
class PublishedEntry:
    url: str
    date: str
    first_published_at: datetime

    def to_dict(self) -> dict[str, str]:
        return {
            "url": self.url,
            "date": self.date,
            "first_published_at": self.first_published_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, raw: object) -> PublishedEntry | None:
        if not isinstance(raw, dict):
            return None
        url = str(raw.get("url") or "")
        if not url:
            return None
        stamp = raw.get("first_published_at")
        try:
            first = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            first = utc_now()
        if first.tzinfo is None:
            first = first.replace(tzinfo=UTC)
        return cls(url=url, date=str(raw.get("date") or ""), first_published_at=first)


def load_published(path: str | Path) -> dict[str, PublishedEntry]:
    """读取集合；文件缺失/损坏时返回空字典（不阻断出报）。"""
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
    entries: dict[str, PublishedEntry] = {}
    for raw in posts.values():
        entry = PublishedEntry.from_dict(raw)
        if entry is not None:
            entries[canonical_url(entry.url)] = entry
    return entries


def published_urls(entries: dict[str, PublishedEntry]) -> set[str]:
    return set(entries)


def save_published(path: str | Path, entries: dict[str, PublishedEntry]) -> None:
    """按首次发布时间保留最近 ``MAX_PUBLISHED`` 条并原子覆盖写入。"""
    ordered = sorted(entries.values(), key=lambda e: e.first_published_at)[-MAX_PUBLISHED:]
    payload = {
        "version": PUBLISHED_VERSION,
        "posts": {canonical_url(entry.url): entry.to_dict() for entry in ordered},
    }
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_suffix(file.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(file)


def record_published(
    path: str | Path,
    url: str,
    *,
    date: str,
    now: datetime | None = None,
) -> bool:
    """幂等追加一条已发布记录；已存在（按规范化 URL）返回 ``False`` 且不改动。"""
    if not url:
        return False
    moment = now or utc_now()
    entries = load_published(path)
    key = canonical_url(url)
    if key in entries:
        return False
    entries[key] = PublishedEntry(url=url, date=date, first_published_at=moment)
    save_published(path, entries)
    return True
