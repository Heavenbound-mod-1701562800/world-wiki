from __future__ import annotations

from models.ingest import Ingest, chunk_markdown


class FakeStore:
    def __init__(self) -> None:
        self.metas: dict[str, dict] = {}
        self.upserted: list[tuple[str, str, dict]] = []
        self.reset_called = False
        self.deleted: list[dict] = []

    def count(self) -> int:
        return len(self.metas)

    def reset(self) -> None:
        self.reset_called = True
        self.metas.clear()

    def get_metadatas(self, ids: list[str]) -> dict[str, dict]:
        return {i: self.metas[i] for i in ids if i in self.metas}

    def upsert(self, documents, ids=None, metadatas=None) -> None:
        for doc_id, text, meta in zip(ids, documents, metadatas):
            self.metas[doc_id] = meta
            self.upserted.append((doc_id, text, meta))

    def delete(self, ids=None, where=None) -> None:
        self.deleted.append({"ids": ids, "where": where})
        if where and "filename" in where:
            name = where["filename"]
            drop = [key for key, meta in self.metas.items() if meta.get("filename") == name]
            for key in drop:
                del self.metas[key]


def _write_md(path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def _md(entry: str, title: str, content: str, page_in: str = "") -> str:
    lines = [
        f"# {entry} · {title}",
        "",
        f"- Entry: {entry}",
        f"- Title: {title}",
        "- Source: https://example.test",
    ]
    if page_in:
        lines.append(f"- In: {page_in}")
    lines.extend(["", "## Content", "", content.strip(), ""])
    return "\n".join(lines)


def test_ingest_skips_unchanged_hash(tmp_path):
    md = tmp_path / "a.md"
    _write_md(md, _md("Foo", "Title", "hello"))
    store = FakeStore()
    ingest = Ingest(store=store, summaries_dir=tmp_path)
    first = ingest.run()
    assert first.upserted == 1
    assert first.skipped == 0
    assert store.upserted[0][0].endswith("-000")
    assert store.upserted[0][1].startswith("# Foo\nin: Title")
    second = ingest.run()
    assert second.upserted == 0
    assert second.skipped == 1


def test_ingest_upserts_when_hash_changes(tmp_path):
    md = tmp_path / "a.md"
    _write_md(md, _md("Foo", "Title", "first"))
    store = FakeStore()
    ingest = Ingest(store=store, summaries_dir=tmp_path)
    ingest.run()
    _write_md(md, _md("Foo", "Title", "second"))
    report = ingest.run()
    assert report.upserted == 1
    assert store.deleted[-1]["where"] == {"filename": "a.md"}
    assert "second" in store.upserted[-1][1]


def test_ingest_reset_clears_store(tmp_path):
    md = tmp_path / "a.md"
    _write_md(md, _md("Foo", "Title", "body"))
    store = FakeStore()
    ingest = Ingest(store=store, summaries_dir=tmp_path)
    ingest.run()
    report = ingest.run(reset=True)
    assert store.reset_called is True
    assert report.upserted == 1


def test_chunk_prefix_and_nested_headings():
    body = (
        "### Act I\n\n"
        "#### Setting Sail\n\n"
        "The Alcor is ready.\n"
    )
    pieces = chunk_markdown(
        body, entry="Chapter II", page_in="Archon Quest", size=512, overlap=96
    )
    assert len(pieces) == 1
    text = pieces[0][1]
    assert text.startswith("# Chapter II\nin: Archon Quest")
    assert "### Act I" in text
    assert "#### Setting Sail" in text
    assert "The Alcor is ready." in text


def test_ingest_chunks_headings_each_with_prefix(tmp_path):
    content = (
        "### Prologue\n\n"
        "#### A Path Through the Storm\n\n"
        "While overlooking Liyue Harbor, the Traveler waits.\n\n"
        "#### The Crux Clash\n\n"
        "Jinyou is no match for the Traveler.\n"
    )
    md = tmp_path / "Chapter_II__Summary.md"
    _write_md(md, _md("Chapter II", "Summary", content, page_in="Archon Quest"))
    store = FakeStore()
    Ingest(store=store, summaries_dir=tmp_path).run()
    texts = [doc for _i, doc, _m in store.upserted]
    assert len(texts) == 2
    assert all(doc.startswith("# Chapter II\nin: Archon Quest") for doc in texts)
    assert any("#### A Path Through the Storm" in doc for doc in texts)
    assert any("#### The Crux Clash" in doc for doc in texts)


def test_chunk_windows_overlap():
    body = "alpha " * 80
    pieces = chunk_markdown(body, entry="E", page_in="T", size=20, overlap=6)
    assert len(pieces) >= 2
    first = pieces[0][1].split("in: T", 1)[1]
    second = pieces[1][1].split("in: T", 1)[1]
    assert "alpha" in first and "alpha" in second
