from __future__ import annotations

from models.ingest import Ingest


class FakeStore:
    def __init__(self) -> None:
        self.metas: dict[str, dict] = {}
        self.upserted: list[tuple[str, str, dict]] = []
        self.reset_called = False

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


def _write_md(path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def test_ingest_skips_unchanged_hash(tmp_path):
    md = tmp_path / "a.md"
    text = "# Title\n\n- 条目: Foo\n- 标题: Title\n\nhello\n"
    _write_md(md, text)
    store = FakeStore()
    ingest = Ingest(store=store, summaries_dir=tmp_path)
    first = ingest.run()
    assert first.upserted == 1
    assert first.skipped == 0
    second = ingest.run()
    assert second.upserted == 0
    assert second.skipped == 1


def test_ingest_upserts_when_hash_changes(tmp_path):
    md = tmp_path / "a.md"
    _write_md(md, "# Title\n\nfirst\n")
    store = FakeStore()
    ingest = Ingest(store=store, summaries_dir=tmp_path)
    ingest.run()
    _write_md(md, "# Title\n\nsecond\n")
    report = ingest.run()
    assert report.upserted == 1
    assert store.upserted[-1][1].endswith("second")


def test_ingest_reset_clears_store(tmp_path):
    md = tmp_path / "a.md"
    _write_md(md, "# Title\n\nbody\n")
    store = FakeStore()
    ingest = Ingest(store=store, summaries_dir=tmp_path)
    ingest.run()
    report = ingest.run(reset=True)
    assert store.reset_called is True
    assert report.upserted == 1
