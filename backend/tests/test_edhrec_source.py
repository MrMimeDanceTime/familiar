"""EDHREC-as-a-source tests.

Stage 2 used to fetch the commander's page only to annotate cards Scryfall had
already returned, so a card EDHREC recommends that no query matched could not
enter the pool at all. And ``shaping._STRIP_FIELDS`` dropped the annotation
before stage 4 read it, so the selection model never saw a play rate either.

The load-bearing detail: ``inclusion`` is a raw deck COUNT, not a rate. It
equals ``num_decks`` exactly, and ``potential_decks`` varies per card, so
ranking on it buries new cards. The rate is computed here.
"""

import pytest

from app.pipeline.edhrec_source import (
    EdhrecCard,
    collect,
    hydrate,
    rank,
)


def _view(name, synergy, num_decks, potential=20000):
    return {
        "name": name,
        "synergy": synergy,
        "inclusion": num_decks,  # EDHREC sets this equal to num_decks
        "num_decks": num_decks,
        "potential_decks": potential,
    }


def _recs(**lists):
    return {
        "commander": "Korvold, Fae-Cursed King",
        "categories": {
            tag: {"header": tag.title(), "cards": cards}
            for tag, cards in lists.items()
        },
    }


class FakeStore:
    def __init__(self, cards):
        self._cards = {k.lower(): v for k, v in cards.items()}

    def by_names(self, names):
        return {
            n.lower(): self._cards[n.lower()]
            for n in names if n.lower() in self._cards
        }


def _card(name, identity=("B", "R")):
    return {
        "name": name,
        "oracle_id": f"oid-{name.lower().replace(' ', '-')}",
        "color_identity": list(identity),
        "type_line": "Creature",
        "cmc": 3.0,
    }


# ── The rate, which is the thing `inclusion` is not ──────────────────────


def test_inclusion_rate_is_computed_not_read():
    """`inclusion` equals num_decks (a count). The rate needs the denominator."""
    card = EdhrecCard(
        name="Mayhem Devil", synergy=0.48, num_decks=15343,
        potential_decks=20489, category="High Synergy", signal="high-synergy",
    )
    assert card.inclusion_rate == pytest.approx(0.7488, abs=0.001)


def test_rate_accounts_for_a_smaller_eligible_window():
    """A new card in 95 of 2,893 eligible decks is played MORE often than an old
    card in 500 of 20,000 — the raw count says the opposite."""
    new_card = EdhrecCard("New", 0.1, 95, 2893, "c", "s")
    old_card = EdhrecCard("Old", 0.1, 500, 20000, "c", "s")
    assert new_card.num_decks < old_card.num_decks
    assert new_card.inclusion_rate > old_card.inclusion_rate


def test_rate_is_none_without_a_denominator():
    assert EdhrecCard("X", 0.1, 50, None, "c", "s").inclusion_rate is None


# ── Collecting ───────────────────────────────────────────────────────────


def test_collects_signal_lists():
    recs = _recs(
        highsynergycards=[_view("Mayhem Devil", 0.48, 15343)],
        topcards=[_view("Sol Ring", 0.02, 18000)],
    )
    cards = collect(recs)
    by_name = {c.name: c for c in cards}
    assert by_name["Mayhem Devil"].signal == "high-synergy"
    assert by_name["Sol Ring"].signal == "top-card"


def test_signal_lists_win_over_type_lists_on_duplicates():
    """A card in both lists keeps the more informative label."""
    recs = _recs(
        highsynergycards=[_view("Mayhem Devil", 0.48, 15343)],
        creatures=[_view("Mayhem Devil", 0.48, 15343)],
    )
    cards = collect(recs)
    assert len(cards) == 1
    assert cards[0].signal == "high-synergy"


def test_type_lists_can_be_excluded():
    recs = _recs(
        highsynergycards=[_view("Mayhem Devil", 0.48, 15343)],
        creatures=[_view("Other Card", 0.1, 500)],
    )
    cards = collect(recs, include_type_lists=False)
    assert [c.name for c in cards] == ["Mayhem Devil"]


def test_collect_survives_a_malformed_page():
    assert collect({}) == []
    assert collect({"categories": {"topcards": "not a dict"}}) == []
    assert collect({"categories": {"topcards": {"cards": [None, 5]}}}) == []


def test_nameless_view_is_skipped():
    recs = _recs(topcards=[{"synergy": 0.1}, _view("Real", 0.2, 100)])
    assert [c.name for c in collect(recs)] == ["Real"]


# ── Ranking: the off-meta knob ───────────────────────────────────────────


def test_off_meta_zero_follows_popularity():
    cards = [
        EdhrecCard("Staple", 0.02, 18000, 20000, "c", "top-card"),
        EdhrecCard("Specific", 0.40, 4000, 20000, "c", "high-synergy"),
    ]
    assert [c.name for c in rank(cards, off_meta=0.0)][0] == "Staple"


def test_off_meta_one_follows_synergy():
    """The whole point of the knob: surface what is specific to THIS commander
    rather than what everyone in these colours plays."""
    cards = [
        EdhrecCard("Staple", 0.02, 18000, 20000, "c", "top-card"),
        EdhrecCard("Specific", 0.40, 4000, 20000, "c", "high-synergy"),
    ]
    assert [c.name for c in rank(cards, off_meta=1.0)][0] == "Specific"


def test_off_meta_is_clamped():
    cards = [EdhrecCard("A", 0.1, 100, 1000, "c", "s")]
    assert rank(cards, off_meta=5.0) == rank(cards, off_meta=1.0)
    assert rank(cards, off_meta=-3.0) == rank(cards, off_meta=0.0)


def test_ranking_handles_missing_numbers():
    cards = [
        EdhrecCard("NoData", None, None, None, "c", "s"),
        EdhrecCard("HasData", 0.3, 5000, 10000, "c", "s"),
    ]
    assert [c.name for c in rank(cards, off_meta=0.5)][0] == "HasData"


# ── Hydration ────────────────────────────────────────────────────────────


def test_hydrate_returns_full_card_objects_with_signal():
    store = FakeStore({"Mayhem Devil": _card("Mayhem Devil")})
    cards = [EdhrecCard("Mayhem Devil", 0.48, 15343, 20489, "High Synergy", "high-synergy")]
    out = hydrate(cards, store)
    assert out[0]["name"] == "Mayhem Devil"
    assert out[0]["edhrec"]["synergy"] == 0.48
    assert out[0]["edhrec"]["inclusion_rate"] == pytest.approx(0.7488, abs=0.001)
    assert out[0]["edhrec"]["signal"] == "high-synergy"


def test_unresolvable_name_is_dropped_not_guessed():
    """A wrong card is worse than a missing one."""
    store = FakeStore({})
    out = hydrate([EdhrecCard("Ghost Card", 0.1, 100, 1000, "c", "s")], store)
    assert out == []


def test_hydrate_filters_by_colour_identity():
    store = FakeStore({
        "In Colour": _card("In Colour", identity=("B",)),
        "Off Colour": _card("Off Colour", identity=("G",)),
    })
    cards = [
        EdhrecCard("In Colour", 0.2, 100, 1000, "c", "s"),
        EdhrecCard("Off Colour", 0.3, 100, 1000, "c", "s"),
    ]
    out = hydrate(cards, store, frozenset({"B", "R"}))
    assert [c["name"] for c in out] == ["In Colour"]


def test_hydrate_empty_input():
    assert hydrate([], FakeStore({})) == []


# ── The strip bug ────────────────────────────────────────────────────────


def test_shaping_no_longer_drops_the_edhrec_annotation():
    """`_STRIP_FIELDS` omitted `edhrec`, so stage 2 attached the signal and
    stage 3 threw it away before the selection model could read it."""
    from app.pipeline.shaping import DeckContext, render_pool, shape

    raw = [{
        "name": "Mayhem Devil",
        "oracle_id": "oid-mayhem",
        "type_line": "Creature — Devil",
        "oracle_text": "Whenever a player sacrifices a permanent...",
        "color_identity": ["B", "R"],
        "legal_commander": True,
        "cmc": 3.0,
        "edhrec_rank": 579,
        "edhrec": {
            "synergy": 0.48, "inclusion_rate": 0.749,
            "signal": "high-synergy", "category": "High Synergy",
        },
    }]
    ctx = DeckContext(identity=frozenset({"B", "R"}), card_names_lower=frozenset())
    shaped = shape(raw, ctx, {})

    assert shaped[0].edhrec is not None
    rendered = render_pool(shaped)
    assert "75% of decks" in rendered
    assert "synergy +0.48" in rendered
    assert "high-synergy" in rendered


def test_render_pool_omits_edhrec_when_absent():
    """A card with no EDHREC data must not render an empty annotation."""
    from app.pipeline.shaping import DeckContext, render_pool, shape

    raw = [{
        "name": "Obscure Card", "oracle_id": "oid-obscure",
        "type_line": "Creature", "color_identity": [], "legal_commander": True,
    }]
    ctx = DeckContext(identity=frozenset(), card_names_lower=frozenset())
    assert "EDHREC:" not in render_pool(shape(raw, ctx, {}))
