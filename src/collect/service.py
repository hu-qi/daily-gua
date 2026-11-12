"""采集编排：X API 优先 → 种子链接走公开镜像 → RSS 公开源兜底。

设计原则：**任何单通道失败都不阻断出报**。返回结果里带 ``warnings`` 与 ``degraded``，
由 agent/publish 层在瓜报与 Issue 中明示数据来源，避免「看起来像全量采集」的误导。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..common.config import Settings
from ..common.http import HttpClient, HttpError
from ..common.models import Post, utc_now
from ..common.redaction import get_logger
from .rss import RssCollector, default_feeds
from .web import WebProbe
from .x_api import XApiCollector
from .x_mirror import XMirrorCollector, is_x_status_url

LOGGER = get_logger(__name__)

SOURCE_PRIORITY = {"x_api": 4, "x_mirror": 3, "web": 2, "seed": 2, "rss": 1, "unknown": 0}


@dataclass
class CollectionResult:
    posts: list[Post] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    degraded: bool = False

    def add_source(self, name: str) -> None:
        if name not in self.sources:
            self.sources.append(name)


def merge_results(results: list[CollectionResult]) -> CollectionResult:
    merged = CollectionResult()
    index: dict[str, Post] = {}
    for result in results:
        for post in result.posts:
            existing = index.get(post.key)
            if existing is None:
                index[post.key] = post
                continue
            # 同一帖子多通道命中：保留指标更全、来源更可靠的一条
            if SOURCE_PRIORITY.get(post.source, 0) > SOURCE_PRIORITY.get(existing.source, 0) or (
                post.metrics.engagement > existing.metrics.engagement
            ):
                index[post.key] = post
        for source in result.sources:
            merged.add_source(source)
        merged.warnings.extend(result.warnings)
        merged.degraded = merged.degraded or result.degraded
    merged.posts = sorted(index.values(), key=lambda p: p.created_at, reverse=True)
    return merged


def collect_from_x_api(settings: Settings, http: HttpClient) -> CollectionResult:
    result = CollectionResult()
    if not settings.x_api_configured or settings.x_bearer_token is None:
        result.warnings.append("X_BEARER_TOKEN 未配置，跳过 X 官方 API 采集")
        result.degraded = True
        return result

    collector = XApiCollector(http, settings.x_bearer_token, base_url=settings.x_api_base)
    for keyword in settings.x_keywords:
        try:
            posts = collector.recent_search(keyword)
            result.posts.extend(posts)
            result.add_source(f"x_api:search:{keyword}")
        except HttpError as exc:
            result.warnings.append(f"X 关键词检索失败（{keyword}）: {exc}")
            result.degraded = True
    for handle in settings.x_handles:
        try:
            posts = collector.user_timeline(handle)
            result.posts.extend(posts)
            result.add_source(f"x_api:user:{handle.lstrip('@')}")
        except HttpError as exc:
            result.warnings.append(f"X 关注列表采集失败（{handle}）: {exc}")
            result.degraded = True
    return result


def collect_seed_posts(
    settings: Settings,
    http: HttpClient,
    seeds: tuple[str, ...],
    *,
    fallback: bool = False,
) -> CollectionResult:
    """种子链接：X 链接走公开镜像，其他链接走网页探测。

    ``fallback=True`` 表示这是「无新素材」时才启用的兜底种子，会在来源里
    单独标注 ``seed:fallback``，供瓜报如实披露。
    """
    result = CollectionResult()
    if not seeds:
        return result
    mirror = (
        XMirrorCollector(http, base_url=settings.mirror_base) if settings.mirror_enabled else None
    )
    probe = WebProbe(http)
    for url in seeds:
        try:
            if is_x_status_url(url):
                if mirror is None:
                    result.warnings.append(f"镜像通道关闭，跳过种子帖: {url}")
                    result.degraded = True
                    continue
                result.posts.append(mirror.fetch_post(url))
                result.add_source("x_mirror:seed")
            else:
                doc = probe.fetch(url)
                if not doc.ok:
                    result.warnings.append(f"种子链接不可读（{url}）: {doc.note}")
                    result.degraded = True
                    continue
                from ..common.models import Post

                result.posts.append(
                    Post(
                        id=url,
                        url=url,
                        author=doc.url.split("/")[2] if doc.url.count("/") >= 3 else "web",
                        text=doc.as_text(),
                        created_at=utc_now(),
                        source="seed",
                    )
                )
                result.add_source("web:seed")
        except HttpError as exc:
            result.warnings.append(f"种子链接采集失败（{url}）: {exc}")
            result.degraded = True
    if fallback and result.posts:
        result.add_source("seed:fallback")
        result.warnings.append("候选池与采集均无新内容，已启用内置兜底种子")
    return result


def collect_from_rss(settings: Settings, http: HttpClient) -> CollectionResult:
    result = CollectionResult()
    feeds = default_feeds(settings.rss_feeds)
    if not feeds:
        return result
    posts, warnings = RssCollector(http, feeds).fetch()
    result.posts.extend(posts)
    result.warnings.extend(warnings)
    if posts:
        result.add_source("rss")
    else:
        result.degraded = True
    return result


def collect(
    settings: Settings,
    http: HttpClient | None = None,
    *,
    seeds: tuple[str, ...] | None = None,
) -> CollectionResult:
    """按优先级采集并合并；返回空结果时由上层报错退出（不静默出空报）。"""
    client = http or HttpClient(timeout=settings.http_timeout_s, retries=settings.http_retries)
    effective_seeds = settings.seed_urls if seeds is None else seeds
    parts = [
        collect_from_x_api(settings, client),
        collect_seed_posts(settings, client, effective_seeds),
        collect_from_rss(settings, client),
    ]
    merged = merge_results(parts)
    if not merged.posts:
        merged.warnings.append("所有采集通道均无结果")
        merged.degraded = True
    else:
        LOGGER.info(
            "采集完成：%d 条候选，来源 %s，告警 %d 条",
            len(merged.posts),
            ",".join(merged.sources) or "无",
            len(merged.warnings),
        )
    return merged
