"""瓜报文件格式：front matter 解析与「回读摘要」。

publish 层写文件、agent 的评论机器人读文件，两边共用这里的极小 YAML 子集解析器，
避免引入 YAML 依赖，也避免 publish ↔ agent 互相 import。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_FRONT_MATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_SECTION = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_QUOTE_AUTHOR = re.compile(r"^>\s*\*\*(.+?)\*\*[：:]\s*(.*)$")
_QUOTE_LINK = re.compile(r"^>\s*\[([^\]]+)\]\((\S+?)\)\s*$")
_BULLET = re.compile(r"^[-*]\s+(.*)$")


def _unquote(raw: str) -> str:
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        try:
            return str(json.loads(text))
        except json.JSONDecodeError:
            return text[1:-1]
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1]
    return text


def parse_front_matter(text: str) -> dict[str, object]:
    """解析 ``---`` 包裹的扁平键值 + 列表；无 front matter 返回空字典。"""
    match = _FRONT_MATTER.match(text)
    if not match:
        return {}
    data: dict[str, object] = {}
    current_list: list[str] | None = None
    for raw_line in match.group(1).splitlines():
        if not raw_line.strip() or raw_line.strip().startswith("#"):
            continue
        stripped = raw_line.strip()
        if stripped.startswith("- "):
            if current_list is not None:
                current_list.append(_unquote(stripped[2:]))
            continue
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        if value == "":
            current_list = []
            data[key] = current_list
            continue
        current_list = None
        if value.isdigit() and not value.startswith('"'):
            data[key] = int(value)
        else:
            data[key] = _unquote(value)
    return data


def body_of(text: str) -> str:
    match = _FRONT_MATTER.match(text)
    return text[match.end() :] if match else text


def _sections(body: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    positions = [(m.start(), m.group(1)) for m in _SECTION.finditer(body)]
    for index, (start, name) in enumerate(positions):
        end = positions[index + 1][0] if index + 1 < len(positions) else len(body)
        sections[name] = body[start:end].splitlines()[1:]
    return sections


def _find(sections: dict[str, list[str]], *keywords: str) -> list[str]:
    for name, lines in sections.items():
        if any(keyword in name for keyword in keywords):
            return lines
    return []


@dataclass(frozen=True)
class QuoteDigest:
    author: str
    text: str
    link: str


@dataclass(frozen=True)
class ReportDigest:
    date: str
    title: str = ""
    headline: str = ""
    melon_score: int = 0
    melon_level: str = ""
    generated_by: str = ""
    primary_url: str = ""
    sources: tuple[str, ...] = ()
    unverified: tuple[str, ...] = ()
    quotes: tuple[QuoteDigest, ...] = ()
    path: str = ""
    meta: dict[str, object] = field(default_factory=dict)


def digest_from_text(text: str, *, path: str = "") -> ReportDigest:
    meta = parse_front_matter(text)
    body = body_of(text)
    sections = _sections(body)

    unverified: list[str] = []
    for line in _find(sections, "未证实", "待核实", "存疑"):
        bullet = _BULLET.match(line.strip())
        if bullet:
            unverified.append(bullet.group(1).strip())

    quotes: list[QuoteDigest] = []
    author, quote_text, link = "", "", ""
    for line in _find(sections, "关键原文", "原文"):
        stripped = line.strip()
        if not stripped:
            if quote_text:
                quotes.append(QuoteDigest(author=author, text=quote_text, link=link))
                author, quote_text, link = "", "", ""
            continue
        author_match = _QUOTE_AUTHOR.match(stripped)
        if author_match:
            if quote_text:
                quotes.append(QuoteDigest(author=author, text=quote_text, link=link))
            author, quote_text, link = author_match.group(1).strip(), author_match.group(2).strip(), ""
            continue
        link_match = _QUOTE_LINK.match(stripped)
        if link_match:
            link = link_match.group(2).strip()
    if quote_text:
        quotes.append(QuoteDigest(author=author, text=quote_text, link=link))

    sources = tuple(str(u) for u in (meta.get("sources") or []) if str(u).strip())
    return ReportDigest(
        date=str(meta.get("date") or ""),
        title=str(meta.get("title") or ""),
        headline=str(meta.get("headline") or ""),
        melon_score=int(meta.get("melon_score") or 0),
        melon_level=str(meta.get("melon_level") or ""),
        generated_by=str(meta.get("generated_by") or ""),
        primary_url=str(meta.get("primary_url") or ""),
        sources=sources,
        unverified=tuple(unverified),
        quotes=tuple(quotes),
        path=path,
        meta=meta,
    )


def load_digest(path: str | Path) -> ReportDigest | None:
    file = Path(path)
    if not file.is_file():
        return None
    try:
        text = file.read_text(encoding="utf-8")
    except OSError:
        return None
    return digest_from_text(text, path=str(file))
