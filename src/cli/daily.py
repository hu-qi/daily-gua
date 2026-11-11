"""每日一瓜主流程：采集 → 打分 → LLM 整理 → 落盘 → README/feed → 当日 Issue。

对应 ``daily-gua.yml``；也支持本地 ``python -m src.cli.daily --dry-run`` 试跑。
未配置 Secrets 时仍会走「公开镜像 + RSS」兜底并产出瓜报（Issue 步骤自动跳过）。
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..agent.generate import generate_report
from ..agent.render import render_issue_body, render_markdown
from ..collect.service import collect
from ..common.config import Settings, load_settings, site_url
from ..common.frontmatter import digest_from_text
from ..common.http import HttpClient, HttpError
from ..common.redaction import setup_logging
from ..publish.feed import FEED_FILE, render_feed
from ..publish.issue import BUSINESS_LABELS, GitHubClient, issue_title
from ..publish.readme import update_readme
from ..publish.writer import list_reports, write_report
from ..rank.scorer import RankedResult, rank_posts
from ..rank.snapshots import load_snapshots, prune_snapshots, save_snapshots
from .env import beijing_today, emit, load_dotenv, valid_date


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成当日瓜报并开当日 Issue")
    parser.add_argument("--date", type=valid_date, default=None, help="瓜报日期（默认北京今天）")
    parser.add_argument("--seed", action="append", default=[], help="额外种子链接，可重复")
    parser.add_argument("--no-llm", action="store_true", help="强制走降级（仅链接汇总）")
    parser.add_argument("--no-issue", action="store_true", help="不创建/更新 Issue")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写文件不发帖")
    parser.add_argument("--gua-dir", default=None)
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--top", type=int, default=5, help="送入 LLM 的备选数量")
    return parser


def _candidate_pool(state_dir: str) -> list[str]:
    """投瓜候选池（``state/candidates.json``）里待评估的链接。"""
    path = Path(state_dir) / "candidates.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    entries = data.get("candidates") if isinstance(data, dict) else None
    urls: list[str] = []
    for entry in entries or []:
        url = str((entry or {}).get("url") or "")
        if url and url not in urls:
            urls.append(url)
    return urls


def run(argv: list[str] | None = None) -> int:
    load_dotenv()
    setup_logging()
    args = build_parser().parse_args(argv)
    settings: Settings = load_settings()
    gua_dir = args.gua_dir or settings.gua_dir
    state_dir = args.state_dir or settings.state_dir
    date = args.date or beijing_today()
    now = datetime.now().astimezone()

    http = HttpClient(timeout=settings.http_timeout_s, retries=settings.http_retries)
    seeds: list[str] = list(args.seed)
    seeds.extend(settings.seed_urls)
    seeds.extend(_candidate_pool(state_dir))
    seeds = list(dict.fromkeys(seeds))

    collected = collect(settings, http, seeds=tuple(seeds))
    if not collected.posts:
        return emit(
            {
                "ok": False,
                "reason": "所有采集通道均无结果",
                "warnings": collected.warnings,
            },
            exit_code=2,
        )

    snapshot_path = Path(state_dir) / "snapshots.json"
    snapshots = prune_snapshots(load_snapshots(snapshot_path), now=now)
    ranked: RankedResult = rank_posts(collected.posts, snapshots=snapshots, now=now)
    if ranked.top1 is None:
        return emit({"ok": False, "reason": "没有可用候选（正文为空或互动不足）"}, exit_code=2)

    primary = ranked.top1.post
    evidence = [item.post for item in ranked.scored[: args.top]]
    generated = generate_report(
        primary,
        evidence,
        ranked.candidates,
        date=date,
        settings=settings,
        sources_used=tuple(collected.sources),
        warnings=tuple(collected.warnings),
        degrade_reason="--no-llm 指定强制降级" if args.no_llm else None,
        now=now,
    )
    report = generated.report
    markdown = render_markdown(report)

    report_file = None
    if not args.dry_run:
        report_file = write_report(gua_dir, date, markdown)
        if not snapshot_path.parent.exists():
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        save_snapshots(snapshot_path, collected.posts, now=now)
        digests = list_reports(gua_dir)
        readme_path = Path("README.md")
        if readme_path.is_file():
            readme_path.write_text(
                update_readme(
                    readme_path.read_text(encoding="utf-8"),
                    digests,
                    site_url=site_url(),
                ),
                encoding="utf-8",
            )
        feed = render_feed(digests, site_url=site_url(), now=now)
        Path(FEED_FILE).write_text(feed, encoding="utf-8")

    issue_payload: dict[str, Any] = {}
    if not args.dry_run and not args.no_issue:
        client = GitHubClient(
            http,
            token=settings.github_token,
            repository=settings.github_repository,
            api_base=settings.github_api_base,
        )
        if client.available:
            # 首次运行自愈：幂等创建 gua / daily-gua / gua-submit / gua-accepted 四个业务标签，
            # 否则投瓜模板标签不存在会被 GitHub 静默丢弃，gua-from-issue 链路永不触发。
            client.ensure_labels(BUSINESS_LABELS)
            report_url = (
                f"{client.web_base()}/blob/main/{gua_dir}/{date}.md"
                if client.repository
                else ""
            )
            body = render_issue_body(report, report_url=report_url, site_url=site_url())
            issue, created = client.ensure_issue(
                title=issue_title(date), body=body, labels=["gua", "daily-gua"]
            )
            if not created:
                # 同日重跑：瓜报文件已覆盖，Issue 正文必须同步刷新，否则页面与 Issue 不一致
                number = int(issue.get("number") or 0)
                if number:
                    try:
                        client.update_issue_body(number, body)
                    except HttpError as exc:
                        client.warn(f"刷新 Issue #{number} 正文失败（{exc}），正文仍是上一次的版本")
            issue_payload = {
                "number": issue.get("number"),
                "url": issue.get("html_url"),
                "created": created,
                "warnings": client.drain_warnings(),
            }
        else:
            issue_payload = {"skipped": "GitHub 未配置（缺 GITHUB_TOKEN / GITHUB_REPOSITORY）"}

    digest = digest_from_text(markdown, path=str(report_file or ""))
    return emit(
        {
            "ok": True,
            "date": date,
            "headline": report.headline,
            "melon": {"score": digest.melon_score, "level": digest.melon_level},
            "generated_by": report.meta.generated_by,
            "degraded": report.meta.degraded,
            "downgrade_reason": report.meta.downgrade_reason,
            "report_file": str(report_file) if report_file else None,
            "issue": issue_payload,
            "sources_used": collected.sources,
            "warnings": [*collected.warnings, *(issue_payload.get("warnings") or [])],
            "notes": generated.notes,
            "dry_run": args.dry_run,
        }
    )


def main() -> int:  # pragma: no cover - 入口包装
    return run()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
