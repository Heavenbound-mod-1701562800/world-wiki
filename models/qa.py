"""基于已入库资料回答世界观问题。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Optional

from libs.llm import LLM
from libs.store import Chunk, Store
from models.dictionary import Dictionary

SYSTEM_PROMPT = """你是原神世界观解释助手。
只能依据给定资料回答，不要编造资料中没有的设定。
资料是英文原文；「专名对照」给出已有中文译名。回答用简洁中文，专名必须用对照表中的译名；对照表没有的专名保持英文，禁止自行音译。
资料中带〔出处〕或 NPC Dialogue 的内容，只是该角色/文本的说法，回答时必须限定为「据 NPC … 所述」，不得说成确定的世界设定。
无出处的条目可按资料中的设定陈述。
若资料不足，明确说明「根据现有资料无法确定」，可简要指出缺什么信息。
若能回答，不要说「根据某某文档」这样的句子，直接回答。
必要时分点。"""


@dataclass
class Answer:
    """一次问答：问题、回答、检索到的资料块。"""

    question: str
    answer: str
    sources: list[Chunk] = field(default_factory=list)

    def source_dicts(self) -> list[dict]:
        """把 sources 编成 API / SSE 用的字典列表。"""
        return [
            {
                "id": chunk.id,
                "document": chunk.document,
                "metadata": chunk.metadata,
                "distance": chunk.distance,
            }
            for chunk in self.sources
        ]


@dataclass
class AnswerStream:
    """流式作答：for 循环拿 token，结束后 .result 是 Answer。"""

    question: str
    sources: list[Chunk]
    _tokens: Iterable[str] = field(repr=False)
    _parts: list[str] = field(default_factory=list, init=False, repr=False)
    _done: bool = field(default=False, init=False, repr=False)

    def __iter__(self) -> Iterator[str]:
        self._parts.clear()
        self._done = False
        for delta in self._tokens:
            self._parts.append(delta)
            yield delta
        self._done = True

    @property
    def result(self) -> Answer:
        """完整回答；须先把 token 迭代完。"""
        if not self._done:
            raise RuntimeError("流式回答尚未结束")
        return Answer(
            question=self.question,
            answer="".join(self._parts).strip(),
            sources=self.sources,
        )


@dataclass
class QA:
    """检索 + LLM 作答（不负责入库）。"""

    store: Store = field(default_factory=Store)
    llm: Optional[LLM] = None
    top_k: int = 5

    def __post_init__(self) -> None:
        if self.llm is None:
            self.llm = self.store.llm

    def _prepare(
        self, question: str, *, top_k: int | None = None
    ) -> tuple[str, list[Chunk], list[dict[str, str]]]:
        q = question.strip()
        if not q:
            raise ValueError("问题不能为空")
        if self.store.count() == 0:
            raise RuntimeError("向量库为空。请先运行 --ingest。")

        sources = self.store.query(q, top_k=top_k or self.top_k)
        context = self._format_context(sources)
        blob = "\n\n".join(chunk.document or "" for chunk in sources)
        glossary = Dictionary.matches_in(blob)
        if glossary:
            glossary_block = "\n".join(f"{en} → {zh}" for en, zh in glossary)
        else:
            glossary_block = "（无）"
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"问题：{q}\n\n"
                    f"专名对照：\n{glossary_block}\n\n"
                    f"资料：\n{context}\n\n"
                    "请依据资料回答。"
                ),
            },
        ]
        return q, sources, messages

    def ask(self, question: str, *, top_k: int | None = None) -> Answer:
        """检索相关资料后生成回答（非流式）。"""
        q, sources, messages = self._prepare(question, top_k=top_k)
        assert self.llm is not None
        answer = self.llm.chat(messages, temperature=0.2).strip()
        return Answer(question=q, answer=answer, sources=sources)

    def ask_stream(
        self, question: str, *, top_k: int | None = None
    ) -> AnswerStream:
        """检索后流式生成；迭代 token，结束后读 stream.result。"""
        q, sources, messages = self._prepare(question, top_k=top_k)
        assert self.llm is not None
        return AnswerStream(
            question=q,
            sources=sources,
            _tokens=self.llm.chat_stream(messages, temperature=0.2),
        )

    @staticmethod
    def _format_context(chunks: list[Chunk]) -> str:
        if not chunks:
            return "（无相关资料）"
        parts: list[str] = []
        for i, chunk in enumerate(chunks, 1):
            label = (
                chunk.metadata.get("label")
                or chunk.metadata.get("title")
                or chunk.metadata.get("filename")
                or "未命名"
            )
            body = re.sub(r"\n{3,}", "\n\n", chunk.document).strip()
            parts.append(f"[{i}] {label}\n{body}")
        return "\n\n---\n\n".join(parts)
