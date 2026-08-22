from __future__ import annotations

import json

import pytest
from bs4 import BeautifulSoup

from models.wiki import Chapter, Citation, Wiki, _parse_summary_json


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
    html = """
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
    chapters = Wiki(min_chapter_chars=10)._split_chapters(
        html, source_url="https://example.test/wiki/Mondstadt"
    )
    titles = [c.title for c in chapters]
    assert "Old World" in titles
    assert "Archon War" in titles
    assert all(c.entry == "Mondstadt" for c in chapters)
