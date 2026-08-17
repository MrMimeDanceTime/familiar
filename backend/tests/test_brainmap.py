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
from app.brainmap.mechanical import (
    MechanicalLayer,
    build_relationship,
    is_flavour_tag,
)
from app.brainmap.personal import PersonalLayer


def _card(name, edhrec=None, oracle_id=None):
    return {
        "name": name,
        "oracle_id": oracle_id or f"oid-{name.lower().replace(' ', '-')}",
        "edhrec": edhrec,
    }


class FakeStore:
    """Minimal store: commander text, tag vocabulary, and per-card tags."""

    def __init__(self, commander_text="", tags=None, vocabulary=None,
                 related=None, colour_artifacts=None, breadth=None):
        self._commander_text = commander_text
        self._tags = tags or {}
        self._vocabulary = vocabulary or set(
            s for slugs in (tags or {}).values() for s in slugs
        )
        self._related = related or {}
        self._colour_artifacts = colour_artifacts or set()
        self._breadth = breadth or {}

    def by_name(self, name):
        return {
            "name": name,
            "oracle_text": self._commander_text,
            "oracle_id": "oid-commander",
        }

    def tags_for_many(self, oracle_ids):
        return {oid: self._tags.get(oid, set()) for oid in oracle_ids}

    def tag_slugs(self, pattern=None, limit=100):
        slugs = self._vocabulary
        if pattern:
            slugs = {s for s in slugs if pattern in s}
        return [(s, 1) for s in slugs]

    def tags_for(self, oracle_id):
        return self._tags.get(oracle_id, set())

    def related_tags(self, slugs, *, min_lift=5.0, limit=12):
        out = {s: float("inf") for s in slugs}
        for s in slugs:
            for partner, lift in self._related.get(s, {}).items():
                if lift >= min_lift:
                    out[partner] = lift
        return out

    def is_colour_artifact(self, slug, threshold=0.5):
        return slug in self._colour_artifacts

    def tag_breadth(self, slugs):
        # Fake store tags are all narrow unless a test says otherwise.
        return {s: self._breadth.get(s, 0.001) for s in slugs}


# ── Blending ─────────────────────────────────────────────────────────────


def test_blend_combines_layers():
    scores = {
        CONSENSUS: {"a": LayerScore(CONSENSUS, 1.0)},
        MECHANICAL: {"a": LayerScore(MECHANICAL, 0.0)},
        PERSONAL: {"a": LayerScore(PERSONAL, 0.0)},
    }
    result = blend(scores)
    assert result[0].total == pytest.approx(0.5)


def test_absent_layer_does_not_collapse_a_score():
    """A fresh install has no personal history, and that must not drag every
    card down toward zero.

    The score is still shaded below 1.0 — two of three layers agreeing is real
    but not total evidence — and that shading is deliberate (see
    test_broad_agreement_beats_a_single_confident_layer). What matters is that
    it stays close to the raw score rather than being multiplied away.
    """
    scores = {
        CONSENSUS: {"a": LayerScore(CONSENSUS, 1.0)},
        MECHANICAL: {"a": LayerScore(MECHANICAL, 1.0)},
        PERSONAL: {},
    }
    assert blend(scores)[0].total > 0.85


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


# ── Per-card confidence (found by running a real deck) ───────────────────


def test_unlisted_card_is_not_punished_by_a_silent_layer():
    """Renormalising globally over "layers that scored anything" punishes a card
    the other layers simply have no opinion about.

    Measured on a real Rin and Seri pool: Cat Collector scored mechanical 1.00
    with no EDHREC entry (a fine card that is not on the commander's page) and
    came out at 0.42, below staples scoring on consensus alone. 43 of 60 cards
    landed at exactly 0.00, so most of the pool reached the model unranked.
    """
    scores = {
        CONSENSUS: {"staple": LayerScore(CONSENSUS, 0.62)},
        MECHANICAL: {"unlisted": LayerScore(MECHANICAL, 1.0)},
    }
    result = {c.name: c.total for c in blend(scores)}
    assert result["unlisted"] > result["staple"]


def test_broad_agreement_beats_a_single_confident_layer():
    """The counterweight: per-card renormalisation alone would let one layer at
    1.00 outrank a card all three layers agree is 0.80."""
    scores = {
        CONSENSUS: {"broad": LayerScore(CONSENSUS, 0.8)},
        MECHANICAL: {"broad": LayerScore(MECHANICAL, 0.8)},
        PERSONAL: {
            "broad": LayerScore(PERSONAL, 0.8),
            "thin": LayerScore(PERSONAL, 1.0),
        },
    }
    result = {c.name: c.total for c in blend(scores)}
    assert result["broad"] > result["thin"]


def test_more_evidence_means_more_confidence():
    """A mechanical-only score (weight 0.3) should be trusted more than a
    personal-only one (0.2) at equal raw score."""
    scores = {
        MECHANICAL: {"mech": LayerScore(MECHANICAL, 1.0)},
        PERSONAL: {"pers": LayerScore(PERSONAL, 1.0)},
    }
    result = {c.name: c.total for c in blend(scores)}
    assert result["mech"] > result["pers"]


# ── Learning from structured denials ─────────────────────────────────────
#
# This is why the review surface asks WHY. "Don't need this role" is a claim
# about the deck's current shape — the card may be perfect once that slot opens
# — while "too generic" is a claim about the card. Weighting them identically
# slowly poisons the pool against cards that were only ever mistimed.


def _deny_engine(tmp_path, rows):
    """rows: (card_name, status, deck_id, denial_reason)"""
    from sqlmodel import Session, SQLModel, create_engine

    from app.db.models import DeckProposal

    engine = create_engine(f"sqlite:///{tmp_path}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        for name, status, deck_id, reason in rows:
            session.add(DeckProposal(
                conversation_id=1, deck_id=deck_id, action="add",
                card_name=name, status=status, reasoning="",
                denial_reason=reason,
            ))
        session.commit()
    return engine


def test_role_denial_counts_far_less_than_a_taste_denial(tmp_path):
    """The same card, denied once each way, must not score the same."""
    role = PersonalLayer(_deny_engine(
        tmp_path / "role.db", [("Card A", "denied", 1, "Don't need this role")]
    ))
    taste = PersonalLayer(_deny_engine(
        tmp_path / "taste.db", [("Card A", "denied", 1, "Too generic")]
    ))
    role_weight = role._history(1)["card a"][1]
    taste_weight = taste._history(1)["card a"][1]
    assert role_weight < taste_weight / 3


def test_unlabelled_denial_sits_between(tmp_path):
    """A Skip is a real rejection, but the player declined to say why, so it
    must not carry the weight of an explicit dislike."""
    layers = {
        label: PersonalLayer(_deny_engine(
            tmp_path / f"{key}.db", [("Card A", "denied", 1, reason)]
        ))._history(1)["card a"][1]
        for key, label, reason in [
            ("role", "role", "Don't need this role"),
            ("skip", "skip", None),
            ("taste", "taste", "Too generic"),
        ]
    }
    assert layers["role"] < layers["skip"] < layers["taste"]


def test_curly_apostrophe_reason_is_recognised(tmp_path):
    """The UI sends a typographic apostrophe; a lookup miss would silently
    downgrade the strongest signal to the unlabelled default."""
    curly = PersonalLayer(_deny_engine(
        tmp_path / "curly.db", [("Card A", "denied", 1, "Just don’t like it")]
    ))._history(1)["card a"][1]
    straight = PersonalLayer(_deny_engine(
        tmp_path / "straight.db", [("Card A", "denied", 1, "Just don't like it")]
    ))._history(1)["card a"][1]
    assert curly == straight


def test_unknown_reason_falls_back_rather_than_vanishing(tmp_path):
    layer = PersonalLayer(_deny_engine(
        tmp_path / "unknown.db", [("Card A", "denied", 1, "some freeform text")]
    ))
    assert layer._history(1)["card a"][1] > 0


def test_repeated_denials_accumulate(tmp_path):
    layer = PersonalLayer(_deny_engine(tmp_path / "rep.db", [
        ("Card A", "denied", 1, "Too generic"),
        ("Card A", "denied", 1, "Too generic"),
    ]))
    single = PersonalLayer(_deny_engine(
        tmp_path / "one.db", [("Card A", "denied", 1, "Too generic")]
    ))
    assert layer._history(1)["card a"][1] > single._history(1)["card a"][1]


def test_role_denial_barely_moves_the_score(tmp_path):
    """End to end: a card passed on for the wrong slot should still look
    largely acceptable, because nothing was said against the card."""
    layer = PersonalLayer(_deny_engine(tmp_path / "e2e.db", [
        ("Card A", "approved", 1, None),
        ("Card A", "denied", 1, "Don't need this role"),
    ]))
    scored = layer.score([_card("Card A")], ScoringContext(deck_id=1))
    assert scored["card a"].score > 0.75


# ── Generalising beyond exact card names ─────────────────────────────────


def test_curve_preference_needs_enough_evidence(tmp_path):
    """A handful of denials is noise; reading a curve preference off it would
    be superstition."""
    layer = PersonalLayer(_deny_engine(tmp_path / "thin.db", [
        ("Card A", "approved", 1, None), ("Card B", "denied", 1, None),
    ]))
    assert layer.curve_preference() is None


def test_curve_score_ignores_a_narrow_spread():
    """Measured on the real database: approved 2.28 MV against denied 2.42.
    A 0.14 gap is not a preference, and acting on it would invent taste the
    player has not demonstrated."""
    assert PersonalLayer._curve_score({"cmc": 5.0}, (2.28, 2.42)) is None


def test_curve_score_favours_the_side_the_player_takes_from():
    cheap = PersonalLayer._curve_score({"cmc": 2.0}, (2.0, 5.0))
    expensive = PersonalLayer._curve_score({"cmc": 7.0}, (2.0, 5.0))
    assert cheap is not None and expensive is not None
    assert cheap.score > expensive.score
    assert "cheaper" in cheap.reason


def test_curve_score_stays_weak():
    """"You tend to take cheaper cards" is a far softer claim than "you took
    this card twice", and must never outrank direct history."""
    inferred = PersonalLayer._curve_score({"cmc": 1.0}, (2.0, 6.0))
    assert inferred is not None
    assert 0.35 <= inferred.score <= 0.65


def test_curve_score_needs_a_mana_value():
    assert PersonalLayer._curve_score({"cmc": None}, (2.0, 5.0)) is None


# ── Layer 2: relationships derived, not declared ─────────────────────────
#
# The first version matched commander oracle text against eight hand-written
# themes plus a tribal special case. Measured across 11 real decks, that scored
# 4% of pooled cards and gave FIVE decks exactly zero. Deriving relationships
# from the commander's own tags took the same measurement to 52%.


def _commander_store(commander_tags, card_tags, **kw):
    store = FakeStore(tags={"oid-commander": set(commander_tags), **card_tags}, **kw)
    return store


def test_relationship_comes_from_the_commanders_own_tags():
    """No theme list. Korvold declares `your-sacrifice-matters` himself."""
    store = _commander_store({"your-sacrifice-matters"}, {})
    rel = build_relationship({"your-sacrifice-matters"}, store)
    assert "your-sacrifice-matters" in rel.own


def test_relationship_expands_through_co_occurrence():
    store = _commander_store(
        {"synergy-exile-cast"}, {},
        related={"synergy-exile-cast": {"repeatable-impulsive-draw": 66.5}},
    )
    rel = build_relationship({"synergy-exile-cast"}, store)
    assert rel.related["repeatable-impulsive-draw"] == 66.5


def test_colour_artifact_tags_are_dropped():
    """Torbran carries `synergy-red`, which is true and useless: 80% of its top
    partners are the other colour tags, where every real mechanic measures 0%."""
    store = _commander_store(
        {"synergy-red", "damage-increaser"}, {},
        colour_artifacts={"synergy-red"},
        related={"damage-increaser": {"synergy-burn": 292.9}},
    )
    rel = build_relationship({"synergy-red", "damage-increaser"}, store)
    assert "synergy-red" not in rel.own
    assert "damage-increaser" in rel.own
    assert "synergy-burn" in rel.related


def test_flavour_tags_are_ignored():
    """Korvold carries `alliteration`; expanding it would relate him to every
    card with a catchy name. `cycle-*` alone is 1,594 of 4,383 slugs."""
    for slug in ("alliteration", "cycle-eld-brawler", "punny-name",
                 "unique-type-line", "single-english-word-name"):
        assert is_flavour_tag(slug), slug
    for slug in ("your-sacrifice-matters", "synergy-exile-cast", "typal-cat",
                 "damage-increaser", "synergy-burn", "landfall"):
        assert not is_flavour_tag(slug), slug


def test_flavour_tags_do_not_enter_the_relationship():
    store = _commander_store({"alliteration", "damage-increaser"}, {})
    rel = build_relationship({"alliteration", "damage-increaser"}, store)
    assert rel.own == {"damage-increaser"}


def test_exact_tag_match_outscores_a_related_one():
    """A card doing exactly what the commander cares about is the strongest
    signal available."""
    store = _commander_store(
        {"your-sacrifice-matters"},
        {"oid-exact": {"your-sacrifice-matters"}, "oid-related": {"synergy-clue"}},
        related={"your-sacrifice-matters": {"synergy-clue": 164.6}},
    )
    scored = MechanicalLayer(store).score(
        [_card("Exact", oracle_id="oid-exact"),
         _card("Related", oracle_id="oid-related")],
        ScoringContext(commander="Korvold"),
    )
    assert scored["exact"].score > scored["related"].score


def test_stronger_lift_scores_higher():
    store = _commander_store(
        {"root"},
        {"oid-strong": {"strong"}, "oid-weak": {"weak"}},
        related={"root": {"strong": 100.0, "weak": 6.0}},
    )
    scored = MechanicalLayer(store).score(
        [_card("Strong", oracle_id="oid-strong"),
         _card("Weak", oracle_id="oid-weak")],
        ScoringContext(commander="Cmd"),
    )
    assert scored["strong"].score > scored["weak"].score


def test_unrelated_card_scores_nothing():
    store = _commander_store(
        {"your-sacrifice-matters"}, {"oid-other": {"landfall"}},
    )
    scored = MechanicalLayer(store).score(
        [_card("Unrelated", oracle_id="oid-other")],
        ScoringContext(commander="Korvold"),
    )
    assert scored == {}


def test_declared_themes_add_relationships():
    """A player building tokens under a commander whose text never says "token"
    can say so; the commander's own tags stay the default."""
    store = _commander_store(
        {"damage-increaser"}, {"oid-token": {"repeatable-creature-tokens"}},
        vocabulary={"repeatable-creature-tokens", "damage-increaser"},
    )
    rel = build_relationship(
        {"damage-increaser"}, store, themes=["creature tokens"]
    )
    # Themed tags are tracked separately from the commander's own: they resolve
    # from free text and are far broader, so they score below an exact match.
    # "enchantment ramp" resolves to `land-ramp`, which every ramp spell in the
    # format carries — scoring that at exact strength let a rank-6,410 card tie
    # a rank-26 staple.
    assert "repeatable-creature-tokens" in rel.themed
    assert "repeatable-creature-tokens" not in rel.own


def test_no_commander_yields_no_scores():
    store = _commander_store(set(), {})
    assert MechanicalLayer(store).score([_card("X")], ScoringContext()) == {}


def test_reason_names_the_relationship():
    store = _commander_store(
        {"your-sacrifice-matters"}, {"oid-a": {"your-sacrifice-matters"}},
    )
    scored = MechanicalLayer(store).score(
        [_card("A", oracle_id="oid-a")], ScoringContext(commander="Korvold"),
    )
    assert "your-sacrifice-matters" in scored["a"].reason


def test_themed_match_scores_below_an_exact_one():
    """A commander's own tag is a stronger claim than a word the player typed."""
    store = _commander_store(
        {"your-sacrifice-matters"},
        {"oid-own": {"your-sacrifice-matters"}, "oid-theme": {"land-ramp"}},
        vocabulary={"your-sacrifice-matters", "land-ramp"},
    )
    scored = MechanicalLayer(store).score(
        [_card("Own", oracle_id="oid-own"), _card("Themed", oracle_id="oid-theme")],
        ScoringContext(commander="Korvold", themes=["land ramp"]),
    )
    assert scored["own"].score > scored["themed"].score


def test_more_evidence_never_lowers_a_score():
    """Measured on the real Myrkul pool: Font of Fertility (EDHREC rank 6,410,
    mechanical only) beat Rampant Growth (rank 26, mechanical AND consensus)
    because the extra layer dragged the weighted average down. Being known by
    more layers must never cost a card."""
    one_layer = blend({MECHANICAL: {"a": LayerScore(MECHANICAL, 0.70)}})[0].total
    two_layers = blend({
        MECHANICAL: {"a": LayerScore(MECHANICAL, 0.70)},
        CONSENSUS: {"a": LayerScore(CONSENSUS, 0.70)},
    })[0].total
    assert two_layers >= one_layer


def test_a_staple_is_not_penalised_for_low_synergy():
    """Synergy measures how much MORE a card appears here than in other decks of
    the same colours, so a card played everywhere has near-zero synergy BY
    DEFINITION. Averaging the two buried staples: Rampant Growth at 34% play and
    +0.08 synergy scored 0.245."""
    from app.brainmap.consensus import ConsensusLayer

    scored = ConsensusLayer().score(
        [_card("Staple", {"inclusion_rate": 0.34, "synergy": 0.08})],
        ScoringContext(),
    )
    assert scored["staple"].score >= 0.34


def test_global_rank_carries_cards_off_the_commanders_page():
    """EDHREC's per-commander page lists ~200 cards; the global rank covers 100%
    of the legal pool. Without a fallback, every unlisted card scored on
    mechanical alone and tied — a rank-12,860 card sat level with a rank-1,766
    one, and both above Rampant Growth at rank 26."""
    from app.brainmap.consensus import ConsensusLayer

    scored = ConsensusLayer().score(
        [
            {"name": "Staple", "edhrec_rank": 300},
            {"name": "Fringe", "edhrec_rank": 12000},
        ],
        ScoringContext(),
    )
    assert scored["staple"].score > scored["fringe"].score
    assert "not on this commander's page" in scored["staple"].reason


def test_global_rank_never_beats_a_real_commander_score():
    """"Widely played in the format" is weaker evidence than "played in THIS
    commander's decks", so the fallback must stay below a genuine page entry."""
    from app.brainmap.consensus import ConsensusLayer

    scored = ConsensusLayer().score(
        [
            {"name": "Global", "edhrec_rank": 1},
            {"name": "OnPage", "edhrec": {"inclusion_rate": 0.5, "synergy": 0.1}},
        ],
        ScoringContext(),
    )
    assert scored["onpage"].score > scored["global"].score


def test_unranked_card_still_scores_nothing():
    """A card with neither page data nor a rank has no consensus opinion, and
    the layer must stay silent rather than invent one."""
    from app.brainmap.consensus import ConsensusLayer

    assert ConsensusLayer().score(
        [{"name": "Unknown"}], ScoringContext()
    ) == {}


def test_deep_fringe_gets_no_consensus_score():
    """Past the last band the rank stops meaning anything useful."""
    from app.brainmap.consensus import ConsensusLayer

    assert ConsensusLayer().score(
        [{"name": "Obscure", "edhrec_rank": 25000}], ScoringContext()
    ) == {}


def test_generic_tags_are_discounted():
    """`activated-ability` covers 26.5% of the legal pool. Rin and Seri carries
    it, so Sol Ring and Command Tower matched at exact strength and scored
    mechanical 1.00 — meaning RAISING mechanical weight surfaced MORE staples.
    That inverted the off-meta control: at 1.0 the top ten had a median EDHREC
    rank of 34 against 3,021 at 0.0."""
    store = _commander_store(
        {"typal-cat", "activated-ability"},
        {"oid-generic": {"activated-ability"}, "oid-specific": {"typal-cat"}},
        breadth={"activated-ability": 0.265, "typal-cat": 0.001},
    )
    scored = MechanicalLayer(store).score(
        [_card("Sol Ring", oracle_id="oid-generic"),
         _card("Cat Lord", oracle_id="oid-specific")],
        ScoringContext(commander="Rin and Seri"),
    )
    assert scored["cat lord"].score > scored["sol ring"].score


def test_a_specific_tag_wins_over_a_generic_one_on_the_same_card():
    """A card sharing both must be graded on the specific relationship, or every
    card with an activated ability ranks alongside real tribal payoffs."""
    store = _commander_store(
        {"typal-cat", "activated-ability"},
        {"oid-both": {"typal-cat", "activated-ability"}},
        breadth={"activated-ability": 0.265, "typal-cat": 0.001},
    )
    scored = MechanicalLayer(store).score(
        [_card("Both", oracle_id="oid-both")],
        ScoringContext(commander="Rin and Seri"),
    )
    assert scored["both"].score == 1.0
    assert "typal-cat" in scored["both"].reason


def test_a_generic_match_still_scores_something():
    """Discounted, not dropped — a card can legitimately relate through a broad
    tag, it just must not outrank a specific one."""
    store = _commander_store(
        {"activated-ability"}, {"oid-g": {"activated-ability"}},
        breadth={"activated-ability": 0.265},
    )
    scored = MechanicalLayer(store).score(
        [_card("Generic", oracle_id="oid-g")],
        ScoringContext(commander="Cmd"),
    )
    assert 0 < scored["generic"].score < 1.0


def test_off_meta_never_zeroes_the_consensus_layer():
    """Draining consensus entirely made consensus-only cards total 0.000, so
    they collapsed and the pool fell back to raw edhrec_rank order."""
    scores = {CONSENSUS: {"popular": LayerScore(CONSENSUS, 0.45)}}
    assert blend(scores, off_meta=1.0)[0].total > 0
