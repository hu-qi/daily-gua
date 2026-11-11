"""agent 层：吃瓜指数、解析、校验降级、渲染、生成编排。"""

from __future__ import annotations

import json

import pytest
from src.agent.generate import generate_report
from src.agent.llm import LLMError
from src.agent.melons import coerce_melon_score, level_for, melon_index
from src.agent.parse import PayloadError, extract_json_object, parse_llm_payload
from src.agent.render import render_issue_body, render_markdown
from src.agent.report import allowed_links, build_report, payload_is_cited, sanitize_payload
from src.common.config import Settings
from src.common.frontmatter import digest_from_text
from src.rank.scorer import rank_posts
from tests.conftest import FIXED_NOW, SEED_URL, FakeLLM, make_post

GOOD_PAYLOAD = {
    "headline": "AI 圈互撕：博主称被大号爆锤",
    "summary": f"发帖人 @criscxuan 称某知名 AI 博主被大号爆锤，原文见 {SEED_URL}。",
    "timeline": [
        {"time": "2026-11-11T02:02:48Z", "event": "原帖发布", "link": SEED_URL},
    ],
    "quotes": [{"author": "cxuan", "text": "某知名 AI 博主被大号给爆锤了", "link": SEED_URL}],
    "stances": [{"side": "发帖人", "stance": "指控对方被爆锤", "link": SEED_URL}],
    "unverified": ["被指“被爆锤”的博主身份未被材料点名"],
    "melon_reasons": ["回复与引用显著高于点赞"],
    "sources": [SEED_URL],
    "melon_index": {"score": 76},
}


def test_melon_index_is_bounded_and_deterministic(sample_post):
    first = melon_index(sample_post, now=FIXED_NOW)
    second = melon_index(sample_post, now=FIXED_NOW)
    assert first == second
    assert 0 <= first.score <= 100
    assert first.level == level_for(first.score)
    assert sum(first.breakdown.values()) == first.score
    assert len(first.reasons) == 4


def test_melon_levels_thresholds():
    assert level_for(90) == "沸腾"
    assert level_for(70) == "热"
    assert level_for(45) == "温"
    assert level_for(10) == "微"


def test_melon_index_prefers_fresh_and_loud_posts():
    fresh_hot = make_post(likes=900, replies=400, quotes=100, views=900000,
                          created_at=FIXED_NOW)
    stale_quiet = make_post(
        post_id="2",
        url="https://x.com/a/status/2",
        likes=1,
        replies=0,
        quotes=0,
        views=50,
        created_at=FIXED_NOW.replace(year=2025),
    )
    assert melon_index(fresh_hot, now=FIXED_NOW).score > melon_index(
        stale_quiet, now=FIXED_NOW
    ).score


def test_coerce_melon_score_clamps_and_rejects():
    assert coerce_melon_score(87) == 87
    assert coerce_melon_score("64") == 64
    assert coerce_melon_score(1000) == 100
    assert coerce_melon_score(-5) == 0
    assert coerce_melon_score("很高") is None
    assert coerce_melon_score(True) is None


def test_extract_json_object_from_fenced_output():
    raw = '```json\n{"a": 1, "nested": {"b": "}"}}\n```'
    assert extract_json_object(raw) == {"a": 1, "nested": {"b": "}"}}


def test_extract_json_object_ignores_surrounding_prose():
    raw = '好的，结果如下：{"summary": "x"} 以上。'
    assert extract_json_object(raw) == {"summary": "x"}


def test_parse_llm_payload_text_fallback():
    raw = (
        "## 一句话摘要\n博主自曝被大号爆锤\n\n"
        "## 时间线\n- 2026-11-11T02:02:48Z 原帖发布 https://x.com/criscxuan/status/2120336410989433287\n\n"
        "## 未证实\n- 对方身份未点名\n\n"
        "## 吃瓜指数\n76/100\n"
    )
    payload = parse_llm_payload(raw)
    assert payload["_parsed_from"] == "text"
    assert "爆锤" in payload["summary"]
    assert payload["timeline"][0]["link"] == SEED_URL
    assert payload["unverified"] == ["对方身份未点名"]
    assert payload["melon_score"] == 76


def test_parse_llm_payload_rejects_empty():
    with pytest.raises(PayloadError):
        parse_llm_payload("   ")
    with pytest.raises(PayloadError):
        parse_llm_payload("完全无关的一段话，没有任何结构。")


def test_allowed_links_covers_primary_and_evidence(sample_post):
    evidence = make_post(post_id="2", url="https://x.com/other/status/2")
    allowed = allowed_links([sample_post, evidence])
    assert any("x.com/criscxuan/status" in url for url in allowed)
    assert any(url.endswith("/status/2") for url in allowed)


def test_sanitize_payload_drops_fabricated_links(sample_post):
    allowed = allowed_links([sample_post])
    payload = {
        "summary": "见 https://evil.example/fake 的说法",
        "timeline": [{"time": "", "event": "事件", "link": "https://evil.example/fake"}],
        "sources": ["https://evil.example/fake", SEED_URL],
    }
    clean, dropped = sanitize_payload(payload, allowed)
    assert "evil.example" not in clean["summary"]
    assert clean["timeline"][0]["link"] == ""
    assert clean["sources"] == [SEED_URL]
    assert any("evil.example" in link for link in dropped)
    assert payload_is_cited(clean, allowed) is True


def test_build_report_with_cited_payload(sample_post):
    ranked = rank_posts([sample_post], now=FIXED_NOW)
    report = build_report(
        GOOD_PAYLOAD,
        primary=sample_post,
        evidence=[sample_post],
        candidates=ranked.candidates,
        date="2026-11-11",
    )
    assert report.meta.generated_by == "llm"
    assert report.melon is not None and report.melon.score == 76
    assert report.has_original_link()
    assert SEED_URL in report.original_links
    assert report.unverified


def test_build_report_degrades_when_no_source_link(sample_post):
    ranked = rank_posts([sample_post], now=FIXED_NOW)
    payload = {
        "headline": "纯口述",
        "summary": "没有任何链接的断言",
        "timeline": [{"time": "", "event": "听说", "link": ""}],
        "quotes": [],
        "stances": [],
        "unverified": [],
        "sources": [],
    }
    report = build_report(
        payload,
        primary=sample_post,
        evidence=[sample_post],
        candidates=ranked.candidates,
        date="2026-11-11",
    )
    assert report.meta.generated_by == "degraded"
    assert "无源" in report.meta.downgrade_reason
    assert report.has_original_link()
    assert any("链接汇总" in item or "未经交叉核实" in item for item in report.unverified)


def test_build_report_without_payload_degrades(sample_post):
    report = build_report(
        None,
        primary=sample_post,
        evidence=[sample_post],
        candidates=[],
        date="2026-11-11",
    )
    assert report.meta.degraded
    assert report.headline.startswith("仅链接汇总")


def test_render_markdown_contains_required_sections(sample_post):
    ranked = rank_posts([sample_post], now=FIXED_NOW)
    report = build_report(
        GOOD_PAYLOAD,
        primary=sample_post,
        evidence=[sample_post],
        candidates=ranked.candidates,
        date="2026-11-11",
    )
    markdown = render_markdown(report)
    for expected in ("## 一句话摘瓜", "## 吃瓜指数", "## 来龙去脉", "## 关键原文",
                     "## ⚠️ 未证实", SEED_URL, "**76/100（热）**"):
        assert expected in markdown, expected

    digest = digest_from_text(markdown)
    assert digest.date == "2026-11-11"
    assert digest.melon_score == 76
    assert digest.primary_url == SEED_URL
    assert digest.unverified


def test_render_issue_body_mentions_commands(sample_post):
    ranked = rank_posts([sample_post], now=FIXED_NOW)
    report = build_report(
        GOOD_PAYLOAD,
        primary=sample_post,
        evidence=[sample_post],
        candidates=ranked.candidates,
        date="2026-11-11",
    )
    body = render_issue_body(report, report_url="https://github.com/o/r/blob/main/gua/2026-11-11.md")
    assert "🍉 每日一瓜 2026-11-11" in body
    assert "`/fact-check`" in body
    assert "`/vote 🍉`" in body


def test_generate_report_with_llm_success(sample_post):
    llm = FakeLLM(json.dumps(GOOD_PAYLOAD, ensure_ascii=False))
    ranked = rank_posts([sample_post], now=FIXED_NOW)
    result = generate_report(
        sample_post,
        [sample_post],
        ranked.candidates,
        date="2026-11-11",
        settings=Settings(llm_base_url="https://llm.example/v1", llm_api_key="k"),
        llm=llm,
        now=FIXED_NOW,
    )
    assert result.report.meta.generated_by == "llm"
    assert llm.calls == 1
    assert result.notes == []


def test_generate_report_degrades_on_llm_error(sample_post):
    llm = FakeLLM(error=LLMError("upstream 500"))
    result = generate_report(
        sample_post,
        [sample_post],
        [],
        date="2026-11-11",
        settings=Settings(llm_base_url="https://llm.example/v1", llm_api_key="k"),
        llm=llm,
        now=FIXED_NOW,
    )
    assert result.report.meta.generated_by == "degraded"
    assert any("降级" in note for note in result.notes)


def test_generate_report_degrades_when_llm_unavailable(sample_post):
    result = generate_report(
        sample_post,
        [sample_post],
        [],
        date="2026-11-11",
        settings=Settings(),
        now=FIXED_NOW,
    )
    assert result.report.meta.generated_by == "degraded"
    assert "LLM 未配置" in result.report.meta.downgrade_reason


def test_generate_report_degrades_on_unparseable_output(sample_post):
    llm = FakeLLM("我不知道。")
    result = generate_report(
        sample_post,
        [sample_post],
        [],
        date="2026-11-11",
        settings=Settings(llm_base_url="https://llm.example/v1", llm_api_key="k"),
        llm=llm,
        now=FIXED_NOW,
    )
    assert result.report.meta.generated_by == "degraded"
