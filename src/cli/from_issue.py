"""投瓜回执：用户用「投瓜」Issue 模板提交链接，Agent 核查后回帖并写入候选池。

对应 ``gua-from-issue.yml``（``issues`` opened / labeled ``gua-submit``）。
核查只做可验证的部分：链接是否可达、公开指标、是否 X 状态帖；不替用户下判断。
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from ..collect.web import WebProbe
from ..collect.x_mirror import XMirrorCollector, is_x_status_url
from ..common.config import load_settings
from ..common.http import HttpClient, HttpError
from ..common.redaction import get_logger, setup_logging
from ..publish.issue import GitHubClient
from .env import emit, fail, load_dotenv, load_event

LOGGER = get_logger(__name__)
URL_RE = re.compile(r"https?://[^\s)\]\"'<>]+")
MAX_SEEDS_PER_ISSUE = 5
RECEIPT_MARKER = "🍉 投瓜核查回执"


def extract_urls(text: str, *, limit: int = MAX_SEEDS_PER_ISSUE) -> list[str]:
    """从 Issue 表单正文里抽链接（去重、去尾部标点）。"""
    urls: list[str] = []
    for raw in URL_RE.findall(text or ""):
        url = raw.rstrip(".,;:、，。）)】]")
        if url and url not in urls:
            urls.append(url)
    return urls[:limit]


def _load_candidates(state_dir: str) -> dict[str, Any]:
    path = Path(state_dir) / "candidates.json"
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return {"version": 1, "updated_at": "", "candidates": []}


def add_candidates(state_dir: str, entries: list[dict[str, Any]]) -> int:
    """把核查通过的帖子写进候选池（按 URL 去重），供次日 daily-gua 打分。"""
    payload = _load_candidates(state_dir)
    existing = {str(item.get("url")) for item in payload.get("candidates") or []}
    added = 0
    for entry in entries:
        if entry["url"] in existing:
            continue
        payload.setdefault("candidates", []).append(entry)
        existing.add(entry["url"])
        added += 1
    payload["updated_at"] = datetime.now().astimezone().isoformat()
    path = Path(state_dir) / "candidates.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return added


def check_url(url: str, http: HttpClient, *, mirror_base: str) -> dict[str, Any]:
    """核查单个链接：X 状态帖走镜像，其它走网页探测。"""
    if is_x_status_url(url):
        try:
            post = XMirrorCollector(http, base_url=mirror_base).fetch_post(url)
        except HttpError as exc:
            return {"url": url, "ok": False, "kind": "x", "note": str(exc)}
        return {
            "url": url,
            "ok": True,
            "kind": "x",
            "author": post.display_author,
            "followers": post.author_followers,
            "text": post.short_text(120),
            "metrics": post.metrics.to_dict(),
            "source": post.source,
        }
    doc = WebProbe(http).fetch(url)
    return {
        "url": url,
        "ok": doc.ok,
        "kind": "web",
        "author": "",
        "text": doc.title or "",
        "note": doc.note,
        "source": "web",
    }


def render_receipt(checked: list[dict[str, Any]], *, added: int) -> str:
    lines = [RECEIPT_MARKER, ""]
    if not checked:
        lines.extend(["未在 Issue 正文里找到链接，请按模板填写「瓜链接」后重试。", ""])
        return "\n".join(lines)

    lines.extend(["| 链接 | 可达 | 类型 | 关键信息 |", "| --- | --- | --- | --- |"])
    for item in checked:
        reachable = "✅" if item.get("ok") else "❌"
        kind = "X 帖" if item.get("kind") == "x" else "网页"
        if item.get("ok") and item.get("kind") == "x":
            metrics = item.get("metrics") or {}
            info = f"{item.get('author')} · 赞 {metrics.get('likes')} / 转 {metrics.get('reposts')} / 阅 {metrics.get('views')}"
        elif item.get("ok"):
            info = (item.get("text") or "")[:60]
        else:
            info = item.get("note") or "不可访问"
        info = info.replace("|", "/")
        lines.append(f"| {item['url']} | {reachable} | {kind} | {info} |")
    lines.append("")

    accepted = [item for item in checked if item.get("ok") and item.get("kind") == "x"]
    if accepted and added:
        lines.append(f"结论：{added} 条已进入**次日候选池**，将与当日采集结果一起参与打分。")
        lines.append("")
        lines.append("x 排序依据：互动增速 + 争议度（回复/引用对比点赞）+ 大号参与度。")
    elif accepted:
        lines.append("结论：链接有效，但已在候选池中，本次不重复入池。")
    else:
        lines.append(
            "结论：暂未入池。X 状态帖需可直接访问；网页链接请优先提供能追溯到原始帖的地址。"
        )
    lines.extend(
        [
            "",
            "说明：本回执只陈述可核查事实（链接可达性、公开互动数据），不对瓜的真伪下结论。",
            "如提交的是他人隐私内容，将不予收录。",
        ]
    )
    return "\n".join(lines)


def receipt_covers(client: GitHubClient, issue_number: int, urls: list[str]) -> str:
    """重复事件检测：``issues[labeled]`` 被自身打标再次触发时不能重复回执。

    幂等键是**回执内容**而不是「有没有回执」：只当已有回执覆盖了本次全部链接才跳过；
    用户在 Issue 里补充了新链接时仍然处理（发增量回执），不会被旧回执永久挡住。
    """
    if not urls:
        return ""
    try:
        comments = client.list_comments(issue_number)
    except HttpError as exc:
        LOGGER.warning("读取 Issue #%s 评论失败，按未回执处理: %s", issue_number, exc)
        return ""
    for item in comments:
        body = str(item.get("body") or "")
        if RECEIPT_MARKER in body and all(url in body for url in urls):
            return "该 Issue 下已存在覆盖本次链接的投瓜回执，跳过重复回执"
    return ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="处理投瓜 Issue，回帖并写入候选池")
    parser.add_argument("--issue", type=int, default=0, help="Issue 编号（默认取事件载荷）")
    parser.add_argument("--event", default=None, help="事件载荷 JSON 路径")
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--dry-run", action="store_true", help="只打印回执，不发帖不写池")
    return parser


def run(argv: list[str] | None = None) -> int:
    load_dotenv()
    setup_logging()
    args = build_parser().parse_args(argv)
    settings = load_settings()
    state_dir = args.state_dir or settings.state_dir
    http = HttpClient(timeout=settings.http_timeout_s, retries=settings.http_retries)

    event = load_event(args.event)
    issue_payload = event.get("issue") or {}
    issue_number = int(args.issue or issue_payload.get("number") or 0)
    body = str(issue_payload.get("body") or "")
    title = str(issue_payload.get("title") or "")

    client = GitHubClient(
        http,
        token=settings.github_token,
        repository=settings.github_repository,
        api_base=settings.github_api_base,
    )
    if issue_number and client.available:
        # 幂等建标：模板 labels: ["gua-submit"] 若在仓库不存在会被 GitHub 静默丢弃，
        # 导致 gua-from-issue 的标签触发条件永不成立；这里先补建，再读正文。
        client.ensure_labels(["gua-submit", "gua-accepted"])
        if not body:
            detail = client.get_issue(issue_number)
            body = str(detail.get("body") or "")
            title = str(detail.get("title") or "")

    urls = extract_urls(body)
    if not urls:
        receipt = render_receipt([], added=0)
        if not args.dry_run and issue_number and client.available:
            client.comment(issue_number, receipt)
        return emit({"ok": False, "issue": issue_number, "reason": "未找到链接", "receipt": receipt})

    if issue_number and client.available and not args.dry_run:
        duplicate = receipt_covers(client, issue_number, urls)
        if duplicate:
            return emit(
                {"ok": True, "issue": issue_number, "urls": urls, "skipped": duplicate}
            )

    checked = [check_url(url, http, mirror_base=settings.mirror_base) for url in urls]
    accepted = [item for item in checked if item.get("ok") and item.get("kind") == "x"]
    added = 0
    if not args.dry_run and accepted:
        added = add_candidates(
            state_dir,
            [
                {
                    "url": item["url"],
                    "author": item.get("author", ""),
                    "text": item.get("text", ""),
                    "added_at": datetime.now().astimezone().isoformat(),
                    "origin_issue": issue_number,
                }
                for item in accepted
            ],
        )
    receipt = render_receipt(checked, added=added)

    if not args.dry_run and issue_number:
        if not client.available:
            return fail("GitHub 未配置，无法回帖（GITHUB_TOKEN / GITHUB_REPOSITORY）")
        client.comment(issue_number, receipt)
        if accepted:
            client.add_labels(issue_number, ["gua-accepted"])

    return emit(
        {
            "ok": True,
            "issue": issue_number,
            "title": title,
            "urls": urls,
            "checked": checked,
            "added": added,
            "dry_run": args.dry_run,
        }
    )


def main() -> int:  # pragma: no cover
    return run()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
