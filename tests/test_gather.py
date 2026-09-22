from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from libs.store import Chunk
from models.plan import Aspect, Planner, QueryPlan


def _chunk(doc_id: str, document: str = "", **meta: str) -> Chunk:
    return Chunk(id=doc_id, document=document, metadata=dict(meta), distance=0.1)


def _planner(store: MagicMock, **kwargs: int) -> Planner:
    store.count.return_value = 2
    return Planner(llm=MagicMock(), store=store, **kwargs)


def test_gather_rejects_empty_plans_and_empty_store():
    store = MagicMock()
    store.count.return_value = 0
    planner = Planner(llm=MagicMock(), store=store)
    with pytest.raises(ValueError, match="不能为空"):
        planner.gather([])
    with pytest.raises(RuntimeError, match="向量库为空"):
        planner.gather([QueryPlan("direct", "Archon War")])


def test_gather_direct_one_cell():
    store = MagicMock()
    hit = _chunk("a", "in: Mondstadt\n", title="Archon War")
    store.query.return_value = [hit]
    gathered = _planner(store).gather(
        [QueryPlan("direct", "Archon War Mondstadt")]
    )
    store.query.assert_called_once_with("Archon War Mondstadt", top_k=4)
    assert gathered.chunks == [hit]
    assert len(gathered.cells) == 1
    assert gathered.cells[0][0].query == "Archon War Mondstadt"


def test_gather_nations_hint_is_one_cell():
    store = MagicMock()
    hit = _chunk("a", "# Archon War\n", title="Archon War")
    store.query.return_value = [hit]
    gathered = _planner(store).gather(
        [QueryPlan("direct", "what happened in each nation during the Archon War")]
    )
    store.query.assert_called_once_with(
        "what happened in each nation during the Archon War", top_k=4
    )
    assert len(gathered.cells) == 1
    assert gathered.chunks == [hit]


def test_gather_keeps_store_hits_without_label_filter():
    store = MagicMock()
    funerary = _chunk(
        "f",
        "# War of Funerary Flame\n",
        title="War of Funerary Flame",
        entry="War of Funerary Flame",
    )
    mond = _chunk("m", "# Mondstadt\n", title="Mondstadt", entry="Archon War")
    store.query.return_value = [funerary, mond]
    gathered = _planner(store).gather(
        [
            QueryPlan(
                "multi_aspects",
                "what happened in {0}",
                [Aspect(["Mondstadt"], False)],
            )
        ]
    )
    store.query.assert_called_once_with("what happened in Mondstadt", top_k=4)
    assert [chunk.id for chunk in gathered.cells[0][1]] == ["f", "m"]
    assert [chunk.id for chunk in gathered.chunks] == ["f", "m"]


def test_gather_range_and_two_regions_is_two_cells_not_four():
    store = MagicMock()
    store.query.return_value = [_chunk("x")]
    gathered = _planner(store).gather(
        [
            QueryPlan(
                "multi_aspects",
                "what happened in {0} {1}",
                [
                    Aspect(["Mondstadt", "Inazuma"], False),
                    Aspect(["War of the Pyre", "modern era"], True),
                ],
            )
        ]
    )
    queries = [call.args[0] for call in store.query.call_args_list]
    assert queries == [
        "what happened in Mondstadt from War of the Pyre to modern era",
        "what happened in Inazuma from War of the Pyre to modern era",
    ]
    assert [call.kwargs["top_k"] for call in store.query.call_args_list] == [4, 4]
    assert len(gathered.cells) == 2
    assert all(cell.windows[0].contains for cell, _hits in gathered.cells)
    assert [cell.picks for cell, _hits in gathered.cells] == [
        {"0": "Mondstadt"},
        {"0": "Inazuma"},
    ]


def test_gather_round_robin_dedupes_and_caps():
    store = MagicMock()
    a = _chunk("a")
    d = _chunk("d")
    a_li = _chunk("a")
    b = _chunk("b")
    store.query.side_effect = [[a, d], [a_li, b]]
    gathered = _planner(store, cell_k=4, max_chunks=3).gather(
        [
            QueryPlan(
                "multi_aspects",
                "what happened in {0}",
                [Aspect(["Mondstadt", "Liyue"], False)],
            )
        ]
    )
    assert [chunk.id for chunk in gathered.chunks] == ["a", "d", "b"]


def test_gather_fills_two_discrete_slots():
    store = MagicMock()
    store.query.return_value = [_chunk("x")]
    gathered = _planner(store).gather(
        [
            QueryPlan(
                "multi_aspects",
                "what happened in {1} during {0}",
                [
                    Aspect(["war of funerary flame", "Archon War"], False),
                    Aspect(["Mondstadt", "Inazuma"], False),
                ],
            )
        ]
    )
    queries = [call.args[0] for call in store.query.call_args_list]
    assert queries == [
        "what happened in Mondstadt during war of funerary flame",
        "what happened in Inazuma during war of funerary flame",
        "what happened in Mondstadt during Archon War",
        "what happened in Inazuma during Archon War",
    ]
    assert [call.kwargs["top_k"] for call in store.query.call_args_list] == [4, 4, 4, 4]
    assert len(gathered.cells) == 4
