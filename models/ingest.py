"""把总结 Markdown 写入向量库（按 content_hash 增量）。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import config
from libs.store import Store


@dataclass
class IngestReport:
    """一次入库的计数。"""

    upserted: int = 0
    skipped: int = 0
    total_files: int = 0
    store_count: int = 0


@dataclass
class Ingest:
    """扫描 md → embedding → Chroma upsert。"""

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
        """扫描目录下 .md 写入向量库；content_hash 未变则跳过。"""
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

        candidates = []
        for path in files:
            item = self._candidate_from_md(path)
            if item is not None:
                candidates.append(item)

        if not candidates:
            report.store_count = self.store.count()
            return report

        existing = (
            {}
            if reset
            else self.store.get_metadatas([doc_id for doc_id, _, _ in candidates])
        )
        self._upsert_changed(candidates, existing, report)
        report.store_count = self.store.count()
        return report

    def _upsert_changed(
        self,
        candidates: list[tuple[str, str, dict]],
        existing: dict,
        report: IngestReport,
    ) -> None:
        documents: list[str] = []
        ids: list[str] = []
        metadatas: list[dict] = []
        for doc_id, text, meta in candidates:
            old = existing.get(doc_id) or {}
            if old.get("content_hash") == meta["content_hash"]:
                report.skipped += 1
                continue
            ids.append(doc_id)
            documents.append(text)
            metadatas.append(meta)
        if documents:
            self.store.upsert(documents, ids=ids, metadatas=metadatas)
            report.upserted = len(documents)

    @staticmethod
    def _candidate_from_md(path: Path) -> tuple[str, str, dict] | None:
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return None
        meta = Ingest._meta_from_md(text)
        title = meta.get("title") or Ingest._title_from_md(text) or path.stem
        entry = meta.get("entry") or ""
        label = title if not entry or entry == title else f"{entry} / {title}"
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        doc_id = (
            "md-"
            + hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:24]
        )
        return (
            doc_id,
            text,
            {
                "title": title,
                "entry": entry,
                "label": label,
                "path": str(path.resolve()),
                "filename": path.name,
                "content_hash": digest,
            },
        )

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
        return meta
