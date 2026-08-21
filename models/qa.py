"""基于已入库资料回答世界观问题。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Generator, Optional

from libs.llm import LLM
from libs.store import Chunk, Store

SYSTEM_PROMPT = """你是原神世界观解释助手。
只能依据给定资料回答，不要编造资料中没有的设定。
资料中带〔出处〕或 NPC Dialogue 的内容，只是该角色/文本的说法，回答时必须限定为「据 NPC … 所述」，不得说成确定的世界设定。
无出处的条目可按资料中的设定陈述。
若资料不足，明确说明「根据现有资料无法确定」，可简要指出缺什么信息。
若能回答，不要说「根据某某文档」这样的句子，直接回答。
回答使用简洁中文，必要时分点。"""


@dataclass
class Answer:
    question: str
    answer: str
    sources: list[Chunk] = field(default_factory=list)


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
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"问题：{q}\n\n资料：\n{context}\n\n请依据资料回答。",
            },
        ]
        return q, sources, messages

    def ask(self, question: str, *, top_k: int | None = None) -> Answer:
        """检索相关总结后生成回答（非流式）。"""
        q, sources, messages = self._prepare(question, top_k=top_k)
        assert self.llm is not None
        answer = self.llm.chat(messages, temperature=0.2).strip()
        return Answer(question=q, answer=answer, sources=sources)

    def ask_stream(
        self, question: str, *, top_k: int | None = None
    ) -> Generator[str, None, Answer]:
        """
        流式作答：yield 文本 delta；生成器 return 值为完整 Answer。

        用法：
          gen = qa.ask_stream(q)
          for delta in gen: ...
          answer = gen 的 StopIteration.value，或用以下包装收集。
        """
        q, sources, messages = self._prepare(question, top_k=top_k)
        assert self.llm is not None
        parts: list[str] = []
        for delta in self.llm.chat_stream(messages, temperature=0.2):
            parts.append(delta)
            yield delta
        return Answer(question=q, answer="".join(parts).strip(), sources=sources)

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
