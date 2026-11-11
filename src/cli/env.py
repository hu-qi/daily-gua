"""CLI 公共工具：本地 .env 读取、北京日期、GitHub 事件载荷读取、统一输出。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BEIJING = timezone(timedelta(hours=8))


def load_dotenv(path: str = ".env") -> None:
    """本地运行用：把 ``.env`` 注入环境变量（已存在的变量不覆盖）。

    Actions 里没有 .env 文件，Secret 由平台直接注入，因此这里是纯粹的本地便利。
    """
    file = Path(path)
    if not file.is_file():
        return
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def beijing_today(now: datetime | None = None) -> str:
    moment = now or datetime.now(tz=BEIJING)
    return moment.astimezone(BEIJING).strftime("%Y-%m-%d")


def valid_date(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"日期必须是 YYYY-MM-DD：{value}") from exc
    return value


def load_event(path: str | None = None) -> dict[str, Any]:
    """读取 GitHub Actions 事件载荷（``GITHUB_EVENT_PATH``）。"""
    target = path or os.environ.get("GITHUB_EVENT_PATH") or ""
    if not target:
        return {}
    file = Path(target)
    if not file.is_file():
        return {}
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def emit(payload: dict[str, Any], *, exit_code: int = 0) -> int:
    """结构化结果输出到 stdout（Actions 日志可见，便于排查）。"""
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return exit_code


def fail(message: str, *, exit_code: int = 1) -> int:
    print(f"[error] {message}", file=sys.stderr)
    return exit_code
