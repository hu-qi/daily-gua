"""README 首页索引：只替换标记区间内的内容，其余手写部分（架构图、说明）原样保留。"""

from __future__ import annotations

from ..common.frontmatter import ReportDigest

START_MARKER = "<!-- GUA:INDEX:START -->"
END_MARKER = "<!-- GUA:INDEX:END -->"
EMPTY_HINT = "_还没有瓜报。首次运行 `daily-gua` workflow 或本地 `python -m src.cli.daily` 生成。_"


def render_index(reports: list[ReportDigest], *, limit: int = 14, site_url: str = "") -> str:
    """渲染索引表格；最新一页单独高亮，其余进表格。"""
    if not reports:
        return f"{START_MARKER}\n{EMPTY_HINT}\n{END_MARKER}"
    latest = reports[0]
    lines: list[str] = [START_MARKER, ""]
    headline = latest.headline or latest.title or latest.date
    melon = f"{latest.melon_score}/100（{latest.melon_level}）" if latest.melon_level else "—"
    lines.append(f"**最新一页**：[{latest.date} · {headline}](gua/{latest.date}.md) — 吃瓜指数 {melon}")
    lines.append("")
    lines.extend(["| 日期 | 头条 | 吃瓜指数 | 链接 |", "| --- | --- | --- | --- |"])
    for digest in reports[:limit]:
        headline = (digest.headline or digest.title or "—").replace("|", "/")
        melon = (
            f"{digest.melon_score}（{digest.melon_level}）" if digest.melon_level else "—"
        )
        lines.append(f"| {digest.date} | {headline} | {melon} | [打开](gua/{digest.date}.md) |")
    if len(reports) > limit:
        archive = f"{site_url.rstrip('/')}/" if site_url else "GitHub Pages"
        lines.append("")
        lines.append(f"更早的瓜报见[归档站]({archive})或 `gua/` 目录。")
    lines.append(END_MARKER)
    return "\n".join(lines)


def update_readme(
    readme_text: str,
    reports: list[ReportDigest],
    *,
    limit: int = 14,
    site_url: str = "",
) -> str:
    """把索引写回 README；缺少标记时追加到文件末尾（不破坏已有内容）。"""
    index = render_index(reports, limit=limit, site_url=site_url)
    if START_MARKER in readme_text and END_MARKER in readme_text:
        head, _, rest = readme_text.partition(START_MARKER)
        _, _, tail = rest.partition(END_MARKER)
        return f"{head}{index}{tail}"
    return readme_text.rstrip() + "\n\n" + index + "\n"
