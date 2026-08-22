from __future__ import annotations

import pytest

from models import ask, summarize


def test_summarize_rejects_empty_and_missing_file(tmp_path):
    with pytest.raises(ValueError, match="请提供"):
        summarize([])
    missing = tmp_path / "nope.html"
    with pytest.raises(FileNotFoundError):
        summarize(missing)


def test_ask_rejects_empty_question():
    with pytest.raises(ValueError, match="请提供问题"):
        ask("   ")
