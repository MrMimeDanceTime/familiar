"""Brain map tests.

Three layers, kept separate on purpose. Collapsing them into one number is how
a recommender becomes a conformist: the off-meta control is a mix weight ACROSS
layers, and "high mechanical, low consensus" is the sentence that identifies an
underplayed card that actually works.

The layer-2 premise was validated against live data before this was written. On
Korvold, Fae-Cursed King, 326 free/repeatable sacrifice outlets exist in Jund
identity and 305 of them are absent from EDHREC's 225-card page — Carrion
Feeder, Greater Good, Altar of Dementia, Krark-Clan Ironworks among them. So
layer 2's problem is volume, not precision, which is what the blend solves.
"""

import pytest

from app.brainmap.consensus import ConsensusLayer
from app.brainmap.layers import (
    CONSENSUS,
    MECHANICAL,
    PERSONAL,
    LayerScore,
    ScoringContext,
    blend,
)
from app.brainmap.mechanical import MechanicalLayer, detect_themes, resolve_slugs
from app.brainmap.personal import PersonalLayer


def _card(name, edhrec=None, oracle_id=None):
    return {
        "name": name,
        "oracle_id": oracle_id or f"oid-{name.lower().replace(' ', '-')}",
        "edhrec": edhrec,
    }


class FakeStore:
    """Minimal store: commander text, tag vocabulary, and per-card tags."""

    def __init__(self, commander_text="", tags=None, vocabulary=None):
        self._commander_text = commander_text
        self._tags = tags or {}
        self._vocabulary = vocabulary or set(
            s for slugs in (tags or {}).values() for s in slugs
        )

    def by_name(self, name):
        return {"name": name, "oracle_text": self._commander_text}

    def tags_for_many(self, oracle_ids):
        return {oid: self._tags.get(oid, set()) for oid in oracle_ids}

    def tag_slugs(self, pattern=None, limit=100):
        return [(s, 1) for s in self._vocabulary]


# ── Blending ─────────────────────────────────────────────────────────────


def test_blend_combines_layers():
    scores = {
        CONSENSUS: {"a": LayerScore(CONSENSUS, 1.0)},
        MECHANICAL: {"a": LayerScore(MECHANICAL, 0.0)},
        PERSONAL: {"a": LayerScore(PERSONAL, 0.0)},
    }
    result = blend(scores)
    assert result[0].total == pytest.approx(0.5)


def test_absent_layer_does_not_shrink_totals():
    """A fresh install has no personal history. Without renormalising, every
    card would top out at 0.8 and the numbers would stop meaning anything."""
    scores = {
        CONSENSUS: {"a": LayerScore(CONSENSUS, 1.0)},
        MECHANICAL: {"a": LayerScore(MECHANICAL, 1.0)},
        PERSONAL: {},
    }
    assert blend(scores)[0].total == pytest.approx(1.0)


def test_off_meta_shifts_weight_to_mechanical():
    scores = {
        CONSENSUS: {
            "staple": LayerScore(CONSENSUS, 1.0),
            "hidden": LayerScore(CONSENSUS, 0.2),
        },
        MECHANICAL: {
            "staple": LayerScore(MECHANICAL, 0.0),
            "hidden": LayerScore(MECHANICAL, 1.0),
        },
    }
    assert blend(scores, off_meta=0.0)[0].name == "staple"
    assert blend(scores, off_meta=1.0)[0].name == "hidden"


def test_consensus_breaks_ties():
    """Mechanical scores in a few discrete bands, so ties are common at high
    off_meta and their order would otherwise be arbitrary."""
    scores = {
        CONSENSUS: {
            "popular": LayerScore(CONSENSUS, 0.9),
            "obscure": LayerScore(CONSENSUS, 0.1),
        },
        MECHANICAL: {
            "popular": LayerScore(MECHANICAL, 0.75),
            "obscure": LayerScore(MECHANICAL, 0.75),
        },
    }
    assert blend(scores, off_meta=1.0)[0].name == "popular"


def test_explain_names_contributing_layers():
    scores = {
        CONSENSUS: {"a": LayerScore(CONSENSUS, 0.31, "31% of decks")},
        MECHANICAL: {"a": LayerScore(MECHANICAL, 0.75, "payoff for sacrifice")},
    }
    text = blend(scores)[0].explain()
    assert "consensus" in text and "mechanical" in text
    assert "payoff for sacrifice" in text


# ── Layer 1: consensus ───────────────────────────────────────────────────


def test_consensus_uses_rate_and_synergy():
    cards = [_card("Mayhem Devil", {"inclusion_rate": 0.75, "synergy": 0.48})]
    scored = ConsensusLayer().score(cards, ScoringContext())
    assert scored["mayhem devil"].score > 0.7
    assert "75% of decks" in scored["mayhem devil"].reason


def test_consensus_distinguishes_staple_from_specific():
    """Equal play rates, opposite meanings: the staple has near-zero synergy."""
    cards = [
        _card("Staple", {"inclusion_rate": 0.5, "synergy": 0.02}),
        _card("Specific", {"inclusion_rate": 0.5, "synergy": 0.45}),
    ]
    scored = ConsensusLayer().score(cards, ScoringContext())
    assert scored["specific"].score > scored["staple"].score


def test_consensus_skips_cards_without_data():
    assert ConsensusLayer().score([_card("Unknown")], ScoringContext()) == {}


# ── Layer 2: mechanical ──────────────────────────────────────────────────


KORVOLD_TEXT = (
    "Flying\nWhenever Korvold enters or attacks, sacrifice another permanent.\n"
    "Whenever you sacrifice a permanent, put a +1/+1 counter on Korvold and "
    "draw a card."
)


def test_detects_theme_from_commander_text():
    keys = {t.key for t in detect_themes(KORVOLD_TEXT, [])}
    assert "sacrifice" in keys


def test_detects_declared_themes():
    keys = {t.key for t in detect_themes("", ["treasure tokens"])}
    assert "treasure" in keys


def test_no_themes_without_signal():
    assert detect_themes("Flying. Vigilance.", []) == []


def test_resolve_slugs_drops_slugs_absent_from_the_vocabulary():
    """Tagger's vocabulary is a hierarchy and the bulk export ships only leaf
    taggings, so a plausible slug name can silently match nothing."""
    themes = detect_themes(KORVOLD_TEXT, [])
    store = FakeStore(vocabulary={"free-sacrifice-outlet"})
    hits = resolve_slugs(themes, store)
    assert hits
    assert hits[0].enablers == ["free-sacrifice-outlet"]


def test_mechanical_scores_engine_payoff_and_enabler():
    store = FakeStore(
        commander_text=KORVOLD_TEXT,
        tags={
            "oid-engine": {"free-sacrifice-outlet", "your-sacrifice-matters"},
            "oid-payoff": {"your-sacrifice-matters"},
            "oid-enabler": {"free-sacrifice-outlet"},
        },
    )
    cards = [
        _card("Engine", oracle_id="oid-engine"),
        _card("Payoff", oracle_id="oid-payoff"),
        _card("Enabler", oracle_id="oid-enabler"),
    ]
    scored = MechanicalLayer(store).score(
        cards, ScoringContext(commander="Korvold, Fae-Cursed King")
    )
    assert scored["engine"].score > scored["payoff"].score > scored["enabler"].score
    assert "engine piece" in scored["engine"].reason


def test_mechanical_reason_names_only_the_matched_theme():
    """Korvold triggers both 'sacrifice' and '+1/+1 counters'. Joining every
    detected theme onto every card makes a sacrifice outlet claim to be a
    counters card."""
    store = FakeStore(
        commander_text=KORVOLD_TEXT,
        tags={"oid-sac": {"free-sacrifice-outlet"}},
        vocabulary={"free-sacrifice-outlet", "counters-matter"},
    )
    scored = MechanicalLayer(store).score(
        [_card("Sac Outlet", oracle_id="oid-sac")],
        ScoringContext(commander="Korvold, Fae-Cursed King"),
    )
    reason = scored["sac outlet"].reason
    assert "sacrifice" in reason
    assert "counter" not in reason


def test_mechanical_returns_nothing_without_a_theme():
    store = FakeStore(commander_text="Flying. Vigilance.", tags={})
    scored = MechanicalLayer(store).score(
        [_card("X")], ScoringContext(commander="Vanilla")
    )
    assert scored == {}


def test_mechanical_ignores_untagged_cards():
    store = FakeStore(commander_text=KORVOLD_TEXT, tags={},
                      vocabulary={"free-sacrifice-outlet"})
    scored = MechanicalLayer(store).score(
        [_card("Untagged")], ScoringContext(commander="Korvold, Fae-Cursed King")
    )
    assert scored == {}


# ── Layer 3: personal ────────────────────────────────────────────────────


@pytest.fixture
def history_engine(tmp_path):
    from sqlmodel import Session, SQLModel, create_engine

    from app.db.models import DeckProposal

    engine = create_engine(f"sqlite:///{tmp_path / 'history.db'}")
    SQLModel.metadata.create_all(engine)

    rows = [
        ("Sol Ring", "approved", 1), ("Sol Ring", "approved", 1),
        ("Bad Card", "denied", 1), ("Bad Card", "denied", 1),
        ("Other Deck Card", "approved", 2),
        ("Swamp", "approved", 1), ("Swamp", "approved", 1),
    ]
    with Session(engine) as session:
        for name, status, deck_id in rows:
            session.add(DeckProposal(
                conversation_id=1, deck_id=deck_id, action="add",
                card_name=name, status=status, reasoning="",
            ))
        session.commit()
    return engine


def test_personal_scores_from_history(history_engine):
    layer = PersonalLayer(history_engine)
    scored = layer.score(
        [_card("Sol Ring"), _card("Bad Card")], ScoringContext(deck_id=1)
    )
    assert scored["sol ring"].score == 1.0
    assert scored["bad card"].score == 0.0
    assert "taken this" in scored["sol ring"].reason


def test_personal_ignores_basic_lands(history_engine):
    """Basics are approved constantly and carry no preference signal — on the
    real history they were the top three cards by approval count."""
    scored = PersonalLayer(history_engine).score(
        [_card("Swamp")], ScoringContext(deck_id=1)
    )
    assert "swamp" not in scored


def test_personal_weights_this_deck_over_others(history_engine):
    """A denial for THIS deck is a direct instruction; elsewhere it is weaker."""
    layer = PersonalLayer(history_engine)
    this_deck = layer._history(1)
    other = layer._history(2)
    assert this_deck["sol ring"][0] > other["sol ring"][0]


def test_personal_empty_without_history(tmp_path):
    """No history means 'no opinion', which the blend must not read as
    'everything scores zero'."""
    from sqlmodel import SQLModel, create_engine

    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    SQLModel.metadata.create_all(engine)
    assert PersonalLayer(engine).score([_card("Anything")], ScoringContext()) == {}


def test_personal_survives_a_missing_table(tmp_path):
    from sqlmodel import create_engine

    engine = create_engine(f"sqlite:///{tmp_path / 'bare.db'}")
    assert PersonalLayer(engine).score([_card("X")], ScoringContext()) == {}


# ── Assembly ─────────────────────────────────────────────────────────────


def test_score_pool_survives_a_failing_layer():
    """Ranking is an improvement over unranked candidates, not a precondition
    for producing any."""
    from app.brainmap.map import score_pool

    class Exploding:
        name = MECHANICAL

        def score(self, cards, context):
            raise RuntimeError("boom")

    ranked = score_pool(
        [_card("A", {"inclusion_rate": 0.5, "synergy": 0.1})],
        ScoringContext(),
        store=FakeStore(), engine=None,
        layers=[ConsensusLayer(), Exploding()],
    )
    assert ranked[0].name == "a"


def test_annotate_pool_orders_by_score_and_keeps_unscored():
    from app.brainmap.layers import CardScore
    from app.brainmap.map import annotate_pool

    pool = [_card("Low"), _card("High"), _card("Unscored")]
    ranked = [
        CardScore(name="high", total=0.9),
        CardScore(name="low", total=0.1),
    ]
    out = annotate_pool(pool, ranked)
    assert [c["name"] for c in out] == ["High", "Low", "Unscored"]
    assert out[0]["brainmap"]["total"] == 0.9
    assert "brainmap" not in out[2]


def test_shape_preserves_brain_map_order():
    """`shape` CAPS the pool, so re-sorting on EDHREC rank would discard exactly
    the cards the map ranked highest."""
    from app.pipeline.shaping import DeckContext, shape

    raw = [
        {"name": "Popular", "oracle_id": "o1", "color_identity": [],
         "legal_commander": True, "edhrec_rank": 1,
         "brainmap": {"total": 0.10}},
        {"name": "Best Fit", "oracle_id": "o2", "color_identity": [],
         "legal_commander": True, "edhrec_rank": 9000,
         "brainmap": {"total": 0.95}},
    ]
    ctx = DeckContext(identity=frozenset(), card_names_lower=frozenset())
    assert [c.name for c in shape(raw, ctx, {})] == ["Best Fit", "Popular"]


def test_shape_falls_back_to_rank_without_map_scores():
    from app.pipeline.shaping import DeckContext, shape

    raw = [
        {"name": "Worse", "oracle_id": "o1", "color_identity": [],
         "legal_commander": True, "edhrec_rank": 9000},
        {"name": "Better", "oracle_id": "o2", "color_identity": [],
         "legal_commander": True, "edhrec_rank": 1},
    ]
    ctx = DeckContext(identity=frozenset(), card_names_lower=frozenset())
    assert [c.name for c in shape(raw, ctx, {})] == ["Better", "Worse"]
