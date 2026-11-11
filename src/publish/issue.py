"""GitHub Issue 发布：开当日 Issue、回帖、读写评论（幂等）。

只要 ``GITHUB_TOKEN`` + ``GITHUB_REPOSITORY`` 存在就走 REST；缺失时 ``available=False``，
调用方（CLI）跳过发帖但保留瓜报文件 —— 不静默失败，也不假装成功。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from ..common.http import HttpClient, HttpError
from ..common.redaction import get_logger

LOGGER = get_logger(__name__)


class GitHubClient:
    def __init__(
        self,
        http: HttpClient | None = None,
        *,
        token: str | None,
        repository: str | None,
        api_base: str = "https://api.github.com",
    ) -> None:
        self._http = http or HttpClient()
        self._token = token
        self._repo = repository
        self._base = api_base.rstrip("/")

    @property
    def available(self) -> bool:
        return bool(self._token and self._repo and "/" in str(self._repo))

    @property
    def repository(self) -> str:
        return self._repo or ""

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _url(self, path: str) -> str:
        return f"{self._base}/repos/{self._repo}{path}"

    def _get(self, url: str) -> Any:
        return self._http.get_json(url, self._headers())

    def _post(self, url: str, payload: dict[str, Any]) -> Any:
        return self._http.post_json(url, payload, self._headers())

    def _patch(self, url: str, payload: dict[str, Any]) -> Any:
        return self._http.request("PATCH", url, headers=self._headers(), payload=payload)

    def create_issue(
        self, *, title: str, body: str, labels: list[str] | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        data = self._post(self._url("/issues"), payload)
        if not isinstance(data, dict):
            raise HttpError("创建 Issue 返回格式异常")
        LOGGER.info("已创建 Issue #%s", data.get("number"))
        return data

    def find_issue_by_title(self, title: str) -> dict[str, Any] | None:
        """先搜索再匹配精确标题；搜索不可用时回退到最近 issue 列表。"""
        query = quote(f'repo:{self._repo} in:title "{title}"')
        try:
            data = self._get(f"{self._base}/search/issues?q={query}&per_page=20")
            if isinstance(data, dict):
                for item in data.get("items") or []:
                    if isinstance(item, dict) and item.get("title") == title:
                        return item
                return None
        except HttpError as exc:  # 搜索 API 限流/不可用时回退
            LOGGER.warning("Issue 搜索失败，回退到列表匹配: %s", exc)
        try:
            data = self._get(self._url("/issues?state=all&per_page=100"))
        except HttpError:
            return None
        for item in data if isinstance(data, list) else []:
            if isinstance(item, dict) and item.get("title") == title and "pull_request" not in item:
                return item
        return None

    def ensure_issue(
        self, *, title: str, body: str, labels: list[str] | None = None
    ) -> tuple[dict[str, Any], bool]:
        """按标题幂等开 Issue：已存在则复用（返回 created=False），不重复建帖。"""
        if not self.available:
            raise HttpError("GitHub 未配置（缺 GITHUB_TOKEN / GITHUB_REPOSITORY）")
        existing = self.find_issue_by_title(title)
        if existing is not None:
            LOGGER.info("当日 Issue 已存在：#%s", existing.get("number"))
            return existing, False
        return self.create_issue(title=title, body=body, labels=labels), True

    def update_issue_body(self, number: int, body: str) -> dict[str, Any]:
        data = self._patch(self._url(f"/issues/{number}"), {"body": body})
        if not isinstance(data, dict):
            raise HttpError("更新 Issue 返回格式异常")
        return data

    def get_issue(self, number: int) -> dict[str, Any]:
        data = self._get(self._url(f"/issues/{number}"))
        if not isinstance(data, dict):
            raise HttpError(f"Issue #{number} 返回格式异常")
        return data

    def add_labels(self, number: int, labels: list[str]) -> list[dict[str, Any]]:
        data = self._post(self._url(f"/issues/{number}/labels"), {"labels": labels})
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    def list_comments(self, number: int) -> list[dict[str, Any]]:
        data = self._get(self._url(f"/issues/{number}/comments?per_page=100"))
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    def comment(self, number: int, body: str) -> dict[str, Any]:
        data = self._post(self._url(f"/issues/{number}/comments"), {"body": body})
        if not isinstance(data, dict):
            raise HttpError("创建评论返回格式异常")
        return data

    def issue_url(self, number: int) -> str:
        return f"https://github.com/{self._repo}/issues/{number}"

    def web_base(self) -> str:
        return f"https://github.com/{self._repo}"


def issue_title(date: str) -> str:
    return f"🍉 每日一瓜 {date}"
