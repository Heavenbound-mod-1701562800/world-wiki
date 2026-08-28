from __future__ import annotations

from unittest.mock import MagicMock, patch

from libs.crawler import CrawlerError, FandomWikiCrawler
from libs.utils import clean_text
from models.dictionary import Dictionary, _expand_slash_pairs, _zh_from_other_languages

_TRI_WT = """
==Other Languages==
{{Other Languages
|en      = Tri-Commission
|zhs     = 三奉行
|zhs_rm  = Sān-fèngxíng
|zht     = 三奉行
|zh_tl   = Three Bugyou
}}
"""

_XIAO_WT = """
==Other Languages==
{{Other Languages
|default_hidden = 1
|1_en     = Xiao
|1_zhs    = 魈
|1_zhs_rm = Xiāo
|2_en     = Alatus
|2_zhs    = 金鹏
|2_zhs_rm = Jīnpéng
}}
"""


def _json_response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.json.return_value = payload
    return response


def test_clean_text_strips_quotes_punct_and_space():
    assert clean_text('  "Zhongli"  ') == "Zhongli"
    assert clean_text("「钟离」。") == "钟离"
    assert clean_text('"Xiao!"') == "Xiao"
    assert clean_text("Khaenri'ah") == "Khaenri'ah"
    assert clean_text(None) == ""


def test_add_local_cleans_and_skips_short():
    assert Dictionary.add_local('  "Raiden"  ') is True
    row = Dictionary.get(Dictionary.en == "Raiden")
    assert row.zh == ""
    assert row.source == Dictionary.Source.MANUAL
    assert Dictionary.add_local("ab") is False
    assert Dictionary.add_local("raiden") is False


def test_add_local_skips_not_proper():
    Dictionary.create(en="Vision", zh="", source=Dictionary.Source.NOT_PROPER)
    assert Dictionary.add_local("Vision") is False
    row = Dictionary.get(Dictionary.en == "Vision")
    assert row.source == Dictionary.Source.NOT_PROPER


def test_matches_in_skips_url_empty_zh_and_not_proper():
    Dictionary.create(en="Zhongli", zh="钟离", source=Dictionary.Source.GENSHIN_DICTIONARY)
    Dictionary.create(en="Inazuma", zh="稻妻", source=Dictionary.Source.GENSHIN_DICTIONARY)
    Dictionary.create(en="Ghost", zh="", source=Dictionary.Source.MANUAL)
    Dictionary.create(en="Vision", zh="神之眼", source=Dictionary.Source.NOT_PROPER)
    text = (
        "zhongli visited Inazuman lands. Vision. "
        "See https://genshin-impact.fandom.com/wiki/Inazuma and Ghost."
    )
    hits = Dictionary.matches_in(text)
    names = {en for en, _ in hits}
    assert "Zhongli" in names
    assert "Inazuma" not in names
    assert "Ghost" not in names
    assert "Vision" not in names
    assert Dictionary.to_zh("Vision") is None


def test_matches_in_equates_dashes_and_spaces():
    Dictionary.create(
        en="Entombed City - Ancient Palace",
        zh="雪葬之都·旧宫",
        source=Dictionary.Source.GENSHIN_DICTIONARY,
    )
    Dictionary.create(
        en="Khaenri'ah",
        zh="坎瑞亚",
        source=Dictionary.Source.GENSHIN_DICTIONARY,
    )
    hits = dict(
        Dictionary.matches_in(
            "near the Entombed City Ancient Palace and Entombed City – Ancient Palace."
        )
    )
    assert hits["Entombed City - Ancient Palace"] == "雪葬之都·旧宫"
    hits_hyphen = dict(
        Dictionary.matches_in("the Entombed City-Ancient Palace ruins")
    )
    assert hits_hyphen["Entombed City - Ancient Palace"] == "雪葬之都·旧宫"
    assert dict(Dictionary.matches_in("Khaenriah fell.")) == {}
    assert dict(Dictionary.matches_in("Khaenri'ah fell."))["Khaenri'ah"] == "坎瑞亚"


def test_matches_in_equates_dashed_text_to_spaced_entry():
    Dictionary.create(
        en="Entombed City Ancient Palace",
        zh="雪葬之都·旧宫",
        source=Dictionary.Source.GENSHIN_DICTIONARY,
    )
    hits = dict(Dictionary.matches_in("the Entombed City - Ancient Palace"))
    assert hits["Entombed City Ancient Palace"] == "雪葬之都·旧宫"


def test_matches_in_equates_underscore_not_period():
    Dictionary.create(
        en="Grand Narukami Shrine",
        zh="鸣神大社",
        source=Dictionary.Source.GENSHIN_DICTIONARY,
    )
    hits = dict(Dictionary.matches_in("at the Grand_Narukami_Shrine gate"))
    assert hits["Grand Narukami Shrine"] == "鸣神大社"
    assert dict(Dictionary.matches_in("Grand.Narukami Shrine")) == {}
    assert dict(Dictionary.matches_in("Grand, Narukami Shrine")) == {}


def test_matches_in_finds_zh_ignores_isolators_not_punct():
    Dictionary.create(
        en="Sacred Sakura",
        zh="神樱树",
        source=Dictionary.Source.GENSHIN_DICTIONARY,
    )
    Dictionary.create(
        en="Zhongli",
        zh="钟离",
        source=Dictionary.Source.GENSHIN_DICTIONARY,
    )
    Dictionary.create(
        en="Entombed City - Ancient Palace",
        zh="雪葬之都·旧宫",
        source=Dictionary.Source.GENSHIN_DICTIONARY,
    )
    assert dict(Dictionary.matches_in("门口的神 樱 树很大"))["Sacred Sakura"] == "神樱树"
    assert dict(Dictionary.matches_in("神-樱树"))["Sacred Sakura"] == "神樱树"
    assert dict(Dictionary.matches_in("雪葬之都旧宫"))[
        "Entombed City - Ancient Palace"
    ] == "雪葬之都·旧宫"
    assert dict(Dictionary.matches_in("「钟离」是谁"))["Zhongli"] == "钟离"
    assert dict(Dictionary.matches_in("神樱。树")) == {}
    assert dict(Dictionary.matches_in("「神樱」树")) == {}
    assert dict(Dictionary.matches_in("岩")) == {}


def test_sync_replaces_genshin_keeps_unmatched_upgrades_not_proper():
    Dictionary.create(en="Old", zh="旧", source=Dictionary.Source.GENSHIN_DICTIONARY)
    Dictionary.create(en="KeepMe", zh="", source=Dictionary.Source.MANUAL)
    Dictionary.create(en="upgrade", zh="", source=Dictionary.Source.MANUAL)
    Dictionary.create(en="SkipMe", zh="跳过", source=Dictionary.Source.NOT_PROPER)
    Dictionary.create(en="StayOut", zh="留下", source=Dictionary.Source.NOT_PROPER)

    payload = [
        {"en": "Zhongli", "zhCN": "钟离"},
        {"en": " \"Upgrade\" ", "zhCN": "「升级」"},
        {"en": "SkipMe", "zhCN": "官中"},
    ]
    fake = MagicMock()
    fake.raise_for_status = MagicMock()
    fake.json.return_value = payload

    with patch("models.dictionary.requests.get", return_value=fake):
        n = Dictionary.sync()

    assert n == 3
    assert Dictionary.get_or_none(Dictionary.en == "Old") is None
    local = Dictionary.get(Dictionary.en == "KeepMe")
    assert local.source == Dictionary.Source.MANUAL
    upgraded = Dictionary.get(Dictionary.en == "Upgrade")
    assert upgraded.zh == "升级"
    assert upgraded.source == Dictionary.Source.GENSHIN_DICTIONARY
    official = Dictionary.get(Dictionary.en == "SkipMe")
    assert official.source == Dictionary.Source.GENSHIN_DICTIONARY
    assert official.zh == "官中"
    leftover = Dictionary.get(Dictionary.en == "StayOut")
    assert leftover.source == Dictionary.Source.NOT_PROPER
    assert leftover.zh == "留下"
    assert Dictionary.to_zh("SkipMe") == "官中"
    assert Dictionary.to_zh("Zhongli") == "钟离"


def test_expand_slash_pairs_cartesian_and_keeps_50_50():
    assert _expand_slash_pairs("Zhongli", "钟离") == [("Zhongli", "钟离")]
    assert _expand_slash_pairs("comrade / partner", "伙伴 / 搭档") == [
        ("comrade", "伙伴"),
        ("comrade", "搭档"),
        ("partner", "伙伴"),
        ("partner", "搭档"),
    ]
    assert _expand_slash_pairs("milady / my lady", "小姐") == [
        ("milady", "小姐"),
        ("my lady", "小姐"),
    ]
    assert _expand_slash_pairs("Chihu Rock", "吃虎岩 / 螭虎岩") == [
        ("Chihu Rock", "吃虎岩"),
        ("Chihu Rock", "螭虎岩"),
    ]
    assert _expand_slash_pairs("lose 50/50", "（小保底）歪了") == [
        ("lose 50/50", "（小保底）歪了"),
    ]


def test_sync_expands_slash_aliases():
    Dictionary.create(
        en="comrade / partner",
        zh="伙伴 / 搭档",
        source=Dictionary.Source.GENSHIN_DICTIONARY,
    )
    payload = [
        {"en": "comrade / partner", "zhCN": "伙伴 / 搭档"},
        {"en": "Chihu Rock", "zhCN": "吃虎岩 / 螭虎岩"},
        {"en": "milady / my lady", "zhCN": "小姐"},
        {"en": "lose 50/50", "zhCN": "（小保底）歪了"},
    ]
    fake = MagicMock()
    fake.raise_for_status = MagicMock()
    fake.json.return_value = payload

    with patch("models.dictionary.requests.get", return_value=fake):
        n = Dictionary.sync()

    assert n == 9
    pairs = {(row.en, row.zh) for row in Dictionary.select()}
    assert pairs == {
        ("comrade", "伙伴"),
        ("comrade", "搭档"),
        ("partner", "伙伴"),
        ("partner", "搭档"),
        ("Chihu Rock", "吃虎岩"),
        ("Chihu Rock", "螭虎岩"),
        ("milady", "小姐"),
        ("my lady", "小姐"),
        ("lose 50/50", "（小保底）歪了"),
    }
    assert Dictionary.to_zh("comrade") in {"伙伴", "搭档"}
    assert Dictionary.to_zh("partner") in {"伙伴", "搭档"}
    hits = {
        en: zh
        for en, zh in Dictionary.matches_in(
            "A comrade and partner met at Chihu Rock."
        )
    }
    assert hits["comrade"] == "伙伴 / 搭档"
    assert hits["partner"] == "伙伴 / 搭档"
    assert hits["Chihu Rock"] == "吃虎岩 / 螭虎岩"
    assert "lose" not in hits


def test_zh_from_other_languages_single_and_multi():
    assert _zh_from_other_languages(_TRI_WT, "Tri-Commission") == "三奉行"
    assert _zh_from_other_languages(_XIAO_WT, "Alatus") == "金鹏"
    assert _zh_from_other_languages(_XIAO_WT, "Xiao") == "魈"
    assert _zh_from_other_languages(_XIAO_WT, "Zhongli") is None
    assert _zh_from_other_languages("==Trivia==\nNo table.", "Xiao") is None


def test_lookup_wiki_zh_exact_title():
    crawler = MagicMock()

    def fake_get(_url, *, params=None, **_kwargs):
        action = (params or {}).get("action")
        if action == "query" and "titles" in (params or {}):
            title = params["titles"]
            return _json_response(
                {"query": {"pages": {"1": {"pageid": 1, "title": title}}}}
            )
        if action == "parse":
            return _json_response(
                {"parse": {"title": params["page"], "wikitext": {"*": _TRI_WT}}}
            )
        raise AssertionError(params)

    crawler.get.side_effect = fake_get
    wiki = FandomWikiCrawler(crawler=crawler)
    assert Dictionary.lookup_wiki_zh("Tri-Commission!", crawler=wiki) == "三奉行"


def test_lookup_wiki_zh_search_fallback_multi_group():
    crawler = MagicMock()

    def fake_get(_url, *, params=None, **_kwargs):
        action = (params or {}).get("action")
        if action == "query" and params.get("list") == "search":
            return _json_response({"query": {"search": [{"title": "Xiao"}]}})
        if action == "query" and "titles" in (params or {}):
            title = params["titles"]
            if title == "Alatus":
                return _json_response(
                    {"query": {"pages": {"-1": {"title": title, "missing": ""}}}}
                )
            return _json_response(
                {"query": {"pages": {"1": {"pageid": 1, "title": title}}}}
            )
        if action == "parse":
            return _json_response(
                {"parse": {"title": params["page"], "wikitext": {"*": _XIAO_WT}}}
            )
        raise AssertionError(params)

    crawler.get.side_effect = fake_get
    wiki = FandomWikiCrawler(crawler=crawler)
    assert Dictionary.lookup_wiki_zh("Alatus", crawler=wiki) == "金鹏"


def test_fill_from_wiki_writes_source_wiki():
    Dictionary.create(en="Alatus", zh="", source=Dictionary.Source.MANUAL)
    wiki = MagicMock()
    wiki.resolve_title.return_value = "Xiao"
    wiki.fetch_wikitext.return_value = _XIAO_WT
    assert Dictionary.fill_from_wiki("Alatus", crawler=wiki) is True
    row = Dictionary.get(Dictionary.en == "Alatus")
    assert row.zh == "金鹏"
    assert row.source == Dictionary.Source.WIKI


def test_fill_from_wiki_miss_keeps_manual():
    Dictionary.create(en="Nope", zh="", source=Dictionary.Source.MANUAL)
    wiki = MagicMock()
    wiki.resolve_title.return_value = None
    assert Dictionary.fill_from_wiki("Nope", crawler=wiki) is False
    row = Dictionary.get(Dictionary.en == "Nope")
    assert row.zh == ""
    assert row.source == Dictionary.Source.MANUAL


def test_lookup_wiki_zh_empty_and_crawler_error():
    assert Dictionary.lookup_wiki_zh("  ") is None
    wiki = MagicMock()
    wiki.resolve_title.side_effect = CrawlerError("timeout")
    assert Dictionary.lookup_wiki_zh("Xiao", crawler=wiki) is None


def test_fill_from_wiki_overwrites_not_proper():
    Dictionary.create(en="Vision", zh="", source=Dictionary.Source.NOT_PROPER)
    wiki = MagicMock()
    wiki.resolve_title.return_value = "Vision"
    wiki.fetch_wikitext.return_value = (
        "{{Other Languages\n|en = Vision\n|zhs = 神之眼\n}}"
    )
    assert Dictionary.fill_from_wiki("Vision", crawler=wiki) is True
    row = Dictionary.get(Dictionary.en == "Vision")
    assert row.source == Dictionary.Source.WIKI
    assert row.zh == "神之眼"


def test_fill_from_wiki_skips_genshin_dictionary():
    Dictionary.create(
        en="Zhongli", zh="钟离", source=Dictionary.Source.GENSHIN_DICTIONARY
    )
    wiki = MagicMock()
    wiki.resolve_title.return_value = "Zhongli"
    wiki.fetch_wikitext.return_value = (
        "{{Other Languages\n|en = Zhongli\n|zhs = 岩王帝君\n}}"
    )
    assert Dictionary.fill_from_wiki("Zhongli", crawler=wiki) is False
    row = Dictionary.get(Dictionary.en == "Zhongli")
    assert row.source == Dictionary.Source.GENSHIN_DICTIONARY
    assert row.zh == "钟离"


def test_add_from_wiki_pairs_expands_slash_and_skips_source1():
    Dictionary.create(
        en="Zhongli", zh="钟离", source=Dictionary.Source.GENSHIN_DICTIONARY
    )
    Dictionary.create(en="Ghost", zh="", source=Dictionary.Source.MANUAL)
    n = Dictionary.add_from_wiki_pairs(
        [
            ("Zhongli", "岩王帝君"),
            ("comrade / partner", "伙伴 / 搭档"),
            ("Ghost", "鬼"),
        ]
    )
    assert n == 5
    assert Dictionary.get(Dictionary.en == "Zhongli").zh == "钟离"
    assert Dictionary.get(Dictionary.en == "Zhongli").source == (
        Dictionary.Source.GENSHIN_DICTIONARY
    )
    ghost = Dictionary.get(Dictionary.en == "Ghost")
    assert ghost.zh == "鬼"
    assert ghost.source == Dictionary.Source.WIKI
    pairs = {(row.en, row.zh) for row in Dictionary.select() if row.en != "Zhongli"}
    assert ("comrade", "伙伴") in pairs
    assert ("partner", "搭档") in pairs
    assert ("Ghost", "鬼") in pairs


def test_fill_from_wiki_creates_missing_row():
    wiki = MagicMock()
    wiki.resolve_title.return_value = "Xiao"
    wiki.fetch_wikitext.return_value = _XIAO_WT
    assert Dictionary.fill_from_wiki("Xiao", crawler=wiki) is True
    row = Dictionary.get(Dictionary.en == "Xiao")
    assert row.zh == "魈"
    assert row.source == Dictionary.Source.WIKI


def test_resolve_title_missing_and_empty_search():
    crawler = MagicMock()

    def fake_get(_url, *, params=None, **_kwargs):
        if (params or {}).get("list") == "search":
            return _json_response({"query": {"search": []}})
        return _json_response(
            {"query": {"pages": {"-1": {"title": "Nope", "missing": ""}}}}
        )

    crawler.get.side_effect = fake_get
    assert FandomWikiCrawler(crawler=crawler).resolve_title("Nope") is None


def test_search_requires_two_chars_and_paginates():
    Dictionary.create(en="Zhongli", zh="钟离", source=Dictionary.Source.GENSHIN_DICTIONARY)
    Dictionary.create(en="Xiao", zh="魈", source=Dictionary.Source.WIKI)
    Dictionary.create(en="Alatus", zh="金鹏", source=Dictionary.Source.MANUAL)
    rows, total = Dictionary.search("x")
    assert rows == []
    assert total == 0
    rows, total = Dictionary.search("ia")
    assert total == 1
    assert rows[0].en == "Xiao"
    rows, total = Dictionary.search("at")
    assert total == 1
    assert rows[0].en == "Alatus"
    rows, total = Dictionary.search("钟离")
    assert total == 1
    assert rows[0].en == "Zhongli"
    page, total = Dictionary.search("a", offset=0, limit=1)
    assert total == 0
    page, total = Dictionary.search("at", offset=0, limit=1)
    assert total == 1
    assert page[0].en == "Alatus"


def test_update_entry_and_lookup_many():
    Dictionary.create(en="Xiao", zh="", source=Dictionary.Source.MANUAL)
    assert Dictionary.update_entry("nope", zh="x") is False
    assert Dictionary.update_entry("Xiao", zh="魈", source=Dictionary.Source.WIKI) is True
    row = Dictionary.get(Dictionary.en == "Xiao")
    assert row.zh == "魈"
    assert row.source == Dictionary.Source.WIKI

    wiki = MagicMock()
    wiki.resolve_title.return_value = None
    filled, missed = Dictionary.lookup_many(["Nope", "  "], crawler=wiki)
    assert filled == []
    assert missed == ["Nope"]
