from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from libs.llm import LLM


def _llm() -> LLM:
    obj = object.__new__(LLM)
    obj.api_key = "test-key"
    obj.base_url = "https://example.test/api/v3"
    obj.embedding_model = "doubao-embedding-vision-test"
    obj.timeout = 5.0
    return obj


def _client(posts: list[MagicMock]) -> MagicMock:
    client = MagicMock()
    client.post.side_effect = posts
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    return client


def _ok() -> MagicMock:
    response = MagicMock(status_code=200, text="ok")
    response.json.return_value = {"data": {"embedding": [0.1, 0.2]}}
    return response


def _fail(status: int) -> MagicMock:
    response = MagicMock(status_code=status, text="slow down")
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "rate limited", request=MagicMock(), response=response
    )
    return response


def test_embed_multi_retries_429_then_succeeds():
    client = _client([_fail(429), _ok()])
    with (
        patch("libs.llm.httpx.Client", return_value=client),
        patch("tenacity.nap.sleep"),
    ):
        out = _llm().embed_multi([{"type": "text", "text": "hi"}])
    assert out == [0.1, 0.2]
    assert client.post.call_count == 2


def test_embed_multi_gives_up_after_three_tries():
    client = _client([_fail(429), _fail(429), _fail(429)])
    with (
        patch("libs.llm.httpx.Client", return_value=client),
        patch("tenacity.nap.sleep"),
    ):
        with pytest.raises(httpx.HTTPStatusError):
            _llm().embed_multi([{"type": "text", "text": "hi"}])
    assert client.post.call_count == 3
