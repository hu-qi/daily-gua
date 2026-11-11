"""X 官方 API 采集器（需 ``X_BEARER_TOKEN``）。

只在凭据存在时启用；调用失败由 collect.service 统一兜底，不在此处吞异常。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from ..common.http import HttpClient, HttpError
from ..common.models import Metrics, Post, utc_now

X_FIELDS = "created_at,public_metrics,author_id,lang,entities"
USER_FIELDS = "username,name,public_metrics"


def _parse_tweet(raw: dict[str, Any], users: dict[str, dict[str, Any]]) -> Post | None:
    text = str(raw.get("text") or "").strip()
    tweet_id = str(raw.get("id") or "").strip()
    if not tweet_id or not text:
        return None
    author_id = str(raw.get("author_id") or "")
    user = users.get(author_id, {})
    username = str(user.get("username") or "unknown")
    followers = int((user.get("public_metrics") or {}).get("followers_count") or 0)
    public = raw.get("public_metrics") or {}
    created_raw = raw.get("created_at")
    created_at = utc_now()
    if created_raw:
        from datetime import datetime

        created_at = datetime.fromisoformat(str(created_raw).replace("Z", "+00:00"))
    return Post(
        id=tweet_id,
        url=f"https://x.com/{username}/status/{tweet_id}",
        author=username,
        author_name=str(user.get("name") or ""),
        author_followers=followers,
        text=text,
        created_at=created_at,
        metrics=Metrics(
            likes=int(public.get("like_count") or 0),
            replies=int(public.get("reply_count") or 0),
            reposts=int(public.get("retweet_count") or 0),
            quotes=int(public.get("quote_count") or 0),
            views=int(public.get("impression_count") or 0),
            bookmarks=int(public.get("bookmark_count") or 0),
        ),
        source="x_api",
        lang=str(raw.get("lang") or ""),
    )


class XApiCollector:
    """封装 ``tweets/search/recent`` 与用户时间线两条最常用路径。"""

    def __init__(self, http: HttpClient, bearer_token: str, *, base_url: str) -> None:
        self._http = http
        self._token = bearer_token
        self._base = base_url.rstrip("/")

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base}/{path}?{urlencode(params)}"
        data = self._http.get_json(url, self._headers)
        if not isinstance(data, dict):
            raise HttpError("X API 响应格式异常", url=url)
        return data

    def recent_search(self, query: str, *, max_results: int = 50) -> list[Post]:
        data = self._get(
            "tweets/search/recent",
            {
                "query": f"{query} -is:retweet",
                "max_results": max(10, min(100, max_results)),
                "tweet.fields": X_FIELDS,
                "expansions": "author_id",
                "user.fields": USER_FIELDS,
            },
        )
        users = {str(u.get("id")): u for u in ((data.get("includes") or {}).get("users") or [])}
        posts = [_parse_tweet(t, users) for t in (data.get("data") or [])]
        return [p for p in posts if p is not None]

    def user_timeline(self, handle: str, *, max_results: int = 20) -> list[Post]:
        profile = self._get("users/by/username/" + handle.lstrip("@"), {"user.fields": USER_FIELDS})
        user_id = str((profile.get("data") or {}).get("id") or "")
        if not user_id:
            return []
        data = self._get(
            f"users/{user_id}/tweets",
            {
                "max_results": max(5, min(100, max_results)),
                "tweet.fields": X_FIELDS,
                "exclude": "retweets,replies",
            },
        )
        users = {user_id: (profile.get("data") or {})}
        posts = [_parse_tweet(t, users) for t in (data.get("data") or [])]
        return [p for p in posts if p is not None]
