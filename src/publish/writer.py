"""瓜报落盘：``gua/YYYY-MM-DD.md`` 原子写入 + 回读索引。"""

from __future__ import annotations

import re
from pathlib import Path

from ..common.frontmatter import ReportDigest, digest_from_text

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FILE_NAME = "{date}.md"


def report_path(gua_dir: str | Path, date: str) -> Path:
    if not DATE_RE.match(date):
        raise ValueError(f"日期格式必须是 YYYY-MM-DD，收到: {date}")
    return Path(gua_dir) / FILE_NAME.format(date=date)


def write_report(gua_dir: str | Path, date: str, markdown: str) -> Path:
    """原子写入（先写临时文件再 rename），避免 Actions 中断留下半截文件。"""
    target = report_path(gua_dir, date)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = markdown if markdown.endswith("\n") else markdown + "\n"
    tmp = target.with_suffix(".md.tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(target)
    return target


def read_report(gua_dir: str | Path, date: str) -> str | None:
    path = report_path(gua_dir, date)
    return path.read_text(encoding="utf-8") if path.is_file() else None


def list_reports(gua_dir: str | Path, *, limit: int | None = None) -> list[ReportDigest]:
    """按日期倒序列出全部瓜报（文件名即日期，非法命名跳过）。"""
    folder = Path(gua_dir)
    if not folder.is_dir():
        return []
    digests: list[ReportDigest] = []
    for file in sorted(folder.glob("*.md"), reverse=True):
        if not DATE_RE.match(file.stem):
            continue
        try:
            text = file.read_text(encoding="utf-8")
        except OSError:
            continue
        digest = digest_from_text(text, path=str(file))
        if not digest.date:
            digest = ReportDigest(date=file.stem, path=str(file))
        digests.append(digest)
        if limit is not None and len(digests) >= limit:
            break
    return digests


def report_web_url(gua_dir_date: str, site_url: str) -> str:
    return f"{site_url.rstrip('/')}/gua/{gua_dir_date}.md"


def count_reports(gua_dir: str | Path) -> int:
    return len(list_reports(gua_dir))
