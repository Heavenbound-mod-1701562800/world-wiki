from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from bs4 import BeautifulSoup

from libs.page_store import Page
from models.wiki import Chapter, Citation, Wiki, _parse_summary_json

_SAMPLE_HTML = """
<html><body>
  <h1>Mondstadt</h1>
  <article id="mw-content-text">
    <h2>Old World</h2>
    <p>The old world was destroyed long ago by a great disaster in Teyvat.</p>
    <h2>Archon War</h2>
    <p>Then many gods fought across the land during the Archon War period.</p>
  </article>
</body></html>
"""


def test_parse_summary_json_plain_and_fenced():
    summary, names = _parse_summary_json(
        '{"summary": "  概述  ", "untranslated": ["Foo", 1, "Bar"]}'
    )
    assert summary == "概述"
    assert names == ["Foo", "Bar"]

    fenced = "```json\n" + json.dumps(
        {"summary": "x", "untranslated": []}, ensure_ascii=False
    ) + "\n```"
    summary, names = _parse_summary_json(fenced)
    assert summary == "x"
    assert names == []


def test_parse_summary_json_rejects_bad_payload():
    with pytest.raises(ValueError, match="JSON 对象"):
        _parse_summary_json("[1]")
    with pytest.raises(ValueError, match="summary"):
        _parse_summary_json('{"untranslated": []}')
    with pytest.raises(ValueError, match="untranslated"):
        _parse_summary_json('{"summary": "ok", "untranslated": "nope"}')


def test_citation_bind_inlines_footnote():
    html = """
    <div id="mw-content-text">
      <p>Claim<sup class="reference" id="cite_ref-1">
        <a href="#cite_note-1">[1]</a></sup></p>
      <ol class="references">
        <li id="cite_note-1">
          <span class="reference-text">NPC Dialogue:
            <a href="/wiki/Naoe">Naoe</a></span>
        </li>
      </ol>
    </div>
    """
    root = BeautifulSoup(html, "lxml")
    cites = Citation.bind(root, "https://genshin-impact.fandom.com/wiki/X")
    assert len(cites) == 1
    assert "Naoe" in cites[0].label
    assert cites[0].url.endswith("/wiki/Naoe")
    assert "〔出处：" in root.get_text()


def test_chapter_slug_sanitizes():
    ch = Chapter(title="A:B", entry="Foo/Bar", content="x")
    assert "/" not in ch.slug
    assert ":" not in ch.slug
    assert "Foo-Bar" in ch.slug


def test_split_chapters_from_local_html():
    chapters = Wiki(min_chapter_chars=10)._split_chapters(
        _SAMPLE_HTML, source_url="https://example.test/wiki/Mondstadt"
    )
    titles = [c.title for c in chapters]
    assert "Old World" in titles
    assert "Archon War" in titles
    assert all(c.entry == "Mondstadt" for c in chapters)


def test_run_local_html_writes_md_and_marks_done(tmp_path):
    src = tmp_path / "page.html"
    src.write_text(_SAMPLE_HTML, encoding="utf-8")
    out = tmp_path / "summaries"
    llm = MagicMock()
    llm.chat.return_value = '{"summary": "概述", "untranslated": []}'
    results = Wiki(llm=llm, output_dir=out, min_chapter_chars=10).run(src)
    assert results
    assert all(item.output_path.is_file() for item in results)
    assert "概述" in results[0].output_path.read_text(encoding="utf-8")
    row = Page.get(Page.url == str(src.resolve()))
    assert row.status == "done"
    assert row.chapter_ok == row.chapter_total
    assert row.chapter_total >= 1


def test_run_remote_empty_fetch_marks_failed():
    crawler = MagicMock()
    crawler.fetch_html.return_value = ""
    results = Wiki(crawler=crawler, llm=MagicMock()).run(
        "https://genshin-impact.fandom.com/wiki/Mondstadt"
    )
    assert results == []
    row = Page.get(Page.url == "https://genshin-impact.fandom.com/wiki/Mondstadt")
    assert row.status == "failed"
    assert "下载失败" in row.error


def test_run_one_chapter_fail_is_partial(tmp_path):
    src = tmp_path / "page.html"
    src.write_text(_SAMPLE_HTML, encoding="utf-8")
    out = tmp_path / "summaries"
    wiki = Wiki(llm=MagicMock(), output_dir=out, min_chapter_chars=10)

    def summarize_chapter(chapter: Chapter):
        if chapter.title == "Archon War":
            raise RuntimeError("timeout")
        return "概述", []

    wiki._summarize_chapter = summarize_chapter  # type: ignore[method-assign]
    results = wiki.run(src)
    assert len(results) == 1
    row = Page.get(Page.url == str(src.resolve()))
    assert row.status == "partial"
    assert row.chapter_ok == 1


def test_load_local_mediawiki_json(tmp_path):
    path = tmp_path / "mondstadt.json"
    path.write_text(
        json.dumps(
            {
                "parse": {
                    "title": "Mondstadt",
                    "text": {
                        "*": "<h2>Old World</h2><p>The old world was destroyed.</p>"
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    html, url = Wiki._load_local_page(path)
    assert "mw-content-text" in html
    assert "Mondstadt" in html
    assert url.endswith("/wiki/Mondstadt")


def test_render_markdown_lists_unique_citations():
    chapter = Chapter(
        title="T",
        entry="E",
        content="x",
        source_url="https://example.test/wiki/E",
        citations=[
            Citation(note_id="1", label="NPC", url="https://example.test/wiki/N"),
            Citation(note_id="1", label="NPC", url="https://example.test/wiki/N"),
            Citation(note_id="2", label="Book"),
        ],
    )
    md = Wiki._render_markdown(chapter, "概述")
    assert "## 出处" in md
    assert md.count("NPC") == 1
    assert "Book" in md
    assert "https://example.test/wiki/N" in md

