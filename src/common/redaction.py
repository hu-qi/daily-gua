"""日志脱敏：写入 stdout / 文件 / Issue 之前统一抹掉已知密钥与常见凭据模式。

规则：
1. 已知 Secrets 的**具体值**（从环境变量收集）一律替换为掩码；
2. 常见凭据形态（Bearer/sk-/ghp_/URL 查询参数）按正则掩码；
3. 只掩码，不打印，不做任何「先输出再处理」的路径。
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable, Mapping

MASK = "***"
SECRET_ENV_NAMES = (
    "LLM_API_KEY",
    "X_BEARER_TOKEN",
    "X_API_KEY",
    "X_API_SECRET",
    "X_ACCESS_TOKEN",
    "X_ACCESS_TOKEN_SECRET",
    "GITHUB_TOKEN",
    "GH_TOKEN",
)

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(authorization\s*[:=]\s*)(bearer\s+)?([A-Za-z0-9._\-]{8,})"), r"\1\2" + MASK),
    (re.compile(r"(?i)\bbearer\s+([A-Za-z0-9._\-]{8,})"), "Bearer " + MASK),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), "sk-" + MASK),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{10,}"), "gh*_" + MASK),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{10,}"), "github_pat_" + MASK),
    (re.compile(r"(?i)([?&](?:key|token|api_key|access_token|apikey)=)([^&\s\"']+)"), r"\1" + MASK),
    (re.compile(r"(?i)(x-api-key\s*[:=]\s*)([A-Za-z0-9._\-]{6,})"), r"\1" + MASK),
)


def collect_secret_values(env: Mapping[str, str] | None = None) -> set[str]:
    """从环境变量收集需要抹掉的密钥明文（长度 <6 的值不收集，避免误伤）。"""
    source = os.environ if env is None else env
    values: set[str] = set()
    for name in SECRET_ENV_NAMES:
        value = (source.get(name) or "").strip()
        if len(value) >= 6:
            values.add(value)
    return values


class Redactor:
    """把一组已知密钥值与常见凭据模式替换为掩码。"""

    def __init__(self, secrets: Iterable[str] | None = None) -> None:
        self._secrets: set[str] = {s for s in (secrets or ()) if len(s) >= 6}

    def register(self, *values: str) -> Redactor:
        for value in values:
            if value and len(value) >= 6:
                self._secrets.add(value)
        return self

    def redact(self, text: object) -> str:
        result = str(text)
        # 先抹具体值（长的先替换，避免短值破坏长值）
        for secret in sorted(self._secrets, key=len, reverse=True):
            result = result.replace(secret, MASK)
        for pattern, replacement in _PATTERNS:
            result = pattern.sub(replacement, result)
        return result


def default_redactor(env: Mapping[str, str] | None = None) -> Redactor:
    return Redactor(collect_secret_values(env))


class RedactingFilter(logging.Filter):
    """日志 Filter：在格式化前对消息与参数做脱敏。"""

    def __init__(self, redactor: Redactor | None = None) -> None:
        super().__init__()
        self._redactor = redactor or default_redactor()

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - logging API
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - 极端格式化错误不阻断日志
            message = str(record.msg)
        record.msg = self._redactor.redact(message)
        record.args = ()
        return True


def get_logger(name: str, *, level: int | None = None) -> logging.Logger:
    """带脱敏 Filter 的 logger（同一 logger 不重复挂 Filter）。"""
    logger = logging.getLogger(name)
    if level is not None:
        logger.setLevel(level)
    if not any(isinstance(f, RedactingFilter) for f in logger.filters):
        logger.addFilter(RedactingFilter())
    return logger


def setup_logging(level: int = logging.INFO) -> None:
    """配置根 logger 的 handler（脱敏 Filter 挂在 handler 上，覆盖第三方库日志）。"""
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
