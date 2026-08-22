from __future__ import annotations

from unittest.mock import MagicMock, patch

from models.dictionary import Dictionary, _text


def test_text_strips_quotes_and_space():
    assert _text('  "Zhongli"  ') == "Zhongli"
    assert _text("「钟离」") == "钟离"
    assert _text(None) == ""


def test_add_local_cleans_and_skips_short():
    assert Dictionary.add_local('  "Raiden"  ') is True
    row = Dictionary.get(Dictionary.en == "Raiden")
    assert row.zh == ""
    assert row.source == Dictionary.Source.LOCAL
    assert Dictionary.add_local("ab") is False
    assert Dictionary.add_local("raiden") is False


def test_matches_in_word_boundary_skips_url_and_empty_zh():
    Dictionary.create(en="Zhongli", zh="钟离", source=Dictionary.Source.GENSHIN_DICTIONARY)
    Dictionary.create(en="Inazuma", zh="稻妻", source=Dictionary.Source.GENSHIN_DICTIONARY)
    Dictionary.create(en="Ghost", zh="", source=Dictionary.Source.LOCAL)
    text = (
        "zhongli visited Inazuman lands. "
        "See https://genshin-impact.fandom.com/wiki/Inazuma and Ghost."
    )
    hits = Dictionary.matches_in(text)
    names = {en for en, _ in hits}
    assert "Zhongli" in names
    assert "Inazuma" not in names
    assert "Ghost" not in names


def test_sync_replaces_genshin_keeps_local():
    Dictionary.create(en="Old", zh="旧", source=Dictionary.Source.GENSHIN_DICTIONARY)
    Dictionary.create(en="KeepMe", zh="", source=Dictionary.Source.LOCAL)
    Dictionary.create(en="upgrade", zh="", source=Dictionary.Source.LOCAL)

    payload = [
        {"en": "Zhongli", "zhCN": "钟离"},
        {"en": " \"Upgrade\" ", "zhCN": "「升级」"},
    ]
    fake = MagicMock()
    fake.raise_for_status = MagicMock()
    fake.json.return_value = payload

    with patch("models.dictionary.requests.get", return_value=fake):
        n = Dictionary.sync()

    assert n == 2
    assert Dictionary.get_or_none(Dictionary.en == "Old") is None
    local = Dictionary.get(Dictionary.en == "KeepMe")
    assert local.source == Dictionary.Source.LOCAL
    upgraded = Dictionary.get(Dictionary.en == "Upgrade")
    assert upgraded.zh == "升级"
    assert upgraded.source == Dictionary.Source.GENSHIN_DICTIONARY
    assert Dictionary.to_zh("Zhongli") == "钟离"
