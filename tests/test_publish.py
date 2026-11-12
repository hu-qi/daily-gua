"""publish 层：落盘、README 索引、feed、Issue 客户端、Pages 构建。"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import pytest
from src.common.http import HttpClient
from src.publish.feed import render_feed
from src.publish.issue import BUSINESS_LABELS, GitHubClient, issue_title
from src.publish.pages import build_site, markdown_to_html
from src.publish.readme import END_MARKER, START_MARKER, render_index, update_readme
from src.publish.writer import list_reports, read_report, report_path, write_report
from tests.conftest import FakeTransport, json_route

FEED_NAME = "feed.xml"

REPORT = """---
date: "2026-11-11"
title: "🍉 每日一瓜 2026-11-11"
headline: "AI 圈互撕"
melon_score: 74
melon_level: "热"
generated_by: "llm"
primary_url: "https://x.com/criscxuan/status/2120336410989433287"
sources:
  - "https://x.com/criscxuan/status/2120336410989433287"
---

# 🍉 每日一瓜 · 2026-11-11

> AI 圈互撕

## ⚠️ 未证实 / 待核实

- 对方身份未点名
- 截图内容未核实

## 关键原文

> **cxuan**：某知名 AI 博主被大号给爆锤了
>
> [原始链接](https://x.com/criscxuan/status/2120336410989433287)
"""


def test_write_and_read_report(tmp_path):
    path = write_report(tmp_path, "2026-11-11", REPORT)
    assert path == tmp_path / "2026-11-11.md"
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert read_report(tmp_path, "2026-11-11") == path.read_text(encoding="utf-8")


def test_write_report_rejects_bad_date(tmp_path):
    with pytest.raises(ValueError):
        write_report(tmp_path, "11/11/2026", REPORT)


def test_write_report_is_atomic_overwrite(tmp_path):
    write_report(tmp_path, "2026-11-11", REPORT)
    write_report(tmp_path, "2026-11-11", REPORT.replace("74", "88"))
    assert "88" in report_path(tmp_path, "2026-11-11").read_text(encoding="utf-8")
    assert not list(tmp_path.glob("*.tmp"))


def test_list_reports_sorted_desc(tmp_path):
    write_report(tmp_path, "2026-11-09", REPORT.replace("2026-11-11", "2026-11-09"))
    write_report(tmp_path, "2026-11-11", REPORT)
    (tmp_path / "notes.md").write_text("no front matter", encoding="utf-8")
    digests = list_reports(tmp_path)
    assert [d.date for d in digests] == ["2026-11-11", "2026-11-09"]
    assert digests[0].melon_score == 74
    assert digests[0].primary_url.startswith("https://x.com/")


def test_readme_index_replacement_is_idempotent(tmp_path):
    template = f"# 项目\n\n说明文字\n\n{START_MARKER}\n旧的索引\n{END_MARKER}\n\n尾部内容\n"
    first = update_readme(template, [], site_url="https://o.github.io/r")
    second = update_readme(first, [], site_url="https://o.github.io/r")
    assert first == second
    assert "旧的索引" not in first
    assert "还没有瓜报" in first
    assert "尾部内容" in first
    assert "说明文字" in first


def test_readme_index_lists_reports(tmp_path):
    write_report(tmp_path, "2026-11-11", REPORT)
    digests = list_reports(tmp_path)
    index = render_index(digests, site_url="https://o.github.io/r")
    assert "最新一页" in index
    assert "2026-11-11" in index
    assert "gua/2026-11-11.md" in index


def test_readme_appends_when_markers_missing():
    updated = update_readme("# 项目\n", [], site_url="")
    assert START_MARKER in updated and updated.index("# 项目") < updated.index(START_MARKER)


def test_feed_is_valid_rss(tmp_path):
    write_report(tmp_path, "2026-11-11", REPORT)
    digests = list_reports(tmp_path)
    feed = render_feed(digests, site_url="https://o.github.io/r")
    root = ET.fromstring(feed)  # 解析成功即合法 XML
    assert root.tag == "rss"
    channel = root.find("channel")
    assert channel is not None
    items = channel.findall("item")
    assert len(items) == 1
    assert items[0].findtext("link") == "https://o.github.io/r/gua/2026-11-11.md"
    assert items[0].findtext("pubDate", "").endswith("+0000")


def test_feed_escapes_special_characters(tmp_path):
    write_report(tmp_path, "2026-11-11", REPORT.replace("AI 圈互撕", "A & B <c>"))
    feed = render_feed(list_reports(tmp_path), site_url="https://o.github.io/r")
    ET.fromstring(feed)
    assert "&amp;" in feed or "&lt;" in feed


def test_feed_without_reports_is_still_valid():
    feed = render_feed([], site_url="https://o.github.io/r")
    root = ET.fromstring(feed)
    assert root.find("channel") is not None
    assert root.find("channel").findall("item") == []


def _client(transport: FakeTransport) -> GitHubClient:
    return GitHubClient(
        HttpClient(transport=transport, retries=0, sleep=lambda _: None),
        token="gh-test-token",
        repository="o/r",
    )


def test_github_client_unavailable_without_credentials():
    assert GitHubClient(token=None, repository=None).available is False
    assert GitHubClient(token="t", repository="o/r").available is True


def test_ensure_issue_creates_when_absent():
    transport = FakeTransport(
        routes={
            "GET https://api.github.com/repos/o/r/labels": json_route(
                [{"name": name} for name in BUSINESS_LABELS]
            ),
            "GET https://api.github.com/search/issues": json_route({"items": []}),
            "POST https://api.github.com/repos/o/r/issues": json_route(
                {"number": 7, "html_url": "https://github.com/o/r/issues/7"}
            ),
        }
    )
    issue, created = _client(transport).ensure_issue(
        title=issue_title("2026-11-11"), body="body", labels=["gua"]
    )
    assert created is True
    assert issue["number"] == 7
    posts = [call for call in transport.calls if call[0] == "POST"]
    assert len(posts) == 1  # 标签已存在 → 只发一次建帖请求


@pytest.mark.parametrize("name", ["gua", "daily-gua", "gua-submit", "gua-accepted"])
def test_business_labels_are_declared_for_bootstrap(name):
    """四个业务标签必须由代码自建，不能依赖维护者手工创建。"""
    assert name in BUSINESS_LABELS


def test_ensure_labels_creates_only_missing():
    transport = FakeTransport(
        routes={
            "GET https://api.github.com/repos/o/r/labels": json_route(
                [{"name": "bug"}, {"name": "gua"}]
            ),
            "POST https://api.github.com/repos/o/r/labels": json_route({"name": "x"}, status=201),
        }
    )
    status = _client(transport).ensure_labels(["gua", "daily-gua", "BUG"])
    assert status == {"gua": "exists", "daily-gua": "created", "BUG": "exists"}
    posts = [call for call in transport.calls if call[0] == "POST"]
    assert len(posts) == 1


def test_ensure_labels_tolerates_already_exists_race():
    """并发创建返回 422/409 时按已存在处理，不抛异常。"""
    calls: list[tuple[str, str]] = []

    def transport(method, url, headers, data, timeout):  # noqa: ANN001 - 传输层协议
        calls.append((method, url))
        if method == "GET" and "/labels" in url:
            return 200, json.dumps([{"name": "gua"}]).encode()
        if method == "POST" and url.endswith("/labels"):
            return 422, b'{"message":"already_exists"}'
        return 404, b""

    client = GitHubClient(
        HttpClient(transport=transport, retries=0, sleep=lambda _: None),
        token="gh-test-token",
        repository="o/r",
    )
    status = client.ensure_labels(["gua", "daily-gua"])
    assert status == {"gua": "exists", "daily-gua": "exists"}


def test_ensure_issue_retries_without_labels_on_422():
    """标签缺失导致 422：去标签重试并留下告警，绝不让标签阻断出报。"""
    created_bodies: list[dict] = []

    def transport(method, url, headers, data, timeout):  # noqa: ANN001 - 传输层协议
        if method == "GET" and url.endswith("/labels?per_page=100"):
            return 200, json.dumps([{"name": "gua"}, {"name": "daily-gua"}]).encode()
        if method == "GET" and "search/issues" in url:
            return 200, json.dumps({"items": []}).encode()
        if method == "POST" and url.endswith("/issues"):
            created_bodies.append(json.loads(data.decode()) if data else {})
            if len(created_bodies) == 1:
                return 422, b'{"message":"Validation Failed"}'
            return 201, json.dumps({"number": 9, "html_url": "u"}).encode()
        return 404, b""

    client = GitHubClient(
        HttpClient(transport=transport, retries=0, sleep=lambda _: None),
        token="gh-test-token",
        repository="o/r",
    )
    issue, created = client.ensure_issue(
        title=issue_title("2026-11-11"), body="body", labels=["gua", "daily-gua"]
    )
    assert created is True
    assert issue["number"] == 9
    assert "labels" in created_bodies[0]
    assert "labels" not in created_bodies[1]  # 重试时去标签
    assert any("去标签" in warning for warning in client.drain_warnings())


def test_add_labels_does_not_raise_when_label_missing():
    """gua-accepted 不存在时不能中断回执链路：建标后打标，失败只告警。"""
    transport = FakeTransport(
        routes={
            "GET https://api.github.com/repos/o/r/labels": json_route([{"name": "bug"}]),
            "POST https://api.github.com/repos/o/r/labels": json_route({"name": "gua-accepted"}),
            "POST https://api.github.com/repos/o/r/issues/7/labels": json_route(
                [{"name": "gua-accepted"}]
            ),
        }
    )
    client = _client(transport)
    labels = client.add_labels(7, ["gua-accepted"])
    assert labels == [{"name": "gua-accepted"}]
    posts = [call for call in transport.calls if call[0] == "POST"]
    assert len(posts) == 2  # 先建标、再打标
    assert client.drain_warnings() == []


@pytest.mark.parametrize("status", [403, 404, 422])
def test_add_labels_skips_when_label_creation_fails(status):
    def transport(method, url, headers, data, timeout):  # noqa: ANN001 - 传输层协议
        if method == "GET" and "/labels" in url:
            return 200, json.dumps([{"name": "bug"}]).encode()
        if method == "POST":
            return status, b'{"message":"nope"}'
        return 404, b""

    client = GitHubClient(
        HttpClient(transport=transport, retries=0, sleep=lambda _: None),
        token="gh-test-token",
        repository="o/r",
    )
    assert client.add_labels(7, ["gua-accepted"]) == []
    assert any("失败" in warning for warning in client.drain_warnings())


def test_ensure_issue_is_idempotent_by_title():
    transport = FakeTransport(
        routes={
            "GET https://api.github.com/search/issues": json_route(
                {"items": [{"number": 3, "title": issue_title("2026-11-11")}]}
            )
        }
    )
    issue, created = _client(transport).ensure_issue(
        title=issue_title("2026-11-11"), body="body"
    )
    assert created is False
    assert issue["number"] == 3
    assert transport.call_count == 1


def test_ensure_issue_falls_back_to_list_when_search_misses():
    """搜索索引有延迟：搜索成功但未命中时也必须回退列表，否则同日重跑会重复建帖。"""
    transport = FakeTransport(
        routes={
            "GET https://api.github.com/search/issues": json_route({"items": []}),
            "GET https://api.github.com/repos/o/r/issues?state=all": json_route(
                [
                    {"number": 3, "title": issue_title("2026-11-11")},
                    {"number": 4, "title": "别的 Issue"},
                ]
            ),
        }
    )
    issue, created = _client(transport).ensure_issue(
        title=issue_title("2026-11-11"), body="body"
    )
    assert created is False
    assert issue["number"] == 3
    assert [call[1] for call in transport.calls if "/issues?state=all" in call[1]]


def test_find_issue_by_title_ignores_pull_requests():
    transport = FakeTransport(
        routes={
            "GET https://api.github.com/search/issues": json_route({"items": []}),
            "GET https://api.github.com/repos/o/r/issues?state=all": json_route(
                [{"number": 9, "title": issue_title("2026-11-11"), "pull_request": {}}]
            ),
        }
    )
    assert _client(transport).find_issue_by_title(issue_title("2026-11-11")) is None


def test_comment_and_list_comments():
    transport = FakeTransport(
        routes={
            "GET https://api.github.com/repos/o/r/labels": json_route(
                [{"name": "gua-accepted"}]
            ),
            "GET https://api.github.com/repos/o/r/issues/7/comments": json_route(
                [{"id": 11, "body": "/vote 🍉"}]
            ),
            "POST https://api.github.com/repos/o/r/issues/7/comments": json_route(
                {"id": 12, "html_url": "https://github.com/o/r/issues/7#c12"}
            ),
            "PATCH https://api.github.com/repos/o/r/issues/7": json_route(
                {"number": 7, "body": "new"}
            ),
            "POST https://api.github.com/repos/o/r/issues/7/labels": json_route(
                [{"name": "gua-accepted"}]
            ),
        }
    )
    client = _client(transport)
    assert client.list_comments(7)[0]["id"] == 11
    assert client.comment(7, "hi")["id"] == 12
    assert client.update_issue_body(7, "new")["body"] == "new"
    assert client.add_labels(7, ["gua-accepted"])[0]["name"] == "gua-accepted"


def test_markdown_to_html_renders_core_elements():
    html = markdown_to_html(
        "# 标题\n\n> 引用\n\n- 项一\n- 项二\n\n| a | b |\n| - | - |\n| 1 | 2 |\n\n**粗** [链](https://x.com/a)"
    )
    assert "<h1>标题</h1>" in html
    assert "<blockquote>" in html
    assert "<li>项一</li>" in html
    assert "<table>" in html
    assert "<strong>粗</strong>" in html
    assert '<a href="https://x.com/a">链</a>' in html


def test_markdown_to_html_escapes_link_query_once():
    """带 & 的链接不得二次转义成 &amp;amp;（Pages 上点击会失效）。"""
    html = markdown_to_html("[参考](https://example.com/a?b=1&c=2)")
    assert 'href="https://example.com/a?b=1&amp;c=2"' in html
    assert "&amp;amp;" not in html


def test_markdown_to_html_escapes_link_text_once():
    html = markdown_to_html("[A & B](https://example.com/x)")
    assert "A &amp; B" in html
    assert "&amp;amp;" not in html


def test_markdown_to_html_escapes_scripts():
    html = markdown_to_html('<script>alert("x")</script>')
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_markdown_to_html_drops_dangerous_link_schemes():
    html = markdown_to_html("[坏链](javascript:alert(1)) [数据](data:text/html,x)")
    assert "javascript:" not in html
    assert "data:text" not in html
    assert 'href="#"' in html


def test_build_site_generates_pages(tmp_path):
    gua = tmp_path / "gua"
    write_report(gua, "2026-11-11", REPORT)
    feed = tmp_path / "feed.xml"
    feed.write_text(render_feed(list_reports(gua), site_url="https://o.github.io/r"), encoding="utf-8")
    out = tmp_path / "_site"
    written = build_site(gua, out, site_url="https://o.github.io/r", feed_path=feed)
    index = (out / "index.html").read_text(encoding="utf-8")
    assert "2026-11-11" in index
    assert (out / "gua" / "2026-11-11.html").is_file()
    assert (out / "gua" / "2026-11-11.md").is_file()
    assert (out / FEED_NAME).is_file()
    assert (out / ".nojekyll").is_file()
    assert any(path.name == "index.html" for path in written)
