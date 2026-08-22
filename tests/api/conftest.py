"""Flask 测试客户端；不打真实 LLM / Wiki / Chroma。"""

from __future__ import annotations

import pytest

from api.app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client
