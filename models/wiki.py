"""抓取指定 HTML → 按章节拆分 → LLM 精简世界观 → 保存 Markdown。"""

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
from libs.llm import LLM
from libs.page_store import Page
from libs.task_queue import llm_queue
from models.dictionary import Dictionary

logger = logging.getLogger(__name__)

DEFAULT_WIKI_ORIGIN = "https://genshin-impact.fandom.com"
_JSON_FENCE_RE = re.compile(
    r"^```(?:json)?\s*(.*?)\s*```$",
    re.IGNORECASE | re.DOTALL,
)

DEFAULT_SYSTEM_PROMPT = """你是原神世界观资料整理助手。
请从给定章节原文中提取并精简「世界观相关」信息，忽略玩法数值、养成攻略、活动公告、抽卡信息等。
重点保留：人物/组织关系、地理与势力、历史事件与时间线、设定术语、重要道具/概念的含义与影响。
只输出一个 JSON 对象，不要 markdown 围栏，不要其它说明。字段：
- summary：简洁中文 Markdown 字符串（先一句话总览，再条目列出关键设定点）
- untranslated：字符串数组，原文里出现、专名对照未覆盖、因此必须保持英文的专有名词
规则：
- 不确定或原文未明说的内容不要脑补
- 不要复述与世界观无关的攻略废话
- 专有名词禁止自行音译或另起中文名
- 「专名对照」里的命中项，summary 必须用对照表中文
- 对照表未列出的英文专名在 summary 里原样保留英文，并列入 untranslated
- untranslated 只收专有名词，必须在原文中出现过；不要编造，不要塞普通英文词，不要列入已在对照表中的词
- 不要写成「中文（英文）」并列
- 可译的是普通叙述和类别说法（因果、职位描述），不是对照表以外的专有名词
- 原文中的〔出处：…〕必须留在对应设定点后；出处里的专名规则同上；URL 可省略
- 若出处是 NPC 对话 / 角色口述，须写明「据某某对话所述」，不要写成官方设定陈述"""


def _parse_summary_json(raw: str) -> tuple[str, list[str]]:
    text = (raw or "").strip()
    fenced = _JSON_FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("总结不是 JSON 对象")
    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("JSON 缺少 summary")
    untranslated = data.get("untranslated") or []
    if not isinstance(untranslated, list):
        raise ValueError("untranslated 不是 list")
    names = [item for item in untranslated if isinstance(item, str)]
    return summary.strip(), names


@dataclass
class Citation:
    """一条 MediaWiki 脚注：注释文字 + 链接，不跟随抓取目标页。"""

    note_id: str
    label: str
    url: str = ""
    ref_id: str = ""

    def marker(self) -> str:
        """内联〔出处〕文本。"""
        if self.url:
            return f"〔出处：{self.label} | {self.url}〕"
        return f"〔出处：{self.label}〕"

    @classmethod
    def bind(cls, root: Tag, base_url: str = "") -> list[Citation]:
        """把 sup.reference 换成内联〔出处〕标记，并返回文中每一次引用。"""
        origin = cls._origin(base_url)
        notes = cls._index_notes(root)
        collected: list[Citation] = []
        for sup in list(root.select("sup.reference")):
            cite = cls._from_sup(sup, notes, origin)
            if cite is None:
                sup.decompose()
                continue
            collected.append(cite)
            sup.replace_with(NavigableString(cite.marker()))
        return collected

    @staticmethod
    def _origin(base_url: str) -> str:
        parsed = urlparse(base_url)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
        return DEFAULT_WIKI_ORIGIN

    @staticmethod
    def _norm_id(value: str) -> str:
        return html.unescape(value or "").lstrip("#")

    @classmethod
    def _index_notes(cls, root: Tag) -> dict[str, Tag]:
        notes: dict[str, Tag] = {}
        for li in root.select("ol.references li, .references li"):
            note_id = cls._norm_id(li.get("id") or "")
            if note_id:
                notes[note_id] = li
        return notes

    @classmethod
    def _from_sup(
        cls,
        sup: Tag,
        notes: dict[str, Tag],
        origin: str,
    ) -> Citation | None:
        ref_id = cls._norm_id(sup.get("id") or "")
        anchor = sup.find("a", href=True)
        note_id = cls._norm_id((anchor.get("href") if anchor else "") or "")
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
        return cls(
            note_id=note_id,
            label=label,
            url=url,
            ref_id=ref_id,
        )


@dataclass
class Chapter:
    """一条总结单元：条目（页面/词条）+ 拆分标题。"""

    title: str
    content: str
    entry: str = ""
    level: int = 2
    order: int = 0
    source_url: str = ""
    citations: list[Citation] = field(default_factory=list)

    @staticmethod
    def _safe_name(text: str, *, max_len: int = 80) -> str:
        base = re.sub(r"[\\/:*?\"<>|]+", "-", text).strip()
        base = re.sub(r"\s+", "_", base)
        base = re.sub(r"_+", "_", base).strip("._-")
        return (base[:max_len] or "untitled")

    @property
    def slug(self) -> str:
        """用作 md 文件名的条目__标题。"""
        entry = self._safe_name(self.entry or "未命名条目")
        title = self._safe_name(self.title or "未命名标题")
        if entry == title:
            return entry
        return f"{entry}__{title}"


@dataclass
class Result:
    """一章总结的落盘结果。"""

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
    ) -> list[Result]:
        """加载来源（可多个）→ 按页拆章总结 → 落盘，并写入 SQLite 状态。"""
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
                self._process_remote(url, max_chapters=max_chapters, output_dir=output_dir)
            )

        for path in local_paths:
            if not path.exists():
                raise FileNotFoundError(f"找不到本地 HTML：{path}")
            results.extend(
                self._process_local(path, max_chapters=max_chapters, output_dir=output_dir)
            )

        return results

    def _process_remote(
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
        raw_path = self._save_one_raw(url, html_text)
        return self._split_and_summarize(
            html_text,
            source_url=url,
            raw_path=raw_path,
            max_chapters=max_chapters,
            output_dir=output_dir,
        )

    def _process_local(
        self,
        path: Path,
        *,
        max_chapters: Optional[int],
        output_dir: Optional[Path],
    ) -> list[Result]:
        html_text, page_url = self._load_local_page(path)
        Page.upsert(page_url, status="fetching", error="", raw_path=str(path.resolve()))
        return self._split_and_summarize(
            html_text,
            source_url=page_url,
            raw_path=path.resolve(),
            max_chapters=max_chapters,
            output_dir=output_dir,
        )

    def _split_and_summarize(
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
        if not chapters:
            Page.upsert(
                source_url,
                title=title,
                status="failed",
                error="未拆出有效章节",
                chapter_total=0,
                chapter_ok=0,
                raw_path=raw,
            )
            return []

        Page.upsert(
            source_url,
            title=title,
            status="summarizing",
            error="",
            chapter_total=len(chapters),
            chapter_ok=0,
            raw_path=raw,
        )
        results, failed = self._summarize_and_save(chapters, output_dir=output_dir)
        ok = len(results)
        if ok == 0:
            status, err = "failed", f"全部 {len(chapters)} 章总结失败"
        elif failed:
            status, err = "partial", f"跳过 {failed} 章"
        else:
            status, err = "done", ""
        Page.upsert(
            source_url,
            title=title,
            status=status,
            error=err,
            chapter_total=len(chapters),
            chapter_ok=ok,
            raw_path=raw,
        )
        return results

    @staticmethod
    def _load_local_page(path: Path) -> tuple[str, str]:
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

    def _save_one_raw(self, url: str, page_html: str) -> Path:
        path = self._default_raw_path(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(page_html, encoding="utf-8")
        logger.info("已保存 HTML：%s", path)
        return path

    @staticmethod
    def _default_raw_path(url: str) -> Path:
        parsed = urlparse(url)
        host = parsed.netloc.replace(":", "_") or "page"
        path = parsed.path.strip("/") or "index"
        slug = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", path, flags=re.UNICODE)
        slug = re.sub(r"_+", "_", slug).strip("_")[:80] or "index"
        raw_dir = config.RAW_DIR
        raw_dir.mkdir(parents=True, exist_ok=True)
        return raw_dir / f"{host}_{slug}.html"

    def _summarize_and_save(
        self,
        chapters: list[Chapter],
        *,
        output_dir: Optional[Path] = None,
    ) -> tuple[list[Result], int]:
        if not chapters:
            return [], 0

        out_dir = Path(output_dir or self.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        queue = llm_queue()
        futures = [queue.submit(self._summarize_chapter, ch) for ch in chapters]

        results: list[Result] = []
        failed = 0
        for chapter, future in zip(chapters, futures):
            try:
                summary, untranslated = future.result()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                failed += 1
                logger.error(
                    "总结失败，已跳过：%s / %s (%s)",
                    chapter.entry or "?",
                    chapter.title,
                    exc,
                )
                continue
            for name in untranslated:
                Dictionary.add_local(name)
            path = out_dir / f"{chapter.slug}.md"
            path.write_text(self._render_markdown(chapter, summary), encoding="utf-8")
            results.append(
                Result(chapter=chapter, summary=summary, output_path=path)
            )
            Page.upsert(
                chapter.source_url,
                status="summarizing",
                chapter_ok=len(results),
            )

        if failed:
            logger.warning("本页总结：成功 %d 章，跳过 %d 章", len(results), failed)
        return results, failed

    def _summarize_chapter(self, chapter: Chapter) -> tuple[str, list[str]]:
        blob = "\n".join(
            (chapter.entry or "", chapter.title or "", chapter.content or "")
        )
        glossary = Dictionary.matches_in(blob)
        if glossary:
            glossary_block = "\n".join(f"{en} → {zh}" for en, zh in glossary)
        else:
            glossary_block = "（无）"
        user_prompt = (
            f"来源：{chapter.source_url or '未知'}\n"
            f"条目：{chapter.entry or '未知'}\n"
            f"标题：{chapter.title}\n\n"
            f"专名对照：\n{glossary_block}\n\n"
            f"原文：\n{chapter.content}"
        )
        if self.llm is None:
            self.llm = LLM()
        raw = self.llm.chat(
            [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        ).strip()
        return _parse_summary_json(raw)

    def _split_chapters(
        self,
        page_html: str,
        *,
        source_url: str = "",
        page_title: Optional[str] = None,
    ) -> list[Chapter]:
        soup = BeautifulSoup(page_html, "lxml")
        root = self._pick_content_root(soup)
        catalog = Citation.bind(root, source_url)

        for junk in root.select(
            "script, style, nav, footer, .toc, .navbox, .mw-editsection, "
            "ol.references, .references"
        ):
            junk.decompose()

        entry = page_title or self._extract_page_title(soup) or "未命名条目"
        chapters = self._split_by_heading_traversal(
            root, entry, source_url, catalog
        )

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
                        citations=self._citations_in_text(full, catalog),
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
        catalog: list[Citation],
    ) -> list[Chapter]:
        headings = root.find_all(self.heading_tags)
        if not headings:
            return []

        chapters: list[Chapter] = []
        order = 0
        heading_set = set(headings)
        preface = self._collect_preface(headings[0])
        if len(preface) >= self.min_chapter_chars:
            chapters.append(
                Chapter(
                    title="前言",
                    content=preface,
                    entry=page_title,
                    level=1,
                    order=order,
                    source_url=source_url,
                    citations=self._citations_in_text(preface, catalog),
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
            content = self._collect_heading_content(heading, heading_set)
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
                    citations=self._citations_in_text(content, catalog),
                )
            )
            order += 1

        return chapters

    def _collect_preface(self, first: Tag) -> str:
        parts: list[str] = []
        for sib in first.previous_siblings:
            text = self._sibling_plain_text(sib)
            if text:
                parts.append(text)
        return self._normalize_text("\n".join(reversed(parts)))

    def _collect_heading_content(self, heading: Tag, heading_set: set) -> str:
        parts: list[str] = []
        for sib in heading.next_siblings:
            if isinstance(sib, Tag) and (
                sib in heading_set or sib.name in self.heading_tags
            ):
                break
            if isinstance(sib, Tag):
                nested = sib.find(self.heading_tags)
                if nested and nested in heading_set:
                    break
            text = self._sibling_plain_text(sib)
            if text:
                parts.append(text)
        return self._normalize_text("\n".join(parts))

    @staticmethod
    def _sibling_plain_text(sib: NavigableString | Tag) -> str:
        if isinstance(sib, NavigableString):
            return str(sib).strip()
        if isinstance(sib, Tag):
            return sib.get_text("\n", strip=True)
        return ""

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
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        return text.strip()

    @staticmethod
    def _render_markdown(chapter: Chapter, summary: str) -> str:
        entry = chapter.entry or "未命名条目"
        title = chapter.title or "未命名标题"
        heading = title if entry == title else f"{entry} · {title}"
        lines = [
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
        if chapter.citations:
            lines.extend(["## 出处", ""])
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
