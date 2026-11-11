"""Pages 构建：把 ``gua/*.md`` 与 ``feed.xml`` 渲染进 ``_site``（供 actions/deploy-pages 发布）。"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..common.config import load_settings, site_url
from ..common.redaction import setup_logging
from ..publish.feed import FEED_FILE
from ..publish.pages import build_site
from .env import emit, load_dotenv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="构建 GitHub Pages 静态归档站")
    parser.add_argument("--gua-dir", default=None)
    parser.add_argument("--out-dir", default="_site")
    parser.add_argument("--site-title", default="🍉 每日一瓜")
    return parser


def run(argv: list[str] | None = None) -> int:
    load_dotenv()
    setup_logging()
    args = build_parser().parse_args(argv)
    settings = load_settings()
    gua_dir = args.gua_dir or settings.gua_dir
    written = build_site(
        gua_dir,
        args.out_dir,
        site_title=args.site_title,
        site_url=site_url(),
        feed_path=Path(FEED_FILE),
    )
    return emit(
        {
            "ok": True,
            "out_dir": args.out_dir,
            "files": [str(p) for p in written],
            "count": len(written),
            "site_url": site_url(),
        }
    )


def main() -> int:  # pragma: no cover
    return run()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
