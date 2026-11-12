"""RSS / Atom 公开源采集（凭据完全缺失时的兜底通道）。

RSS 只有标题、摘要与链接，没有互动指标；rank 层会把零指标帖子排在 X 帖子之后，
仅当 X 通道无结果时才会成为当日主角。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from ..common.http import HttpClient, HttpError
from ..common.models import Post


@dataclass(frozen=True)
class FeedSpec:
    name: str
    url: str


def _local(tag: str) -> str:
    """去掉 XML 命名空间，统一按本地名匹配（RSS/Atom 混用）。"""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _find_text(node: ET.Element, *names: str) -> str:
    for child in node:
        if _local(child.tag) in names and (child.text or "").strip():
            return (child.text or "").strip()
    return ""


def _find_author(node: ET.Element) -> str:
    """RSS 用扁平「creator」，Atom 用嵌套「author/name」，两种都要取到。"""
    for child in node:
        tag = _local(child.tag)
        if tag == "creator" and (child.text or "").strip():
            return (child.text or "").strip()
        if tag == "author":
            if (child.text or "").strip():
                return (child.text or "").strip()
            for sub in child:
                if _local(sub.tag) == "name" and (sub.text or "").strip():
                    return (sub.text or "").strip()
    return ""


def _find_link(node: ET.Element) -> str:
    for child in node:
        if _local(child.tag) != "link":
            continue
        href = child.get("href")
        if href:
            return href.strip()
        if (child.text or "").strip():
            return (child.text or "").strip()
    return ""


def _parse_date(raw: str) -> datetime:
    if not raw:
        return datetime(1970, 1, 1, tzinfo=UTC)
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return datetime(1970, 1, 1, tzinfo=UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _clean(text: str, limit: int = 600) -> str:
    """压平 HTML 残留与多余空白，保留可读摘要。"""
    import re

    plain = re.sub(r"<[^>]+>", " ", text or "")
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain[:limit]


def parse_feed(xml_text: str, *, feed_name: str = "rss", limit: int = 30) -> list[Post]:
    """解析 RSS 2.0 / Atom，返回统一 Post（无互动指标）。"""
    if not xml_text.strip():
        return []
    try:
        root = ET.fromstring(xml_text)  # noqa: S314 - 仅解析受信任的公开 feed，且不处理外部实体
    except ET.ParseError as exc:
        raise HttpError(f"feed 解析失败: {feed_name}") from exc

    items = [node for node in root.iter() if _local(node.tag) in {"item", "entry"}]
    posts: list[Post] = []
    for item in items[:limit]:
        link = _find_link(item)
        title = _find_text(item, "title")
        if not link or not title:
            continue
        summary = _find_text(item, "description", "summary", "content", "encoded")
        author = _find_author(item)
        published = _find_text(item, "pubDate", "published", "updated", "date")
        posts.append(
            Post(
                id=link,
                url=link,
                author=author or feed_name,
                text=f"{title}\n{_clean(summary)}".strip(),
                created_at=_parse_date(published),
                source="rss",
            )
        )
    return posts


class RssCollector:
    """按 feed 列表抓取；单个 feed 失败只记录告警，不阻断整体流程。"""

    def __init__(self, http: HttpClient, feeds: tuple[FeedSpec, ...]) -> None:
        self._http = http
        self._feeds = feeds

    def fetch(self) -> tuple[list[Post], list[str]]:
        posts: list[Post] = []
        warnings: list[str] = []
        for feed in self._feeds:
            try:
                xml_text = self._http.get_text(feed.url, {"Accept": "application/rss+xml, */*"})
                parsed = parse_feed(xml_text, feed_name=feed.name)
                if not parsed:
                    warnings.append(f"{feed.name}: 未解析到条目")
                posts.extend(parsed)
            except HttpError as exc:
                warnings.append(f"{feed.name}: {exc}")
        return posts, warnings


def default_feeds(urls: tuple[str, ...]) -> tuple[FeedSpec, ...]:
    """用 URL 生成 FeedSpec；名称取域名主标签，用于作者兜底展示。"""
    from urllib.parse import urlparse

    specs: list[FeedSpec] = []
    for url in urls:
        host = urlparse(url).netloc.lower().removeprefix("www.")
        label = host.split(".")[0] if host else "rss"
        specs.append(FeedSpec(name=label, url=url))
    return tuple(specs)


def feed_document(items: list[dict[str, Any]]) -> str:  # pragma: no cover - 预留扩展点
    """占位：如需自建 feed 在此实现。"""
    raise NotImplementedError
