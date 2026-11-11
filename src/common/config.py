"""配置与 Secrets 读取。

所有凭据只从环境变量（GitHub Actions Secrets）读取，不写盘、不打印、不落日志。
本地运行时通过 ``.env``（不入库，见 ``.env.example``）注入同名变量。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

DEFAULT_LLM_MODEL = "antigravity/gemini-3.8-flash"
DEFAULT_X_API_BASE = "https://api.x.com/2"
DEFAULT_MIRROR_BASE = "https://api.fxtwitter.com"
DEFAULT_SEED_URLS = ("https://x.com/criscxuan/status/2120336410989433287",)
DEFAULT_RSS_FEEDS = (
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "https://hnrss.org/newest?q=AI&count=30",
)


def _csv(value: str | None) -> tuple[str, ...]:
    """逗号 / 换行分隔的列表转元组，去空白去空项。"""
    if not value:
        return ()
    raw = value.replace("\n", ",")
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _flag(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


@dataclass(frozen=True)
class Settings:
    """运行时配置快照（不可变，便于测试与传递）。"""

    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str = DEFAULT_LLM_MODEL
    x_bearer_token: str | None = None
    x_api_base: str = DEFAULT_X_API_BASE
    mirror_base: str = DEFAULT_MIRROR_BASE
    mirror_enabled: bool = True
    github_token: str | None = None
    github_repository: str | None = None
    github_api_base: str = "https://api.github.com"
    gua_dir: str = "gua"
    state_dir: str = "state"
    http_timeout_s: float = 15.0
    http_retries: int = 3
    llm_timeout_s: float = 90.0
    llm_max_tokens: int = 4096
    llm_temperature: float = 0.4
    token_budget: int = 24000
    seed_urls: tuple[str, ...] = field(default_factory=tuple)
    x_keywords: tuple[str, ...] = field(default_factory=tuple)
    x_handles: tuple[str, ...] = field(default_factory=tuple)
    rss_feeds: tuple[str, ...] = field(default_factory=tuple)

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_base_url and self.llm_api_key)

    @property
    def x_api_configured(self) -> bool:
        return bool(self.x_bearer_token)

    @property
    def github_configured(self) -> bool:
        return bool(self.github_token and self.github_repository)


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """从环境变量构造 Settings。缺失的凭据留空，由调用方决定降级策略。"""
    source = os.environ if env is None else env
    seeds = _csv(source.get("SEED_URLS")) or DEFAULT_SEED_URLS
    feeds = _csv(source.get("RSS_FEEDS")) or DEFAULT_RSS_FEEDS
    return Settings(
        llm_base_url=(source.get("LLM_BASE_URL") or "").strip() or None,
        llm_api_key=(source.get("LLM_API_KEY") or "").strip() or None,
        llm_model=(source.get("LLM_MODEL") or "").strip() or DEFAULT_LLM_MODEL,
        x_bearer_token=(source.get("X_BEARER_TOKEN") or "").strip() or None,
        x_api_base=(source.get("X_API_BASE") or "").strip() or DEFAULT_X_API_BASE,
        mirror_base=(source.get("X_MIRROR_BASE") or "").strip() or DEFAULT_MIRROR_BASE,
        mirror_enabled=_flag(source.get("X_MIRROR_ENABLED"), True),
        github_token=(source.get("GITHUB_TOKEN") or "").strip() or None,
        github_repository=(source.get("GITHUB_REPOSITORY") or "").strip() or None,
        github_api_base=(source.get("GITHUB_API_URL") or "").strip() or "https://api.github.com",
        gua_dir=(source.get("GUA_DIR") or "").strip() or "gua",
        state_dir=(source.get("GUA_STATE_DIR") or "").strip() or "state",
        http_timeout_s=float(source.get("HTTP_TIMEOUT_S") or 15.0),
        http_retries=int(source.get("HTTP_RETRIES") or 3),
        llm_timeout_s=float(source.get("LLM_TIMEOUT_S") or 90.0),
        llm_max_tokens=int(source.get("LLM_MAX_TOKENS") or 4096),
        llm_temperature=float(source.get("LLM_TEMPERATURE") or 0.4),
        token_budget=int(source.get("LLM_TOKEN_BUDGET") or 24000),
        seed_urls=seeds,
        x_keywords=_csv(source.get("X_KEYWORDS")),
        x_handles=_csv(source.get("X_HANDLES")),
        rss_feeds=feeds,
    )


def site_url(env: Mapping[str, str] | None = None) -> str:
    """站点根地址：优先显式配置，其次由 GITHUB_REPOSITORY 推导 Pages 地址。"""
    source = os.environ if env is None else env
    explicit = (source.get("SITE_URL") or "").strip()
    if explicit:
        return explicit.rstrip("/")
    repo = (source.get("GITHUB_REPOSITORY") or "").strip()
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner}.github.io/{name}"
    return "https://example.github.io/daily-gua"
