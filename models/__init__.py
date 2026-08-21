"""业务层：基于 libs 组装的世界观处理流水线。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import config
from models.dictionary import Dictionary
from models.ingest import Ingest, IngestReport
from models.qa import Answer, QA
from models.wiki import Chapter, Citation, Result, Wiki

logger = logging.getLogger(__name__)

__all__ = [
    "Answer",
    "Chapter",
    "Citation",
    "Dictionary",
    "Ingest",
    "IngestReport",
    "QA",
    "Result",
    "Wiki",
    "ask",
    "ask_stream",
    "ingest",
    "summarize",
]


def summarize(
    sources: str | Path | Iterable[str | Path],
    *,
    output_dir: str | Path | None = None,
    max_chapters: int | None = None,
    save_html: str | Path | None = None,  # 兼容旧调用；下载页总会写入 data/raw
    heading: str | None = None,
) -> list[Result]:
    """爬取/读取页面并总结为 Markdown。"""
    _ = save_html
    if isinstance(sources, (str, Path)):
        source_list = [sources]
    else:
        source_list = list(sources)
    if not source_list:
        raise ValueError("请提供本地 HTML 路径或 URL（可多个）。")

    for source in source_list:
        source_str = str(source)
        if urlparse(source_str).scheme in {"http", "https"}:
            continue
        if not Path(source_str).exists():
            raise FileNotFoundError(f"找不到本地 HTML：{source_str}")

    heading = heading if heading is not None else config.WIKI_HEADING
    headings = tuple(tag.strip() for tag in heading.split(",") if tag.strip())
    fallback = tuple(
        tag.strip() for tag in config.WIKI_HEADING.split(",") if tag.strip()
    ) or ("h2",)
    results = Wiki(heading_tags=headings or fallback).run(
        source_list,
        output_dir=Path(output_dir) if output_dir else None,
        max_chapters=max_chapters,
    )
    if not results:
        raise RuntimeError("未拆出有效章节，请检查页面结构或调整 heading。")

    logger.info("完成 %d 条：", len(results))
    for item in results:
        ch = item.chapter
        label = (
            ch.title
            if not ch.entry or ch.entry == ch.title
            else f"{ch.entry} / {ch.title}"
        )
        logger.info("  - %s -> %s", label, item.output_path)
    return results


def ingest(
    *,
    reset: bool | None = None,
    summaries_dir: str | Path | None = None,
) -> IngestReport:
    """把 summaries 下的 md 写入向量库。"""
    if reset is None:
        reset = config.INGEST_RESET
    report = Ingest().run(summaries_dir=summaries_dir, reset=reset)
    action = "重建入库" if reset else "增量入库"
    logger.info(
        "%s：写入 %d，跳过 %d，扫描 %d 个 md，库内共 %d 条",
        action,
        report.upserted,
        report.skipped,
        report.total_files,
        report.store_count,
    )
    return report


def ask(
    question: str,
    *,
    top_k: int | None = None,
    show_sources: bool | None = None,
) -> Answer:
    """基于向量库回答问题（非流式）。"""
    q = (question or "").strip()
    if not q:
        raise ValueError('请提供问题，例如：python scripts/main.py --ask "风神是谁？"')

    if top_k is None:
        top_k = config.ASK_TOP_K
    if show_sources is None:
        show_sources = config.ASK_SHOW_SOURCES

    qa = QA(top_k=top_k)
    if qa.store.count() == 0:
        raise RuntimeError("记忆里啥也没有")

    result = qa.ask(q, top_k=top_k)
    logger.info("%s", result.answer)
    _log_sources(result, show_sources=show_sources)
    return result


def ask_stream(
    question: str,
    *,
    top_k: int | None = None,
    show_sources: bool | None = None,
):
    """
    流式回答：yield SSE 友好事件 dict。

      {"type": "delta", "text": "..."}
      {"type": "done", "question": "...", "answer": "...", "sources": [...]?}
    """
    q = (question or "").strip()
    if not q:
        raise ValueError('请提供问题，例如：python scripts/main.py --ask "风神是谁？"')

    if top_k is None:
        top_k = config.ASK_TOP_K
    if show_sources is None:
        show_sources = config.ASK_SHOW_SOURCES

    qa = QA(top_k=top_k)
    if qa.store.count() == 0:
        raise RuntimeError("记忆里啥也没有")

    gen = qa.ask_stream(q, top_k=top_k)
    try:
        while True:
            delta = next(gen)
            yield {"type": "delta", "text": delta}
    except StopIteration as stop:
        result = stop.value
        if not isinstance(result, Answer):
            raise RuntimeError("流式回答未返回完整结果") from stop
        logger.info("%s", result.answer)
        _log_sources(result, show_sources=show_sources)
        done: dict = {
            "type": "done",
            "question": result.question,
            "answer": result.answer,
        }
        if show_sources:
            done["sources"] = [
                {
                    "id": chunk.id,
                    "document": chunk.document,
                    "metadata": chunk.metadata,
                    "distance": chunk.distance,
                }
                for chunk in result.sources
            ]
        yield done


def _log_sources(result: Answer, *, show_sources: bool) -> None:
    if not (show_sources and result.sources):
        return
    logger.info("来源：")
    for i, chunk in enumerate(result.sources, 1):
        label = (
            chunk.metadata.get("label")
            or chunk.metadata.get("title")
            or chunk.metadata.get("filename")
        )
        dist = f"{chunk.distance:.4f}" if chunk.distance is not None else "?"
        logger.info("  [%d] %s (distance=%s)", i, label, dist)
