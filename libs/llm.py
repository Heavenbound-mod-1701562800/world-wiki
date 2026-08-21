"""火山方舟大模型 API 封装（OpenAI 兼容协议 + 多模态 Embedding）。"""

from __future__ import annotations

import logging
from typing import Any, Generator, Iterable, Optional, Sequence

import httpx
from openai import APIConnectionError, APITimeoutError, OpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

import config
from libs.task_queue import llm_queue

logger = logging.getLogger(__name__)

_MULTIMODAL_MARKERS = ("embedding-vision", "embedding_vision", "multimodal")


class LLM:
    """调用火山方舟 Chat / Embedding 接口。"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        chat_model: Optional[str] = None,
        embedding_model: Optional[str] = None,
        timeout: float | None = None,
    ) -> None:
        self.api_key = api_key or config.require_ark_api_key()
        self.base_url = (base_url or config.ARK_BASE_URL).rstrip("/")
        self.chat_model = chat_model or config.ARK_CHAT_MODEL
        self.embedding_model = embedding_model or config.ARK_EMBEDDING_MODEL
        self.timeout = timeout if timeout is not None else config.LLM_TIMEOUT
        # 火山 API 走直连；不要跟着 Windows/v2ray 系统代理（否则会卡在代理上）
        self._http_client = httpx.Client(
            timeout=httpx.Timeout(self.timeout, connect=20.0),
            proxy=None,
            trust_env=False,
        )
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            http_client=self._http_client,
            timeout=self.timeout,
            max_retries=2,
        )

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(
            (APITimeoutError, APIConnectionError, httpx.TimeoutException)
        ),
    )
    def chat(
        self,
        messages: list[dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> str:
        """非流式对话，返回 assistant 文本。"""
        model_name = model or self.chat_model
        params: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        params.update(kwargs)

        completion = self.client.chat.completions.create(**params)
        content = completion.choices[0].message.content
        return content or ""

    def chat_stream(
        self,
        messages: list[dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> Generator[str, None, None]:
        """流式对话，逐段 yield 文本 delta。"""
        model_name = model or self.chat_model
        logger.info("LLM chat_stream model=%s messages=%d", model_name, len(messages))
        params: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        params.update(kwargs)

        stream = self.client.chat.completions.create(**params)
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    def embed(
        self,
        texts: str | Iterable[str],
        model: Optional[str] = None,
    ) -> list[list[float]]:
        """文本向量化，返回与输入顺序一致的 embedding 列表。"""
        if isinstance(texts, str):
            inputs = [texts]
        else:
            inputs = list(texts)
        if not inputs:
            return []

        model_name = model or self.embedding_model
        if self._is_multimodal(model_name):
            # multimodal 一次融成一个向量：每条文本丢进 llm TaskQueue
            queue = llm_queue()
            futures = [
                queue.submit(
                    self.embed_multi,
                    [{"type": "text", "text": text}],
                    model=model_name,
                )
                for text in inputs
            ]
            return queue.gather(futures)

        response = self.client.embeddings.create(
            model=model_name,
            input=inputs,
            encoding_format="float",
        )
        sorted_data = sorted(response.data, key=lambda item: item.index)
        return [item.embedding for item in sorted_data]

    def embed_multi(
        self,
        parts: Sequence[dict[str, Any]],
        *,
        model: Optional[str] = None,
        dimensions: Optional[int] = None,
    ) -> list[float]:
        """
        多模态向量化（/embeddings/multimodal）。

        part 示例：
          {"type": "text", "text": "..."}
          {"type": "image_url", "image_url": {"url": "https://..."}}
        同一请求内多个 part 融成一个向量。
        """
        model_name = model or self.embedding_model
        payload: dict[str, Any] = {
            "model": model_name,
            "encoding_format": "float",
            "input": list(parts),
        }
        if dimensions is not None:
            payload["dimensions"] = dimensions

        url = f"{self.base_url}/embeddings/multimodal"
        with httpx.Client(timeout=self.timeout, proxy=None, trust_env=False) as client:
            response = client.post(
                url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if response.status_code >= 400:
            raise RuntimeError(
                f"embed_multi 失败 ({response.status_code}): {response.text}"
            )

        data = response.json().get("data")
        if isinstance(data, dict):
            embedding = data.get("embedding")
        elif isinstance(data, list) and data:
            embedding = data[0].get("embedding")
        else:
            embedding = None

        if not embedding:
            raise RuntimeError(f"embed_multi 响应缺少向量: {response.text[:300]}")
        return list(embedding)

    @staticmethod
    def _is_multimodal(model: str) -> bool:
        name = model.lower()
        return any(marker in name for marker in _MULTIMODAL_MARKERS)
