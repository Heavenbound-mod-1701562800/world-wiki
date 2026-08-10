"""RAG 用本地向量库封装（ChromaDB + 火山 Embedding）。"""

from __future__ import annotations

import chromadb
import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

import config
from libs.llm import LLM


@dataclass
class Chunk:
    id: str
    document: str
    metadata: dict[str, Any]
    distance: float | None = None


class Store:
    """面向世界观条目的本地向量检索库。"""

    def __init__(
        self,
        collection_name: str = "genshin_lore",
        persist_dir: str | Path | None = None,
        llm: Optional[LLM] = None,
    ) -> None:

        self.persist_dir = Path(persist_dir or config.VECTOR_STORE_DIR)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.llm = llm or LLM()
        self._client = chromadb.PersistentClient(path=str(self.persist_dir))
        self.collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    @staticmethod
    def make_id(*parts: str) -> str:
        raw = "||".join(parts)
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
        return f"{digest}-{uuid.uuid4().hex[:8]}"

    def count(self) -> int:
        return self.collection.count()

    def get_metadatas(self, ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        """按 id 批量读取 metadata；不存在的 id 不会出现在结果里。"""
        id_list = list(ids)
        if not id_list:
            return {}
        result = self.collection.get(ids=id_list, include=["metadatas"])
        found_ids = result.get("ids") or []
        metas = result.get("metadatas") or []
        out: dict[str, dict[str, Any]] = {}
        for i, doc_id in enumerate(found_ids):
            out[doc_id] = dict(metas[i] or {})
        return out

    def upsert(
        self,
        documents: Sequence[str],
        ids: Optional[Sequence[str]] = None,
        metadatas: Optional[Sequence[dict[str, Any]]] = None,
        embeddings: Optional[Sequence[Sequence[float]]] = None,
    ) -> list[str]:
        """写入/更新文档。未提供 embedding 时自动调用火山向量模型。"""
        docs = list(documents)
        if not docs:
            return []

        final_ids = list(ids) if ids is not None else [
            self.make_id(doc[:80]) for doc in docs
        ]
        if len(final_ids) != len(docs):
            raise ValueError("ids 数量必须与 documents 一致")

        final_metas: list[dict[str, Any]]
        if metadatas is None:
            final_metas = [{} for _ in docs]
        else:
            final_metas = [dict(m) for m in metadatas]
            if len(final_metas) != len(docs):
                raise ValueError("metadatas 数量必须与 documents 一致")

        if embeddings is None:
            vectors = self.llm.embed(docs)
        else:
            vectors = [list(v) for v in embeddings]
            if len(vectors) != len(docs):
                raise ValueError("embeddings 数量必须与 documents 一致")

        self.collection.upsert(
            ids=final_ids,
            documents=docs,
            metadatas=final_metas,
            embeddings=vectors,
        )
        return final_ids

    def query(
        self,
        text: str,
        top_k: int = 5,
        where: Optional[dict[str, Any]] = None,
    ) -> list[Chunk]:
        """语义检索。"""
        if not text.strip():
            return []

        vectors = self.llm.embed(text)
        if not vectors:
            return []
        result = self.collection.query(
            query_embeddings=[vectors[0]],
            n_results=top_k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )

        ids = (result.get("ids") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]

        chunks: list[Chunk] = []
        for i, doc_id in enumerate(ids):
            chunks.append(
                Chunk(
                    id=doc_id,
                    document=documents[i] or "",
                    metadata=dict(metadatas[i] or {}),
                    distance=distances[i] if i < len(distances) else None,
                )
            )
        return chunks

    def delete(
        self,
        ids: Optional[Sequence[str]] = None,
        where: Optional[dict[str, Any]] = None,
    ) -> None:
        if ids is None and where is None:
            raise ValueError("删除时必须提供 ids 或 where")
        kwargs: dict[str, Any] = {}
        if ids is not None:
            kwargs["ids"] = list(ids)
        if where is not None:
            kwargs["where"] = where
        self.collection.delete(**kwargs)

    def reset(self) -> None:
        name = self.collection.name
        self._client.delete_collection(name)
        self.collection = self._client.get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine"},
        )
