"""瓜报结构与校验：引用白名单、未证实标注、降级为链接汇总。

核心防线（对应验收「产出必须含 ≥1 原链，否则降级」）：
- LLM 输出的每个链接都必须出现在**本次采集材料**的链接白名单里，否则被丢弃；
- 若丢弃后整份输出不再引用任何一条材料链接，则判定为「无源」，整份降级为链接汇总。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..common.models import Post, canonical_url
from ..common.privacy import mask_personal
from .melons import MelonScore, coerce_melon_score, melon_index

MAX_CANDIDATES = 3
DEFAULT_UNVERIFIED = "本页信息来自公开材料，未取得当事方书面确认；读者请以原始链接为准。"


@dataclass(frozen=True)
class TimelineItem:
    time: str
    event: str
    link: str = ""


@dataclass(frozen=True)
class Quote:
    author: str
    text: str
    link: str = ""


@dataclass(frozen=True)
class Stance:
    side: str
    stance: str
    link: str = ""


@dataclass(frozen=True)
class Source:
    title: str
    url: str


@dataclass(frozen=True)
class Candidate:
    url: str
    author: str
    score: float
    summary: str


@dataclass(frozen=True)
class ReportMeta:
    generated_by: str = "llm"  # llm | manual | degraded
    llm_model: str = ""
    parsed_from: str = ""
    collected_at: str = ""
    sources_used: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    downgrade_reason: str = ""
    dropped_links: tuple[str, ...] = ()
    llm_melon_suggestion: str = ""  # LLM 自评分与系统确定性值的偏差（仅记录，不生效）

    @property
    def degraded(self) -> bool:
        return self.generated_by == "degraded"

    @property
    def label(self) -> str:
        return {
            "llm": "LLM 生成",
            "manual": "编辑人工整理",
            "degraded": "降级为链接汇总",
        }.get(self.generated_by, self.generated_by or "未知")


@dataclass(frozen=True)
class GuaReport:
    date: str
    headline: str
    summary: str
    timeline: tuple[TimelineItem, ...] = ()
    quotes: tuple[Quote, ...] = ()
    stances: tuple[Stance, ...] = ()
    unverified: tuple[str, ...] = ()
    melon: MelonScore | None = None
    sources: tuple[Source, ...] = ()
    candidates: tuple[Candidate, ...] = ()
    meta: ReportMeta = field(default_factory=ReportMeta)
    primary: Post | None = None

    @property
    def original_links(self) -> tuple[str, ...]:
        links: list[str] = []
        for item in (self.sources,):
            links.extend(source.url for source in item if source.url)
        links.extend(q.link for q in self.quotes if q.link)
        links.extend(t.link for t in self.timeline if t.link)
        seen: set[str] = set()
        ordered: list[str] = []
        for link in links:
            if link not in seen:
                seen.add(link)
                ordered.append(link)
        return tuple(ordered)

    def has_original_link(self) -> bool:
        return bool(self.original_links)


def allowed_links(posts: list[Post]) -> set[str]:
    """材料链接白名单（规范化后比较，容忍跟踪参数差异）。"""
    allowed: set[str] = set()
    for post in posts:
        if post.url:
            allowed.add(canonical_url(post.url))
            allowed.add(post.url)
    return allowed


def _link_allowed(link: str, allowed: set[str]) -> bool:
    return canonical_url(link) in allowed or link in allowed


def _strip_unknown_links(text: str, allowed: set[str]) -> tuple[str, list[str]]:
    import re

    dropped: list[str] = []
    for raw in re.findall(r"https?://[^\s)\]\"'<>]+", text or ""):
        if not _link_allowed(raw, allowed):
            text = text.replace(raw, "")
            dropped.append(raw)
    return text, dropped


def _coerce_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def sanitize_payload(
    payload: dict[str, Any], allowed: set[str]
) -> tuple[dict[str, Any], list[str]]:
    """丢弃不在材料白名单里的链接，返回 (清洗后 payload, 被丢弃链接)。"""
    dropped: list[str] = []
    clean: dict[str, Any] = dict(payload)

    for key in ("summary", "headline"):
        if isinstance(clean.get(key), str):
            text, lost = _strip_unknown_links(clean[key], allowed)
            clean[key] = text.strip()
            dropped.extend(lost)

    def _items(key: str) -> list[dict[str, Any]]:
        raw = clean.get(key)
        items: list[dict[str, Any]] = []
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    items.append(dict(item))
                elif isinstance(item, str) and item.strip():
                    items.append({"text": item.strip()})
        return items

    timeline: list[dict[str, Any]] = []
    for item in _items("timeline"):
        link = _coerce_str(item.get("link"))
        event, lost = _strip_unknown_links(_coerce_str(item.get("event") or item.get("text")), allowed)
        dropped.extend(lost)
        if link and not _link_allowed(link, allowed):
            dropped.append(link)
            link = ""
        if event:
            timeline.append({"time": _coerce_str(item.get("time")), "event": event, "link": link})
    clean["timeline"] = timeline

    quotes: list[dict[str, Any]] = []
    for item in _items("quotes"):
        link = _coerce_str(item.get("link"))
        text, lost = _strip_unknown_links(_coerce_str(item.get("text")), allowed)
        dropped.extend(lost)
        if link and not _link_allowed(link, allowed):
            dropped.append(link)
            link = ""
        if text:
            quotes.append({"author": _coerce_str(item.get("author")), "text": text, "link": link})
    clean["quotes"] = quotes

    stances: list[dict[str, Any]] = []
    for item in _items("stances"):
        link = _coerce_str(item.get("link"))
        stance, lost = _strip_unknown_links(
            _coerce_str(item.get("stance") or item.get("text")), allowed
        )
        dropped.extend(lost)
        if link and not _link_allowed(link, allowed):
            dropped.append(link)
            link = ""
        if stance:
            stances.append(
                {"side": _coerce_str(item.get("side")), "stance": stance, "link": link}
            )
    clean["stances"] = stances

    sources: list[str] = []
    for item in clean.get("sources") or []:
        url = item if isinstance(item, str) else _coerce_str((item or {}).get("url"))
        if not url:
            continue
        if _link_allowed(url, allowed):
            if url not in sources:
                sources.append(url)
        else:
            dropped.append(url)
    clean["sources"] = sources

    unverified = [
        _coerce_str(item)
        for item in (clean.get("unverified") or [])
        if _coerce_str(item)
    ]
    clean["unverified"] = unverified
    return clean, dropped


def payload_is_cited(payload: dict[str, Any], allowed: set[str]) -> bool:
    """判断输出是否至少引用了一条材料中的原始链接。"""
    candidates: list[str] = []
    candidates.extend(payload.get("sources") or [])
    for key in ("timeline", "quotes", "stances"):
        for item in payload.get(key) or []:
            if isinstance(item, dict) and item.get("link"):
                candidates.append(str(item["link"]))
    return any(_link_allowed(url, allowed) for url in candidates)


def resolve_melon(llm_score: Any, deterministic: MelonScore, llm_reasons: list[Any]) -> MelonScore:
    """指数**只取确定性结果**；LLM 分数仅作对照记录，正文附其补充理由。

    README / prompts / melons 三处都承诺「指数由系统确定性计算，LLM 只能补充理由、不能改数值」，
    所以这里绝不采用 LLM 数值；给出偏差时在理由里如实披露，避免幻觉改公开数值。
    """
    extras = [str(r).strip() for r in llm_reasons if str(r).strip()][:4]
    reasons = [*extras, *deterministic.reasons]
    coerced = coerce_melon_score(llm_score)
    if coerced is not None and coerced != deterministic.score:
        reasons.insert(
            0,
            f"LLM 曾建议 {coerced}/100，与系统确定性计算 {deterministic.score}/100 不一致，"
            "已按规则忽略（指数不可由 LLM 改写）",
        )
    return MelonScore(
        score=deterministic.score,
        level=deterministic.level,
        emoji=deterministic.emoji,
        reasons=tuple(reasons),
        breakdown=deterministic.breakdown,
    )


def melon_suggestion(llm_score: Any, deterministic: MelonScore) -> str:
    """把 LLM 建议值与偏差写成 meta 字段（空串表示 LLM 未给分或与系统一致）。"""
    coerced = coerce_melon_score(llm_score)
    if coerced is None or coerced == deterministic.score:
        return ""
    return f"{coerced}/100（偏差 {coerced - deterministic.score:+d}，已忽略）"


def _to_candidates(breakdowns: list[Any]) -> tuple[Candidate, ...]:
    items: list[Candidate] = []
    for item in breakdowns[:MAX_CANDIDATES]:
        post = item.post
        items.append(
            Candidate(
                url=post.url,
                author=post.display_author,
                score=round(item.total, 1),
                summary=post.short_text(80),
            )
        )
    return tuple(items)


def build_report(
    payload: dict[str, Any] | None,
    *,
    primary: Post,
    evidence: list[Post],
    candidates: list[Any],
    date: str,
    melon: MelonScore | None = None,
    meta: ReportMeta | None = None,
    generated_by: str = "llm",
) -> GuaReport:
    """用清洗后的 payload 构建报告；payload 缺失/无引用时自动降级。"""
    deterministic = melon or melon_index(primary)
    allowed = allowed_links([primary, *evidence])
    base_meta = meta or ReportMeta(collected_at=primary.created_at.isoformat())

    if payload is None:
        return build_degraded_report(
            primary,
            candidates=candidates,
            date=date,
            melon=deterministic,
            reason=base_meta.downgrade_reason or "未获得可用的 LLM 输出",
            meta=base_meta,
        )

    clean, dropped = sanitize_payload(payload, allowed)
    if not payload_is_cited(clean, allowed):
        return build_degraded_report(
            primary,
            candidates=candidates,
            date=date,
            melon=deterministic,
            reason="生成内容未引用任何原始链接（无源），已降级为链接汇总",
            meta=base_meta,
            dropped=dropped,
        )

    quotes = tuple(
        Quote(author=mask_personal(item["author"]), text=mask_personal(item["text"]), link=item["link"])
        for item in clean.get("quotes") or []
    )
    timeline = tuple(
        TimelineItem(time=item["time"], event=mask_personal(item["event"]), link=item["link"])
        for item in clean.get("timeline") or []
    )
    stances = tuple(
        Stance(side=mask_personal(item["side"]), stance=mask_personal(item["stance"]), link=item["link"])
        for item in clean.get("stances") or []
    )
    sources = tuple(Source(title=_source_title(url, primary, evidence), url=url) for url in clean["sources"])
    if not sources:  # 至少保留主角原链
        sources = (Source(title=_source_title(primary.url, primary, evidence), url=primary.url),)
    unverified = tuple(clean.get("unverified") or []) or (DEFAULT_UNVERIFIED,)
    headline = mask_personal(clean.get("headline") or "") or primary.short_text(40)
    summary = mask_personal(clean.get("summary") or "") or primary.short_text(200)
    llm_score = _payload_score(payload)
    final_melon = resolve_melon(llm_score, deterministic, clean.get("melon_reasons") or [])

    return GuaReport(
        date=date,
        headline=headline,
        summary=summary,
        timeline=timeline,
        quotes=quotes,
        stances=stances,
        unverified=unverified,
        melon=final_melon,
        sources=sources,
        candidates=_to_candidates(candidates),
        meta=ReportMeta(
            generated_by=generated_by,
            llm_model=base_meta.llm_model,
            parsed_from=base_meta.parsed_from or str(payload.get("_parsed_from") or ""),
            collected_at=base_meta.collected_at,
            sources_used=base_meta.sources_used,
            warnings=base_meta.warnings,
            dropped_links=tuple(dropped[:10]),
            llm_melon_suggestion=melon_suggestion(llm_score, deterministic),
        ),
        primary=primary,
    )


def _payload_score(payload: dict[str, Any]) -> Any:
    """LLM 自评分（只作偏差对照，绝不参与公开指数）。"""
    index = payload.get("melon_index")
    if isinstance(index, dict) and "score" in index:
        return index.get("score")
    return payload.get("llm_melon_score")


def _source_title(url: str, primary: Post, evidence: list[Post]) -> str:
    for post in [primary, *evidence]:
        if post.url == url:
            return f"{post.display_author} 原帖"
    return url.split("/")[2] if url.count("/") >= 3 else "原始链接"


def build_degraded_report(
    primary: Post,
    *,
    candidates: list[Any],
    date: str,
    melon: MelonScore | None = None,
    reason: str,
    meta: ReportMeta | None = None,
    dropped: list[str] | None = None,
) -> GuaReport:
    """降级产出：只保留可核查的链接汇总与公开指标，不编任何叙述。"""
    deterministic = melon or melon_index(primary)
    base = meta or ReportMeta()
    created = primary.created_at.isoformat()
    timeline = (
        TimelineItem(time=created, event="原帖发布（材料中的原始时间）", link=primary.url),
    )
    if len(primary.media_urls) > 0:
        timeline = timeline + (
            TimelineItem(
                time=created,
                event=f"原帖附带 {len(primary.media_urls)} 张图片（图文证据，需人工核对内容）",
                link=primary.url,
            ),
        )
    quotes = (Quote(author=primary.display_author, text=primary.short_text(300), link=primary.url),)
    sources = [Source(title=f"{primary.display_author} 原帖", url=primary.url)]
    for item in candidates[:2]:
        sources.append(
            Source(title=f"{item.post.display_author} 备选", url=item.post.url)
        )
    return GuaReport(
        date=date,
        headline=f"仅链接汇总：{primary.short_text(30)}",
        summary=(
            "本次未能获得可用的生成式整理结果，本页未做任何推断或补充，"
            "只保留采集到的原始链接与公开互动数据，供读者自查。"
        ),
        timeline=timeline,
        quotes=quotes,
        stances=(),
        unverified=(
            reason,
            "未经交叉核实，页面不含结论性判断，请以原始链接为准。",
        ),
        melon=deterministic,
        sources=tuple(sources),
        candidates=_to_candidates(candidates),
        meta=ReportMeta(
            generated_by="degraded",
            llm_model=base.llm_model,
            parsed_from="none",
            collected_at=base.collected_at,
            sources_used=base.sources_used,
            warnings=base.warnings,
            downgrade_reason=reason,
            dropped_links=tuple(dropped or ()),
        ),
        primary=primary,
    )
