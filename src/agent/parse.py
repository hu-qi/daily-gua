"""LLM 输出解析：JSON 优先，失败回退 Markdown 段落解析。

LLM 输出是不可信数据：这里只做结构抽取，不做任何事实背书；
链接是否可信由 ``report.sanitize_payload`` 用材料白名单校验。
"""

from __future__ import annotations

import json
import re
from typing import Any

_LINK_RE = re.compile(r"https?://[^\s)\]\"'<>]+")
_FENCE_RE = re.compile(r"^```(?:json|markdown|md)?\s*|\s*```$", re.MULTILINE)
_HEADING_RE = re.compile(r"^#{1,6}\s*(.+?)\s*$")
_BULLET_RE = re.compile(r"^\s*(?:[-*+·]|\d+[.)])\s+(.+)$")

LIST_FIELDS = ("timeline", "quotes", "stances", "unverified", "sources", "melon_reasons")


class PayloadError(ValueError):
    """输出既不是可解析 JSON，也没有可用的结构化段落。"""


def strip_code_fence(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = _FENCE_RE.sub("", text)
    return text.strip()


def extract_json_object(raw: str) -> dict[str, Any] | None:
    """从任意文本中抠出第一个完整 JSON 对象（按括号深度扫描，忽略字符串内的括号）。"""
    text = strip_code_fence(raw)
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : index + 1]
                try:
                    parsed = json.loads(candidate)
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None


def _section_map(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = ""
    for line in text.splitlines():
        heading = _HEADING_RE.match(line)
        if heading:
            current = heading.group(1).strip()
            sections.setdefault(current, [])
            continue
        bullet = _BULLET_RE.match(line)
        body = bullet.group(1).strip() if bullet else line.strip()
        if current and body:
            sections.setdefault(current, []).append(body)
    return sections


def _find_section(sections: dict[str, list[str]], *keywords: str) -> list[str]:
    for name, lines in sections.items():
        if any(keyword in name for keyword in keywords):
            return lines
    return []


def _first_link(text: str) -> str:
    match = _LINK_RE.search(text)
    return match.group(0) if match else ""


def _parse_melon_score(lines: list[str]) -> int | None:
    for line in lines:
        match = re.search(r"(\d{1,3})\s*(?:/|分|｜)?\s*(?:100)?", line)
        if match:
            value = int(match.group(1))
            if 0 <= value <= 100:
                return value
    return None


def parse_text_payload(raw: str) -> dict[str, Any]:
    """把 Markdown 段落式输出映射成与 JSON 相同的字段结构。"""
    text = strip_code_fence(raw)
    sections = _section_map(text)
    summary_lines = _find_section(sections, "摘要", "一句话", "梗概", "发生了什么")
    timeline_lines = _find_section(sections, "时间线", "来龙去脉")
    quote_lines = _find_section(sections, "原文", "引用")
    stance_lines = _find_section(sections, "立场", "各方")
    unverified_lines = _find_section(sections, "未证实", "存疑", "待核实")
    melon_lines = _find_section(sections, "吃瓜指数", "指数")

    payload: dict[str, Any] = {
        "headline": (summary_lines[0][:60] if summary_lines else ""),
        "summary": " ".join(summary_lines).strip(),
        "timeline": [
            {"time": "", "event": line, "link": _first_link(line)} for line in timeline_lines
        ],
        "quotes": [
            {"author": "", "text": line, "link": _first_link(line)} for line in quote_lines
        ],
        "stances": [{"side": "", "stance": line, "link": _first_link(line)} for line in stance_lines],
        "unverified": list(unverified_lines),
        "melon_reasons": [],
        "sources": sorted(set(_LINK_RE.findall(text))),
    }
    if payload["headline"] and not payload["summary"]:
        payload["summary"] = payload["headline"]
    score = _parse_melon_score(melon_lines)
    if score is not None:
        payload["melon_score"] = score
    payload["_parsed_from"] = "text"
    if not any(payload[key] for key in ("summary", "timeline", "quotes", "stances", "unverified")):
        raise PayloadError("文本回退解析未得到任何可用段落")
    return payload


def parse_llm_payload(raw: str) -> dict[str, Any]:
    """JSON 优先，失败回退文本解析；两者都失败抛 PayloadError。"""
    if not raw or not raw.strip():
        raise PayloadError("LLM 输出为空")
    parsed = extract_json_object(raw)
    if parsed is not None:
        result: dict[str, Any] = {"_parsed_from": "json"}
        for key, value in parsed.items():
            if key in LIST_FIELDS and not isinstance(value, list):
                continue
            result[key] = value
        if isinstance(result.get("melon_index"), dict):
            score = result["melon_index"].get("score")
            if isinstance(score, (int, float)):
                result["melon_score"] = score
        return result
    return parse_text_payload(raw)
