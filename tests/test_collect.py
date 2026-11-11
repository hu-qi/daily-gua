"""collect 层：镜像解析、RSS/Atom 解析、通道降级与去重。"""

from __future__ import annotations

from src.collect.rss import FeedSpec, RssCollector, parse_feed
from src.collect.service import collect
from src.collect.web import parse_html_document
from src.collect.x_mirror import is_x_status_url, post_from_mirror_payload
from src.common.config import Settings
from src.common.http import HttpClient
from tests.conftest import (
    ATOM_SAMPLE,
    RSS_SAMPLE,
    SEED_URL,
    FakeTransport,
    json_route,
    make_post,
)


def _client(transport: FakeTransport) -> HttpClient:
    return HttpClient(transport=transport, retries=0, sleep=lambda _: None)


def test_mirror_payload_maps_to_post(mirror_payload):
    post = post_from_mirror_payload(mirror_payload, fallback_url=SEED_URL)
    assert post.author == "criscxuan"
    assert post.author_name == "cxuan"
    assert post.author_followers == 3087
    assert post.metrics.likes == 190
    assert post.metrics.views == 48097
    assert post.url == SEED_URL
    assert post.source == "x_mirror"
    assert post.media_urls == ("https://pbs.twimg.com/media/AAAA.jpg",)
    assert post.created_at.tzinfo is not None


def test_mirror_payload_supports_flat_vxtwitter_shape():
    payload = {
        "text": "某某翻车了",
        "user_screen_name": "someone",
        "user_name": "某人",
        "likes": 5,
        "retweets": 1,
        "replies": 2,
        "quotes": 0,
        "views": 100,
        "date": "Wed Nov 11 02:02:48 +0000 2026",
        "id": "42",
    }
    post = post_from_mirror_payload(payload, fallback_url="https://x.com/someone/status/42")
    assert post.author == "someone"
    assert post.metrics.engagement > 0
    assert post.created_at.year == 2026


def test_is_x_status_url():
    assert is_x_status_url(SEED_URL)
    assert not is_x_status_url("https://example.com/posts/1")


def test_parse_rss_and_atom():
    rss_posts = parse_feed(RSS_SAMPLE, feed_name="techcrunch")
    assert len(rss_posts) == 1
    assert rss_posts[0].url == "https://example.com/posts/1"
    assert rss_posts[0].author == "rookie"
    assert "发布会现场演示失败" in rss_posts[0].text
    assert "<p>" not in rss_posts[0].text

    atom_posts = parse_feed(ATOM_SAMPLE, feed_name="atom")
    assert len(atom_posts) == 1
    assert atom_posts[0].url == "https://example.com/posts/2"
    assert atom_posts[0].author == "anon"


def test_rss_collector_tolerates_broken_feed():
    transport = FakeTransport(
        routes={
            "https://good.example": (200, RSS_SAMPLE.encode()),
            "https://bad.example": (500, b"boom"),
        }
    )
    collector = RssCollector(
        _client(transport),
        (FeedSpec("good", "https://good.example/feed"), FeedSpec("bad", "https://bad.example/feed")),
    )
    posts, warnings = collector.fetch()
    assert len(posts) == 1
    assert any("bad" in warning for warning in warnings)


def test_parse_html_document_extracts_meta():
    html = (
        "<html><head><title>标题 | 站点</title>"
        '<meta property="og:description" content="一段摘要"></head></html>'
    )
    doc = parse_html_document(html, url="https://example.com/x")
    assert doc.ok
    assert doc.title == "标题 | 站点"
    assert doc.description == "一段摘要"


def test_collect_falls_back_when_credentials_missing(mirror_payload):
    """验收要求：采集凭据缺失时走公开镜像 / RSS 兜底仍能产出。"""
    transport = FakeTransport(
        routes={
            "https://api.fxtwitter.com": json_route(mirror_payload),
            "https://feed.example": (200, RSS_SAMPLE.encode()),
        }
    )
    settings = Settings(seed_urls=(SEED_URL,), rss_feeds=("https://feed.example/rss",))
    result = collect(settings, _client(transport))
    assert result.degraded is True
    assert any(source.startswith("x_mirror") for source in result.sources)
    assert any(source == "rss" for source in result.sources)
    assert len(result.posts) == 2
    assert any("X_BEARER_TOKEN" in warning for warning in result.warnings)


def test_collect_dedupes_same_post_from_two_channels(mirror_payload):
    transport = FakeTransport(routes={"https://api.fxtwitter.com": json_route(mirror_payload)})
    settings = Settings(seed_urls=(SEED_URL, SEED_URL))
    result = collect(settings, _client(transport))
    assert len(result.posts) == 1


def test_collect_reports_total_failure():
    transport = FakeTransport(default=(500, b"nope"))
    settings = Settings(seed_urls=(SEED_URL,), rss_feeds=("https://feed.example/rss",))
    result = collect(settings, _client(transport))
    assert result.posts == []
    assert result.degraded is True
    assert any("均无结果" in warning for warning in result.warnings)


def test_collect_seed_web_url_uses_probe():
    transport = FakeTransport(
        routes={
            "https://blog.example": (
                200,
                b"<html><head><title>Blog post</title></head><body></body></html>",
            )
        }
    )
    settings = Settings(seed_urls=("https://blog.example/post",), rss_feeds=())
    result = collect(settings, _client(transport))
    assert len(result.posts) == 1
    assert result.posts[0].source == "seed"


def test_make_post_helper_key(sample_post):
    assert sample_post.key == "x:2120336410989433287"
    assert make_post(post_id="1", url="https://x.com/a/status/1").key == "x:1"
