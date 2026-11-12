"""测试公共设施：固定时钟、假传输层、假 LLM、样本文档。

原则（见 SKILL.md 六·Best Practices）：
- 时间一律固定（``FIXED_NOW``），断言用范围/增量，不做毫秒级相等比较；
- 网络一律通过 ``FakeTransport`` 注入，用例不依赖真实外网；
- LLM 一律用 ``FakeLLM``，用例不依赖真实凭据。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from src.common.http import HttpClient
from src.common.models import Metrics, Post

FIXED_NOW = datetime(2026, 11, 11, 6, 0, tzinfo=UTC)
SEED_URL = "https://x.com/criscxuan/status/2120336410989433287"


def make_post(
    *,
    post_id: str = "2120336410989433287",
    url: str = SEED_URL,
    author: str = "criscxuan",
    text: str = "某知名 AI 博主被大号给爆锤了",
    created_at: datetime | None = None,
    likes: int = 190,
    replies: int = 48,
    reposts: int = 12,
    quotes: int = 10,
    views: int = 48097,
    followers: int = 3087,
    source: str = "x_mirror",
    media: tuple[str, ...] = (),
) -> Post:
    return Post(
        id=post_id,
        url=url,
        author=author,
        author_name=author,
        author_followers=followers,
        text=text,
        created_at=created_at or datetime(2026, 11, 11, 2, 2, 48, tzinfo=UTC),
        metrics=Metrics(
            likes=likes,
            replies=replies,
            reposts=reposts,
            quotes=quotes,
            views=views,
        ),
        source=source,
        media_urls=media,
    )


@dataclass
class FakeTransport:
    """按 URL 前缀返回固定响应；记录调用次数，便于断言重试行为。"""

    routes: dict[str, tuple[int, bytes]] = field(default_factory=dict)
    default: tuple[int, bytes] = (404, b"")
    calls: list[tuple[str, str]] = field(default_factory=list)
    failures: int = 0  # 前 N 次调用返回 500（模拟瞬时故障）
    fail_status: int = 500

    def __call__(self, method, url, headers, data, timeout):  # noqa: ANN001 - 传输层协议
        self.calls.append((method, url))
        if self.failures > 0:
            self.failures -= 1
            return self.fail_status, b'{"error":"temporary"}'
        for key, response in self.routes.items():
            method_prefix, _, prefix = key.rpartition(" ")
            if method_prefix and method_prefix.upper() in {"GET", "POST", "PATCH", "PUT", "DELETE"}:
                if method.upper() == method_prefix.upper() and url.startswith(prefix):
                    return response
            elif url.startswith(key):
                return response
        return self.default

    @property
    def call_count(self) -> int:
        return len(self.calls)


def json_route(payload: object, status: int = 200) -> tuple[int, bytes]:
    return status, json.dumps(payload, ensure_ascii=False).encode("utf-8")


@pytest.fixture()
def fixed_now() -> datetime:
    return FIXED_NOW


@pytest.fixture()
def sample_post() -> Post:
    return make_post()


@pytest.fixture()
def http_client() -> HttpClient:
    """无重试、无等待、无网络的客户端。"""
    return HttpClient(transport=FakeTransport(), retries=0, sleep=lambda _: None)


@pytest.fixture()
def mirror_payload() -> dict:
    return {
        "code": 200,
        "message": "OK",
        "tweet": {
            "url": SEED_URL,
            "id": "2120336410989433287",
            "text": "某知名 AI 博主被大号给爆锤了",
            "author": {
                "screen_name": "criscxuan",
                "name": "cxuan",
                "followers": 3087,
            },
            "replies": 48,
            "retweets": 12,
            "likes": 190,
            "bookmarks": 30,
            "quotes": 10,
            "views": 48097,
            "created_timestamp": 1794362568,
            "lang": "zh",
            "media": {
                "all": [
                    {
                        "type": "photo",
                        "url": "https://pbs.twimg.com/media/AAAA.jpg?name=orig",
                    }
                ]
            },
        },
    }


RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>AI Feed</title>
<item>
  <title>某模型发布翻车</title>
  <link>https://example.com/posts/1</link>
  <description><![CDATA[<p>发布会现场演示失败</p>]]></description>
  <pubDate>Wed, 11 Nov 2026 02:00:00 +0000</pubDate>
  <dc:creator xmlns:dc="http://purl.org/dc/elements/1.1/">rookie</dc:creator>
</item>
</channel></rss>
"""

ATOM_SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Feed</title>
  <entry>
    <title>争议言论引战</title>
    <link href="https://example.com/posts/2"/>
    <summary>当事人已回应</summary>
    <updated>2026-11-11T03:00:00Z</updated>
    <author><name>anon</name></author>
  </entry>
</feed>
"""


class FakeLLM:
    """可控的 LLM 客户端替身。"""

    def __init__(self, response: str = "", *, available: bool = True, error: Exception | None = None):
        self.response = response
        self.available = available
        self.error = error
        self.calls = 0
        self.last_messages: list[dict[str, str]] = []

    def complete(self, messages, *, max_tokens=None, temperature=None):  # noqa: ANN001
        self.calls += 1
        self.last_messages = messages
        if self.error is not None:
            raise self.error
        return self.response
