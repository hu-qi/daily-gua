"""RSS 2.0 feed 生成（``feed.xml``），供订阅器与 Pages 使用。

用 stdlib 手工拼 XML 并显式转义，避免引入 feedgen 依赖（Actions 里少一个依赖就少一种失败模式）。
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from xml.sax.saxutils import escape

from ..common.frontmatter import ReportDigest

FEED_FILE = "feed.xml"
DEFAULT_TITLE = "🍉 每日一瓜"
DEFAULT_DESCRIPTION = "每天北京时间 09:00，自动整理科技 / AI 圈当天最热的一个瓜。"
PUBLISH_HOUR_UTC = 1  # 北京时间 09:00


def _rfc822(date_str: str) -> str:
    try:
        day = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        day = datetime.now(tz=UTC).date()
    stamp = datetime.combine(day, time(PUBLISH_HOUR_UTC, 0), tzinfo=UTC)
    return stamp.strftime("%a, %d %b %Y %H:%M:%S +0000")


def render_feed(
    reports: list[ReportDigest],
    *,
    site_url: str,
    title: str = DEFAULT_TITLE,
    description: str = DEFAULT_DESCRIPTION,
    limit: int = 20,
    now: datetime | None = None,
) -> str:
    """生成 RSS 2.0；无瓜报时也输出合法 feed（订阅端不报错）。"""
    base = site_url.rstrip("/")
    build_stamp = (now or datetime.now(tz=UTC)).astimezone(UTC)
    lines: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        "  <channel>",
        f"    <title>{escape(title)}</title>",
        f"    <link>{escape(base)}</link>",
        f"    <description>{escape(description)}</description>",
        "    <language>zh-CN</language>",
        f"    <lastBuildDate>{build_stamp.strftime('%a, %d %b %Y %H:%M:%S +0000')}</lastBuildDate>",
        f'    <atom:link href="{escape(base)}/{FEED_FILE}" rel="self" type="application/rss+xml"/>',
    ]
    for digest in reports[:limit]:
        link = f"{base}/gua/{digest.date}.md"
        headline = digest.headline or digest.title or digest.date
        summary = (
            f"{headline}（吃瓜指数 {digest.melon_score}/100 · {digest.melon_level}）"
            if digest.melon_level
            else headline
        )
        lines.extend(
            [
                "    <item>",
                f"      <title>{escape(f'{DEFAULT_TITLE} {digest.date} · {headline}')}</title>",
                f"      <link>{escape(link)}</link>",
                f'      <guid isPermaLink="true">{escape(link)}</guid>',
                f"      <pubDate>{_rfc822(digest.date)}</pubDate>",
                f"      <description>{escape(summary)}</description>",
                "    </item>",
            ]
        )
    lines.extend(["  </channel>", "</rss>", ""])
    return "\n".join(lines)


def feed_path(directory: str = ".") -> str:
    from pathlib import Path

    return str(Path(directory) / FEED_FILE)
