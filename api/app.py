"""Flask 入口：POST /summarize | /ingest | /ask（参数与 scripts/main 一致）。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request
from flask_cors import CORS

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from models import ask as do_ask
from models import ingest as do_ingest
from models import summarize as do_summarize

config.setup_logging()

app = Flask(__name__)
CORS(app)


def _json_body() -> dict[str, Any]:
    return request.get_json(silent=True) or {}


def _error(exc: Exception, status: int = 400):
    return jsonify({"error": str(exc)}), status


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/summarize")
def summarize():
    """对应 CLI --summarize。"""
    body = _json_body()
    sources = list(body.get("sources") or []) + list(body.get("urls") or [])
    try:
        results = do_summarize(
            sources,
            output_dir=body.get("output_dir"),
            max_chapters=body.get("max_chapters"),
            save_html=body.get("save_html"),
            heading=body.get("heading"),
        )
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        return _error(exc)

    return jsonify(
        {
            "count": len(results),
            "results": [
                {
                    "entry": item.chapter.entry,
                    "title": item.chapter.title,
                    "source_url": item.chapter.source_url,
                    "slug": item.chapter.slug,
                    "summary": item.summary,
                    "output_path": str(item.output_path),
                }
                for item in results
            ],
        }
    )


@app.post("/ingest")
def ingest():
    """对应 CLI --ingest / --ingest --reset。"""
    body = _json_body()
    reset = body.get("reset")
    try:
        report = do_ingest(reset=reset)
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        return _error(exc)

    return jsonify(
        {
            "reset": config.INGEST_RESET if reset is None else bool(reset),
            "upserted": report.upserted,
            "skipped": report.skipped,
            "total_files": report.total_files,
            "store_count": report.store_count,
        }
    )


@app.post("/ask")
def ask():
    """对应 CLI --ask。"""
    body = _json_body()
    show_sources = body.get("show_sources")
    try:
        result = do_ask(
            body.get("question") or "",
            top_k=body.get("top_k"),
            show_sources=show_sources,
        )
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        return _error(exc)

    resolved_show = (
        config.ASK_SHOW_SOURCES if show_sources is None else bool(show_sources)
    )
    payload: dict[str, Any] = {
        "question": result.question,
        "answer": result.answer,
    }
    if resolved_show:
        payload["sources"] = [
            {
                "id": chunk.id,
                "document": chunk.document,
                "metadata": chunk.metadata,
                "distance": chunk.distance,
            }
            for chunk in result.sources
        ]
    return jsonify(payload)


def main() -> None:
    app.run(host=config.API_HOST, port=config.API_PORT, debug=config.API_DEBUG)


if __name__ == "__main__":
    main()
