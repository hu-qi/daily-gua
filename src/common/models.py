"""跨层共享领域模型：Post（统一帖子）与 Metrics（互动指标快照）。

放在 common 是为了避免 collect / rank / agent / publish 之间互相 import 造成循环依赖：
依赖方向恒为 ``common ← 各层``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse, urlunparse

X_HOSTS = frozenset({"x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"})


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class Metrics:
    """某时刻的互动指标。差分缺口一律按 0 处理，由 rank 层判断是否可用。"""

    likes: int = 0
    replies: int = 0
    reposts: int = 0
    quotes: int = 0
    views: int = 0
    bookmarks: int = 0

    @property
    def engagement(self) -> int:
        """加权互动量：回复/引用权重更高（争议信号更强）。"""
        return self.likes + 2 * self.reposts + 2 * self.replies + 2 * self.quotes

    def to_dict(self) -> dict[str, int]:
        return {
            "likes": self.likes,
            "replies": self.replies,
            "reposts": self.reposts,
            "quotes": self.quotes,
            "views": self.views,
            "bookmarks": self.bookmarks,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> Metrics:
        data = _as_dict(raw)
        fields = ("likes", "replies", "reposts", "quotes", "views", "bookmarks")
        return cls(**{name: int(data.get(name) or 0) for name in fields})

    def delta(self, previous: Metrics) -> Metrics:
        return Metrics(
            likes=max(0, self.likes - previous.likes),
            replies=max(0, self.replies - previous.replies),
            reposts=max(0, self.reposts - previous.reposts),
            quotes=max(0, self.quotes - previous.quotes),
            views=max(0, self.views - previous.views),
            bookmarks=max(0, self.bookmarks - previous.bookmarks),
        )


def canonical_url(url: str) -> str:
    """去掉 query/fragment 与跟踪参数，作为去重键的规范化地址。"""
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower()
    if host in X_HOSTS:
        host = "x.com"
    path = parsed.path.rstrip("/")
    return urlunparse((parsed.scheme or "https", host, path, "", "", ""))


def status_id_from_url(url: str) -> str | None:
    """从 x.com/twitter.com 状态链接中取状态 id。"""
    parsed = urlparse(url)
    if parsed.netloc.lower() not in X_HOSTS:
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if "status" in parts:
        index = parts.index("status")
        if index + 1 < len(parts) and parts[index + 1].isdigit():
            return parts[index + 1]
    return None


def handle_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.netloc.lower() not in X_HOSTS:
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if parts and parts[0] not in {"i", "status", "intent", "home"}:
        return parts[0].lstrip("@")
    return None


@dataclass(frozen=True)
class Post:
    """统一帖子模型：X API / X 镜像 / RSS / 种子链接都归一为此结构。"""

    id: str
    url: str
    author: str
    text: str
    created_at: datetime
    metrics: Metrics = field(default_factory=Metrics)
    author_followers: int = 0
    source: str = "unknown"
    media_urls: tuple[str, ...] = ()
    lang: str = ""
    author_name: str = ""

    @property
    def key(self) -> str:
        """去重键：X 状态用平台 id，其余用规范化 URL。"""
        sid = status_id_from_url(self.url)
        if sid:
            return f"x:{sid}"
        return canonical_url(self.url)

    @property
    def display_author(self) -> str:
        if self.author and self.author_name and self.author_name != self.author:
            return f"{self.author_name} (@{self.author})"
        return f"@{self.author}" if self.author else "未知来源"

    def short_text(self, limit: int = 180) -> str:
        text = " ".join(self.text.split())
        return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "author": self.author,
            "author_name": self.author_name,
            "author_followers": self.author_followers,
            "text": self.text,
            "created_at": self.created_at.isoformat(),
            "metrics": self.metrics.to_dict(),
            "source": self.source,
            "media_urls": list(self.media_urls),
            "lang": self.lang,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> Post:
        data = _as_dict(raw)
        created = data.get("created_at")
        created_at = (
            datetime.fromisoformat(str(created).replace("Z", "+00:00"))
            if created
            else datetime(1970, 1, 1, tzinfo=UTC)
        )
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        return cls(
            id=str(data.get("id") or data.get("url") or ""),
            url=str(data.get("url") or ""),
            author=str(data.get("author") or ""),
            author_name=str(data.get("author_name") or ""),
            author_followers=int(data.get("author_followers") or 0),
            text=str(data.get("text") or ""),
            created_at=created_at,
            metrics=Metrics.from_dict(data.get("metrics")),
            source=str(data.get("source") or "unknown"),
            media_urls=tuple(str(u) for u in (data.get("media_urls") or ())),
            lang=str(data.get("lang") or ""),
        )


def utc_now() -> datetime:
    return datetime.now(tz=UTC)
