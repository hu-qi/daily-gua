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

# 业务标签：首次运行由 ``ensure_labels`` 幂等创建，不依赖维护者手工建标。
LABEL_SPECS: dict[str, tuple[str, str]] = {
    "gua": ("f0b429", "每日一瓜：当日瓜报"),
    "daily-gua": ("f0b429", "每日一瓜：每日自动生成的当日 Issue"),
    "gua-submit": ("0e8a16", "投瓜：用户通过 Issue 模板提交的瓜线索"),
    "gua-accepted": ("0e8a16", "投瓜：已核实并进入次日候选池"),
}
BUSINESS_LABELS: tuple[str, ...] = tuple(LABEL_SPECS)


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
        self._known_labels: set[str] | None = None
        self.last_warnings: list[str] = []

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

    def _warn(self, message: str) -> None:
        self.last_warnings.append(message)
        LOGGER.warning(message)

    def drain_warnings(self) -> list[str]:
        """取走本轮告警（标签创建失败、去标签重试等），调用方需如实上报。"""
        warnings = list(self.last_warnings)
        self.last_warnings.clear()
        return warnings

    def _fetch_label_names(self) -> set[str]:
        try:
            data = self._get(self._url("/labels?per_page=100"))
        except HttpError as exc:
            self._warn(f"读取仓库标签失败（{exc}），将逐个尝试创建")
            return set()
        names = {
            str(item.get("name") or "").strip().lower()
            for item in (data if isinstance(data, list) else [])
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        }
        return names

    @staticmethod
    def _label_payload(name: str) -> dict[str, Any]:
        color, description = LABEL_SPECS.get(name, ("ededed", ""))
        return {"name": name, "color": color, "description": description}

    def ensure_labels(self, names: list[str] | tuple[str, ...] | None) -> dict[str, str]:
        """幂等创建业务标签：已存在记 ``exists``，新建记 ``created``，失败记 ``failed``。"""
        wanted = [str(name).strip() for name in dict.fromkeys(names or []) if str(name).strip()]
        if not wanted:
            return {}
        if self._known_labels is None:
            self._known_labels = self._fetch_label_names()
        status: dict[str, str] = {}
        for name in wanted:
            if name.lower() in self._known_labels:
                status[name] = "exists"
                continue
            try:
                self._post(self._url("/labels"), self._label_payload(name))
            except HttpError as exc:
                if exc.status in (409, 422):  # 并发创建/已存在：按已存在处理
                    self._known_labels.add(name.lower())
                    status[name] = "exists"
                else:
                    self._warn(f"创建标签 {name} 失败（{exc}）")
                    status[name] = "failed"
                continue
            self._known_labels.add(name.lower())
            status[name] = "created"
        return status

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
        """按标题幂等开 Issue：已存在则复用（返回 created=False），不重复建帖。

        标签先幂等创建再挂；若 GitHub 仍以 422 拒绝（标签不存在/无权限），
        则去标签重试并在 ``last_warnings`` 留下告警 —— 绝不因标签阻断出报。
        """
        if not self.available:
            raise HttpError("GitHub 未配置（缺 GITHUB_TOKEN / GITHUB_REPOSITORY）")
        existing = self.find_issue_by_title(title)
        if existing is not None:
            LOGGER.info("当日 Issue 已存在：#%s", existing.get("number"))
            return existing, False

        wanted = [str(name).strip() for name in dict.fromkeys(labels or []) if str(name).strip()]
        status = self.ensure_labels(wanted) if wanted else {}
        usable = [name for name in wanted if status.get(name) != "failed"]
        missing = [name for name in wanted if name not in usable]
        if missing:
            self._warn(f"标签 {missing} 创建失败，本次 Issue 将不带这些标签")
        try:
            return self.create_issue(title=title, body=body, labels=usable or None), True
        except HttpError as exc:
            if exc.status != 422 or not usable:
                raise
            self._warn(f"创建 Issue 带标签被 GitHub 拒绝（422: {exc}），已去标签重试")
            return self.create_issue(title=title, body=body, labels=None), True

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
        """打标签：先幂等建标；被 422 拒绝时如实告警并返回空（不中断回执链路）。"""
        wanted = [str(name).strip() for name in dict.fromkeys(labels or []) if str(name).strip()]
        if not wanted:
            return []
        status = self.ensure_labels(wanted)
        usable = [name for name in wanted if status.get(name) != "failed"]
        if not usable:
            self._warn(f"标签 {wanted} 创建失败，Issue #{number} 未打标签")
            return []
        try:
            data = self._post(self._url(f"/issues/{number}/labels"), {"labels": usable})
        except HttpError as exc:
            if exc.status != 422:
                raise
            self._warn(f"Issue #{number} 打标签被拒绝（422: {exc}），已跳过标签")
            return []
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
