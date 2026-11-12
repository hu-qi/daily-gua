"""瓜报 Markdown 渲染（写入 ``gua/YYYY-MM-DD.md`` 与当日 Issue 正文）。

Front matter 使用「按键 + JSON 字符串值」的极简 YAML 子集，便于 publish 层稳定解析；
正文固定小节顺序，保证历史瓜报可被 Pages / feed 统一消费。
"""

from __future__ import annotations

import json

from .report import GuaReport

BRAND = "🍉 每日一瓜"
COMPLIANCE_NOTE = (
    "本页仅基于公开内容整理，每条事实附原始链接；推测与未经证实的信息已单独标注；"
    "涉及手机号 / 邮箱 / 住址等隐私一律打码，当事人回应优先展示。"
    "如当事方要求更正，请在当日 Issue 留言，更正将置顶。"
)


def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_front_matter(report: GuaReport) -> str:
    melon = report.melon
    lines = [
        "---",
        f"date: {_quote(report.date)}",
        f"title: {_quote(report_title(report.date))}",
        f"headline: {_quote(report.headline)}",
        f"melon_score: {melon.score if melon else 0}",
        f"melon_level: {_quote(melon.level if melon else '未知')}",
        f"generated_by: {_quote(report.meta.generated_by)}",
        f"primary_url: {_quote(report.primary.url if report.primary else '')}",
        "sources:",
    ]
    for url in report.original_links:
        lines.append(f"  - {_quote(url)}")
    lines.append("---")
    return "\n".join(lines)


def report_title(date: str) -> str:
    return f"{BRAND} {date}"


def _link(text: str, url: str) -> str:
    return f"[{text}]({url})" if url else text


def _melon_section(report: GuaReport) -> list[str]:
    melon = report.melon
    if melon is None:
        return []
    lines = ["## 吃瓜指数", "", f"**{melon.score}/100（{melon.level}）** {melon.emoji}", ""]
    lines.extend(f"- {reason}" for reason in melon.reasons)
    lines.append("")
    return lines


def render_markdown(report: GuaReport) -> str:
    """渲染完整瓜报正文（含 front matter）。"""
    parts: list[str] = [render_front_matter(report), ""]
    parts.append(f"# {BRAND} · {report.date}")
    parts.append("")
    parts.append(f"> {report.headline}")
    parts.append("")

    if report.meta.downgrade_reason:
        parts.append(f"> **降级说明**：{report.meta.downgrade_reason}")
        parts.append("")

    parts.extend(["## 一句话摘瓜", "", report.summary.strip() or "（暂无摘要）", ""])
    parts.extend(_melon_section(report))

    parts.extend(["## 来龙去脉（时间线）", ""])
    if report.timeline:
        for item in report.timeline:
            when = f"`{item.time}` " if item.time else ""
            link = f" — {_link('原链', item.link)}" if item.link else ""
            parts.append(f"- {when}{item.event}{link}")
    else:
        parts.append("- 材料中未提供可核实的完整时间线。")
    parts.append("")

    parts.extend(["## 关键原文", ""])
    if report.quotes:
        for quote in report.quotes:
            author = quote.author or "材料作者"
            parts.append(f"> **{author}**：{quote.text}")
            if quote.link:
                parts.append(">")
                parts.append(f"> {_link('原始链接', quote.link)}")
            parts.append("")
    else:
        parts.extend(["- 材料中未包含可引用的原文。", ""])

    parts.extend(["## 各方立场", ""])
    if report.stances:
        for stance in report.stances:
            side = f"**{stance.side}**：" if stance.side else ""
            link = f" — {_link('出处', stance.link)}" if stance.link else ""
            parts.append(f"- {side}{stance.stance}{link}")
    else:
        parts.append("- 截至采集时点，材料中未出现对立双方或多方表态。")
    parts.append("")

    parts.extend(["## ⚠️ 未证实 / 待核实", ""])
    for item in report.unverified:
        parts.append(f"- {item}")
    parts.append("")

    parts.extend(["## 候选瓜（Top3 备选）", ""])
    if report.candidates:
        parts.extend(["| 排序 | 来源 | 打分 | 摘要 |", "| --- | --- | --- | --- |"])
        for index, cand in enumerate(report.candidates, start=1):
            parts.append(f"| {index} | {cand.author} | {cand.score} | {cand.summary} |")
    else:
        parts.append("- 今日候选池仅此一条。")
    parts.append("")

    meta = report.meta
    parts.extend(["## 数据来源与采集说明", ""])
    parts.append(f"- 采集通道：{'、'.join(meta.sources_used) or '未记录'}")
    model_hint = f"（{meta.llm_model}）" if meta.llm_model else ""
    parts.append(f"- 加工方式：{meta.label}" + (model_hint if meta.generated_by == "llm" else ""))
    if meta.parsed_from:
        parts.append(f"- 解析路径：{meta.parsed_from}")
    if meta.collected_at:
        parts.append(f"- 数据时点：{meta.collected_at}")
    if meta.downgrade_reason:
        parts.append(f"- 降级原因：{meta.downgrade_reason}")
    if meta.dropped_links:
        parts.append(f"- 已丢弃无法溯源的链接：{len(meta.dropped_links)} 条")
    for warning in meta.warnings:
        parts.append(f"- 采集告警：{warning}")
    parts.append("")

    parts.extend(["## 合规说明", "", COMPLIANCE_NOTE, ""])
    return "\n".join(parts).rstrip() + "\n"


def render_issue_body(report: GuaReport, *, report_url: str = "", site_url: str = "") -> str:
    """当日 Issue 正文：摘要 + 时间线 + 原文 + 未证实 + 全文链接 + 互动命令。"""
    melon = report.melon
    lines = [f"## {report_title(report.date)}", "", f"> {report.headline}", ""]
    if melon:
        lines.extend(
            [f"**吃瓜指数**：{melon.score}/100（{melon.level}）{melon.emoji}", ""]
        )

    lines.extend(["### 一句话摘瓜", "", report.summary.strip() or "（暂无摘要）", ""])

    lines.extend(["### 时间线", ""])
    for item in report.timeline[:6]:
        when = f"`{item.time}` " if item.time else ""
        link = f" — {_link('原链', item.link)}" if item.link else ""
        lines.append(f"- {when}{item.event}{link}")
    if not report.timeline:
        lines.append("- 材料中未提供可核实的完整时间线。")
    lines.append("")

    lines.extend(["### 关键原文", ""])
    for quote in report.quotes[:3]:
        author = quote.author or "材料作者"
        lines.append(f"> **{author}**：{quote.text}")
        if quote.link:
            lines.append(f"> {_link('原始链接', quote.link)}")
        lines.append("")
    if not report.quotes:
        lines.extend(["- 材料中未包含可引用的原文。", ""])

    lines.extend(["### ⚠️ 未证实 / 待核实", ""])
    for item in report.unverified:
        lines.append(f"- {item}")
    lines.append("")

    if report.candidates:
        lines.extend(["### 今日备选", ""])
        for index, cand in enumerate(report.candidates, start=1):
            lines.append(f"{index}. {cand.author}（{cand.score}）— {_link('原链', cand.url)}")
        lines.append("")

    if report_url:
        lines.extend([f"**全文瓜报**：{_link(f'gua/{report.date}.md', report_url)}", ""])
    if site_url:
        lines.extend([f"**历史归档**：{site_url}", ""])

    lines.extend(
        [
            "---",
            "**互动命令**（直接在本 Issue 评论）：",
            "",
            "- `/more` — 追更：补充最新互动数据与新增动向",
            "- `/fact-check` — 证据核查：逐条核对未证实项，给出可核验结论",
            "- `/vote 🍉` / `/vote 🫠` — 吃瓜打分：🍉 真瓜 / 🫠 假瓜",
            "",
            f"<sub>本 Issue 由 GitHub Actions + LLM Agent 自动生成（{report.meta.label}）·"
            " 每条事实附原链 · 更正请直接留言，将置顶处理</sub>",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_comment_receipt(report: GuaReport) -> str:
    receipt = f"今日瓜报已更新：**{report.headline}**"
    if report.melon:
        receipt += f"\n\n吃瓜指数 {report.melon.score}/100（{report.melon.level}）{report.melon.emoji}"
    return receipt
