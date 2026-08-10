"""基于已入库资料回答世界观问题。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from libs.llm import LLM
from libs.store import Chunk, Store

SYSTEM_PROMPT = """你是原神世界观解释助手。
只能依据给定资料回答，不要编造资料中没有的设定。
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

    def ask(self, question: str, *, top_k: int | None = None) -> Answer:
        """检索相关总结后生成回答。"""
        q = question.strip()
        if not q:
            raise ValueError("问题不能为空")
        if self.store.count() == 0:
            raise RuntimeError("向量库为空。请先运行 --ingest。")

        sources = self.store.query(q, top_k=top_k or self.top_k)
        context = self._format_context(sources)
        assert self.llm is not None
        answer = self.llm.chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"问题：{q}\n\n资料：\n{context}\n\n请依据资料回答。",
                },
            ],
            temperature=0.2,
        ).strip()
        return Answer(question=q, answer=answer, sources=sources)

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
