"""Query-broadening tests.

The cheatsheet instructs the model to loosen an over-tight query, but the model
never sees a result count so it cannot follow that instruction. The harness
does it instead. The invariant that matters most here: broadening relaxes only
the model's discretionary constraints, NEVER colour identity or format, so it
can't leak an illegal card into the pool.
"""

import pytest

from app.pipeline.broaden import (
    has_intent,
    relax_once,
    search_with_broadening,
)


def test_drops_mana_value_bound_first():
    q = "id<=rakdos otag:ramp mv<=3 f:commander"
    relaxed, dropped = relax_once(q)
    assert "mv<=3" not in relaxed
    assert dropped == "mana value bound"
    assert "id<=rakdos" in relaxed
    assert "f:commander" in relaxed


def test_never_drops_identity_or_format():
    """The legality filters must survive every relaxation round."""
    q = "id<=rakdos otag:ramp mv<=3 keyword:flying pow>=4 r:rare f:commander"
    for _ in range(6):
        step = relax_once(q)
        if step is None:
            break
        q = step[0]
        assert "id<=rakdos" in q
        assert "f:commander" in q


def test_relaxation_order():
    """mv, then keyword, then pow/tou, then otag, then rarity."""
    q = "id<=br t:creature mv<=4 keyword:flying pow>=3 otag:ramp r:rare f:commander"
    order = []
    while True:
        step = relax_once(q)
        if step is None:
            break
        q, dropped = step
        order.append(dropped)
    assert order == [
        "mana value bound",
        "keyword filter",
        "power/toughness bound",
        "oracle tag",
        "rarity filter",
    ]


def test_returns_none_when_nothing_relaxable():
    assert relax_once("id<=rakdos f:commander") is None


def test_handles_cmc_and_manavalue_aliases():
    for term in ("cmc<=3", "manavalue>=6", "mv=2"):
        q = f"id<=br {term} f:commander"
        relaxed, _ = relax_once(q)
        assert term not in relaxed


def test_does_not_corrupt_grouped_syntax():
    """Removing a term must not leave unbalanced parens behind."""
    q = "id<=br t:creature (keyword:trample or keyword:flying) mv>=6 f:commander"
    relaxed, _ = relax_once(q)
    assert relaxed.count("(") == relaxed.count(")")


def test_search_stops_when_query_fills():
    calls = []

    def search(q):
        calls.append(q)
        return [{"name": f"c{i}"} for i in range(20)]

    results, trail = search_with_broadening(search, "id<=br otag:ramp mv<=2 f:commander")
    assert len(results) == 20
    assert calls == ["id<=br otag:ramp mv<=2 f:commander"]
    assert trail == ["id<=br otag:ramp mv<=2 f:commander"]


def test_search_broadens_when_underfilled():
    """The whole point: a too-tight query recovers instead of contributing nothing."""
    def search(q):
        if "mv<=1" in q:
            return [{"name": "only-one"}]
        return [{"name": f"c{i}"} for i in range(12)]

    results, trail = search_with_broadening(
        search, "id<=br otag:ramp mv<=1 f:commander", min_hits=8
    )
    assert len(results) == 12
    assert len(trail) == 2


def test_search_keeps_largest_result_set():
    """A relaxation can return fewer cards; never take a worse pool than one
    already in hand."""
    def search(q):
        if "mv<=2" in q:
            return [{"name": f"c{i}"} for i in range(5)]
        return [{"name": "worse"}]

    results, _ = search_with_broadening(
        search, "id<=br otag:ramp mv<=2 f:commander", min_hits=8
    )
    assert len(results) == 5


def test_search_respects_max_rounds():
    calls = []

    def search(q):
        calls.append(q)
        return []

    search_with_broadening(
        search,
        "id<=br t:creature mv<=4 keyword:flying pow>=3 otag:ramp r:rare f:commander",
        min_hits=8,
        max_rounds=2,
    )
    # Original query plus at most 2 relaxation rounds.
    assert len(calls) == 3


def test_search_terminates_when_nothing_left_to_relax():
    calls = []

    def search(q):
        calls.append(q)
        return []

    results, trail = search_with_broadening(
        search, "id<=br f:commander", min_hits=8, max_rounds=5
    )
    assert results == []
    assert calls == ["id<=br f:commander"]


def test_dropping_otag_recovers_an_invented_tag():
    """Tagger slugs are not guessable (the vocabulary is a hierarchy and the
    bulk export ships only leaves), so a model inventing a plausible tag name
    is a normal failure that returns exactly zero cards. Dropping the tag
    recovers the query as long as some intent survives."""
    def search(q):
        if "otag:" in q:
            return []
        return [{"name": f"c{i}"} for i in range(10)]

    results, trail = search_with_broadening(
        search, "id<=br otag:invented-tag t:creature f:commander", min_hits=8
    )
    assert len(results) == 10
    assert "otag:" not in trail[-1]
    assert "t:creature" in trail[-1]


def test_never_relaxes_past_the_last_intent_term():
    """A query relaxed down to bare legality is no longer a search for anything.

    Verified live 2026-08-15: `id<=br f:commander` returns Sol Ring and Command
    Tower — real cards that match the colours and have nothing to do with the
    request. Contributing nothing beats contributing confident noise.
    """
    searched = []

    def search(q):
        searched.append(q)
        return []

    results, trail = search_with_broadening(
        search, "id<=br otag:invented-tag f:commander", min_hits=8
    )
    assert results == []
    # Only the original ran; relaxing the otag would have left no intent.
    assert searched == ["id<=br otag:invented-tag f:commander"]
    assert trail == ["id<=br otag:invented-tag f:commander"]


@pytest.mark.parametrize("query,expected", [
    ("id<=br f:commander", False),
    ("id<=br otag:ramp f:commander", True),
    ("id<=br t:creature f:commander", True),
    ('id<=br o:"draw a card" f:commander', True),
    ("id<=br is:dual f:commander", True),
    ("id<=br produces:c f:commander", True),
    ("id<=br mv<=3 f:commander", False),
    ("id<=br r:rare f:commander", False),
])
def test_has_intent(query, expected):
    """Only terms describing what a card DOES count as intent. A mana-value or
    rarity bound narrows the pool but doesn't say what is being looked for."""
    assert has_intent(query) is expected
