"""端到端（全部离线）：daily / from_issue / comment_agent / x_share / pages 五条 CLI 链路。"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from src.cli import comment_agent, daily, from_issue, pages, x_share
from src.common.http import HttpClient as RealHttpClient
from tests.conftest import RSS_SAMPLE, SEED_URL, FakeTransport, json_route

GOOD_PAYLOAD = {
    "headline": "AI 圈互撕：博主称被大号爆锤",
    "summary": f"发帖人称某知名 AI 博主被大号爆锤，原文见 {SEED_URL}。",
    "timeline": [{"time": "2026-11-11T02:02:48Z", "event": "原帖发布", "link": SEED_URL}],
    "quotes": [{"author": "cxuan", "text": "某知名 AI 博主被大号给爆锤了", "link": SEED_URL}],
    "stances": [{"side": "发帖人", "stance": "称对方被爆锤", "link": SEED_URL}],
    "unverified": ["被指对象未在正文中点名"],
    "melon_reasons": ["回复与引用高于点赞"],
    "sources": [SEED_URL],
}

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

## ⚠️ 未证实 / 待核实

- 被爆锤对象未点名
"""

README = """# 项目

说明文字。

<!-- GUA:INDEX:START -->
旧的索引
<!-- GUA:INDEX:END -->

尾部内容
"""


@pytest.fixture()
def workspace(monkeypatch, tmp_path):
    """离线工作区：env 指向临时目录、无凭据、README 含索引标记。"""
    for key in ("LLM_API_KEY", "LLM_BASE_URL", "GITHUB_TOKEN", "X_BEARER_TOKEN", "SITE_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GUA_DIR", str(tmp_path / "gua"))
    monkeypatch.setenv("GUA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SEED_URLS", SEED_URL)
    monkeypatch.setenv("RSS_FEEDS", "https://feed.example/rss")
    monkeypatch.setenv("SITE_URL", "https://o.github.io/r")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "README.md").write_text(README, encoding="utf-8")
    return tmp_path


def _patch_transport(monkeypatch, module, transport: FakeTransport) -> None:
    monkeypatch.setattr(
        module,
        "HttpClient",
        lambda **kwargs: RealHttpClient(transport=transport, retries=0, sleep=lambda _: None),
    )


def _mirror_transport(payload: dict, *, rss: str = "", extra: dict | None = None) -> FakeTransport:
    routes: dict = {"https://api.fxtwitter.com": json_route(payload)}
    if rss:
        routes["https://feed.example"] = (200, rss.encode())
    routes.update(extra or {})
    return FakeTransport(routes=routes)


def _output(capsys) -> dict:
    out = capsys.readouterr().out
    return json.loads(out[out.index("{") :])


def test_daily_produces_report_without_credentials(workspace, monkeypatch, capsys, mirror_payload):
    _patch_transport(monkeypatch, daily, _mirror_transport(mirror_payload))
    code = daily.run(["--date", "2026-11-11"])
    assert code == 0
    payload = _output(capsys)
    assert payload["ok"] is True
    assert payload["generated_by"] == "degraded"
    assert payload["issue"].get("skipped")  # GitHub 未配置 → 明确跳过
    assert payload["melon"]["score"] > 0

    report = (workspace / "gua" / "2026-11-11.md").read_text(encoding="utf-8")
    assert SEED_URL in report
    assert "## ⚠️ 未证实" in report
    assert "## 吃瓜指数" in report
    assert "## 一句话摘瓜" in report

    readme = (workspace / "README.md").read_text(encoding="utf-8")
    assert "旧的索引" not in readme
    assert "2026-11-11" in readme
    assert "尾部内容" in readme

    feed = (workspace / "feed.xml").read_text(encoding="utf-8")
    root = ET.fromstring(feed)
    assert root.find("channel").findall("item")

    state = json.loads((workspace / "state" / "snapshots.json").read_text(encoding="utf-8"))
    assert state["posts"]


def test_daily_with_llm_generates_narrative(workspace, monkeypatch, capsys, mirror_payload):
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "llm-key-for-test")

    class StubLLMClient:
        def __init__(self, settings, http=None, *, budget=None):  # noqa: ANN001
            self.available = True

        def complete(self, messages, *, max_tokens=None, temperature=None):  # noqa: ANN001
            return json.dumps(GOOD_PAYLOAD, ensure_ascii=False)

    monkeypatch.setattr("src.agent.generate.LLMClient", StubLLMClient)
    _patch_transport(monkeypatch, daily, _mirror_transport(mirror_payload))

    code = daily.run(["--date", "2026-11-11"])
    assert code == 0
    payload = _output(capsys)
    assert payload["generated_by"] == "llm"
    report = (workspace / "gua" / "2026-11-11.md").read_text(encoding="utf-8")
    assert "AI 圈互撕" in report
    assert "被指对象未在正文中点名" in report


def test_daily_creates_issue_when_github_configured(workspace, monkeypatch, capsys, mirror_payload):
    monkeypatch.setenv("GITHUB_TOKEN", "gh-test-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    transport = _mirror_transport(
        mirror_payload,
        extra={
            "GET https://api.github.com/repos/o/r/labels": json_route(
                [{"name": "gua"}, {"name": "daily-gua"}]
            ),
            "POST https://api.github.com/repos/o/r/labels": json_route({"name": "new"}, status=201),
            "GET https://api.github.com/search/issues": json_route({"items": []}),
            "POST https://api.github.com/repos/o/r/issues": json_route(
                {"number": 7, "html_url": "https://github.com/o/r/issues/7"}
            ),
        },
    )
    _patch_transport(monkeypatch, daily, transport)

    code = daily.run(["--date", "2026-11-11", "--no-llm"])
    assert code == 0
    payload = _output(capsys)
    assert payload["issue"]["number"] == 7
    assert payload["issue"]["created"] is True
    assert payload["issue"]["warnings"] == []
    # 首次运行自愈：gua-submit / gua-accepted 不存在时被创建（投瓜链路才有触发标签）
    assert any(call[0] == "POST" and call[1].endswith("/labels") for call in transport.calls)


def test_daily_still_reports_when_labels_cannot_be_created(
    workspace, monkeypatch, capsys, mirror_payload
):
    """标签建不出来也必须出报：去标签重试 + 告警，不让 Issue 步骤整体失败。"""
    monkeypatch.setenv("GITHUB_TOKEN", "gh-test-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    transport = _mirror_transport(
        mirror_payload,
        extra={
            "GET https://api.github.com/search/issues": json_route({"items": []}),
            "POST https://api.github.com/repos/o/r/issues": json_route(
                {"number": 8, "html_url": "https://github.com/o/r/issues/8"}
            ),
        },
    )
    _patch_transport(monkeypatch, daily, transport)

    code = daily.run(["--date", "2026-11-11", "--no-llm"])
    assert code == 0
    payload = _output(capsys)
    assert payload["issue"]["number"] == 8
    assert any("标签" in warning for warning in payload["issue"]["warnings"])


def test_daily_dry_run_writes_nothing(workspace, monkeypatch, capsys, mirror_payload):
    _patch_transport(monkeypatch, daily, _mirror_transport(mirror_payload))
    code = daily.run(["--date", "2026-11-11", "--dry-run"])
    assert code == 0
    payload = _output(capsys)
    assert payload["report_file"] is None
    assert not (workspace / "gua").exists()


def test_daily_exits_2_when_all_channels_fail(workspace, monkeypatch, capsys):
    _patch_transport(monkeypatch, daily, FakeTransport(default=(500, b"boom")))
    code = daily.run(["--date", "2026-11-11"])
    assert code == 2
    payload = _output(capsys)
    assert payload["ok"] is False


def test_from_issue_extracts_urls_and_dedupes():
    body = (
        "### 瓜链接\n\nhttps://x.com/criscxuan/status/2120336410989433287\n"
        "另一个 https://example.com/post/1。\n"
        "重复 https://x.com/criscxuan/status/2120336410989433287"
    )
    urls = from_issue.extract_urls(body)
    assert urls == [SEED_URL, "https://example.com/post/1"]


def test_from_issue_candidate_pool_dedupes(workspace):
    added = from_issue.add_candidates(
        str(workspace / "state"), [{"url": SEED_URL, "author": "cxuan"}]
    )
    assert added == 1
    again = from_issue.add_candidates(
        str(workspace / "state"), [{"url": SEED_URL, "author": "cxuan"}]
    )
    assert again == 0


def test_from_issue_render_receipt_marks_unreachable():
    receipt = from_issue.render_receipt(
        [{"url": "https://x.com/a/status/1", "ok": False, "kind": "x", "note": "404"}], added=0
    )
    assert "❌" in receipt
    assert "404" in receipt


def test_from_issue_run_dry_run(workspace, monkeypatch, capsys, mirror_payload):
    _patch_transport(monkeypatch, from_issue, _mirror_transport(mirror_payload))
    event_file = workspace / "event.json"
    event_file.write_text(
        json.dumps(
            {
                "issue": {
                    "number": 42,
                    "title": "投瓜",
                    "body": f"### 瓜链接\n\n{SEED_URL}",
                }
            }
        ),
        encoding="utf-8",
    )
    code = from_issue.run(["--event", str(event_file), "--dry-run"])
    assert code == 0
    payload = _output(capsys)
    assert payload["checked"][0]["ok"] is True
    assert payload["added"] == 0  # dry-run 不写候选池
    assert not (workspace / "state" / "candidates.json").exists()


def test_from_issue_skips_repeat_receipt_when_label_event_refires(
    workspace, monkeypatch, capsys, mirror_payload
):
    """issues: [opened, labeled] + 自身 add_labels 会重复触发，必须按已回执提前返回。"""
    monkeypatch.setenv("GITHUB_TOKEN", "gh-test-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    event_file = workspace / "event.json"
    event_file.write_text(
        json.dumps(
            {"issue": {"number": 42, "title": "投瓜", "body": f"### 瓜链接\n\n{SEED_URL}"}}
        ),
        encoding="utf-8",
    )
    posted: list[dict] = []

    def transport(method, url, headers, data, timeout):  # noqa: ANN001 - 传输层协议
        if method == "GET" and url.endswith("/labels?per_page=100"):
            return 200, json.dumps([{"name": "gua-submit"}, {"name": "gua-accepted"}]).encode()
        if method == "GET" and url.endswith("/issues/42/comments?per_page=100"):
            return 200, json.dumps(
                [{"id": 7, "body": f"🍉 投瓜核查回执\n\n| {SEED_URL} | ✅ | X 帖 |"}]
            ).encode()
        if method == "POST" and url.endswith("/issues/42/comments"):
            posted.append(json.loads(data.decode()) if data else {})
            return 201, json.dumps({"id": 1}).encode()
        return 404, b""

    monkeypatch.setattr(
        from_issue,
        "HttpClient",
        lambda **kwargs: RealHttpClient(transport=transport, retries=0, sleep=lambda _: None),
    )
    code = from_issue.run(["--event", str(event_file)])
    assert code == 0
    payload = _output(capsys)
    assert "回执" in payload["skipped"]
    assert posted == []  # 未重复回执


def test_from_issue_processes_new_link_added_after_receipt(
    workspace, monkeypatch, capsys, mirror_payload
):
    """用户在 Issue 里补了新链接：不能被旧回执永久挡住，要发增量回执。"""
    monkeypatch.setenv("GITHUB_TOKEN", "gh-test-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    event_file = workspace / "event.json"
    event_file.write_text(
        json.dumps(
            {
                "issue": {
                    "number": 44,
                    "title": "投瓜",
                    "body": f"### 瓜链接\n\n{SEED_URL}\n\n补充 https://example.com/news/1",
                }
            }
        ),
        encoding="utf-8",
    )
    posted: list[dict] = []

    def transport(method, url, headers, data, timeout):  # noqa: ANN001 - 传输层协议
        if method == "GET" and url.endswith("/labels?per_page=100"):
            return 200, json.dumps([{"name": "gua-submit"}, {"name": "gua-accepted"}]).encode()
        if method == "GET" and url.endswith("/issues/44/comments?per_page=100"):
            return 200, json.dumps(
                [{"id": 7, "body": f"🍉 投瓜核查回执\n\n| {SEED_URL} | ✅ | X 帖 |"}]
            ).encode()
        if method == "POST" and url.endswith("/issues/44/comments"):
            posted.append(json.loads(data.decode()) if data else {})
            return 201, json.dumps({"id": 8}).encode()
        if "api.fxtwitter.com" in url:
            return json_route(mirror_payload)
        if "example.com/news/1" in url:
            return 200, b"<html><head><title>news</title></head></html>"
        return 404, b""

    monkeypatch.setattr(
        from_issue,
        "HttpClient",
        lambda **kwargs: RealHttpClient(transport=transport, retries=0, sleep=lambda _: None),
    )
    code = from_issue.run(["--event", str(event_file)])
    assert code == 0
    payload = _output(capsys)
    assert "skipped" not in payload
    assert len(posted) == 1
    assert "example.com/news/1" in posted[0]["body"]


def test_comment_agent_dry_run_offline(workspace, monkeypatch, capsys, mirror_payload):
    (workspace / "gua").mkdir()
    (workspace / "gua" / "2026-11-11.md").write_text(REPORT, encoding="utf-8")
    _patch_transport(monkeypatch, comment_agent, _mirror_transport(mirror_payload))

    for body in ("/more", "/fact-check", "/vote 🍉"):
        code = comment_agent.run(["--issue", "1", "--comment-id", "9", "--body", body, "--dry-run"])
        assert code == 0
        payload = _output(capsys)
        assert payload["command"] in {"more", "fact-check", "vote"}
        assert len(payload["reply"]) > 20


def test_comment_agent_idempotency_state(workspace):
    state_dir = str(workspace / "state")
    assert comment_agent.is_handled(state_dir, 123) is False
    comment_agent.mark_handled(state_dir, 123, command="more", issue=1)
    assert comment_agent.is_handled(state_dir, 123) is True


def test_comment_agent_skips_non_command(workspace, capsys):
    code = comment_agent.run(["--issue", "1", "--comment-id", "5", "--body", "只是围观"])
    assert code == 0
    payload = _output(capsys)
    assert payload.get("skipped")


def test_comment_agent_skips_bot_comments(workspace, capsys):
    code = comment_agent.run(
        ["--issue", "1", "--comment-id", "6", "--body", "/vote 🍉", "--author", "grapedev-bot"]
    )
    assert code == 0
    payload = _output(capsys)
    assert payload.get("skipped")


def test_daily_refreshes_issue_body_on_same_day_rerun(
    workspace, monkeypatch, capsys, mirror_payload
):
    """同日重跑：瓜报文件被覆盖，Issue 正文必须同步刷新（update_issue_body 不再无调用方）。"""
    monkeypatch.setenv("GITHUB_TOKEN", "gh-test-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    patched: list[dict] = []

    def transport(method, url, headers, data, timeout):  # noqa: ANN001 - 传输层协议
        if method == "GET" and url.endswith("/labels?per_page=100"):
            return 200, json.dumps(
                [{"name": name} for name in ("gua", "daily-gua", "gua-submit", "gua-accepted")]
            ).encode()
        if method == "GET" and "search/issues" in url:
            return 200, json.dumps(
                {"items": [{"number": 3, "title": "🍉 每日一瓜 2026-11-11", "html_url": "u"}]}
            ).encode()
        if method == "PATCH" and url.endswith("/issues/3"):
            patched.append(json.loads(data.decode()) if data else {})
            return 200, json.dumps({"number": 3, "body": "new"}).encode()
        if "api.fxtwitter.com" in url:
            return json_route(mirror_payload)
        if "feed.example" in url:
            return 200, RSS_SAMPLE.encode()
        return 404, b""

    monkeypatch.setattr(
        daily,
        "HttpClient",
        lambda **kwargs: RealHttpClient(transport=transport, retries=0, sleep=lambda _: None),
    )
    code = daily.run(["--date", "2026-11-11", "--no-llm"])
    assert code == 0
    payload = _output(capsys)
    assert payload["issue"]["number"] == 3
    assert payload["issue"]["created"] is False
    assert len(patched) == 1
    assert "🍉 每日一瓜 2026-11-11" in patched[0]["body"]


def test_comment_agent_manual_run_without_comment_id_is_not_persisted(
    workspace, monkeypatch, capsys, mirror_payload
):
    """workflow_dispatch 手工试跑 comment_id=0：不得把 "0" 写进幂等状态。"""
    monkeypatch.setenv("GITHUB_TOKEN", "gh-test-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    (workspace / "gua").mkdir()
    (workspace / "gua" / "2026-11-11.md").write_text(REPORT, encoding="utf-8")

    def transport(method, url, headers, data, timeout):  # noqa: ANN001 - 传输层协议
        if method == "POST" and url.endswith("/issues/1/comments"):
            return 201, json.dumps({"id": 55, "html_url": "c55"}).encode()
        if "api.fxtwitter.com" in url:
            return json_route(mirror_payload)
        return 404, b""

    monkeypatch.setattr(
        comment_agent,
        "HttpClient",
        lambda **kwargs: RealHttpClient(transport=transport, retries=0, sleep=lambda _: None),
    )
    for _ in range(2):
        code = comment_agent.run(["--issue", "1", "--comment-id", "0", "--body", "/more"])
        assert code == 0
        payload = _output(capsys)
        assert payload["posted"] is True
        assert payload["idempotent"] is False
    state_file = workspace / "state" / "handled_comments.json"
    assert not state_file.is_file()


def test_x_share_draft_dry_run(workspace, capsys):
    (workspace / "gua").mkdir()
    (workspace / "gua" / "2026-11-11.md").write_text(REPORT, encoding="utf-8")
    code = x_share.run(["--date", "2026-11-11", "--dry-run"])
    assert code == 0
    payload = _output(capsys)
    assert payload["auto_posted"] is False
    assert payload["intent_url"].startswith("https://x.com/intent/tweet?text=")
    assert not (workspace / "dist").exists()


def test_x_share_writes_draft(workspace, capsys):
    (workspace / "gua").mkdir()
    (workspace / "gua" / "2026-11-11.md").write_text(REPORT, encoding="utf-8")
    code = x_share.run(["--date", "2026-11-11"])
    assert code == 0
    payload = _output(capsys)
    draft = Path(payload["draft"]).read_text(encoding="utf-8")
    assert "不会自动发帖" in draft
    assert "吃瓜指数 74" in draft
    assert "gua/2026-11-11.md" in draft


def test_pages_cli_builds_site(workspace, capsys):
    (workspace / "gua").mkdir()
    (workspace / "gua" / "2026-11-11.md").write_text(REPORT, encoding="utf-8")
    code = pages.run([])
    assert code == 0
    payload = _output(capsys)
    assert payload["count"] > 0
    index = (workspace / "_site" / "index.html").read_text(encoding="utf-8")
    assert "2026-11-11" in index
    assert (workspace / "_site" / "gua" / "2026-11-11.html").is_file()
