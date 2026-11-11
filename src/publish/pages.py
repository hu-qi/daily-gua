"""GitHub Pages 静态站：把 ``gua/*.md`` 渲染成 HTML 归档页（按日期倒序）。

只实现瓜报用到的 Markdown 子集（标题/列表/引用/表格/粗体/链接/行内代码），
不引入 markdown 依赖，避免 Actions 里多一层安装失败风险。
"""

from __future__ import annotations

import html
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

from ..common.frontmatter import ReportDigest, body_of, digest_from_text
from .feed import FEED_FILE

INLINE_CODE = re.compile(r"`([^`]+)`")
BOLD = re.compile(r"\*\*(.+?)\*\*")
LINK = re.compile(r"\[([^\]]+)\]\((\S+?)\)")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

STYLE = """
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,
sans-serif;max-width:820px;margin:0 auto;padding:24px 16px 64px;line-height:1.75;color:#222}
h1{font-size:1.9rem;line-height:1.3}h2{margin-top:2rem;border-bottom:1px solid #eee;padding-bottom:.3rem}
blockquote{margin:1rem 0;padding:.6rem 1rem;background:#fff8e6;border-left:4px solid #f0b429}
code{background:#f4f4f4;padding:.1rem .3rem;border-radius:4px;font-size:.9em}
table{border-collapse:collapse;width:100%}th,td{border:1px solid #e4e4e4;padding:.4rem .6rem;text-align:left}
a{color:#0b66c3}.meta{color:#666;font-size:.9rem}ul{padding-left:1.3rem}
""".strip()


def _inline(text: str) -> str:
    escaped = html.escape(text)
    escaped = INLINE_CODE.sub(lambda m: f"<code>{m.group(1)}</code>", escaped)
    escaped = BOLD.sub(lambda m: f"<strong>{m.group(1)}</strong>", escaped)
    escaped = LINK.sub(
        lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">{m.group(1)}</a>', escaped
    )
    return escaped


def markdown_to_html(markdown: str) -> str:
    """极简 Markdown → HTML（按行状态机）。"""
    out: list[str] = []
    list_open = False
    in_table = False
    in_quote = False
    for raw in markdown.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            if list_open:
                out.append("</ul>")
                list_open = False
            if in_table:
                out.append("</table>")
                in_table = False
            if in_quote:
                out.append("</blockquote>")
                in_quote = False
            continue
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            content = stripped[level:].strip()
            if list_open:
                out.append("</ul>")
                list_open = False
            if in_table:
                out.append("</table>")
                in_table = False
            out.append(f"<h{min(level, 6)}>{_inline(content)}</h{min(level, 6)}>")
            continue
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                continue
            if not in_table:
                out.append("<table>")
                in_table = True
                body = "".join(f"<th>{_inline(c)}</th>" for c in cells)
                out.append(f"<tr>{body}</tr>")
                continue
            body = "".join(f"<td>{_inline(c)}</td>" for c in cells)
            out.append(f"<tr>{body}</tr>")
            continue
        if stripped.startswith(">"):
            if in_table:
                out.append("</table>")
                in_table = False
            if not in_quote:
                out.append("<blockquote>")
                in_quote = True
            out.append(f"<p>{_inline(stripped.lstrip('> ').strip())}</p>")
            continue
        if stripped.startswith(("- ", "* ")):
            if in_quote:
                out.append("</blockquote>")
                in_quote = False
            if not list_open:
                out.append("<ul>")
                list_open = True
            out.append(f"<li>{_inline(stripped[2:])}</li>")
            continue
        if re.match(r"^\d+[.)]\s+", stripped):
            if not list_open:
                out.append("<ul>")
                list_open = True
            item = re.sub(r"^\d+[.)]\s+", "", stripped)
            out.append(f"<li>{_inline(item)}</li>")
            continue
        if list_open:
            out.append("</ul>")
            list_open = False
        if in_table:
            out.append("</table>")
            in_table = False
        if in_quote:
            out.append("</blockquote>")
            in_quote = False
        if set(stripped) <= set("-—") and len(stripped) >= 3:
            out.append("<hr>")
            continue
        out.append(f"<p>{_inline(stripped)}</p>")
    for flag, tag in ((list_open, "ul"), (in_table, "table"), (in_quote, "blockquote")):
        if flag:
            out.append(f"</{tag}>")
    return "\n".join(out)


def _page(title: str, body: str, *, footer: str = "") -> str:
    return (
        "<!doctype html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"utf-8\">\n"
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(title)}</title>\n<style>{STYLE}</style>\n</head>\n<body>\n"
        f"<header><h1>{html.escape(title)}</h1></header>\n<main>\n{body}\n</main>\n"
        f"{footer}\n</body>\n</html>\n"
    )


def build_site(
    gua_dir: str | Path,
    out_dir: str | Path,
    *,
    site_title: str = "🍉 每日一瓜",
    site_url: str = "",
    feed_path: str | Path | None = None,
    now: datetime | None = None,
) -> list[Path]:
    """生成 ``_site``：首页索引 + 每篇瓜报的 HTML 与 Markdown 原文件 + feed 副本。"""
    source = Path(gua_dir)
    target = Path(out_dir)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    (target / ".nojekyll").write_text("", encoding="utf-8")
    (target / "gua").mkdir(exist_ok=True)

    written: list[Path] = []
    digests: list[ReportDigest] = []
    for file in sorted(source.glob("*.md"), reverse=True):
        if not DATE_RE.match(file.stem):
            continue
        text = file.read_text(encoding="utf-8")
        digest = digest_from_text(text, path=str(file))
        digests.append(digest)
        (target / "gua" / file.name).write_text(text, encoding="utf-8")
        body = markdown_to_html(body_of(text))
        melon = (
            f'<p class="meta">吃瓜指数 {digest.melon_score}/100（{digest.melon_level}）·'
            f" 生成方式：{digest.generated_by or '未知'}</p>"
            if digest.melon_level
            else ""
        )
        page = _page(
            f"{site_title} · {digest.date}",
            melon + body,
            footer='<footer><p><a href="../index.html">返回归档</a></p></footer>',
        )
        page_path = target / "gua" / f"{file.stem}.html"
        page_path.write_text(page, encoding="utf-8")
        written.extend([page_path, target / "gua" / file.name])

    latest = digests[0] if digests else None
    rows = "\n".join(
        f'<li><a href="gua/{d.date}.html">{d.date}</a> · {html.escape(d.headline or "—")}'
        f' · 指数 {d.melon_score if d.melon_level else "—"}</li>'
        for d in digests
    )
    index_body = (
        (f'<p class="meta">最新一页：<a href="gua/{latest.date}.html">{latest.date}</a></p>'
         if latest
         else '<p class="meta">还没有瓜报。</p>')
        + f"<h2>历史归档</h2><ul>{rows}</ul>"
        + (f'<p class="meta"><a href="{html.escape(site_url)}/{FEED_FILE}">RSS 订阅</a></p>'
           if site_url else "")
    )
    index_page = _page(site_title, index_body)
    (target / "index.html").write_text(index_page, encoding="utf-8")
    written.append(target / "index.html")

    if feed_path is not None and Path(feed_path).is_file():
        shutil.copyfile(feed_path, target / FEED_FILE)
        written.append(target / FEED_FILE)
    return written


def build_timestamp(now: datetime | None = None) -> str:
    return (now or datetime.now(tz=UTC)).strftime("%Y-%m-%d %H:%M UTC")
