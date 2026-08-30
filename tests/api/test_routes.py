from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from libs.store import Chunk
from models.ingest import IngestReport
from models.qa import Answer
from models.wiki import Chapter, Result


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.get_json() == {"status": "ok"}


def test_pages_uses_page_store(client):
    rows = [{"url": "https://example.test/wiki/A", "status": "done"}]
    with patch("api.app.Page.list_pages", return_value=rows) as listed:
        res = client.get("/pages")
    assert res.status_code == 200
    assert res.get_json() == {"pages": rows}
    listed.assert_called_once()


def test_summarize_ok(client):
    chapter = Chapter(
        title="Old World",
        content="x",
        entry="Mondstadt",
        source_url="https://example.test/wiki/Mondstadt",
    )
    fake = [
        Result(
            chapter=chapter,
            summary="The old world was destroyed.",
            output_path=Path("a.md"),
        ),
        Result(
            chapter=chapter,
            summary="Then many gods fought.",
            output_path=Path("b.md"),
        ),
    ]
    with patch("api.app.do_summarize", return_value=fake) as summarize:
        res = client.post(
            "/summarize",
            json={"urls": ["https://example.test/wiki/Mondstadt"]},
        )
    assert res.status_code == 200
    body = res.get_json()
    assert body["page_count"] == 1
    assert body["document_count"] == 2
    assert body["pages"] == ["https://example.test/wiki/Mondstadt"]
    assert body["results"][0]["slug"] == chapter.slug
    assert "untranslated" not in body
    summarize.assert_called_once()


def test_summarize_bad_input_is_400(client):
    with patch("api.app.do_summarize", side_effect=ValueError("请提供")):
        res = client.post("/summarize", json={"urls": []})
    assert res.status_code == 400
    assert res.get_json()["error"] == "请提供"


def test_summarize_runtime_error_is_400(client):
    with patch("api.app.do_summarize", side_effect=RuntimeError("超时")):
        res = client.post("/summarize", json={"urls": ["https://x.test"]})
    assert res.status_code == 400
    assert res.get_json()["error"] == "超时"


def test_summarize_unexpected_is_500(client):
    with patch("api.app.do_summarize", side_effect=OSError("disk")):
        res = client.post("/summarize", json={"urls": ["https://x.test"]})
    assert res.status_code == 500
    assert "disk" in res.get_json()["error"]


def test_ingest_ok(client):
    report = IngestReport(upserted=2, skipped=1, total_files=3, store_count=10)
    with patch("api.app.do_ingest", return_value=report) as ingest:
        res = client.post("/ingest", json={"reset": True})
    assert res.status_code == 200
    assert res.get_json() == {
        "reset": True,
        "upserted": 2,
        "skipped": 1,
        "total_files": 3,
        "store_count": 10,
    }
    ingest.assert_called_once_with(reset=True)


def test_ask_json_ok(client):
    answer = Answer(
        question="岩神是谁",
        answer="钟离。",
        sources=[
            Chunk(id="1", document="钟离是岩神。", metadata={"label": "钟离"}),
        ],
    )
    with patch("api.app.do_ask", return_value=answer) as ask:
        res = client.post(
            "/ask",
            json={"question": "岩神是谁", "stream": False, "show_sources": True},
        )
    assert res.status_code == 200
    body = res.get_json()
    assert body["answer"] == "钟离。"
    assert body["sources"][0]["id"] == "1"
    ask.assert_called_once()


def test_ask_json_rejects_empty(client):
    with patch("api.app.do_ask", side_effect=ValueError("请提供问题")):
        res = client.post("/ask", json={"question": "  ", "stream": False})
    assert res.status_code == 400


def test_ask_stream_sse(client):
    def events(**_kwargs):
        yield {"type": "delta", "text": "岩"}
        yield {"type": "done", "question": "谁", "answer": "岩神"}

    with patch("api.app.do_ask_stream", side_effect=lambda *a, **k: events()):
        with patch("api.app.config.ASK_STREAM", True):
            res = client.post("/ask", json={"question": "谁"})
    assert res.status_code == 200
    assert res.mimetype == "text/event-stream"
    payloads = [
        json.loads(line[len("data: ") :])
        for line in res.get_data(as_text=True).splitlines()
        if line.startswith("data: ")
    ]
    assert payloads[0] == {"type": "delta", "text": "岩"}
    assert payloads[-1]["type"] == "done"
    assert payloads[-1]["answer"] == "岩神"


def test_dictionary_search_requires_two_chars(client):
    res = client.get("/dictionary?q=x")
    assert res.status_code == 400


def test_dictionary_search_ok(client):
    row = type("Row", (), {"en": "Xiao", "zh": "魈", "source": 2})()
    with patch("api.app.Dictionary.search", return_value=([row], 1)) as search:
        res = client.get("/dictionary?q=xi&offset=0&limit=50")
    assert res.status_code == 200
    body = res.get_json()
    assert body["total"] == 1
    assert body["items"][0] == {"en": "Xiao", "zh": "魈", "source": 2}
    search.assert_called_once_with("xi", offset=0, limit=50)


def test_dictionary_patch_ok_and_404(client):
    with patch("api.app.Dictionary.update_entry", return_value=True) as updated:
        res = client.patch("/dictionary", json={"en": "Xiao", "zh": "魈", "source": 2})
    assert res.status_code == 200
    updated.assert_called_once()
    with patch("api.app.Dictionary.update_entry", return_value=False):
        res = client.patch("/dictionary", json={"en": "Missing"})
    assert res.status_code == 404


def test_dictionary_post_ok(client):
    with patch("api.app.Dictionary.add", return_value=(1, ["Zhongli"])) as added:
        res = client.post(
            "/dictionary",
            json={"pairs": [{"en": "Xiao", "zh": "魈"}], "source": 2},
        )
    assert res.status_code == 200
    assert res.get_json() == {"ok": True, "added": 1, "skipped": ["Zhongli"]}
    added.assert_called_once_with([("Xiao", "魈")], source=2)


def test_dictionary_post_rejects_empty_and_invalid(client):
    res = client.post("/dictionary", json={"pairs": []})
    assert res.status_code == 400
    with patch("api.app.Dictionary.add", side_effect=ValueError("第 1 对无效")):
        res = client.post(
            "/dictionary",
            json={"pairs": [{"en": "ab", "zh": "短"}]},
        )
    assert res.status_code == 400
    assert res.get_json()["error"] == "第 1 对无效"


def test_dictionary_lookup_ok(client):
    with patch(
        "api.app.Dictionary.lookup_many",
        return_value=(["Alatus"], ["Nope"]),
    ) as lookup:
        res = client.post("/dictionary/lookup", json={"terms": ["Alatus", "Nope"]})
    assert res.status_code == 200
    assert res.get_json() == {"filled": ["Alatus"], "missed": ["Nope"]}
    lookup.assert_called_once()
