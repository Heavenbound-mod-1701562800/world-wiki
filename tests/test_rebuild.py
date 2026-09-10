from __future__ import annotations

from unittest.mock import MagicMock, patch

from libs.page_store import Page
from models.dictionary import Dictionary
from scripts.rebuild import (
    clear_rebuild_targets,
    collect_raw_jobs,
    rebuild,
    wiki_url_for,
)

_HTML = """
<html><body>
  <h1>Chapter II</h1>
  <article id="mw-content-text">
    <h2>Summary</h2>
    <p>The Traveler sails to Inazuma and meets the Raiden Shogun.</p>
  </article>
</body></html>
"""


def test_wiki_url_for_prefers_http_stored(tmp_path):
    path = tmp_path / "x.html"
    path.write_text(_HTML, encoding="utf-8")
    url = "https://genshin-impact.fandom.com/wiki/Chapter_II"
    assert wiki_url_for(path, url) == url


def test_wiki_url_for_from_h1(tmp_path):
    path = tmp_path / "x.html"
    path.write_text(_HTML, encoding="utf-8")
    assert wiki_url_for(path, "") == (
        "https://genshin-impact.fandom.com/wiki/Chapter_II"
    )


def test_collect_raw_jobs_merges_page_and_dir(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    listed = raw / "listed.html"
    listed.write_text(_HTML, encoding="utf-8")
    extra = raw / "extra.html"
    extra.write_text(_HTML.replace("Chapter II", "Inazuma/History"), encoding="utf-8")
    Page.upsert(
        "https://genshin-impact.fandom.com/wiki/Chapter_II",
        status="done",
        raw_path=str(listed),
    )
    monkeypatch.setattr("scripts.rebuild.config.RAW_DIR", raw)
    jobs = {path.name: url for path, url in collect_raw_jobs(raw)}
    assert jobs["listed.html"] == "https://genshin-impact.fandom.com/wiki/Chapter_II"
    assert jobs["extra.html"] == (
        "https://genshin-impact.fandom.com/wiki/Inazuma/History"
    )


def test_clear_rebuild_targets_keeps_dictionary(tmp_path, monkeypatch):
    summaries = tmp_path / "summaries"
    summaries.mkdir()
    stale = summaries / "old.md"
    stale.write_text("# old\n", encoding="utf-8")
    monkeypatch.setattr("scripts.rebuild.config.SUMMARIES_DIR", summaries)
    Dictionary.create(en="Zhongli", zh="钟离", source=Dictionary.Source.MANUAL)
    Page.upsert("https://example.test/wiki/X", status="done", raw_path="x.html")

    deleted = clear_rebuild_targets()
    assert deleted == 1
    assert not stale.exists()
    assert Page.select().count() == 0
    row = Dictionary.get(Dictionary.en == "Zhongli")
    assert row.zh == "钟离"


def test_rebuild_empty_raw_is_error(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.rebuild.config.RAW_DIR", tmp_path / "empty")
    assert rebuild(raw_dir=tmp_path / "empty") == 1


def test_rebuild_process_then_ingest_reset(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "page.html").write_text(_HTML, encoding="utf-8")
    summaries = tmp_path / "summaries"
    summaries.mkdir()
    monkeypatch.setattr("scripts.rebuild.config.RAW_DIR", raw)
    monkeypatch.setattr("scripts.rebuild.config.SUMMARIES_DIR", summaries)
    monkeypatch.setattr("config.SUMMARIES_DIR", summaries)

    ingest = MagicMock()
    with patch("scripts.rebuild.ingest", ingest):
        assert rebuild(raw_dir=raw) == 0
    ingest.assert_called_once_with(reset=True)
    assert list(summaries.glob("*.md"))
    row = Page.get(Page.url == "https://genshin-impact.fandom.com/wiki/Chapter_II")
    assert row.status == "done"
