"""通用网页探测：非 X 链接（投瓜的博客、论坛、新闻）取标题与摘要，作为证据补充。"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from ..common.http import HttpClient, HttpError, static_url_problem

_TITLE = re.compile(r"(?is)<title[^>]*>(.*?)</title>")
_OG_TITLE = re.compile(
    r"""(?is)<meta[^>]+property=["']og:title["'][^>]+content=["']([^"']+)["']"""
)
_OG_DESC = re.compile(
    r"""(?is)<meta[^>]+(?:property|name)=["'](?:og:description|description)["'][^>]+content=["']([^"']+)["']"""
)

MAX_BODY_CHARS = 200_000  # 只解析头部元信息，超大页面截断后再匹配，避免无界读入


def url_problem(url: str) -> str:
    """投瓜链接的静态安全校验：复用 ``common.http`` 的统一地址策略。

    返回空串表示可探测；否则返回中文原因（供回执如实说明「已跳过」）。
    这里只做无网络的字面量校验；**域名解析结果与每次重定向**由
    ``common.http.url_target_problem`` 与固定已校验 IP 的连接再次校验
    （防 DNS 重绑定/302 绕过）。
    """
    reason = static_url_problem(url)
    return f"{reason}，已跳过" if reason else ""


@dataclass(frozen=True)
class WebDoc:
    url: str
    title: str
    description: str
    ok: bool
    note: str = ""

    def as_text(self) -> str:
        head = self.title or self.url
        return f"{head}\n{self.description}".strip()


def _first(pattern: re.Pattern[str], page: str) -> str:
    match = pattern.search(page)
    if not match:
        return ""
    return " ".join(html.unescape(match.group(1)).split())


def parse_html_document(raw: str, *, url: str) -> WebDoc:
    """从 HTML 中抽标题与摘要（不解析正文，避免版权与体积问题）。"""
    title = _first(_OG_TITLE, raw) or _first(_TITLE, raw)
    description = _first(_OG_DESC, raw)
    return WebDoc(
        url=url,
        title=title[:300],
        description=description[:600],
        ok=bool(title or description),
        note="" if (title or description) else "未提取到标题/摘要",
    )


class WebProbe:
    """抓取网页头部元信息；失败返回 ``ok=False`` 而不抛异常（投瓜回帖需要给出原因）。"""

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def fetch(self, url: str) -> WebDoc:
        problem = url_problem(url)
        if problem:
            return WebDoc(url=url, title="", description="", ok=False, note=problem)
        try:
            raw = self._http.get_text(url, {"Accept": "text/html, */*"})
        except HttpError as exc:
            return WebDoc(url=url, title="", description="", ok=False, note=str(exc))
        doc = parse_html_document(raw[:MAX_BODY_CHARS], url=url)
        return doc


def doc_post(url: str, doc: WebDoc):  # pragma: no cover - 类型便捷包装
    """把网页探测结果包成 Post（供 collect.service 统一打分）。"""
    from ..common.models import Post

    return Post(
        id=url,
        url=url,
        author=doc.url.split("/")[2] if doc.url.count("/") >= 2 else "web",
        text=doc.as_text(),
        created_at=datetime.now(tz=UTC),
        source="web",
    )
