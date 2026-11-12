"""agent 层：LLM 调用、prompt、解析、瓜报校验与渲染。"""

from __future__ import annotations

from .generate import GenerateResult, generate_report
from .llm import LLMClient, LLMError
from .melons import MelonScore, coerce_melon_score, level_for, melon_index
from .parse import PayloadError, extract_json_object, parse_llm_payload, parse_text_payload
from .prompts import build_comment_messages, build_messages, format_materials
from .render import render_issue_body, render_markdown, report_title
from .report import (
    Candidate,
    GuaReport,
    Quote,
    ReportMeta,
    Source,
    Stance,
    TimelineItem,
    allowed_links,
    build_degraded_report,
    build_report,
    payload_is_cited,
    sanitize_payload,
)

__all__ = [
    "Candidate",
    "GenerateResult",
    "GuaReport",
    "LLMClient",
    "LLMError",
    "MelonScore",
    "PayloadError",
    "Quote",
    "ReportMeta",
    "Source",
    "Stance",
    "TimelineItem",
    "allowed_links",
    "build_comment_messages",
    "build_degraded_report",
    "build_messages",
    "build_report",
    "coerce_melon_score",
    "extract_json_object",
    "format_materials",
    "generate_report",
    "level_for",
    "melon_index",
    "parse_llm_payload",
    "parse_text_payload",
    "payload_is_cited",
    "render_issue_body",
    "render_markdown",
    "report_title",
    "sanitize_payload",
]
