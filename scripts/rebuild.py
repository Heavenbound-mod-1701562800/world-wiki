"""用已下载的 Wiki 页面重建章节 md，并清空向量库后切块入库。

保留词典（Genshin Dictionary / 手动词条 / Wiki OL）。会删除：
  - data/summaries/*.md
  - pages 表（随后按 raw 重写）
  - Chroma 集合（ingest reset）

用法：
  python scripts/rebuild.py
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

import config
from libs.page_store import Page
from models import ingest
from models.wiki import DEFAULT_WIKI_ORIGIN, Wiki

logger = logging.getLogger("rebuild")

_RAW_SUFFIXES = {".html", ".htm", ".json"}


def wiki_url_for(path: Path, stored_url: str = "") -> str:
    """优先用 pages 表里的 http URL；否则从 JSON / h1 还原词条地址。"""
    stored = (stored_url or "").strip()
    if urlparse(stored).scheme in {"http", "https"}:
        return stored
    html, loaded = Wiki.load_local_page(path)
    if urlparse(loaded).scheme in {"http", "https"}:
        return loaded
    soup = BeautifulSoup(html, "lxml")
    h1 = soup.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else ""
    if title:
        return f"{DEFAULT_WIKI_ORIGIN}/wiki/{title.replace(' ', '_')}"
    return loaded


def collect_raw_jobs(raw_dir: Path | None = None) -> list[tuple[Path, str]]:
    """Page.raw_path 与 data/raw 里仍在的 html/json。"""
    by_path: dict[Path, str] = {}
    for page in Page.select():
        if not page.raw_path:
            continue
        path = Path(page.raw_path)
        if path.is_file():
            by_path[path.resolve()] = page.url or ""
    root = Path(raw_dir or config.RAW_DIR)
    if root.is_dir():
        for path in sorted(root.iterdir()):
            if path.suffix.lower() not in _RAW_SUFFIXES or not path.is_file():
                continue
            by_path.setdefault(path.resolve(), "")
    jobs: list[tuple[Path, str]] = []
    for path, stored in sorted(by_path.items(), key=lambda item: str(item[0]).casefold()):
        jobs.append((path, wiki_url_for(path, stored)))
    return jobs


def clear_rebuild_targets() -> int:
    """删掉旧 md，清空 pages 表。返回删除的 md 数量。"""
    deleted = 0
    summaries = config.SUMMARIES_DIR
    if summaries.is_dir():
        for md in summaries.glob("*.md"):
            md.unlink()
            deleted += 1
    Page.delete().execute()  # pylint: disable=no-value-for-parameter
    return deleted


def rebuild(*, raw_dir: Path | None = None) -> int:
    """拆章写 md 后 reset 入库。成功返回 0。"""
    jobs = collect_raw_jobs(raw_dir)
    if not jobs:
        logger.error("没有已下载页面。请确认 data/raw 或 pages.raw_path。")
        return 1

    logger.info("将按 %d 个本地文件重建（保留词典）", len(jobs))
    n_md = clear_rebuild_targets()
    logger.info("已删除 %d 个旧 md，并清空 pages 表", n_md)

    wiki = Wiki()
    total = 0
    for path, url in jobs:
        logger.info("拆章 %s → %s", path.name, url)
        results = wiki.process_local(path, source_url=url)
        total += len(results)
    logger.info("写成 %d 章 md", total)
    if total == 0:
        logger.error("未拆出有效章节，已中止入库。")
        return 1

    ingest(reset=True)
    return 0


def main() -> int:
    """CLI 入口。"""
    config.setup_logging()
    return rebuild()


if __name__ == "__main__":
    raise SystemExit(main())
