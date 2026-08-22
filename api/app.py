"""Flask 入口：POST /summarize | /ingest | /ask（参数与 scripts/main 一致）。"""

from __future__ import annotations

import json
import mimetypes
import sys
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context
from flask_cors import CORS

# Windows 注册表常把 .js 标成 text/plain，Chrome 会拒绝执行 type=module
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".mjs")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# pylint: disable=wrong-import-position
import config
from models import ask as do_ask
from models import ask_stream as do_ask_stream
from models import ingest as do_ingest
from models import summarize as do_summarize
from libs.page_store import Page
# pylint: enable=wrong-import-position

FRONTEND_DIST = ROOT / "frontend" / "dist"

config.setup_logging()

app = Flask(__name__)
CORS(app)


def _json_body() -> dict[str, Any]:
    return request.get_json(silent=True) or {}


def _error(exc: Exception, status: int = 400):
    return jsonify({"error": str(exc)}), status


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.get("/health")
def health():
    """存活探测。"""
    return jsonify({"status": "ok"})


@app.get("/pages")
def pages():
    """已抓取/总结过的 URL 目录。"""
    return jsonify({"pages": Page.list_pages()})


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
            heading=body.get("heading"),
        )
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        return _error(exc)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return _error(exc, status=500)

    return jsonify(
        {
            "page_count": len({item.chapter.source_url for item in results}),
            "document_count": len(results),
            "pages": sorted({item.chapter.source_url for item in results}),
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
    """对应 CLI --ask。body.stream 缺省用 config.ASK_STREAM。"""
    body = _json_body()
    show_sources = body.get("show_sources")
    stream = body.get("stream")
    if stream is None:
        stream = config.ASK_STREAM

    if stream:
        return _ask_stream_response(
            body.get("question") or "",
            top_k=body.get("top_k"),
            show_sources=show_sources,
        )

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
        payload["sources"] = result.source_dicts()
    return jsonify(payload)


def _ask_stream_response(
    question: str,
    *,
    top_k: Any,
    show_sources: Any,
) -> Response:
    @stream_with_context
    def generate():
        """把 ask_stream 事件写成 SSE data 行。"""
        try:
            for event in do_ask_stream(
                question,
                top_k=top_k,
                show_sources=show_sources,
            ):
                yield _sse(event)
        except (ValueError, FileNotFoundError, RuntimeError) as exc:
            yield _sse({"type": "error", "error": str(exc)})
        except Exception as exc:  # pylint: disable=broad-exception-caught
            yield _sse({"type": "error", "error": str(exc)})

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


_STATIC_SUFFIXES = {
    ".js",
    ".mjs",
    ".css",
    ".map",
    ".svg",
    ".png",
    ".jpg",
    ".jpeg",
    ".ico",
    ".woff",
    ".woff2",
    ".json",
    ".txt",
    ".webp",
}


def _spa_file(rel_path: str):
    """托管 vite build 产物；无扩展名的未知路径回退 index.html，给 React Router 用。"""
    if not FRONTEND_DIST.is_dir():
        return jsonify({"error": "前端未构建，请先运行 npm run build"}), 404

    dist = FRONTEND_DIST.resolve()
    target = (dist / rel_path).resolve() if rel_path else dist / "index.html"
    try:
        target.relative_to(dist)
    except ValueError:
        return jsonify({"error": "not found"}), 404

    if rel_path and target.is_file():
        return send_from_directory(dist, rel_path)

    # 静态资源缺失时不要回退 HTML，否则 Chrome 会当成脚本加载并白屏
    if rel_path and Path(rel_path).suffix.lower() in _STATIC_SUFFIXES:
        return jsonify({"error": "not found"}), 404
    return send_from_directory(dist, "index.html")


@app.get("/")
def spa_root():
    """站点根：前端 index.html。"""
    return _spa_file("")


@app.get("/<path:path>")
def spa(path: str):
    """其余路径交给 React Router / 静态资源。"""
    return _spa_file(path)


def main() -> None:
    """开发用 Flask server。"""
    app.run(host=config.API_HOST, port=config.API_PORT, debug=config.API_DEBUG)


if __name__ == "__main__":
    main()
