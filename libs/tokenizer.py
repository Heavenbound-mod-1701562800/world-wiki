"""jieba 封装：只定义 Tokenizer 类，不创建实例。

要用 tokenizer：`from models.dictionary import get_tokenizer`，不要在这里或别处自己 new。
"""

from __future__ import annotations

import logging
from typing import Iterable

import jieba
from jieba import Tokenizer as JiebaTokenizer

jieba.setLogLevel(logging.WARNING)


class Tokenizer:
    def __init__(self) -> None:
        self._inst = JiebaTokenizer()
        self._words: frozenset[str] | None = None

    def reload(self, words: Iterable[str]) -> None:
        tok = JiebaTokenizer()
        kept = {word for word in words if word}
        for word in kept:
            tok.add_word(word)
        self._inst = tok
        self._words = frozenset(kept)

    def ensure(self, words: Iterable[str]) -> None:
        frozen = frozenset(word for word in words if word)
        if frozen == self._words:
            return
        self.reload(frozen)

    def cut(self, text: str) -> list[str]:
        return self._inst.lcut(text, HMM=False) if text else []
