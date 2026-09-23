from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from libs.store import Chunk
from models.dictionary import Dictionary
from models.plan import Gathered
from models.qa import AnswerStream, QA


def _patch_planner(chunks: list[Chunk]) -> MagicMock:
    planner = MagicMock()
    planner.plan.return_value = [object()]
    planner.gather.return_value = Gathered(chunks=chunks, cells=[])
    return planner


@patch("models.qa.Planner")
def test_prepare_rejects_empty_question_and_empty_store(planner_cls):
    store = MagicMock()
    store.count.return_value = 0
    qa = QA(store=store, llm=MagicMock())
    with pytest.raises(ValueError, match="不能为空"):
        qa.ask("  ")
    with pytest.raises(RuntimeError, match="向量库为空"):
        qa.ask("风神是谁")
    planner_cls.assert_not_called()


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


@patch("models.qa.Planner")
def test_ask_uses_gathered_chunks(planner_cls):
    chunk = Chunk(id="1", document="钟离是岩神。", metadata={"label": "钟离"})
    store = MagicMock()
    store.count.return_value = 1
    llm = MagicMock()
    llm.chat.return_value = "  岩神。  "
    planner = _patch_planner([chunk])
    planner_cls.return_value = planner
    result = QA(store=store, llm=llm, top_k=3).ask("岩神是谁", top_k=2)
    assert result.answer == "岩神。"
    assert result.sources == [chunk]
    planner_cls.assert_called_once_with(llm=llm, store=store)
    planner.plan.assert_called_once_with("岩神是谁")
    planner.gather.assert_called_once_with(planner.plan.return_value, max_chunks=2)
    store.query.assert_not_called()
    user = llm.chat.call_args.args[0][1]["content"]
    assert user.startswith("问题：岩神是谁\n")


@patch("models.qa.Planner")
def test_ask_passes_english_source_and_glossary(planner_cls):
    Dictionary.create(
        en="Zhongli", zh="钟离", source=Dictionary.Source.GENSHIN_DICTIONARY
    )
    chunk = Chunk(
        id="1",
        document="Zhongli is the Geo Archon of Liyue.",
        metadata={"label": "Zhongli"},
    )
    store = MagicMock()
    store.count.return_value = 1
    llm = MagicMock()
    llm.chat.return_value = "钟离是岩神。"
    planner_cls.return_value = _patch_planner([chunk])
    QA(store=store, llm=llm).ask("岩神是谁")
    user = llm.chat.call_args.args[0][1]["content"]
    assert user.startswith("问题：岩神是谁\n")
    assert "Zhongli is the Geo Archon of Liyue." in user
    assert "Zhongli → 钟离" in user
    assert "专名对照：" in user
    store.query.assert_not_called()


@patch("models.qa.Planner")
def test_ask_stream_builds_answer_after_tokens(planner_cls):
    chunk = Chunk(id="1", document="钟离是岩神。", metadata={"label": "钟离"})
    store = MagicMock()
    store.count.return_value = 1
    llm = MagicMock()
    llm.chat_stream.return_value = iter(["岩", "神"])
    planner_cls.return_value = _patch_planner([chunk])
    stream = QA(store=store, llm=llm).ask_stream("岩神是谁")
    assert "".join(stream) == "岩神"
    assert stream.result.answer == "岩神"
    assert stream.result.sources == [chunk]


def test_answer_stream_result_before_iter_raises():
    stream = AnswerStream(question="谁", sources=[], _tokens=["x"])
    with pytest.raises(RuntimeError, match="尚未结束"):
        _ = stream.result
