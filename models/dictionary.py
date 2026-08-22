"""中英词表：Genshin Dictionary words.json → peewee Dictionary。"""

from __future__ import annotations

import logging
import re
from enum import IntEnum
from typing import Any, Optional

import requests
from peewee import SmallIntegerField, TextField

import config
from libs.db import BaseModel, database

logger = logging.getLogger(__name__)

WORDS_JSON_URL = "https://dataset.genshin-dictionary.com/words.json"
_MIN_EN_LEN = 3
_MAX_EN_LEN = 64
_URL_RE = re.compile(r"https?://[^\s\]）>\"']+", re.IGNORECASE)
# 只剥两侧：英文/中文/直角（方）引号，不含中间的撇号
_EDGE_QUOTES = (
    "\"'"
    "“”„‟«»"
    "‘’‚‛‹›"
    "「」『』｢｣"
    "〝〞〟"
    "＂＇"
)


def _text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().strip(_EDGE_QUOTES).strip()


class Dictionary(BaseModel):
    """data/pages.sqlite 上的中英专名。"""

    class Source(IntEnum):
        """1=Genshin Dictionary，2=总结时本地补录。"""

        GENSHIN_DICTIONARY = 1
        LOCAL = 2

    en = TextField(index=True)
    zh = TextField(default="")
    source = SmallIntegerField(default=Source.GENSHIN_DICTIONARY, index=True)

    class Meta:
        table_name = "dictionary"
        primary_key = False

    def __repr__(self) -> str:
        return f"<Dictionary en='{self.en}', zh='{self.zh}', source={self.source}>"

    @classmethod
    def count(cls) -> int:
        """词条总数。"""
        return cls.select().count()  # pylint: disable=no-value-for-parameter

    @classmethod
    def sync(cls) -> int:
        """拉取 words.json，只替换 source=1；本地补录保留，命中官中则升级。"""
        rows = cls._fetch_rows()
        incoming = {en.casefold(): (en, zh) for en, zh in rows}
        with database.atomic():
            by_fold: dict[str, Dictionary] = {}
            for row in cls.select():
                fold = _text(row.en).casefold()
                if fold and fold not in by_fold:
                    by_fold[fold] = row
            for fold, (en, zh) in incoming.items():
                existing = by_fold.get(fold)
                if existing is None:
                    cls.create(en=en, zh=zh, source=cls.Source.GENSHIN_DICTIONARY)
                    continue
                existing.en = en
                existing.zh = zh
                existing.source = cls.Source.GENSHIN_DICTIONARY
                existing.save()
            stale = [
                row
                for row in cls.select()
                if row.source == cls.Source.GENSHIN_DICTIONARY
                and _text(row.en).casefold() not in incoming
            ]
            if stale:
                cls.delete().where(
                    cls.en.in_([row.en for row in stale]),
                    cls.source == cls.Source.GENSHIN_DICTIONARY,
                ).execute()
        logger.info("词表已同步：%d 条官中（%s）", len(rows), WORDS_JSON_URL)
        return len(rows)

    @classmethod
    def add_local(cls, en: str) -> bool:
        """清洗后写入 source=2、zh 空；已有相同 en（大小写不敏感）则跳过。"""
        key = _text(en)
        if not _MIN_EN_LEN <= len(key) <= _MAX_EN_LEN:
            return False
        fold = key.casefold()
        for row in cls.select(cls.en):
            if _text(row.en).casefold() == fold:
                return False
        cls.create(en=key, zh="", source=cls.Source.LOCAL)
        return True

    @classmethod
    def to_zh(cls, en: str) -> Optional[str]:
        """英文专名 → 中文；没有或未译则 None。"""
        key = _text(en)
        if not key:
            return None
        row = cls.get_or_none(cls.en == key)
        if row is None or not row.zh:
            return None
        return str(row.zh)

    @classmethod
    def to_en(cls, zh: str) -> Optional[str]:
        key = _text(zh)
        if not key:
            return None
        row = cls.get_or_none(cls.zh == key)
        return str(row.en) if row else None

    @classmethod
    def matches_in(cls, text: str) -> list[tuple[str, str]]:
        """正文里出现过的专名（大小写不敏感）；不含 URL；跳过未译。"""
        haystack = _URL_RE.sub(" ", text or "").casefold()
        by_fold: dict[str, tuple[str, str]] = {}
        for row in cls.select(cls.en, cls.zh):
            en = _text(row.en)
            zh = _text(row.zh)
            if not zh or not _MIN_EN_LEN <= len(en) <= _MAX_EN_LEN:
                continue
            folded = en.casefold()
            if folded not in by_fold:
                by_fold[folded] = (en, zh)

        hits: dict[str, tuple[str, str]] = {}
        hlen = len(haystack)
        for i in range(hlen):
            if i > 0 and haystack[i - 1].isascii() and haystack[i - 1].isalpha():
                continue
            max_n = min(_MAX_EN_LEN, hlen - i)
            for n in range(max_n, _MIN_EN_LEN - 1, -1):
                j = i + n
                if j < hlen and haystack[j].isascii() and haystack[j].isalpha():
                    continue
                found = by_fold.get(haystack[i:j])
                if found:
                    hits[found[0].casefold()] = found

        return sorted(hits.values(), key=lambda item: len(item[0]), reverse=True)

    @classmethod
    def _fetch_rows(cls) -> list[tuple[str, str]]:
        response = requests.get(
            WORDS_JSON_URL,
            timeout=config.HTTP_TIMEOUT,
            proxies=config.http_proxies(),
            headers={"User-Agent": config.DEFAULT_USER_AGENT},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError(f"词表格式异常：期望 JSON 数组，实际 {type(payload).__name__}")

        seen: set[tuple[str, str]] = set()
        rows: list[tuple[str, str]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            en = _text(item.get("en"))
            zh = _text(item.get("zhCN"))
            if not en or not zh:
                continue
            pair = (en, zh)
            if pair in seen:
                continue
            seen.add(pair)
            rows.append(pair)
        if not rows:
            raise RuntimeError("词表为空：words.json 中没有同时含 en / zhCN 的条目")
        return rows
