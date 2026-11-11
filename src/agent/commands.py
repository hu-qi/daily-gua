"""评论区命令处理：``/more`` 追更、``/fact-check`` 核查、``/vote`` 吃瓜打分。

设计要点：
- 全部结果都能在**无 LLM** 时产出（降级为数据表 + 链接），LLM 只做润色；
- 回复必须带原始链接，避免变成无源断言；
- 幂等由调用方按 comment id 去重（见 cli.comment_agent），本模块只负责内容。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from ..collect.x_mirror import XMirrorCollector, is_x_status_url
from ..common.frontmatter import ReportDigest
from ..common.http import HttpClient, HttpError
from ..common.models import Metrics, Post, utc_now
from ..rank.snapshots import PostSnapshot

COMMAND_RE = re.compile(r"(?:^|\s)/(more|fact-check|vote)\b\s*(.*)$", re.IGNORECASE | re.MULTILINE)
VOTE_MELON = "🍉"
VOTE_BAD = "🫠"
SELF_MARKER = "每日一瓜"


@dataclass(frozen=True)
class CommentCommand:
    name: str
    argument: str
    raw: str


def parse_command(body: str) -> CommentCommand | None:
    """取首个命令；``/vote 🍉`` 的票型从参数里解析。"""
    if not body:
        return None
    match = COMMAND_RE.search(body)
    if not match:
        return None
    return CommentCommand(
        name=match.group(1).lower(),
        argument=(match.group(2) or "").strip(),
        raw=body,
    )


def parse_vote(argument: str) -> str | None:
    if VOTE_MELON in argument:
        return VOTE_MELON
    if VOTE_BAD in argument:
        return VOTE_BAD
    lowered = argument.lower()
    if lowered in {"melon", "true", "真瓜", "瓜", "1", "yes", "y"}:
        return VOTE_MELON
    if lowered in {"bad", "false", "假瓜", "烂瓜", "0", "no", "n"}:
        return VOTE_BAD
    return None


def tally_votes(comments: list[dict[str, object]]) -> tuple[int, int]:
    """统计票数：同一条评论只算一次，只认 ``/vote`` 命令。"""
    melon = bad = 0
    for item in comments:
        body = str(item.get("body") or "")
        user = item.get("user") or {}
        login = str(user.get("login") or "") if isinstance(user, dict) else ""
        if not login or "bot" in login.lower():
            continue
        command = parse_command(body)
        if command is None or command.name != "vote":
            continue
        vote = parse_vote(command.argument)
        if vote == VOTE_MELON:
            melon += 1
        elif vote == VOTE_BAD:
            bad += 1
    return melon, bad


def _metric_row(label: str, before: int, after: int) -> str:
    delta = after - before
    sign = f"+{delta}" if delta >= 0 else str(delta)
    return f"| {label} | {before} | {after} | {sign} |"


def render_more(
    report: ReportDigest,
    *,
    latest: Post | None,
    previous: PostSnapshot | None,
    checked_at: datetime,
    error: str = "",
) -> str:
    """``/more``：对比上次快照与当前指标，给出增量与数据来源。"""
    when = checked_at.strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"追更 · {report.date}（数据时点 {when}）", ""]
    if error or latest is None:
        lines.extend(
            [
                f"暂时未能取到最新数据：{error or '上游不可达'}。",
                "",
                f"原帖：{report.primary_url}" if report.primary_url else "",
                "可在 5 分钟后再次评论 `/more` 重试；本回复不包含推测内容。",
            ]
        )
        return "\n".join(line for line in lines if line)

    before = previous.metrics if previous else Metrics()
    after = latest.metrics
    lines.extend(
        [
            "| 指标 | 上次 | 当前 | 增量 |",
            "| --- | --- | --- | --- |",
            _metric_row("点赞", before.likes, after.likes),
            _metric_row("回复", before.replies, after.replies),
            _metric_row("转发", before.reposts, after.reposts),
            _metric_row("引用", before.quotes, after.quotes),
            _metric_row("阅读", before.views, after.views),
            "",
        ]
    )
    if previous is not None:
        hours = (checked_at - previous.captured_at).total_seconds() / 3600.0
        if hours > 0:
            growth = (after.engagement - before.engagement) / hours
            lines.append(f"加权互动增速约 {growth:.1f}/小时（区间 {hours:.1f} 小时）。")
            lines.append("")
    lines.append(f"最新原文：{latest.url}")
    if report.primary_url and report.primary_url != latest.url:
        lines.append(f"原帖链接：{report.primary_url}")
    lines.append("")
    lines.append("以上均为平台公开指标，不含推断；如需逐条核查请评论 `/fact-check`。")
    return "\n".join(lines)


def render_fact_check(
    report: ReportDigest,
    *,
    reachable: dict[str, bool],
    current: Post | None,
    checked_at: datetime,
) -> str:
    """``/fact-check``：把「未证实项」和「链接可达性」摊开给读者自查。"""
    ok_primary = current is not None
    lines = [f"证据核查 · {report.date}（核查时点 {checked_at.strftime('%Y-%m-%d %H:%M UTC')}）", ""]
    lines.extend(["| 核查项 | 结论 | 依据 |", "| --- | --- | --- |"])
    if report.primary_url:
        status = "✅ 链接可访问" if ok_primary else "⚠️ 链接不可访问 / 需人工核对"
        lines.append(f"| 主帖是否仍可访问 | {status} | {report.primary_url} |")
    unreachable = [url for url, ok in reachable.items() if not ok]
    if reachable:
        lines.append(
            f"| 引用链接可达性 | {len(reachable) - len(unreachable)}/{len(reachable)} 可访问 | "
            f"{', '.join(unreachable) if unreachable else '全部可访问'} |"
        )
    for item in report.unverified[:6]:
        lines.append(f"| {_short(item, 40)} | ⚠️ 未获独立来源证实 | {report.primary_url or '—'} |")
    lines.append("")
    if current is not None:
        lines.append(
            f"当前公开互动：点赞 {current.metrics.likes} · 回复 {current.metrics.replies} · "
            f"引用 {current.metrics.quotes} · 阅读 {current.metrics.views}。"
        )
    lines.extend(
        [
            "",
            "核查原则：只有能指回原始链接的内容才算已核实；"
            "仅凭截图、转述或群聊截图的信息一律标注「未证实」。",
            "若你是当事人或持有直接证据，欢迎在本 Issue 补充链接，更正会置顶。",
        ]
    )
    return "\n".join(lines)


def render_vote(melon: int, bad: int, *, vote: str | None, author: str) -> str:
    total = melon + bad
    lines = ["吃瓜打分（🍉 真瓜 / 🫠 假瓜）", ""]
    if vote:
        lines.append(f"已记录 @{author} 的投票：{vote}")
        lines.append("")
    if total == 0:
        lines.append("目前还没有有效票。评论 `/vote 🍉` 或 `/vote 🫠` 即可计票。")
        return "\n".join(lines)
    melon_pct = round(melon / total * 100)
    lines.extend(
        [
            f"🍉 真瓜：{melon} 票（{melon_pct}%）",
            f"🫠 假瓜：{bad} 票（{100 - melon_pct}%）",
            "",
            "计票口径：每位用户每类命令计一次，机器人评论不计票。",
        ]
    )
    return "\n".join(lines)


def _short(text: str, limit: int) -> str:
    flat = " ".join(str(text).split()).replace("|", "/")
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


class CommentAgent:
    """把命令解析 → 取数 → 渲染串起来。取数失败也给出可读回复（不静默）。"""

    def __init__(
        self,
        *,
        http: HttpClient | None = None,
        mirror_base: str = "https://api.fxtwitter.com",
        fetch_post=None,
    ) -> None:
        self._http = http or HttpClient()
        self._mirror_base = mirror_base
        self._fetch_post = fetch_post

    def _latest(self, url: str) -> tuple[Post | None, str]:
        if not url or not is_x_status_url(url):
            return None, "非 X 链接，无法自动取最新指标"
        try:
            if self._fetch_post is not None:
                return self._fetch_post(url), ""
            collector = XMirrorCollector(self._http, base_url=self._mirror_base)
            return collector.fetch_post(url), ""
        except HttpError as exc:
            return None, str(exc)

    def handle(
        self,
        command: CommentCommand,
        *,
        report: ReportDigest,
        snapshot: PostSnapshot | None,
        issue_comments: list[dict[str, object]],
        author: str,
        now: datetime | None = None,
    ) -> str:
        moment = now or utc_now()
        if command.name == "more":
            latest, error = self._latest(report.primary_url)
            return render_more(
                report,
                latest=latest,
                previous=snapshot,
                checked_at=moment,
                error=error,
            )
        if command.name == "fact-check":
            latest, error = self._latest(report.primary_url)
            reachable = dict.fromkeys(report.sources[:5], True)
            if error:
                reachable[report.primary_url or "primary"] = False
            return render_fact_check(
                report, reachable=reachable, current=latest, checked_at=moment
            )
        vote = parse_vote(command.argument)
        melon, bad = tally_votes(issue_comments)
        return render_vote(melon, bad, vote=vote, author=author)
