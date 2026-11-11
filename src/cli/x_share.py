"""X 分享草稿：只生成文案 + intent 链接，**不自动发帖**（人工确认后自行点击发布）。

对应 ``x-share.yml``（仅 ``workflow_dispatch``）。产物落在 ``dist/x-draft-<date>.md``，
并可作为当日 Issue 的评论（``--comment``）供人工取用。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import quote

from ..common.config import load_settings, site_url
from ..common.frontmatter import load_digest
from ..common.http import HttpClient
from ..common.redaction import setup_logging
from ..publish.issue import GitHubClient, issue_title
from ..publish.writer import list_reports
from .env import emit, load_dotenv

MAX_X_TEXT = 180


def build_text(headline: str, score: int, level: str, emoji: str = "") -> str:
    """文案控制长度（X 单条 280 字符上限，中文按字计）。"""
    core = f"🍉 今日瓜：{headline}"
    meta = f"吃瓜指数 {score}/100（{level}）{emoji}".strip()
    text = f"{core}\n{meta}"
    return text[:MAX_X_TEXT]


def build_intent_url(text: str, url: str) -> str:
    return f"https://x.com/intent/tweet?text={quote(text)}&url={quote(url)}"


def render_draft(date: str, headline: str, score: int, level: str, issue_url: str) -> str:
    text = build_text(headline, score, level)
    intent = build_intent_url(text, issue_url)
    return (
        f"# X 分享草稿 · {date}\n\n"
        "> 不会自动发帖。请人工确认文案后点击 intent 链接手动发布。\n\n"
        f"## 文案\n\n```\n{text}\n{issue_url}\n```\n\n"
        f"## 一键打开意图链接\n\n{intent}\n\n"
        f"## 关联 Issue\n\n{issue_url}\n"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成 X 分享草稿（不自动发帖）")
    parser.add_argument("--date", default=None)
    parser.add_argument("--gua-dir", default=None)
    parser.add_argument("--out-dir", default="dist")
    parser.add_argument("--issue-url", default="")
    parser.add_argument("--comment", action="store_true", help="把草稿作为评论发到当日 Issue")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def run(argv: list[str] | None = None) -> int:
    load_dotenv()
    setup_logging()
    args = build_parser().parse_args(argv)
    settings = load_settings()
    gua_dir = args.gua_dir or settings.gua_dir

    date = args.date
    if not date:
        reports = list_reports(gua_dir, limit=1)
        if not reports:
            return emit({"ok": False, "reason": "没有可分享的瓜报"}, exit_code=2)
        date = reports[0].date

    digest = load_digest(Path(gua_dir) / f"{date}.md")
    if digest is None:
        return emit({"ok": False, "reason": f"瓜报不存在: {date}"}, exit_code=2)

    http = HttpClient(timeout=settings.http_timeout_s, retries=settings.http_retries)
    client = GitHubClient(
        http,
        token=settings.github_token,
        repository=settings.github_repository,
        api_base=settings.github_api_base,
    )
    issue_url = args.issue_url
    if not issue_url and client.available:
        existing = client.find_issue_by_title(issue_title(date))
        if existing is not None:
            issue_url = str(existing.get("html_url") or "")
    if not issue_url:
        site = site_url()
        issue_url = f"{site}/gua/{date}.md" if site else ""

    draft = render_draft(
        date, digest.headline, digest.melon_score, digest.melon_level, issue_url
    )
    path = None
    if not args.dry_run:
        target = Path(args.out_dir) / f"x-draft-{date}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(draft, encoding="utf-8")
        path = str(target)
        if args.comment and client.available:
            existing = client.find_issue_by_title(issue_title(date))
            if existing is not None:
                client.comment(int(existing["number"]), draft)

    return emit(
        {
            "ok": True,
            "date": date,
            "draft": path,
            "issue_url": issue_url,
            "intent_url": build_intent_url(
                build_text(digest.headline, digest.melon_score, digest.melon_level), issue_url
            ),
            "auto_posted": False,
            "dry_run": args.dry_run,
        }
    )


def main() -> int:  # pragma: no cover
    return run()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
