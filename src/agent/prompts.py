"""Prompt 构造：只喂材料、强制引用、强制标注不确定项。"""

from __future__ import annotations

from ..common.budget import truncate_to_tokens
from ..common.models import Post
from .melons import MelonScore

SYSTEM_PROMPT = (
    "你是「每日一瓜」的中文编辑，负责把公开可核查的材料整理成一页瓜报。\n"
    "硬性规则：\n"
    "1) 只使用用户消息里给出的材料；材料没写的事一律不写，不推测、不补全。\n"
    "2) 每条事实必须带材料中的原始链接；没有链接的结论只能放进 unverified 列表。\n"
    "3) 材料没有点名的人（例如只说「某知名 AI 博主」「大号」）不得替读者指认身份；\n"
    "   只能如实写「材料未点名」。\n"
    "4) 不做人身攻击，不复述手机号/邮箱/住址等隐私；涉及隐私一律打码或跳过。\n"
    "5) 当事人回应优先展示；若材料中没有回应，明确写「截至采集时点未见回应」。\n"
    "6) 吃瓜指数由系统按互动指标计算，你只能补充理由，不能自行改数值。\n"
    "7) 只输出 JSON，不要代码块、不要解释。"
)

_SCHEMA_HINT = """请输出如下 JSON（字段缺失就用空数组/空字符串，不要编造）：
{
  "headline": "18 字以内的一句话摘瓜",
  "summary": "3-5 句话讲清发生了什么，每句话能对应材料",
  "timeline": [{"time": "ISO8601 或材料中的时间", "event": "发生了什么", "link": "材料中的链接"}],
  "quotes": [{"author": "材料中的作者", "text": "原文或原文摘录", "link": "材料中的链接"}],
  "stances": [{"side": "哪一方/哪个人", "stance": "其立场或说法", "link": "材料中的链接"}],
  "unverified": ["尚未被材料证实、或材料未点名的说法"],
  "melon_reasons": ["补充吃瓜理由，不要改分数"],
  "sources": ["用到的材料链接，至少 1 条"]
}

材料如下（每条前面的 [n] 是编号，link 为原始链接）：
"""


def format_materials(primary: Post, evidence: list[Post], *, max_chars_per_post: int = 1200) -> str:
    lines: list[str] = []
    items = [primary, *[p for p in evidence if p.key != primary.key]]
    for index, post in enumerate(items, start=1):
        text = truncate_to_tokens(post.text, max_chars_per_post)
        metrics = post.metrics
        lines.append(
            f"[{index}] source={post.source} author={post.display_author} "
            f"followers={post.author_followers} link={post.url}\n"
            f"    created_at={post.created_at.isoformat()}\n"
            f"    metrics: likes={metrics.likes} replies={metrics.replies} "
            f"reposts={metrics.reposts} quotes={metrics.quotes} views={metrics.views}\n"
            f"    text: {text}"
        )
    return "\n".join(lines)


def build_messages(
    primary: Post,
    evidence: list[Post],
    melon: MelonScore,
    *,
    token_budget: int = 6000,
) -> list[dict[str, str]]:
    """构造 messages；材料按 token 预算截断，保证 prompt 不超预算。"""
    header = (
        _SCHEMA_HINT
        + f"\n系统已计算：吃瓜指数 {melon.score}/100（{melon.level}），"
        + "；".join(melon.reasons)
        + "\n\n"
    )
    budget_for_materials = max(500, token_budget - 400)
    materials = truncate_to_tokens(format_materials(primary, evidence), budget_for_materials)
    user = header + materials
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def build_comment_messages(
    command: str,
    issue_title: str,
    report_excerpt: str,
    extra: str,
) -> list[dict[str, str]]:
    """评论 Agent 的 prompt：基于已有瓜报与补充材料生成回帖。"""
    system = (
        "你是「每日一瓜」的评论区编辑。只根据给定材料回答，引用必须带链接；"
        "不确定就写「未证实」。不要人身攻击，不要输出隐私。中文回答，200 字以内。"
    )
    user = (
        f"命令：{command}\nIssue：{issue_title}\n\n已有瓜报摘录：\n{report_excerpt}\n\n"
        f"补充材料：\n{extra or '（无）'}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
