"""评论命令：解析、投票统计、三种回复渲染、CommentAgent 取数失败降级。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from src.agent.commands import (
    CommentAgent,
    parse_command,
    parse_vote,
    render_fact_check,
    render_more,
    render_vote,
    tally_votes,
)
from src.common.frontmatter import digest_from_text
from src.common.http import HttpError
from src.common.models import Metrics
from src.rank.snapshots import PostSnapshot
from tests.conftest import FIXED_NOW, SEED_URL, make_post

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
- 截图内容未核实
"""


def test_parse_command_variants():
    command = parse_command("说点什么\n/fact-check 求证一下")
    assert command is not None
    assert command.name == "fact-check"
    assert command.argument == "求证一下"

    vote = parse_command("/vote 🍉")
    assert vote is not None and vote.name == "vote" and vote.argument == "🍉"

    assert parse_command("普通评论，没有命令") is None
    assert parse_command("") is None


def test_parse_vote_accepts_emoji_and_words():
    assert parse_vote("🍉") == "🍉"
    assert parse_vote("🫠") == "🫠"
    assert parse_vote("真瓜") == "🍉"
    assert parse_vote("假瓜") == "🫠"
    assert parse_vote("随便") is None


def test_tally_votes_ignores_bots_and_duplicates():
    comments = [
        {"user": {"login": "alice"}, "body": "/vote 🍉"},
        {"user": {"login": "bob"}, "body": "同意 /vote 🫠"},
        {"user": {"login": "grapedev-bot"}, "body": "/vote 🍉"},
        {"user": {"login": "carol"}, "body": "只是聊天"},
    ]
    assert tally_votes(comments) == (1, 1)


def test_render_vote_without_votes():
    text = render_vote(0, 0, vote=None, author="alice")
    assert "还没有有效票" in text


def test_render_vote_with_tally():
    text = render_vote(3, 1, vote="🍉", author="alice")
    assert "已记录 @alice 的投票" in text
    assert "🍉 真瓜：3 票（75%）" in text
    assert "🫠 假瓜：1 票（25%）" in text


def test_render_more_shows_deltas(sample_post):
    digest = digest_from_text(REPORT)
    previous = PostSnapshot(
        key=sample_post.key,
        url=sample_post.url,
        author=sample_post.author,
        captured_at=FIXED_NOW - timedelta(hours=4),
        metrics=Metrics(likes=100, replies=20, reposts=5, quotes=2, views=10000),
    )
    text = render_more(
        digest,
        latest=sample_post,
        previous=previous,
        checked_at=FIXED_NOW,
    )
    assert "| 点赞 | 100 | 190 | +90 |" in text
    assert "加权互动增速" in text
    assert sample_post.url in text


def test_render_more_handles_upstream_error(sample_post):
    digest = digest_from_text(REPORT)
    text = render_more(digest, latest=None, previous=None, checked_at=FIXED_NOW, error="502")
    assert "暂时未能取到最新数据" in text
    assert "502" in text
    assert SEED_URL in text


def test_render_fact_check_lists_unverified(sample_post):
    digest = digest_from_text(REPORT)
    text = render_fact_check(
        digest,
        reachable={SEED_URL: True},
        current=sample_post,
        checked_at=FIXED_NOW,
    )
    assert "证据核查" in text
    assert "✅ 链接可访问" in text
    assert "被爆锤对象未点名" in text
    assert SEED_URL in text


def test_render_fact_check_flags_unreachable(sample_post):
    digest = digest_from_text(REPORT)
    text = render_fact_check(
        digest, reachable={SEED_URL: False}, current=None, checked_at=FIXED_NOW
    )
    assert "链接不可访问" in text


def test_comment_agent_more_uses_injected_fetch(sample_post):
    digest = digest_from_text(REPORT)
    agent = CommentAgent(fetch_post=lambda url: sample_post)
    command = parse_command("/more")
    assert command is not None
    text = agent.handle(
        command,
        report=digest,
        snapshot=None,
        issue_comments=[],
        author="alice",
        now=FIXED_NOW,
    )
    assert sample_post.url in text


def test_comment_agent_more_reports_fetch_failure():
    digest = digest_from_text(REPORT)

    def boom(url):
        raise HttpError("mirror down")

    agent = CommentAgent(fetch_post=boom)
    command = parse_command("/more")
    assert command is not None
    text = agent.handle(
        command, report=digest, snapshot=None, issue_comments=[], author="alice", now=FIXED_NOW
    )
    assert "mirror down" in text


def test_comment_agent_vote_tally():
    digest = digest_from_text(REPORT)
    agent = CommentAgent(fetch_post=lambda url: make_post())
    command = parse_command("/vote 🍉")
    assert command is not None
    comments = [
        {"user": {"login": "alice"}, "body": "/vote 🍉"},
        {"user": {"login": "bob"}, "body": "/vote 🫠"},
    ]
    text = agent.handle(
        command,
        report=digest,
        snapshot=None,
        issue_comments=comments,
        author="alice",
        now=FIXED_NOW,
    )
    assert "🍉 真瓜：1 票（50%）" in text


@pytest.mark.parametrize("command_name", ["more", "fact-check", "vote 🍉"])
def test_every_command_produces_non_empty_reply(command_name, sample_post):
    digest = digest_from_text(REPORT)
    agent = CommentAgent(fetch_post=lambda url: sample_post)
    command = parse_command(f"/{command_name}")
    assert command is not None
    text = agent.handle(
        command, report=digest, snapshot=None, issue_comments=[], author="bob", now=FIXED_NOW
    )
    assert isinstance(text, str) and len(text) > 20
