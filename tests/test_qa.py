from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from libs.store import Chunk
from models.dictionary import Dictionary
from models.qa import AnswerStream, QA


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


def test_ask_appends_english_names_to_retrieval_query():
    Dictionary.create(
        en="Zhongli", zh="钟离", source=Dictionary.Source.GENSHIN_DICTIONARY
    )
    Dictionary.create(
        en="Grand Narukami Shrine",
        zh="鸣神大社",
        source=Dictionary.Source.GENSHIN_DICTIONARY,
    )
    store = MagicMock()
    store.count.return_value = 1
    store.query.return_value = [
        Chunk(id="1", document="Zhongli.", metadata={"label": "Zhongli"})
    ]
    llm = MagicMock()
    llm.chat.return_value = "答"
    qa = QA(store=store, llm=llm)
    result = qa.ask("鸣神大社门口的钟离")
    store.query.assert_called_once_with(
        "鸣神大社门口的钟离, Grand Narukami Shrine, Zhongli",
        top_k=5,
    )
    assert result.question == "鸣神大社门口的钟离"
    user = llm.chat.call_args.args[0][1]["content"]
    assert user.startswith("问题：鸣神大社门口的钟离\n")
    assert "问题：鸣神大社门口的钟离, Grand" not in user


def test_ask_skips_english_already_in_question():
    Dictionary.create(
        en="Zhongli", zh="钟离", source=Dictionary.Source.GENSHIN_DICTIONARY
    )
    store = MagicMock()
    store.count.return_value = 1
    store.query.return_value = [
        Chunk(id="1", document="Zhongli.", metadata={"label": "Zhongli"})
    ]
    llm = MagicMock()
    llm.chat.return_value = "答"
    QA(store=store, llm=llm).ask("Who is Zhongli")
    store.query.assert_called_once_with("Who is Zhongli", top_k=5)


def test_ask_passes_english_source_and_glossary():
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
    store.query.return_value = [chunk]
    llm = MagicMock()
    llm.chat.return_value = "钟离是岩神。"
    qa = QA(store=store, llm=llm)
    qa.ask("岩神是谁")
    messages = llm.chat.call_args.args[0]
    user = messages[1]["content"]
    assert "Zhongli is the Geo Archon of Liyue." in user
    assert "Zhongli → 钟离" in user
    assert "专名对照：" in user


def test_ask_stream_builds_answer_after_tokens():
    chunk = Chunk(id="1", document="钟离是岩神。", metadata={"label": "钟离"})
    store = MagicMock()
    store.count.return_value = 1
    store.query.return_value = [chunk]
    llm = MagicMock()
    llm.chat_stream.return_value = iter(["岩", "神"])
    qa = QA(store=store, llm=llm)
    stream = qa.ask_stream("岩神是谁")
    assert "".join(stream) == "岩神"
    assert stream.result.answer == "岩神"
    assert stream.result.sources == [chunk]


def test_answer_stream_result_before_iter_raises():
    stream = AnswerStream(question="谁", sources=[], _tokens=["x"])
    with pytest.raises(RuntimeError, match="尚未结束"):
        _ = stream.result

