from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from libs.store import Chunk
from models import ask, ask_stream, summarize
from models.qa import AnswerStream


def test_summarize_rejects_empty_and_missing_file(tmp_path):
    with pytest.raises(ValueError, match="请提供"):
        summarize([])
    missing = tmp_path / "nope.html"
    with pytest.raises(FileNotFoundError):
        summarize(missing)


def test_ask_rejects_empty_question():
    with pytest.raises(ValueError, match="请提供问题"):
        ask("   ")


def test_ask_stream_emits_delta_and_done():
    chunk = Chunk(id="1", document="钟离是岩神。", metadata={"label": "钟离"}, distance=0.1)
    fake = AnswerStream(question="岩神是谁", sources=[chunk], _tokens=["钟", "离"])
    qa = MagicMock()
    qa.ask_stream.return_value = fake
    with patch("models.QA", return_value=qa):
        events = list(ask_stream("岩神是谁", show_sources=True, top_k=2))
    assert events[0] == {"type": "delta", "text": "钟"}
    assert events[1] == {"type": "delta", "text": "离"}
    done = events[-1]
    assert done["type"] == "done"
    assert done["answer"] == "钟离"
    assert done["sources"][0]["id"] == "1"
    qa.ask_stream.assert_called_once_with("岩神是谁", top_k=2)
