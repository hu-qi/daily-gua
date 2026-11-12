"""collect 层：把 X / 公开镜像 / RSS / 网页统一成 ``Post``。"""

from __future__ import annotations

from .rss import FeedSpec, RssCollector, default_feeds, parse_feed
from .service import (
    CollectionResult,
    collect,
    collect_from_rss,
    collect_from_x_api,
    collect_seed_posts,
    merge_results,
)
from .web import WebDoc, WebProbe, parse_html_document
from .x_api import XApiCollector
from .x_mirror import XMirrorCollector, is_x_status_url, post_from_mirror_payload

__all__ = [
    "CollectionResult",
    "FeedSpec",
    "RssCollector",
    "WebDoc",
    "WebProbe",
    "XApiCollector",
    "XMirrorCollector",
    "collect",
    "collect_from_rss",
    "collect_from_x_api",
    "collect_seed_posts",
    "default_feeds",
    "is_x_status_url",
    "merge_results",
    "parse_feed",
    "parse_html_document",
    "post_from_mirror_payload",
]
