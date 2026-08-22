from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from libs.store import Chunk
from models.qa import QA


def test_prepare_rejects_empty_question_and_empty_store():
    store = MagicMock()
    store.count.return_value = 0
    qa = QA(store=store, llm=MagicMock())
    with pytest.raises(ValueError, match="不能为空"):
        qa.ask("  ")
    with pytest.raises(RuntimeError, match="向量库为空"):
        qa.ask("风神是谁")


def test_format_context_uses_label():
    chunks = [
        Chunk(id="1", document="alpha\n\n\nbeta", metadata={"label": "A"}),
        Chunk(id="2", document="gamma", metadata={"filename": "x.md"}),
    ]
    text = QA._format_context(chunks)
    assert "[1] A" in text
    assert "alpha" in text
    assert "[2] x.md" in text
    assert QA._format_context([]) == "（无相关资料）"


def test_ask_uses_store_and_llm():
    chunk = Chunk(id="1", document="钟离是岩神。", metadata={"label": "钟离"})
    store = MagicMock()
    store.count.return_value = 1
    store.query.return_value = [chunk]
    llm = MagicMock()
    llm.chat.return_value = "  岩神。  "
    qa = QA(store=store, llm=llm, top_k=3)
    result = qa.ask("岩神是谁", top_k=2)
    assert result.answer == "岩神。"
    store.query.assert_called_once_with("岩神是谁", top_k=2)
    assert result.sources == [chunk]
