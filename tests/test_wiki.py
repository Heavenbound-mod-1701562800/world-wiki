from __future__ import annotations

import json
from unittest.mock import MagicMock

from bs4 import BeautifulSoup

from libs.page_store import Page
from models.dictionary import Dictionary
from models.wiki import Chapter, Citation, Wiki

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

_OL_HTML = """
<html><body>
  <h1>Kannazuka</h1>
  <article id="mw-content-text">
    <h2>Lore</h2>
    <p>Kannazuka is an island region of Inazuma without a ruling god.</p>
    <h2>Other Languages</h2>
    <table class="wikitable">
      <tr><th>Language</th><th>Official Name</th></tr>
      <tr><td>English</td><td>Kannazuka</td></tr>
      <tr>
        <td>Chinese<br>(Simplified)</td>
        <td>神无冢<br>Shénwúzhǒng</td>
      </tr>
      <tr><td>Japanese</td><td>神無塚</td></tr>
    </table>
    <h2>Trivia</h2>
    <p>The name Kannazuka literally means a hill without gods.</p>
  </article>
</body></html>
"""


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
    assert "〔reference:" in root.get_text()


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


def test_split_drops_page_tabs_from_preface():
    html = """
    <html><body>
      <h1>Inazuma/Culture</h1>
      <article id="mw-content-text">
        <div class="custom-tabs-default custom-tabs">
          <span class="inactive-tab"><a href="/wiki/Inazuma">Overview</a></span>
          <span class="active-tab"><strong>Culture</strong></span>
          <span class="inactive-tab"><a href="/wiki/Inazuma/History">History</a></span>
          <span class="inactive-tab"><a href="/wiki/Inazuma/Design">Design</a></span>
          <span class="inactive-tab"><a href="/wiki/Inazuma/Gallery">Gallery</a></span>
        </div>
        <p>The Inazuma archipelago is sub-divided into three main factions.</p>
        <h2>Fashion</h2>
        <p>Women in Inazuma tend to have bangs.</p>
      </article>
    </body></html>
    """
    chapters = Wiki(min_chapter_chars=10)._split_chapters(html)
    intro = next(c for c in chapters if c.title == "Introduction")
    assert "The Inazuma archipelago" in intro.content
    assert "Overview" not in intro.content
    assert "Gallery" not in intro.content


def test_split_drops_image_gallery_captions():
    html = """
    <html><body>
      <h1>Inazuma/Culture</h1>
      <article id="mw-content-text">
        <h2>Fashion</h2>
        <div class="wikia-gallery">
          <div class="wikia-gallery-item">
            <div class="thumb"><img alt="" class="thumbimage"/></div>
            <div class="lightbox-caption">Children's attire (Kouichi)</div>
          </div>
        </div>
        <p>Women in Inazuma tend to have bangs.</p>
        <div class="thumb tright">
          <div class="thumbcaption">A Kairagi's samurai attire</div>
        </div>
      </article>
    </body></html>
    """
    chapters = Wiki(min_chapter_chars=10)._split_chapters(html)
    blob = "\n".join(c.content for c in chapters)
    assert "Women in Inazuma tend to have bangs." in blob
    assert "Kouichi" not in blob
    assert "Kairagi" not in blob


def test_split_keeps_inline_links_without_extra_breaks():
    html = """
    <html><body>
      <h1>Inazuma/Culture</h1>
      <article id="mw-content-text">
        <h2>Designs, Motifs, and Symbols</h2>
        <p>Inazuma's Character Cards are similar to <a href="/wiki/Mondstadt">Mondstadt's</a>: it features one large circle.</p>
        <h3>Mon</h3>
        <p>Clans may have a <i>mon (紋)</i> or seal by which they are identified.</p>
      </article>
    </body></html>
    """
    chapters = Wiki(min_chapter_chars=10)._split_chapters(html)
    text = chapters[0].content
    assert "similar to Mondstadt's: it features" in text
    assert "Mondstadt's :" not in text
    assert "similar to\nMondstadt's" not in text
    assert "mon (紋) or seal" in text
    assert "Mon" in text


def test_split_skips_other_languages_heading():
    chapters = Wiki(min_chapter_chars=10)._split_chapters(
        _OL_HTML, source_url="https://example.test/wiki/Kannazuka"
    )
    titles = [c.title for c in chapters]
    assert "Lore" in titles
    assert "Trivia" in titles
    assert "Other Languages" not in titles
    row = Dictionary.get(Dictionary.en == "Kannazuka")
    assert row.zh == "神无冢"
    assert row.source == Dictionary.Source.WIKI


def test_run_local_html_writes_english_md_and_marks_done(tmp_path):
    src = tmp_path / "page.html"
    src.write_text(_SAMPLE_HTML, encoding="utf-8")
    out = tmp_path / "summaries"
    results = Wiki(output_dir=out, min_chapter_chars=10).run(src)
    assert results
    assert all(item.output_path.is_file() for item in results)
    text = results[0].output_path.read_text(encoding="utf-8")
    assert "## Content" in text
    assert "The old world was destroyed" in text
    assert "概述" not in text
    row = Page.get(Page.url == str(src.resolve()))
    assert row.status == "done"
    assert row.chapter_ok == row.chapter_total
    assert row.chapter_total >= 1


def test_run_skips_other_languages_md(tmp_path):
    src = tmp_path / "page.html"
    src.write_text(_OL_HTML, encoding="utf-8")
    out = tmp_path / "summaries"
    results = Wiki(output_dir=out, min_chapter_chars=10).run(src)
    names = {item.output_path.name for item in results}
    assert not any("Other_Languages" in name for name in names)
    assert any("Lore" in name for name in names)
    assert Dictionary.get(Dictionary.en == "Kannazuka").zh == "神无冢"


def test_run_remote_empty_fetch_marks_failed():
    crawler = MagicMock()
    crawler.fetch_html.return_value = ""
    results = Wiki(crawler=crawler).run(
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
    wiki = Wiki(output_dir=out, min_chapter_chars=10)
    original = wiki._write_chapter

    def write_chapter(chapter, out_dir):
        if chapter.title == "Archon War":
            raise RuntimeError("disk")
        return original(chapter, out_dir)

    wiki._write_chapter = write_chapter  # type: ignore[method-assign]
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
    md = Wiki._render_markdown(chapter, "English body")
    assert "## Citations" in md
    assert "## Content" in md
    assert md.count("NPC") == 1
    assert "Book" in md
    assert "https://example.test/wiki/N" in md
    assert "English body" in md
