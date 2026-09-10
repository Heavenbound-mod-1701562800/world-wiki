"""中英词表：Genshin Dictionary words.json → peewee Dictionary。"""

from __future__ import annotations

import logging
import re
from enum import IntEnum
from typing import Any, Iterable, Optional

import requests
from peewee import SmallIntegerField, TextField

import config
from libs.crawler import CrawlerError, FandomWikiCrawler
from libs.db import BaseModel, database
from libs.tokenizer import Tokenizer
from libs.utils import clean_text

logger = logging.getLogger(__name__)

WORDS_JSON_URL = "https://dataset.genshin-dictionary.com/words.json"
_MIN_EN_LEN = 2
_MAX_EN_LEN = 64
_MIN_ZH_LEN = 1
_MAX_ZH_LEN = 64
_URL_RE = re.compile(r"https?://[^\s\]）>\"']+", re.IGNORECASE)
_SLASH_ALT_RE = re.compile(r"\s+/\s+")
_SOFT_SEP_RE = re.compile(r"[-–—−‐‑_]+")
_ZH_ISOLATOR_RE = re.compile(r"[-–—−‐‑_·・\s]+")
_SPACE_RE = re.compile(r"\s+")
_OL_PARAM_RE = re.compile(
    r"^(?:(\d+)_)?([A-Za-z0-9]+)\s*=\s*(.*)$",
    re.DOTALL,
)
# 句读/引号为硬边界；ASCII 撇号不算引号（Khaenri'ah）
_BOUNDARIES = frozenset(
    ".:,;。，、：；"
    "\"“”„‟«»"
    "「」『』｢｣"
    "〝〞〟＂"
)


class Dictionary(BaseModel):
    """data/pages.sqlite 上的中英专名。"""

    class Source(IntEnum):
        """词条来源。覆盖优先级 1 > 2 > 3 = -1"""

        NOT_PROPER = -1
        GENSHIN_DICTIONARY = 1
        WIKI = 2
        MANUAL = 3

    # 3 与 -1 同级；数值本身不是优先级
    _SOURCE_RANK = {
        Source.GENSHIN_DICTIONARY: 2,
        Source.WIKI: 1,
        Source.MANUAL: 0,
        Source.NOT_PROPER: 0,
    }

    en = TextField(index=True)
    zh = TextField(default="")
    source = SmallIntegerField(default=Source.GENSHIN_DICTIONARY, index=True)

    class Meta:
        table_name = "dictionary"
        primary_key = False

    def __repr__(self) -> str:
        return f"<Dictionary en='{self.en}', zh='{self.zh}', source={self.source}>"

    @classmethod
    def sync(cls) -> int:
        """拉取 words.json。命中则写成 source=1（覆盖 2/3/-1）；未命中的 2/3/-1 保留。

        en/zh 里用空格+/+空格分隔的是别名，展开为笛卡尔积：任一侧都能对译到另一侧任一。
        """
        rows = cls._fetch_rows()
        incoming_folds = {en.casefold() for en, _ in rows}
        with database.atomic():
            cls.delete().where(  # pylint: disable=no-value-for-parameter
                cls.source == cls.Source.GENSHIN_DICTIONARY
            ).execute()
            covered = [
                row.en
                for row in cls.select()
                if clean_text(row.en).casefold() in incoming_folds
            ]
            if covered:
                cls.delete().where(  # pylint: disable=no-value-for-parameter
                    cls.en.in_(covered)
                ).execute()
            for en, zh in rows:
                cls.create(en=en, zh=zh, source=cls.Source.GENSHIN_DICTIONARY)
        logger.info("词表已同步：%d 条官中（%s）", len(rows), WORDS_JSON_URL)
        return len(rows)

    @classmethod
    def add(
        cls,
        pairs: Iterable[tuple[str, str]],
        *,
        source: int = Source.MANUAL,
        strict: bool = True,
    ) -> tuple[int, list[str]]:
        """清洗后写成指定 source（默认 3）。

        strict=True：任一对无效则整批不写。strict=False：无效对丢掉，其余照写。
        已有同行则按覆盖规则：能覆盖才写（空 zh 可补译），否则跳过。
        en/zh 里用空格+/+空格分隔的别名展开为笛卡尔积。
        """
        parsed = cls._coerce_source(source)
        prepared: list[tuple[str, str]] = []
        for i, item in enumerate(pairs, start=1):
            try:
                prepared.extend(cls._expand_valid_pair(item, index=i))
            except ValueError:
                if strict:
                    raise
        added = 0
        skipped: list[str] = []
        with database.atomic():
            for en, zh in prepared:
                if cls._add(en, zh, parsed):
                    added += 1
                else:
                    skipped.append(en)
        return added, skipped

    @staticmethod
    def _expand_valid_pair(item: Any, *, index: int) -> list[tuple[str, str]]:
        """清洗并展开一对；无效则 ValueError。"""
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            raise ValueError(f"第 {index} 对无效")
        en = clean_text(item[0])
        zh = clean_text(item[1])
        if not en or not zh:
            raise ValueError(f"第 {index} 对无效：中英文须都填写")
        expanded = _expand_slash_pairs(en, zh)
        if not expanded:
            raise ValueError(f"第 {index} 对无效：中英文须都填写")
        prepared: list[tuple[str, str]] = []
        for item_en, item_zh in expanded:
            if not _MIN_EN_LEN <= len(item_en) <= _MAX_EN_LEN:
                raise ValueError(
                    f"第 {index} 对无效：英文长度须为 {_MIN_EN_LEN}–{_MAX_EN_LEN}"
                )
            if not item_zh or len(item_zh) > _MAX_ZH_LEN:
                raise ValueError(
                    f"第 {index} 对无效：中文长度须为 1–{_MAX_ZH_LEN}"
                )
            prepared.append((item_en, item_zh))
        return prepared

    @classmethod
    def _coerce_source(cls, source: Any) -> int:
        """source 须为 -1 / 1 / 2 / 3。"""
        try:
            parsed = int(source)
        except (TypeError, ValueError) as exc:
            raise ValueError("source 无效") from exc
        if parsed not in {
            cls.Source.NOT_PROPER,
            cls.Source.GENSHIN_DICTIONARY,
            cls.Source.WIKI,
            cls.Source.MANUAL,
        }:
            raise ValueError("source 无效")
        return parsed

    @classmethod
    def search(
        cls, q: str, *, offset: int = 0, limit: int = 50
    ) -> tuple[list["Dictionary"], int]:
        """en/zh 包含 q（大小写不敏感）；q 不足 2 字返回空。"""
        needle = clean_text(q).casefold()
        if len(needle) < 2:
            return [], 0
        hits = [
            row
            for row in cls.select()
            if needle in clean_text(row.en).casefold()
            or needle in clean_text(row.zh).casefold()
        ]
        hits.sort(key=lambda row: clean_text(row.en).casefold())
        start = max(offset, 0)
        size = max(limit, 1)
        return hits[start : start + size], len(hits)

    @classmethod
    def update_entry(
        cls,
        en: str,
        *,
        zh: Optional[str] = None,
        source: Optional[int] = None,
    ) -> bool:
        """按清洗后的 en 改第一行的 zh/source。"""
        key = clean_text(en)
        if not key:
            return False
        fold = key.casefold()
        updates: dict[str, Any] = {}
        if zh is not None:
            updates["zh"] = clean_text(zh)
        if source is not None:
            updates["source"] = cls._coerce_source(source)
        for row in cls.select():
            if clean_text(row.en).casefold() != fold:
                continue
            if updates:
                cls.update(**updates).where(
                    cls.en == row.en,
                    cls.source == row.source,
                ).execute()
            return True
        return False

    @classmethod
    def lookup_many(
        cls, terms: Iterable[str], *, crawler: Any = None
    ) -> tuple[list[str], list[str]]:
        """批量 fill_from_wiki；共用同一个 crawler。"""
        wiki = crawler or FandomWikiCrawler()
        filled: list[str] = []
        missed: list[str] = []
        seen: set[str] = set()
        for term in terms:
            key = clean_text(term)
            if not key or key.casefold() in seen:
                continue
            seen.add(key.casefold())
            if cls.fill_from_wiki(key, crawler=wiki):
                filled.append(key)
            else:
                missed.append(key)
        return filled, missed

    @classmethod
    def lookup_wiki_zh(cls, en: str, *, crawler: Any = None) -> Optional[str]:
        """查 Genshin Wiki Other Languages；组内 en 对上才返回 zhs。"""
        key = clean_text(en)
        if not key:
            return None
        wiki = crawler or FandomWikiCrawler()
        try:
            title = wiki.resolve_title(key)
            if not title:
                return None
            wikitext = wiki.fetch_wikitext(title)
        except CrawlerError as exc:
            logger.error("Wiki 补译失败：%s (%s)", key, exc)
            return None
        return _zh_from_other_languages(wikitext, key)

    @classmethod
    def fill_from_wiki(cls, en: str, *, crawler: Any = None) -> bool:
        """查到中文则写成 source=2（覆盖 3/-1，刷新 2）；不降级 1。查不到不改。"""
        zh = cls.lookup_wiki_zh(en, crawler=crawler)
        if not zh:
            return False
        key = clean_text(en)
        fold = key.casefold()
        for row in cls.select():
            if clean_text(row.en).casefold() != fold:
                continue
            if not cls._can_overwrite(cls.Source.WIKI, row.source):
                return False
            # 表无主键，save() 会再插一行
            cls.update(zh=zh, source=cls.Source.WIKI).where(
                cls.en == row.en,
                cls.source == row.source,
            ).execute()
            return True
        cls.create(en=key, zh=zh, source=cls.Source.WIKI)
        return True

    @classmethod
    def _add(cls, en: str, zh: str, source: int) -> bool:
        """同 en+zh 或空 zh 则写成 source；已有更高级则跳过，否则新插一行。"""
        fold = en.casefold()
        same = [
            row
            for row in cls.select()
            if clean_text(row.en).casefold() == fold
        ]
        for row in same:
            if clean_text(row.zh) == zh:
                if not cls._can_overwrite(source, row.source):
                    return False
                cls.update(en=en, zh=zh, source=source).where(
                    cls.en == row.en,
                    cls.source == row.source,
                    cls.zh == row.zh,
                ).execute()
                return True
        for row in same:
            if not clean_text(row.zh) and cls._can_overwrite(source, row.source):
                cls.update(zh=zh, source=source).where(
                    cls.en == row.en,
                    cls.source == row.source,
                ).execute()
                return True
        if any(not cls._can_overwrite(source, row.source) for row in same):
            return False
        cls.create(en=en, zh=zh, source=source)
        return True

    @classmethod
    def _can_overwrite(cls, incoming: int, existing: int) -> bool:
        """同级只允许刷新同一 source；更低级不能覆盖更高级。"""
        if incoming == existing:
            return True
        return cls._SOURCE_RANK.get(incoming, -1) > cls._SOURCE_RANK.get(
            existing, -1
        )

    @classmethod
    def to_zh(cls, en: str) -> Optional[str]:
        """英文专名 → 中文；没有、未译或 -1 则 None。"""
        key = clean_text(en)
        if not key:
            return None
        # 同一 en 可能对应多个 zh（别名笛卡尔积）
        row = cls.select().where(cls.en == key).first()
        if (
            row is None
            or not row.zh
            or row.source == cls.Source.NOT_PROPER
        ):
            return None
        return str(row.zh)

    @classmethod
    def matches_in(cls, text: str) -> list[tuple[str, str]]:
        """正文里出现过的专名（en 或 zh）；不含 URL；跳过未译和 -1。"""
        raw = _URL_RE.sub(" ", text or "")
        by_en, by_zh = cls._match_indexes()
        hits: dict[str, tuple[str, str]] = {}
        _scan_en(raw, by_en, hits)
        _scan_zh(raw, by_zh, hits)
        return sorted(hits.values(), key=lambda item: len(item[0]), reverse=True)

    @classmethod
    def _match_indexes(
        cls,
    ) -> tuple[
        dict[str, tuple[str, list[str]]],
        dict[str, list[tuple[str, list[str]]]],
    ]:
        """同一批行：规范化 en / zh 各做一份内存索引。"""
        by_en: dict[str, tuple[str, list[str]]] = {}
        by_zh: dict[str, list[tuple[str, list[str]]]] = {}
        for row in cls.select(cls.en, cls.zh, cls.source):
            if row.source == cls.Source.NOT_PROPER:
                continue
            en = clean_text(row.en)
            zh = clean_text(row.zh)
            if not zh or not _MIN_EN_LEN <= len(en) <= _MAX_EN_LEN:
                continue
            en_key = _en_key(en)
            if not _MIN_EN_LEN <= len(en_key) <= _MAX_EN_LEN:
                continue
            record = by_en.setdefault(en_key, (en, []))
            if zh not in record[1]:
                record[1].append(zh)
            zh_key = _zh_key(zh)
            if _MIN_ZH_LEN <= len(zh_key) <= _MAX_ZH_LEN:
                bucket = by_zh.setdefault(zh_key, [])
                if record not in bucket:
                    bucket.append(record)
        return by_en, by_zh

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
            en = clean_text(item.get("en"))
            zh = clean_text(item.get("zhCN"))
            if not en or not zh:
                continue
            for pair in _expand_slash_pairs(en, zh):
                if pair in seen:
                    continue
                seen.add(pair)
                rows.append(pair)
        if not rows:
            raise RuntimeError("词表为空：words.json 中没有同时含 en / zhCN 的条目")
        return rows


def _en_key(text: str) -> str:
    """英文匹配：大小写不敏感；下划线/连字符/空白收成单空格。撇号保留。"""
    collapsed = _SOFT_SEP_RE.sub(" ", (text or "").casefold())
    return _SPACE_RE.sub(" ", collapsed).strip()


def _zh_key(text: str) -> str:
    """中文匹配：去掉空白、下划线、连字符、间隔号。"""
    return _ZH_ISOLATOR_RE.sub("", text or "")


def _scan_en(
    text: str,
    by_en: dict[str, tuple[str, list[str]]],
    hits: dict[str, tuple[str, str]],
) -> None:
    haystack = _en_key(text)
    hlen = len(haystack)
    for i in range(hlen):
        if haystack[i] in _BOUNDARIES:
            continue
        if i > 0 and haystack[i - 1].isascii() and haystack[i - 1].isalpha():
            continue
        max_n = min(_MAX_EN_LEN, hlen - i)
        for n in range(max_n, _MIN_EN_LEN - 1, -1):
            j = i + n
            if j < hlen and haystack[j].isascii() and haystack[j].isalpha():
                continue
            window = haystack[i:j]
            if any(ch in _BOUNDARIES for ch in window):
                continue
            found = by_en.get(window)
            if found:
                hits[found[0].casefold()] = (found[0], " / ".join(found[1]))


def _scan_zh(
    text: str,
    by_zh: dict[str, list[tuple[str, list[str]]]],
    hits: dict[str, tuple[str, str]],
) -> None:
    haystack = _zh_key(text)
    if not haystack:
        return
    for token in get_tokenizer().cut(haystack):
        if not token or any(ch in _BOUNDARIES for ch in token):
            continue
        found = by_zh.get(token)
        if found:
            for en, zhs in found:
                hits[en.casefold()] = (en, " / ".join(zhs))


def _expand_slash_pairs(en: str, zh: str) -> list[tuple[str, str]]:
    """'A / B' × '甲 / 乙'：任一侧都能对译到另一侧任一。'50/50' 不含空格，不拆。"""
    ens = [clean_text(part) for part in _SLASH_ALT_RE.split(en)]
    zhs = [clean_text(part) for part in _SLASH_ALT_RE.split(zh)]
    ens = [part for part in ens if part]
    zhs = [part for part in zhs if part]
    if not ens or not zhs:
        return []
    return [(e, z) for e in ens for z in zhs]


def zh_terms() -> set[str]:
    """当前表里两字及以上的中文专名（原样 + 去隔离符）。"""
    words: set[str] = set()
    for row in Dictionary.select(Dictionary.zh, Dictionary.source):
        if row.source == Dictionary.Source.NOT_PROPER:
            continue
        for part in _SLASH_ALT_RE.split(row.zh or ""):
            part = clean_text(part)
            keyed = _zh_key(part)
            if len(keyed) < 2:
                continue
            words.update((part, keyed))
    return words


def _extract_template(wikitext: str, name: str) -> Optional[str]:
    """取出 {{name ...}} 的内部（含嵌套模板）。"""
    match = re.compile(
        r"\{\{\s*" + re.escape(name) + r"\b",
        re.IGNORECASE,
    ).search(wikitext or "")
    if not match:
        return None
    depth = 0
    i = match.start()
    while i < len(wikitext):
        if wikitext.startswith("{{", i):
            depth += 1
            i += 2
            continue
        if wikitext.startswith("}}", i):
            depth -= 1
            i += 2
            if depth == 0:
                return wikitext[match.start() + 2 : i - 2]
            continue
        i += 1
    return None


def _split_template_params(body: str) -> list[str]:
    """按顶层 | 切开模板参数。"""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    i = 0
    while i < len(body):
        if body.startswith("{{", i):
            depth += 1
            buf.append("{{")
            i += 2
            continue
        if body.startswith("}}", i):
            depth -= 1
            buf.append("}}")
            i += 2
            continue
        if body[i] == "|" and depth == 0:
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(body[i])
        i += 1
    if buf:
        parts.append("".join(buf))
    return parts


def _zh_from_other_languages(wikitext: str, en: str) -> Optional[str]:
    """Other Languages 里 en 对上的那一组的 zhs。"""
    inner = _extract_template(wikitext, "Other Languages")
    if inner is None:
        return None
    want = clean_text(en).casefold()
    groups: dict[str, dict[str, str]] = {}
    order: list[str] = []
    for raw in _split_template_params(inner):
        match = _OL_PARAM_RE.match(raw.strip())
        if not match:
            continue
        prefix = match.group(1) or ""
        key = match.group(2)
        if key not in {"en", "zhs"}:
            continue
        if prefix not in groups:
            groups[prefix] = {}
            order.append(prefix)
        groups[prefix][key] = match.group(3).strip()
    for prefix in order:
        group = groups[prefix]
        if clean_text(group.get("en", "")).casefold() != want:
            continue
        zh = clean_text(group.get("zhs", ""))
        if zh:
            return zh
    return None


tokenizer: Tokenizer | None = None  # pylint: disable=invalid-name


def get_tokenizer() -> Tokenizer:
    """对外唯一入口。单例；灌入当前词表的两字及以上中文专名。"""
    global tokenizer  # pylint: disable=global-statement
    if tokenizer is None:
        tokenizer = Tokenizer()
    tokenizer.ensure(zh_terms())
    return tokenizer
