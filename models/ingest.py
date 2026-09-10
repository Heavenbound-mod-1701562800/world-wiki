"""把总结 Markdown 写入向量库（按 content_hash 增量；切块入库）。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

import config
from libs.store import Store

_HEADERS = (
    ("###", "h3"),
    ("####", "h4"),
    ("#####", "h5"),
    ("######", "h6"),
)
_HEADER_SPLITTER = MarkdownHeaderTextSplitter(
    headers_to_split_on=list(_HEADERS),
    strip_headers=True,
)


@dataclass
class IngestReport:
    """一次入库的计数。"""

    upserted: int = 0
    skipped: int = 0
    total_files: int = 0
    store_count: int = 0


@dataclass
class Ingest:
    """扫描 md → 切块 embedding → Chroma upsert。"""

    store: Store = field(default_factory=Store)
    summaries_dir: Path = field(default_factory=lambda: config.SUMMARIES_DIR)

    def __post_init__(self) -> None:
        self.summaries_dir = Path(self.summaries_dir)

    def run(
        self,
        summaries_dir: str | Path | None = None,
        *,
        reset: bool = False,
    ) -> IngestReport:
        """扫描目录下 .md 切块写入向量库；整文件 content_hash 未变则跳过。"""
        root = Path(summaries_dir or self.summaries_dir)
        if not root.exists():
            raise FileNotFoundError(f"总结目录不存在：{root}")

        files = sorted(root.glob("*.md"))
        report = IngestReport(total_files=len(files))
        if not files:
            report.store_count = self.store.count()
            return report

        if reset:
            self.store.reset()

        file_jobs: list[tuple[str, str, list[tuple[str, str, dict]]]] = []
        probe_ids: list[str] = []
        for path in files:
            chunks = self._chunks_from_md(path)
            if not chunks:
                continue
            file_id = chunks[0][0].rsplit("-", 1)[0]
            digest = chunks[0][2]["content_hash"]
            file_jobs.append((path.name, digest, chunks))
            probe_ids.append(f"{file_id}-000")

        if not file_jobs:
            report.store_count = self.store.count()
            return report

        existing = {} if reset else self.store.get_metadatas(probe_ids)
        self._upsert_changed(file_jobs, existing, report)
        report.store_count = self.store.count()
        return report

    def _upsert_changed(
        self,
        file_jobs: list[tuple[str, str, list[tuple[str, str, dict]]]],
        existing: dict,
        report: IngestReport,
    ) -> None:
        documents: list[str] = []
        ids: list[str] = []
        metadatas: list[dict] = []
        for filename, digest, chunks in file_jobs:
            probe = chunks[0][0]
            old = existing.get(probe) or {}
            if old.get("content_hash") == digest:
                report.skipped += 1
                continue
            self.store.delete(where={"filename": filename})
            for doc_id, text, meta in chunks:
                ids.append(doc_id)
                documents.append(text)
                metadatas.append(meta)
        if documents:
            self.store.upsert(documents, ids=ids, metadatas=metadatas)
            report.upserted = len(documents)

    @classmethod
    def _chunks_from_md(cls, path: Path) -> list[tuple[str, str, dict]]:
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return []
        base_meta, file_id, page_in = cls._file_meta(path, text)
        pieces = chunk_markdown(
            _content_section(text),
            entry=base_meta["entry"],
            page_in=page_in,
            size=config.INGEST_CHUNK_TOKENS,
            overlap=config.INGEST_CHUNK_OVERLAP,
        )
        out: list[tuple[str, str, dict]] = []
        for i, (heading, piece) in enumerate(pieces):
            item = dict(base_meta)
            if heading:
                item["heading"] = heading
            out.append((f"{file_id}-{i:03d}", piece, item))
        return out

    @classmethod
    def _file_meta(cls, path: Path, text: str) -> tuple[dict, str, str]:
        parsed = cls._meta_from_md(text)
        title = parsed.get("title") or cls._title_from_md(text) or path.stem
        entry = parsed.get("entry") or title
        label = title if not entry or entry == title else f"{entry} / {title}"
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        file_id = (
            "md-"
            + hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:24]
        )
        base_meta = {
            "title": title,
            "entry": entry,
            "label": label,
            "path": str(path.resolve()),
            "filename": path.name,
            "content_hash": digest,
        }
        return base_meta, file_id, parsed.get("in") or title

    @staticmethod
    def _title_from_md(text: str) -> str:
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("# "):
                return line[2:].strip()
        return ""

    @staticmethod
    def _meta_from_md(text: str) -> dict[str, str]:
        meta: dict[str, str] = {}
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("- 条目:") or line.startswith("- Entry:"):
                meta["entry"] = line.split(":", 1)[1].strip()
            elif line.startswith("- 标题:") or line.startswith("- Title:"):
                meta["title"] = line.split(":", 1)[1].strip()
            elif line.startswith("- In:") or line.startswith("- in:"):
                meta["in"] = line.split(":", 1)[1].strip()
        return meta


def chunk_markdown(
    body: str,
    *,
    entry: str,
    page_in: str,
    size: int,
    overlap: int,
) -> list[tuple[str, str]]:
    """按小标题切，过长再 512/96 窗口。返回 (heading, 带前缀的块)。"""
    body = (body or "").strip()
    if not body:
        return []
    windows = RecursiveCharacterTextSplitter(
        chunk_size=max(size, 1) * 4,
        chunk_overlap=max(0, overlap) * 4,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    results: list[tuple[str, str]] = []
    docs = _HEADER_SPLITTER.split_text(body) or [
        Document(page_content=body, metadata={})
    ]
    for doc in docs:
        text = (doc.page_content or "").strip()
        if not text:
            continue
        path = _path_from_meta(doc.metadata)
        heading = "\n".join(path)
        for window in windows.split_text(text):
            piece = window.strip()
            if piece:
                results.append((heading, _prefixed(entry, page_in, path, piece)))
    return results


def _path_from_meta(meta: dict | None) -> list[str]:
    path: list[str] = []
    for hashes, key in _HEADERS:
        title = (meta or {}).get(key)
        if title:
            path.append(f"{hashes} {title}")
    return path


def _content_section(text: str) -> str:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "## Content":
            end = len(lines)
            for j in range(i + 1, len(lines)):
                if lines[j].strip() == "## Citations":
                    end = j
                    break
            return "\n".join(lines[i + 1 : end]).strip()
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("# ") or stripped.startswith("- ") or stripped == "":
            i += 1
            continue
        break
    return "\n".join(lines[i:]).strip() or text.strip()


def _prefixed(entry: str, page_in: str, path: list[str], body: str) -> str:
    lines = [f"# {entry or 'untitled'}", f"in: {page_in or entry or 'untitled'}"]
    if path:
        lines.append("")
        lines.extend(path)
    lines.append("")
    lines.append(body.strip())
    return "\n".join(lines).strip() + "\n"
