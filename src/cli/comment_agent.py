"""评论区 Agent：解析 ``/more`` / ``/fact-check`` / ``/vote`` 并回帖。

对应 ``gua-comment-agent.yml``（``issue_comment``）。幂等：按 comment id 去重，
状态落在 ``state/handled_comments.json`` 并随 workflow 提交回仓库。
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from ..agent.commands import CommentAgent, parse_command
from ..common.config import load_settings
from ..common.frontmatter import load_digest
from ..common.http import HttpClient
from ..common.models import canonical_url, status_id_from_url
from ..common.redaction import setup_logging
from ..publish.issue import GitHubClient
from ..publish.writer import list_reports
from ..rank.snapshots import load_snapshots
from .env import emit, fail, load_dotenv, load_event

TITLE_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
STATE_FILE = "handled_comments.json"


def issue_date_from_title(title: str) -> str | None:
    match = TITLE_DATE_RE.search(title or "")
    return match.group(1) if match else None


def _load_state(state_dir: str) -> dict[str, Any]:
    path = Path(state_dir) / STATE_FILE
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return {"version": 1, "handled": {}}


def mark_handled(state_dir: str, comment_id: int, *, command: str, issue: int) -> None:
    payload = _load_state(state_dir)
    payload.setdefault("handled", {})[str(comment_id)] = {
        "command": command,
        "issue": issue,
        "at": datetime.now().astimezone().isoformat(),
    }
    # 只保留最近 500 条，避免状态文件无限膨胀
    handled = payload["handled"]
    if len(handled) > 500:
        trimmed = dict(sorted(handled.items(), key=lambda kv: str(kv[1].get("at") or ""))[-500:])
        payload["handled"] = trimmed
    path = Path(state_dir) / STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def is_handled(state_dir: str, comment_id: int) -> bool:
    return str(comment_id) in (_load_state(state_dir).get("handled") or {})


def snapshot_key(url: str) -> str:
    sid = status_id_from_url(url)
    return f"x:{sid}" if sid else canonical_url(url)


def pick_report_date(state_dir: str, gua_dir: str, title: str) -> str | None:
    date = issue_date_from_title(title)
    if date:
        return date
    reports = list_reports(gua_dir, limit=1)
    return reports[0].date if reports else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="处理 Issue 评论里的 /more /fact-check /vote")
    parser.add_argument("--event", default=None, help="事件载荷 JSON 路径")
    parser.add_argument("--issue", type=int, default=0)
    parser.add_argument("--comment-id", type=int, default=0)
    parser.add_argument("--body", default="", help="评论正文（离线测试用）")
    parser.add_argument("--author", default="")
    parser.add_argument("--gua-dir", default=None)
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--dry-run", action="store_true", help="只打印回复，不发帖")
    return parser


def run(argv: list[str] | None = None) -> int:
    load_dotenv()
    setup_logging()
    args = build_parser().parse_args(argv)
    settings = load_settings()
    gua_dir = args.gua_dir or settings.gua_dir
    state_dir = args.state_dir or settings.state_dir

    event = load_event(args.event)
    comment = event.get("comment") or {}
    issue_payload = event.get("issue") or {}
    body = args.body or str(comment.get("body") or "")
    comment_id = int(args.comment_id or comment.get("id") or 0)
    issue_number = int(args.issue or issue_payload.get("number") or 0)
    title = str(issue_payload.get("title") or "")
    user = comment.get("user") or {}
    author = args.author or str(user.get("login") or "")
    login = (author or "").lower()

    command = parse_command(body)
    if command is None:
        return emit({"ok": True, "skipped": "非命令评论"})
    if login and ("bot" in login or login.endswith("[bot]")):
        return emit({"ok": True, "skipped": "忽略机器人评论"})
    if is_handled(state_dir, comment_id):
        return emit({"ok": True, "skipped": "该评论已处理（幂等去重）", "comment_id": comment_id})

    date = pick_report_date(state_dir, gua_dir, title)
    digest = load_digest(Path(gua_dir) / f"{date}.md") if date else None
    if digest is None:
        reply = (
            "当日瓜报尚未生成（或已归档），暂时无法处理该命令。"
            "瓜报生成后重新评论即可。"
        )
        if not args.dry_run and issue_number:
            client = GitHubClient(
                HttpClient(),
                token=settings.github_token,
                repository=settings.github_repository,
                api_base=settings.github_api_base,
            )
            if client.available:
                client.comment(issue_number, reply)
        return emit({"ok": False, "reason": "缺少当日瓜报", "date": date, "reply": reply})

    http = HttpClient(timeout=settings.http_timeout_s, retries=settings.http_retries)
    snapshots = load_snapshots(Path(state_dir) / "snapshots.json")
    snapshot = snapshots.get(snapshot_key(digest.primary_url))

    comments: list[dict[str, object]] = []
    client = GitHubClient(
        http,
        token=settings.github_token,
        repository=settings.github_repository,
        api_base=settings.github_api_base,
    )
    if issue_number and client.available:
        try:
            comments = client.list_comments(issue_number)
        except Exception:  # 取评论失败时仍处理当前评论
            comments = [comment] if comment else []

    agent = CommentAgent(http=http, mirror_base=settings.mirror_base)
    reply = agent.handle(
        command,
        report=digest,
        snapshot=snapshot,
        issue_comments=comments,
        author=author,
    )

    posted: dict[str, Any] = {}
    if not args.dry_run and issue_number:
        if not client.available:
            return fail("GitHub 未配置，无法回帖（GITHUB_TOKEN / GITHUB_REPOSITORY）")
        posted = client.comment(issue_number, reply)
        mark_handled(state_dir, comment_id, command=command.name, issue=issue_number)

    return emit(
        {
            "ok": True,
            "issue": issue_number,
            "comment_id": comment_id,
            "command": command.name,
            "author": author,
            "posted": bool(posted),
            "comment_url": posted.get("html_url") if isinstance(posted, dict) else "",
            "reply": reply,
            "dry_run": args.dry_run,
        }
    )


def main() -> int:  # pragma: no cover
    return run()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
