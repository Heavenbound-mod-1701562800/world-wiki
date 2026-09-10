"""抓取指定 HTML → 按章节拆分 → 英文 Markdown；Other Languages 写入词典。"""

from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, NavigableString, Tag

import config
from libs.crawler import FandomWikiCrawler
from libs.page_store import Page
from libs.utils import clean_text
from models.dictionary import Dictionary

logger = logging.getLogger(__name__)

DEFAULT_WIKI_ORIGIN = "https://genshin-impact.fandom.com"
_CONTENT_ROOTS = (
    "#mw-content-text",
    ".mw-parser-output",
    "article",
    "main",
    "body",
)
_JUNK = (
    "script, style, nav, footer, .toc, .navbox, .mw-editsection, "
    "ol.references, .references, .custom-tabs, "
    ".wikia-gallery, .lightbox-caption, div.thumb, figure.thumb, ul.gallery"
)


def _element_text(node: Tag) -> str:
    """拼出标签文本：沿用原文空白，只有 br 换成换行；章内小标题写成 md 头。"""
    if node.name == "br":
        return "\n"
    if node.name in {"h3", "h4", "h5", "h6"} and not any(
        str(cls).startswith("pi-") for cls in (node.get("class") or [])
    ):
        headline = node.find(class_="mw-headline")
        title = " ".join((headline or node).get_text(" ", strip=True).split())
        return f"\n{'#' * int(node.name[1])} {title}\n" if title else ""
    if node.name == "dl":
        tags = [child for child in node.children if isinstance(child, Tag)]
        if len(tags) == 1 and tags[0].name == "dt":
            title = " ".join(tags[0].get_text(" ", strip=True).split())
            return f"\n#### {title}\n" if title else ""
    parts: list[str] = []
    for child in node.children:
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif isinstance(child, Tag):
            parts.append(_element_text(child))
    return "".join(parts)


@dataclass
class Citation:
    """一条 MediaWiki 脚注：注释文字 + 链接，不跟随抓取目标页。"""

    note_id: str
    label: str
    url: str = ""

    def marker(self) -> str:
        """内联〔reference〕文本。"""
        if self.url:
            return f"〔reference: {self.label} | {self.url}〕"
        return f"〔reference: {self.label}〕"

    @classmethod
    def bind(cls, root: Tag, base_url: str = "") -> list[Citation]:
        """把 sup.reference 换成内联〔reference〕标记，并返回文中每一次引用。"""
        parsed = urlparse(base_url)
        origin = (
            f"{parsed.scheme}://{parsed.netloc}"
            if parsed.scheme in {"http", "https"} and parsed.netloc
            else DEFAULT_WIKI_ORIGIN
        )
        notes: dict[str, Tag] = {}
        for li in root.select("ol.references li, .references li"):
            note_id = html.unescape(li.get("id") or "").lstrip("#")
            if note_id:
                notes[note_id] = li
        collected: list[Citation] = []
        for sup in list(root.select("sup.reference")):
            cite = cls._from_sup(sup, notes, origin)
            if cite is None:
                sup.decompose()
                continue
            collected.append(cite)
            sup.replace_with(NavigableString(cite.marker()))
        return collected

    @classmethod
    def _from_sup(
        cls,
        sup: Tag,
        notes: dict[str, Tag],
        origin: str,
    ) -> Citation | None:
        anchor = sup.find("a", href=True)
        note_id = html.unescape((anchor.get("href") if anchor else "") or "").lstrip("#")
        if not note_id:
            return None

        li = notes.get(note_id)
        text_node = li.select_one(".reference-text") if li else None
        href = ""
        if text_node is not None:
            label = " ".join(text_node.get_text(" ", strip=True).split())
            link = text_node.find("a", href=True)
            if link is not None:
                href = (link.get("href") or "").strip()
        else:
            label = " ".join(sup.get_text(" ", strip=True).split())

        if not label:
            label = note_id

        url = urljoin(origin + "/", href) if href else ""
        return cls(note_id=note_id, label=label, url=url)


@dataclass
class Chapter:
    """一条落盘单元：条目（页面/词条）+ 拆分标题。"""

    title: str
    content: str
    entry: str = ""
    source_url: str = ""
    citations: list[Citation] = field(default_factory=list)
    page_in: str = ""

    @property
    def slug(self) -> str:
        """用作 md 文件名的条目__标题。"""
        def safe(text: str) -> str:
            base = re.sub(r"[\\/:*?\"<>|]+", "-", text).strip()
            base = re.sub(r"\s+", "_", base)
            return re.sub(r"_+", "_", base).strip("._-")[:80] or "untitled"

        entry = safe(self.entry or "untitled")
        title = safe(self.title or "untitled")
        return entry if entry == title else f"{entry}__{title}"


@dataclass
class Result:
    """一章英文正文的落盘结果。"""

    chapter: Chapter
    output_path: Path


@dataclass
class Wiki:
    """章节级英文 Markdown 流水线。"""

    crawler: FandomWikiCrawler = field(default_factory=FandomWikiCrawler)
    output_dir: Path = field(default_factory=lambda: config.SUMMARIES_DIR)
    heading_tags: tuple[str, ...] = ("h2",)
    min_chapter_chars: int = 40

    def __post_init__(self) -> None:
        self.output_dir = Path(self.output_dir)

    def run(
        self,
        sources: str | Path | Iterable[str | Path],
        *,
        output_dir: Optional[Path] = None,
        max_chapters: Optional[int] = None,
    ) -> list[Result]:
        """加载来源（可多个）→ 按页拆章写成英文 Markdown → 落盘，并写入 SQLite 状态。"""
        if isinstance(sources, (str, Path)):
            source_list: list[str | Path] = [sources]
        else:
            source_list = list(sources)

        urls: list[str] = []
        local_paths: list[Path] = []
        for source in source_list:
            source_str = str(source)
            if urlparse(source_str).scheme in {"http", "https"}:
                urls.append(source_str.strip())
            else:
                local_paths.append(Path(source_str))

        for url in urls:
            Page.upsert(url, status="pending", error="")

        results: list[Result] = []
        for url in urls:
            results.extend(
                self._fetch(url, max_chapters=max_chapters, output_dir=output_dir)
            )
        for path in local_paths:
            if not path.exists():
                raise FileNotFoundError(f"找不到本地 HTML：{path}")
            results.extend(
                self.process_local(
                    path, output_dir=output_dir, max_chapters=max_chapters
                )
            )
        return results

    def _fetch(
        self,
        url: str,
        *,
        max_chapters: Optional[int],
        output_dir: Optional[Path],
    ) -> list[Result]:
        Page.upsert(url, status="fetching", error="")
        logger.info("下载 %s", url)
        html_text = self.crawler.fetch_html(url)
        if not html_text:
            Page.upsert(url, status="failed", error="下载失败")
            return []
        return self._write_page(
            html_text,
            source_url=url,
            raw_path=self._save_raw(url, html_text),
            max_chapters=max_chapters,
            output_dir=output_dir,
        )

    def process_local(
        self,
        path: str | Path,
        *,
        source_url: str = "",
        output_dir: Optional[Path] = None,
        max_chapters: Optional[int] = None,
    ) -> list[Result]:
        """用已下载 HTML/JSON 拆章写 md，不访问网络。"""
        path = Path(path)
        html_text, page_url = self.load_local_page(path)
        if source_url:
            page_url = source_url
        resolved = path.resolve()
        Page.upsert(page_url, status="fetching", error="", raw_path=str(resolved))
        return self._write_page(
            html_text,
            source_url=page_url,
            raw_path=resolved,
            max_chapters=max_chapters,
            output_dir=output_dir,
        )

    def _write_page(
        self,
        html_text: str,
        *,
        source_url: str,
        raw_path: Path,
        max_chapters: Optional[int],
        output_dir: Optional[Path],
    ) -> list[Result]:
        chapters = self._split_chapters(html_text, source_url=source_url)
        if max_chapters is not None:
            chapters = chapters[:max_chapters]
        title = chapters[0].entry if chapters else ""
        raw = str(raw_path)

        def mark(**kwargs) -> None:
            Page.upsert(source_url, title=title, raw_path=raw, **kwargs)

        if not chapters:
            mark(
                status="failed",
                error="未拆出有效章节",
                chapter_total=0,
                chapter_ok=0,
            )
            return []

        mark(
            status="summarizing",
            error="",
            chapter_total=len(chapters),
            chapter_ok=0,
        )
        out_dir = Path(output_dir or self.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        results: list[Result] = []
        for chapter in chapters:
            try:
                path = out_dir / f"{chapter.slug}.md"
                path.write_text(
                    self._render_markdown(chapter, chapter.content or ""),
                    encoding="utf-8",
                )
                results.append(Result(chapter=chapter, output_path=path))
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.error(
                    "写入失败，已跳过：%s / %s (%s)",
                    chapter.entry or "?",
                    chapter.title,
                    exc,
                )
                continue
            mark(status="summarizing", chapter_ok=len(results))
        failed = len(chapters) - len(results)
        if failed:
            logger.warning("本页写入：成功 %d 章，跳过 %d 章", len(results), failed)
        if not results:
            status, err = "failed", f"全部 {len(chapters)} 章写入失败"
        elif failed:
            status, err = "partial", f"跳过 {failed} 章"
        else:
            status, err = "done", ""
        mark(
            status=status,
            error=err,
            chapter_total=len(chapters),
            chapter_ok=len(results),
        )
        return results

    @staticmethod
    def load_local_page(path: Path) -> tuple[str, str]:
        """读取本地 HTML，或 MediaWiki API JSON（parse.text）。"""
        raw = path.read_text(encoding="utf-8")
        fallback_url = str(path.resolve())
        if path.suffix.lower() != ".json":
            return raw, fallback_url

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return raw, fallback_url

        parse = payload.get("parse") if isinstance(payload, dict) else None
        if not isinstance(parse, dict):
            return raw, fallback_url

        text = parse.get("text") or {}
        body = text.get("*") if isinstance(text, dict) else text
        if not isinstance(body, str) or not body.strip():
            raise ValueError(f"MediaWiki JSON 缺少 parse.text：{path}")

        title = parse.get("title") or path.stem
        title = re.sub(r"<[^>]+>", "", str(title)).strip() or path.stem
        page_url = f"{DEFAULT_WIKI_ORIGIN}/wiki/{title.replace(' ', '_')}"
        return FandomWikiCrawler.wrap_article_html(title, body), page_url

    @staticmethod
    def _save_raw(url: str, page_html: str) -> Path:
        parsed = urlparse(url)
        host = parsed.netloc.replace(":", "_") or "page"
        slug_src = parsed.path.strip("/") or "index"
        slug = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", slug_src, flags=re.UNICODE)
        slug = re.sub(r"_+", "_", slug).strip("_")[:80] or "index"
        config.RAW_DIR.mkdir(parents=True, exist_ok=True)
        dest = config.RAW_DIR / f"{host}_{slug}.html"
        dest.write_text(page_html, encoding="utf-8")
        logger.info("已保存 HTML：%s", dest)
        return dest

    def _split_chapters(
        self,
        page_html: str,
        *,
        source_url: str = "",
    ) -> list[Chapter]:
        soup = BeautifulSoup(page_html, "lxml")
        root: Tag = soup.body or soup
        for selector in _CONTENT_ROOTS:
            node = soup.select_one(selector)
            if node:
                root = node
                break
        catalog = Citation.bind(root, source_url)
        for junk in root.select(_JUNK):
            junk.decompose()

        h1 = soup.find("h1")
        entry = h1.get_text(" ", strip=True) if h1 else ""
        if not entry and soup.title and soup.title.string:
            entry = soup.title.string.strip()
        entry = entry or "untitled"
        page_in = _page_in(entry, root)

        headings = root.find_all(self.heading_tags)
        chapters: list[Chapter] = []
        heading_set = set(headings)
        if headings:
            preface = self._section_text(headings[0], after=False)
            if len(preface) >= self.min_chapter_chars:
                chapters.append(
                    self.create_chapter(
                        "Introduction",
                        preface,
                        entry=entry,
                        source_url=source_url,
                        catalog=catalog,
                        page_in=page_in,
                    )
                )
            for heading in headings:
                title = heading.get_text(" ", strip=True) or f"untitled{len(chapters)}"
                if title.strip().casefold() == "other languages":
                    Dictionary.add(
                        _pairs_from_other_languages(heading, self.heading_tags),
                        source=Dictionary.Source.WIKI,
                        strict=False,
                    )
                    continue
                content = self._section_text(
                    heading, after=True, heading_set=heading_set
                )
                if len(content) < self.min_chapter_chars:
                    continue
                chapters.append(
                    self.create_chapter(
                        title,
                        content,
                        entry=entry,
                        source_url=source_url,
                        catalog=catalog,
                        page_in=page_in,
                    )
                )

        if not chapters:
            full = self._normalize_text(_element_text(root))
            if full:
                chapters = [
                    self.create_chapter(
                        entry,
                        full,
                        entry=entry,
                        source_url=source_url,
                        catalog=catalog,
                        page_in=page_in,
                    )
                ]
        return chapters

    def create_chapter(
        self,
        title: str,
        content: str,
        *,
        entry: str,
        source_url: str,
        catalog: list[Citation],
        page_in: str,
    ) -> Chapter:
        return Chapter(
            title=title,
            content=content,
            entry=entry,
            source_url=source_url,
            citations=self._citations_in_text(content, catalog),
            page_in=page_in,
        )

    def _section_text(
        self,
        node: Tag,
        *,
        after: bool,
        heading_set: set | None = None,
    ) -> str:
        parts: list[str] = []
        siblings = node.next_siblings if after else node.previous_siblings
        for sib in siblings:
            if not isinstance(sib, Tag):
                continue
            if after and heading_set is not None:
                if sib in heading_set or sib.name in self.heading_tags:
                    break
                nested = sib.find(self.heading_tags)
                if nested and nested in heading_set:
                    break
            text = _element_text(sib)
            if text:
                parts.append(text)
        if not after:
            parts.reverse()
        return self._normalize_text("\n".join(parts))

    @staticmethod
    def _citations_in_text(
        content: str,
        catalog: list[Citation],
    ) -> list[Citation]:
        seen: set[str] = set()
        found: list[Citation] = []
        for cite in catalog:
            if cite.note_id in seen:
                continue
            if cite.marker() in content:
                seen.add(cite.note_id)
                found.append(cite)
        return found

    @staticmethod
    def _normalize_text(text: str) -> str:
        text = text.replace("\xa0", " ")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _render_markdown(chapter: Chapter, summary: str) -> str:
        entry = chapter.entry or "untitled"
        title = chapter.title or "untitled"
        heading = title if entry == title else f"{entry} · {title}"
        lines = [
            f"# {heading}",
            "",
            f"- Entry: {entry}",
            f"- Title: {title}",
            f"- Source: {chapter.source_url or 'unknown'}",
        ]
        if chapter.page_in:
            lines.append(f"- In: {chapter.page_in}")
        lines.extend(["", "## Content", "", summary.strip(), ""])
        if chapter.citations:
            lines.extend(["## Citations", ""])
            seen: set[str] = set()
            for cite in chapter.citations:
                if cite.note_id in seen:
                    continue
                seen.add(cite.note_id)
                if cite.url:
                    lines.append(f"- {cite.label} — {cite.url}")
                else:
                    lines.append(f"- {cite.label}")
            lines.append("")
        return "\n".join(lines)


def _page_in(entry: str, root: Tag) -> str:
    """Fandom 页眉那种 in:：子页父标题，否则 infobox type。"""
    if "/" in (entry or ""):
        parent = entry.rsplit("/", 1)[0].strip()
        if parent:
            return parent
    kind = ""
    for node in root.select('[data-source="type"]'):
        classes = " ".join(node.get("class") or [])
        if "label" in classes:
            continue
        text = " ".join(node.get_text(" ", strip=True).split())
        if text and text.casefold() not in {"quest type", "type"}:
            kind = text
            break
    if not kind:
        return ""
    if "quest" in kind.casefold():
        return kind
    return f"{kind} Quest"


def _pairs_from_other_languages(
    heading: Tag, heading_tags: tuple[str, ...]
) -> list[tuple[str, str]]:
    """当前节 wikitable 里 English / Chinese (Simplified) 的官方名。"""
    pairs: list[tuple[str, str]] = []
    en = ""
    for sib in heading.next_siblings:
        if isinstance(sib, Tag) and (
            sib.name in heading_tags or sib.find(heading_tags)
        ):
            break
        if not isinstance(sib, Tag):
            continue
        tables = [sib] if sib.name == "table" else sib.find_all("table")
        for table in tables:
            for tr in table.find_all("tr"):
                cells = tr.find_all(["th", "td"])
                if len(cells) < 2:
                    continue
                lang = cells[0].get_text(" ", strip=True).casefold()
                name = clean_text(
                    cells[1].get_text("\n", strip=True).split("\n", 1)[0]
                )
                if not name:
                    continue
                if lang.startswith("english"):
                    en = name
                elif en and "simplified" in lang:
                    pairs.append((en, name))
                    en = ""
    return pairs
