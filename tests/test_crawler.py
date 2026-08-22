from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from libs.crawler import CrawlerError, FandomWikiCrawler


def _json_response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.json.return_value = payload
    return response


def test_fetch_html_wraps_mediawiki_parse():
    crawler = MagicMock()
    crawler.get.return_value = _json_response(
        {
            "parse": {
                "title": "Mondstadt",
                "displaytitle": "Mondstadt",
                "text": {"*": "<p>The City of Freedom.</p>"},
            }
        }
    )
    html = FandomWikiCrawler(crawler=crawler).fetch_html(
        "https://genshin-impact.fandom.com/wiki/Mondstadt"
    )
    assert "Mondstadt" in html
    assert "mw-content-text" in html
    assert "City of Freedom" in html
    url = crawler.get.call_args.args[0]
    assert url.endswith("/api.php")
    assert crawler.get.call_args.kwargs["params"]["page"] == "Mondstadt"


def test_fetch_html_bad_url_returns_empty():
    html = FandomWikiCrawler(crawler=MagicMock()).fetch_html("not-a-wiki")
    assert html == ""


def test_fetch_html_api_error_returns_empty():
    crawler = MagicMock()
    crawler.get.return_value = _json_response(
        {"error": {"code": "missingtitle", "info": "The page does not exist"}}
    )
    html = FandomWikiCrawler(crawler=crawler).fetch_html(
        "https://genshin-impact.fandom.com/wiki/Missing"
    )
    assert html == ""


def test_fetch_html_empty_body_returns_empty():
    crawler = MagicMock()
    crawler.get.return_value = _json_response(
        {"parse": {"title": "Empty", "text": {"*": "  "}}}
    )
    html = FandomWikiCrawler(crawler=crawler).fetch_html(
        "https://genshin-impact.fandom.com/wiki/Empty"
    )
    assert html == ""


def test_fetch_via_raises_on_bad_url():
    with pytest.raises(CrawlerError, match="不是有效"):
        FandomWikiCrawler(crawler=MagicMock())._fetch_via_mediawiki_api(
            "https://example.test/not-wiki"
        )
