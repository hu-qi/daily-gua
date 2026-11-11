"""X 公开镜像采集器（无凭据兜底）。

X 官方 API 需要付费凭据，而「原帖」的公开元数据可通过公开镜像拿到（``api.fxtwitter.com`` /
``api.vxtwitter.com`` 等）。本模块只读取**单条公开帖子**：正文、作者、互动指标、媒体链接，
不抓取私密内容、不做用户画像。
"""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

from ..common.http import HttpClient, HttpError
from ..common.models import Metrics, Post, handle_from_url, status_id_from_url

MIRROR_SOURCE = "x_mirror"


def _flatten(payload: Any) -> dict[str, Any]:
    """兼容 fxtwitter（``{"tweet": {...}}``）与 vxtwitter（扁平）两种返回。"""
    if not isinstance(payload, dict):
        raise HttpError("镜像返回格式异常")
    tweet = payload.get("tweet")
    if isinstance(tweet, dict):
        return tweet
    if "text" in payload:
        return payload
    raise HttpError("镜像返回缺少正文")


def _author(payload: dict[str, Any]) -> tuple[str, str, int]:
    author = payload.get("author")
    if isinstance(author, dict):
        handle = str(author.get("screen_name") or "").strip()
        name = str(author.get("name") or "").strip()
        followers = int(author.get("followers") or 0)
        return handle, name, followers
    return (
        str(payload.get("user_screen_name") or "").strip(),
        str(payload.get("user_name") or "").strip(),
        int(payload.get("user_followers") or 0),
    )


def _media_urls(payload: dict[str, Any]) -> tuple[str, ...]:
    urls: list[str] = []
    media = payload.get("media")
    if isinstance(media, dict):
        for item in media.get("all") or []:
            url = str((item or {}).get("url") or "").strip()
            if url:
                urls.append(url.split("?")[0])
    elif isinstance(media, list):
        for item in media:
            url = str((item or {}).get("url") or "").strip()
            if url:
                urls.append(url.split("?")[0])
    if not urls and payload.get("mediaURLs"):
        urls = [str(u).split("?")[0] for u in payload["mediaURLs"]]
    return tuple(urls)


def _created_at(payload: dict[str, Any]) -> datetime:
    stamp = payload.get("created_timestamp")
    if isinstance(stamp, (int, float)) and stamp > 0:
        return datetime.fromtimestamp(float(stamp), tz=UTC)
    for key in ("created_at", "date"):
        raw = payload.get(key)
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = parsedate_to_datetime(raw)
            except (TypeError, ValueError):
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed
    return datetime(1970, 1, 1, tzinfo=UTC)


def post_from_mirror_payload(payload: Any, *, fallback_url: str = "") -> Post:
    """把镜像响应解析成统一 Post。纯函数，便于用固定样本文档写测试。"""
    tweet = _flatten(payload)
    handle, name, followers = _author(tweet)
    status_id = str(tweet.get("id") or "").strip() or (status_id_from_url(fallback_url) or "")
    url = str(tweet.get("url") or "").strip() or fallback_url
    if not url and status_id:
        url = f"https://x.com/{handle or 'i'}/status/{status_id}"
    metrics = Metrics(
        likes=int(tweet.get("likes") or 0),
        replies=int(tweet.get("replies") or 0),
        reposts=int(tweet.get("retweets") or 0),
        quotes=int(tweet.get("quotes") or 0),
        views=int(tweet.get("views") or 0),
        bookmarks=int(tweet.get("bookmarks") or 0),
    )
    return Post(
        id=status_id or url,
        url=url,
        author=handle,
        author_name=name,
        author_followers=followers,
        text=str(tweet.get("text") or "").strip(),
        created_at=_created_at(tweet),
        metrics=metrics,
        source=MIRROR_SOURCE,
        media_urls=_media_urls(tweet),
        lang=str(tweet.get("lang") or ""),
    )


class XMirrorCollector:
    """按链接取单帖公开元数据。"""

    def __init__(self, http: HttpClient, *, base_url: str) -> None:
        self._http = http
        self._base = base_url.rstrip("/")

    def fetch_post(self, url: str) -> Post:
        status_id = status_id_from_url(url)
        if not status_id:
            raise HttpError(f"不是可识别的 X 状态链接: {url}", url=url)
        handle = handle_from_url(url)
        target = f"{self._base}/{handle or 'i'}/status/{status_id}"
        payload = self._http.get_json(target)
        post = post_from_mirror_payload(payload, fallback_url=url)
        if not post.text:
            raise HttpError("镜像返回的帖子正文为空", url=target)
        return post

    def fetch_by_id(self, status_id: str, *, handle: str | None = None) -> Post:
        if handle:
            url = f"https://x.com/{handle}/status/{status_id}"
        else:
            url = f"https://x.com/i/status/{status_id}"
        return self.fetch_post(url)


def is_x_status_url(url: str) -> bool:
    """判断是否为 X/Twitter 状态链接（决定走镜像还是通用网页探测）。"""
    parsed = urlparse(url)
    return status_id_from_url(url) is not None and parsed.netloc.lower().endswith(
        ("x.com", "twitter.com")
    )
