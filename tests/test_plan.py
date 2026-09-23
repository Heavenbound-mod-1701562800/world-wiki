from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from models.plan import (
    SLOT_PROMPT,
    SPLIT_PROMPT,
    Aspect,
    PlanCell,
    Planner,
    QueryPlan,
    _plan_from_text,
    _questions_from_text,
)

_PAIRS = [
    ("Archon War", "魔神战争"),
    ("Mondstadt", "蒙德"),
    ("Inazuma", "稻妻"),
]


def test_plan_drops_empty_members_keeps_query():
    plans = _plan_from_text(
        '[{"query": "Inazuma geography", "aspects": [{"members": [], "contains": false}]}]',
        _PAIRS,
    )
    assert plans == [QueryPlan("direct", "Inazuma geography", [])]
    assert plans[0].queries() == [PlanCell("Inazuma geography", {}, [])]


def test_plan_nations_hint_stays_in_query():
    plans = _plan_from_text(
        '[{"query": "what happened in each nation during {0}", "aspects": [{"members": ["war of funerary flame", "Archon War", "modern"], "contains": false}]}]',
        _PAIRS,
    )
    assert plans[0].strategy == "multi_aspects"
    assert plans[0].query == "what happened in each nation during {0}"
    assert plans[0].aspects == [
        Aspect(["war of funerary flame", "Archon War", "modern"], False)
    ]
    assert [cell.picks for cell in plans[0].queries()] == [
        {"0": "war of funerary flame"},
        {"0": "Archon War"},
        {"0": "modern"},
    ]
    assert [cell.slots for cell in plans[0].queries()] == [
        ["war of funerary flame"],
        ["Archon War"],
        ["modern"],
    ]


def test_plan_one_place_one_event():
    plans = _plan_from_text(
        '[{"query": "what happened in Mondstadt during the Archon War", "aspects": []}]',
        _PAIRS,
    )
    assert plans == [
        QueryPlan("direct", "what happened in Mondstadt during the Archon War", [])
    ]
    assert plans[0].queries() == [
        PlanCell("what happened in Mondstadt during the Archon War", {}, [])
    ]


def test_plan_two_named_events():
    plans = _plan_from_text(
        '[{"query": "what happened in Mondstadt during {0}", "aspects": [{"members": ["Archon War", "the Fall of the Divine Flame"], "contains": false}]}]',
        _PAIRS,
    )
    assert plans[0].strategy == "multi_aspects"
    assert plans[0].query == "what happened in Mondstadt during {0}"
    assert plans[0].aspects == [
        Aspect(["Archon War", "the Fall of the Divine Flame"], False)
    ]
    assert [cell.picks for cell in plans[0].queries()] == [
        {"0": "Archon War"},
        {"0": "the Fall of the Divine Flame"},
    ]


def test_plan_time_range_is_direct_contains():
    plans = _plan_from_text(
        '[{"query": "what happened in Mondstadt {0}", "aspects": [{"members": ["the Fall of the Divine Flame", "modern times"], "contains": true}]}]',
        _PAIRS,
    )
    window = Aspect(["the Fall of the Divine Flame", "modern times"], True)
    assert plans == [
        QueryPlan("direct", "what happened in Mondstadt {0}", [window])
    ]
    cells = plans[0].queries()
    assert cells == [
        PlanCell(
            "what happened in Mondstadt {0}",
            {},
            [window],
            ["from the Fall of the Divine Flame to modern times"],
        )
    ]


def test_plan_range_and_two_regions_products_regions_not_endpoints():
    plans = _plan_from_text(
        """[{
            "query": "what happened in {0} {1}",
            "aspects": [
                {"members": ["蒙德", "稻妻"], "contains": false},
                {"members": ["War of the Pyre", "modern era"], "contains": true}
            ]
        }]""",
        _PAIRS,
    )
    assert plans[0].strategy == "multi_aspects"
    cells = plans[0].queries()
    assert [cell.picks for cell in cells] == [
        {"0": "Mondstadt"},
        {"0": "Inazuma"},
    ]
    assert all(
        cell.windows == [Aspect(["War of the Pyre", "modern era"], True)]
        for cell in cells
    )
    assert [cell.slots for cell in cells] == [
        ["Mondstadt", "from War of the Pyre to modern era"],
        ["Inazuma", "from War of the Pyre to modern era"],
    ]


def test_plan_two_events_and_two_regions_is_four_cells():
    plans = _plan_from_text(
        """[{
            "query": "what happened in {1} during {0}",
            "aspects": [
                {"members": ["Archon War", "Cataclysm"], "contains": false},
                {"members": ["Mondstadt", "Inazuma"], "contains": false}
            ]
        }]""",
        _PAIRS,
    )
    assert [cell.picks for cell in plans[0].queries()] == [
        {"0": "Archon War", "1": "Mondstadt"},
        {"0": "Archon War", "1": "Inazuma"},
        {"0": "Cataclysm", "1": "Mondstadt"},
        {"0": "Cataclysm", "1": "Inazuma"},
    ]


def test_plan_keeps_single_member_aspect():
    plans = _plan_from_text(
        '[{"query": "what happened in Mondstadt during {0}", "aspects": [{"members": ["Archon War"], "contains": false}]}]',
        _PAIRS,
    )
    assert plans[0].strategy == "multi_aspects"
    assert plans[0].aspects == [Aspect(["Archon War"], False)]
    assert [cell.picks for cell in plans[0].queries()] == [{"0": "Archon War"}]


def test_plan_rejects_placeholder_mismatch():
    with pytest.raises(ValueError, match="占位符"):
        _plan_from_text(
            '[{"query": "what happened in {0} {1}", "aspects": [{"members": ["Mondstadt", "Inazuma"], "contains": false}]}]',
            _PAIRS,
        )


def test_plan_does_not_trim_compound_member():
    plans = _plan_from_text(
        '[{"query": "what happened in Mondstadt during {0}", "aspects": [{"members": ["坎瑞亚灾变", "Archon War"], "contains": false}]}]',
        [("Khaenri'ah", "坎瑞亚"), ("Mondstadt", "蒙德"), ("Archon War", "魔神战争")],
    )
    assert plans[0].aspects[0].members[0] == "坎瑞亚灾变"


def test_plan_two_subquestions():
    plans = _plan_from_text(
        '[{"query": "Archon War", "aspects": [{"members": []}]}, {"query": "Zhongli", "aspects": []}]',
        _PAIRS,
    )
    assert len(plans) == 2
    assert plans[0] == QueryPlan("direct", "Archon War", [])
    assert plans[1] == QueryPlan("direct", "Zhongli", [])


def test_plan_ignores_leftover_facet():
    plans = _plan_from_text(
        '[{"query": "Archon War", "aspects": [{"facet": "nation", "members": [], "contains": false}]}]',
        _PAIRS,
    )
    assert plans[0] == QueryPlan("direct", "Archon War", [])
    assert plans[0].queries() == [PlanCell("Archon War", {}, [])]


def test_plan_splits_single_from_to_member():
    plans = _plan_from_text(
        '[{"query": "what happened in Mondstadt {0}", "aspects": [{"members": ["from war of funerary flame to modern era"], "contains": true}]}]',
        _PAIRS,
    )
    assert plans[0].aspects == [
        Aspect(["war of funerary flame", "modern era"], True)
    ]
    assert plans[0].queries()[0].slots == [
        "from war of funerary flame to modern era"
    ]


def test_plan_single_contains_member_becomes_discrete():
    plans = _plan_from_text(
        '[{"query": "what happened in Mondstadt {0}", "aspects": [{"members": ["Archon War"], "contains": true}]}]',
        _PAIRS,
    )
    assert plans[0].strategy == "multi_aspects"
    assert plans[0].aspects == [Aspect(["Archon War"], False)]


def test_questions_from_text_keeps_original_strings():
    assert _questions_from_text(
        '["魔神战争期间蒙德发生了什么？", "从葬火之战到现代期间蒙德和稻妻都发生了什么？"]'
    ) == [
        "魔神战争期间蒙德发生了什么？",
        "从葬火之战到现代期间蒙德和稻妻都发生了什么？",
    ]


def test_questions_from_text_rejects_plan_objects():
    with pytest.raises(ValueError, match="切问"):
        _questions_from_text('[{"query": "Archon War", "aspects": []}]')


@patch("models.plan.Dictionary")
def test_planner_splits_then_slots_each_question(dictionary):
    dictionary.matches_in.return_value = []
    dictionary.format_glossary.return_value = ""
    first = "魔神战争期间蒙德发生了什么？"
    second = "从葬火之战到现代期间蒙德和稻妻都发生了什么？"
    slot_first = (
        '[{"query": "What happened in Mondstadt during the Archon War?", '
        '"aspects": []}]'
    )
    slot_second = json.dumps(
        [
            {
                "query": "what happened in {0} {1}",
                "aspects": [
                    {
                        "members": ["Mondstadt", "Inazuma"],
                        "contains": False,
                    },
                    {
                        "members": ["war of funerary flame", "modern"],
                        "contains": True,
                    },
                ],
            }
        ]
    )
    llm = MagicMock()

    def chat(messages, **kwargs):
        system = messages[0]["content"]
        user = messages[-1]["content"]
        if system == SPLIT_PROMPT:
            return json.dumps([first, second], ensure_ascii=False)
        assert system == SLOT_PROMPT
        if first in user:
            return slot_first
        if second in user:
            return slot_second
        raise AssertionError(user)

    llm.chat.side_effect = chat
    plans = Planner(llm=llm).plan(first + second)
    assert [plan.query for plan in plans] == [
        "What happened in Mondstadt during the Archon War?",
        "what happened in {0} {1}",
    ]
    assert plans[1].strategy == "multi_aspects"
    assert [cell.picks for cell in plans[1].queries()] == [
        {"0": "Mondstadt"},
        {"0": "Inazuma"},
    ]
    assert llm.chat.call_count == 3
    systems = [call.args[0][0]["content"] for call in llm.chat.call_args_list]
    assert systems[0] == SPLIT_PROMPT
    assert systems.count(SLOT_PROMPT) == 2


@patch("models.plan.Dictionary")
def test_planner_split_invalid_falls_back_to_whole_question(dictionary):
    dictionary.matches_in.return_value = []
    dictionary.format_glossary.return_value = ""
    llm = MagicMock()
    llm.chat.side_effect = [
        "not json",
        "[]",
        '[{"query": "Inazuma geography", "aspects": []}]',
    ]
    plans = Planner(llm=llm).plan("介绍一下稻妻各个岛的地理信息")
    assert plans == [QueryPlan("direct", "Inazuma geography", [])]
    assert llm.chat.call_count == 3
    assert llm.chat.call_args_list[0].args[0][0]["content"] == SPLIT_PROMPT
    assert llm.chat.call_args_list[2].args[0][0]["content"] == SLOT_PROMPT


_BAD_SLOTS = (
    '[{"query": "what happened in {0} {1} {2}", "aspects": '
    '[{"members": ["Mondstadt", "Inazuma"], "contains": false}, '
    '{"members": ["Archon War"], "contains": false}]}]'
)
_GOOD_SLOTS = (
    '[{"query": "what happened in {0} during {1}", "aspects": '
    '[{"members": ["Mondstadt", "Inazuma"], "contains": false}, '
    '{"members": ["Archon War"], "contains": false}]}]'
)


@patch("models.plan.Dictionary")
def test_plan_one_retries_placeholder_mismatch(dictionary):
    dictionary.matches_in.return_value = []
    dictionary.format_glossary.return_value = ""
    llm = MagicMock()
    llm.chat.side_effect = [_BAD_SLOTS, _GOOD_SLOTS]
    plans = Planner(llm=llm).plan_one("蒙德和稻妻在魔神战争期间发生了什么")
    assert plans[0].query == "what happened in {0} during {1}"
    assert llm.chat.call_count == 2
    assert "校验失败" in llm.chat.call_args_list[1].args[0][-1]["content"]


@patch("models.plan.Dictionary")
def test_plan_one_raises_after_three_failed_parses(dictionary):
    dictionary.matches_in.return_value = []
    dictionary.format_glossary.return_value = ""
    llm = MagicMock()
    llm.chat.side_effect = [_BAD_SLOTS, _BAD_SLOTS, _BAD_SLOTS]
    with pytest.raises(ValueError, match="占位符"):
        Planner(llm=llm).plan_one("蒙德和稻妻在魔神战争期间发生了什么")
    assert llm.chat.call_count == 3
