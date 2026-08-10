"""抓取指定 HTML → 按章节拆分 → LLM 精简世界观 → 保存 Markdown。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup, NavigableString, Tag

import config
from libs.crawler import FandomWikiCrawler
from libs.llm import LLM
from libs.task_queue import llm_queue

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = """你是原神世界观资料整理助手。
请从给定章节原文中提取并精简「世界观相关」信息，忽略玩法数值、养成攻略、活动公告、抽卡信息等。
重点保留：人物/组织关系、地理与势力、历史事件与时间线、设定术语、重要道具/概念的含义与影响。
输出使用简洁中文 Markdown：
- 先给一句话总览
- 再用条目列出关键设定点
- 不确定或原文未明说的内容不要脑补
- 不要复述与世界观无关的攻略废话"""


@dataclass
class Chapter:
    """一条总结单元：条目（页面/词条）+ 拆分标题。"""

    title: str
    content: str
    entry: str = ""
    level: int = 2
    order: int = 0
    source_url: str = ""

    @staticmethod
    def _safe_name(text: str, *, max_len: int = 80) -> str:
        base = re.sub(r"[\\/:*?\"<>|]+", "-", text).strip()
        base = re.sub(r"\s+", "_", base)
        base = re.sub(r"_+", "_", base).strip("._-")
        return (base[:max_len] or "untitled")

    @property
    def slug(self) -> str:
        entry = self._safe_name(self.entry or "未命名条目")
        title = self._safe_name(self.title or "未命名标题")
        if entry == title:
            return entry
        return f"{entry}__{title}"


@dataclass
class Result:
    chapter: Chapter
    summary: str
    output_path: Path


@dataclass
class Wiki:
    """章节级世界观提炼流水线。"""

    crawler: FandomWikiCrawler = field(default_factory=FandomWikiCrawler)
    llm: Optional[LLM] = None
    output_dir: Path = field(default_factory=lambda: config.SUMMARIES_DIR)
    heading_tags: tuple[str, ...] = ("h2",)
    content_selectors: tuple[str, ...] = (
        "#mw-content-text",
        ".mw-parser-output",
        "article",
        "main",
        "body",
    )
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    min_chapter_chars: int = 40

    def __post_init__(self) -> None:
        self.output_dir = Path(self.output_dir)

    def run(
        self,
        sources: str | Path | Iterable[str | Path],
        *,
        output_dir: Optional[Path] = None,
        max_chapters: Optional[int] = None,
        save_html: str | Path | None = None,
    ) -> list[Result]:
        """加载来源（可多个）→ 拆章 → 并行总结 → 落盘。"""
        if isinstance(sources, (str, Path)):
            source_list: list[str | Path] = [sources]
        else:
            source_list = list(sources)

        urls: list[str] = []
        local_paths: list[Path] = []
        for source in source_list:
            source_str = str(source)
            if urlparse(source_str).scheme in {"http", "https"}:
                urls.append(source_str)
            else:
                local_paths.append(Path(source_str))

        chapters: list[Chapter] = []

        if urls:
            logger.info("并行下载 %d 个页面…", len(urls))
            html_by_url = self.crawler.fetch_html_many(urls)
            if save_html is not None:
                self._save_raw_html(html_by_url, save_html)
            for url in urls:
                html = html_by_url.get(url)
                if not html:
                    continue
                page = self._split_chapters(html, source_url=url)
                if max_chapters is not None:
                    page = page[:max_chapters]
                chapters.extend(page)

        for path in local_paths:
            if not path.exists():
                raise FileNotFoundError(f"找不到本地 HTML：{path}")
            html = path.read_text(encoding="utf-8")
            page = self._split_chapters(html, source_url=str(path.resolve()))
            if max_chapters is not None:
                page = page[:max_chapters]
            chapters.extend(page)

        return self._summarize_and_save(chapters, output_dir=output_dir)

    def _get_llm(self) -> LLM:
        if self.llm is None:
            self.llm = LLM()
        return self.llm

    def _save_raw_html(
        self,
        html_by_url: dict[str, str],
        save_html: str | Path,
    ) -> None:
        urls = list(html_by_url)
        for url, html in html_by_url.items():
            if save_html == "auto" or len(urls) > 1:
                path = self._default_raw_path(url)
            else:
                path = Path(save_html)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(html, encoding="utf-8")
            logger.info("已保存 HTML：%s", path)

    @staticmethod
    def _default_raw_path(url: str) -> Path:
        parsed = urlparse(url)
        host = parsed.netloc.replace(":", "_") or "page"
        path = parsed.path.strip("/") or "index"
        slug = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", path, flags=re.UNICODE)
        slug = re.sub(r"_+", "_", slug).strip("_")[:80] or "index"
        raw_dir = config.DATA_DIR / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        return raw_dir / f"{host}_{slug}.html"

    def _summarize_and_save(
        self,
        chapters: list[Chapter],
        *,
        output_dir: Optional[Path] = None,
    ) -> list[Result]:
        if not chapters:
            return []

        out_dir = Path(output_dir or self.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        queue = llm_queue()
        futures = [queue.submit(self._summarize_chapter, ch) for ch in chapters]
        summaries = queue.gather(futures)

        results: list[Result] = []
        for chapter, summary in zip(chapters, summaries):
            path = out_dir / f"{chapter.slug}.md"
            path.write_text(self._render_markdown(chapter, summary), encoding="utf-8")
            results.append(
                Result(chapter=chapter, summary=summary, output_path=path)
            )
        return results

    def _summarize_chapter(self, chapter: Chapter) -> str:
        user_prompt = (
            f"来源：{chapter.source_url or '未知'}\n"
            f"条目：{chapter.entry or '未知'}\n"
            f"标题：{chapter.title}\n\n"
            f"原文：\n{chapter.content}"
        )
        return self._get_llm().chat(
            [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
        ).strip()

    def _split_chapters(
        self,
        html: str,
        *,
        source_url: str = "",
        page_title: Optional[str] = None,
    ) -> list[Chapter]:
        soup = BeautifulSoup(html, "lxml")
        root = self._pick_content_root(soup)

        for junk in root.select(
            "script, style, nav, footer, .toc, .navbox, .mw-editsection, "
            ".reference, .references, sup.reference"
        ):
            junk.decompose()

        entry = page_title or self._extract_page_title(soup) or "未命名条目"
        chapters = self._split_by_heading_traversal(root, entry, source_url)

        if not chapters:
            full = self._normalize_text(root.get_text("\n", strip=True))
            if full:
                chapters = [
                    Chapter(
                        title=entry,
                        content=full,
                        entry=entry,
                        level=1,
                        order=0,
                        source_url=source_url,
                    )
                ]
        return chapters

    def _pick_content_root(self, soup: BeautifulSoup) -> Tag:
        for selector in self.content_selectors:
            node = soup.select_one(selector)
            if node:
                return node
        return soup.body or soup

    def _extract_page_title(self, soup: BeautifulSoup) -> str:
        h1 = soup.find("h1")
        if h1:
            text = h1.get_text(" ", strip=True)
            if text:
                return text
        if soup.title and soup.title.string:
            return soup.title.string.strip()
        return ""

    def _split_by_heading_traversal(
        self,
        root: Tag,
        page_title: str,
        source_url: str,
    ) -> list[Chapter]:
        headings = root.find_all(self.heading_tags)
        if not headings:
            return []

        chapters: list[Chapter] = []
        order = 0
        heading_set = set(headings)

        first = headings[0]
        preface_parts: list[str] = []
        for sib in first.previous_siblings:
            if isinstance(sib, NavigableString):
                text = str(sib).strip()
                if text:
                    preface_parts.append(text)
            elif isinstance(sib, Tag):
                text = sib.get_text("\n", strip=True)
                if text:
                    preface_parts.append(text)
        preface = self._normalize_text("\n".join(reversed(preface_parts)))
        if len(preface) >= self.min_chapter_chars:
            chapters.append(
                Chapter(
                    title="前言",
                    content=preface,
                    entry=page_title,
                    level=1,
                    order=order,
                    source_url=source_url,
                )
            )
            order += 1

        for heading in headings:
            title = heading.get_text(" ", strip=True) or f"未命名标题{order}"
            level = (
                int(heading.name[1])
                if heading.name and heading.name.startswith("h")
                else 2
            )
            parts: list[str] = []
            for sib in heading.next_siblings:
                if isinstance(sib, Tag) and sib in heading_set:
                    break
                if isinstance(sib, Tag) and sib.name in self.heading_tags:
                    break
                if isinstance(sib, NavigableString):
                    text = str(sib).strip()
                    if text:
                        parts.append(text)
                elif isinstance(sib, Tag):
                    nested = sib.find(self.heading_tags)
                    if nested and nested in heading_set:
                        break
                    text = sib.get_text("\n", strip=True)
                    if text:
                        parts.append(text)

            content = self._normalize_text("\n".join(parts))
            if len(content) < self.min_chapter_chars:
                continue
            chapters.append(
                Chapter(
                    title=title,
                    content=content,
                    entry=page_title,
                    level=level,
                    order=order,
                    source_url=source_url,
                )
            )
            order += 1

        return chapters

    @staticmethod
    def _normalize_text(text: str) -> str:
        text = text.replace("\xa0", " ")
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        return text.strip()

    @staticmethod
    def _render_markdown(chapter: Chapter, summary: str) -> str:
        entry = chapter.entry or "未命名条目"
        title = chapter.title or "未命名标题"
        heading = title if entry == title else f"{entry} · {title}"
        return "\n".join(
            [
                f"# {heading}",
                "",
                f"- 条目: {entry}",
                f"- 标题: {title}",
                f"- 来源: {chapter.source_url or '未知'}",
                "",
                "## 世界观精简",
                "",
                summary.strip(),
                "",
            ]
        )
