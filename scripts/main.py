"""唯一 CLI 入口：用 flag 切换模式。

用法：
  python scripts/main.py --summarize samples/mondstadt_demo.html
  python scripts/main.py --summarize URL1 URL2
  python scripts/main.py --summarize --url URL1 --url URL2
  python scripts/main.py --ingest
  python scripts/main.py --ingest --reset
  python scripts/main.py --ask "风神和蒙德的关系是什么？"
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from models import ask, ingest, summarize

logger = logging.getLogger("main")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="原神世界观工具（--summarize / --ingest / --ask）",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--summarize",
        action="store_true",
        help="爬取/读取页面并总结为 Markdown",
    )
    mode.add_argument(
        "--ingest",
        action="store_true",
        help="把 data/summaries 下的 md 写入向量库（按 content_hash 跳过未变更）",
    )
    mode.add_argument(
        "--ask",
        action="store_true",
        help="基于向量库回答问题",
    )

    parser.add_argument(
        "texts",
        nargs="*",
        help="--summarize: 本地 HTML 路径或 URL（可多个）；--ask: 问题文本",
    )

    # summarize
    parser.add_argument(
        "--url",
        action="append",
        default=[],
        help="从该 URL 下载页面后再总结（可重复）",
    )
    parser.add_argument(
        "--save-html",
        nargs="?",
        const="auto",
        default=None,
        metavar="PATH",
        help="兼容旧参数；下载的页面总会写入 data/raw",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default=None,
        help="Markdown 输出目录（默认 data/summaries）",
    )
    parser.add_argument(
        "--max-chapters",
        type=int,
        default=None,
        help="只处理前 N 个章节（调试用；每个来源各自截断）",
    )
    parser.add_argument(
        "--heading",
        default=config.WIKI_HEADING,
        help=f"拆章标题标签，逗号分隔，默认 {config.WIKI_HEADING}",
    )

    # ingest
    parser.add_argument(
        "--reset",
        action="store_true",
        default=config.INGEST_RESET,
        help="配合 --ingest：清空向量库后全量重建",
    )

    # ask
    parser.add_argument(
        "--top-k",
        type=int,
        default=config.ASK_TOP_K,
        help=f"检索条数，默认 {config.ASK_TOP_K}",
    )
    parser.add_argument(
        "--show-sources",
        action="store_true",
        default=config.ASK_SHOW_SOURCES,
        help="配合 --ask：打印检索来源",
    )
    return parser.parse_args()


def run_summarize(args: argparse.Namespace) -> int:
    sources = list(args.texts or []) + list(args.url or [])
    try:
        summarize(
            sources,
            output_dir=args.output_dir,
            max_chapters=args.max_chapters,
            save_html=args.save_html,
            heading=args.heading,
        )
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        logger.error("%s", exc)
        return 1
    return 0


def run_ingest(args: argparse.Namespace) -> int:
    if args.texts:
        logger.error("--ingest 不接受问题文本；若要提问请用 --ask。")
        return 1
    try:
        ingest(reset=args.reset)
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        logger.error("%s", exc)
        return 1
    return 0


def run_ask(args: argparse.Namespace) -> int:
    try:
        ask(
            " ".join(args.texts or []),
            top_k=args.top_k,
            show_sources=args.show_sources,
        )
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        logger.error("%s", exc)
        return 1
    return 0


def main() -> int:
    config.setup_logging()
    args = parse_args()
    if args.summarize:
        return run_summarize(args)
    if args.ingest:
        return run_ingest(args)
    if args.ask:
        return run_ask(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
