"""Tag co-occurrence: deriving which tags relate, from the cards that carry them.

Slug names look like they encode relationships and do not. Substring matching
pairs only 235 of 360 relationship slugs (65%) and produces `draw-matters` ->
`drawback` and `name-matters` -> `punny-name`. Requiring hyphen-token
boundaries scored WORSE (63%). Morphology is an accident of naming, so the
pairing is measured from shared cards instead.
"""

import pytest
from sqlalchemy import text

from app.cards import cooccurrence, schema, store


@pytest.fixture
def tag_db(tmp_path, monkeypatch):
    from app.config import settings
    from app.db import session as db_session

    monkeypatch.setattr(settings, "familiar_db_path", str(tmp_path / "cooc.db"))
    db_session.get_engine.cache_clear()
    schema.ensure_schema()
    yield db_session.get_engine()
    db_session.get_engine.cache_clear()


def _seed(engine, cards):
    """cards: {oracle_id: [slugs]} — every card is commander-legal and playable."""
    with engine.begin() as conn:
        for oracle_id, slugs in cards.items():
            conn.execute(
                text(
                    "INSERT INTO cards (oracle_id, name, legal_commander, playable, raw) "
                    "VALUES (:o, :n, 1, 1, '{}')"
                ),
                {"o": oracle_id, "n": oracle_id},
            )
            for slug in slugs:
                conn.execute(
                    text("INSERT INTO card_tags (oracle_id, slug) VALUES (:o, :s)"),
                    {"o": oracle_id, "s": slug},
                )


def test_related_tags_are_discovered(tag_db):
    """Cards carrying both tags and little else should pair.

    Fixture sizes clear the production thresholds (a partner needs 20+ cards)
    rather than the thresholds being lowered to suit the test — they were
    measured against the real 4,383-slug vocabulary."""
    cards = {f"c{i}": ["payoff", "enabler"] for i in range(25)}
    cards.update({f"n{i}": ["unrelated"] for i in range(60)})
    _seed(tag_db, cards)

    cooccurrence.rebuild()

    partners = store.related_tags(["payoff"], min_lift=2.0)
    assert "enabler" in partners


def test_unrelated_tags_do_not_pair(tag_db):
    cards = {f"a{i}": ["payoff"] for i in range(30)}
    cards.update({f"b{i}": ["unrelated"] for i in range(30)})
    _seed(tag_db, cards)

    cooccurrence.rebuild()

    assert "unrelated" not in store.related_tags(["payoff"], min_lift=2.0)


def test_own_tags_come_back_at_maximum_strength(tag_db):
    """A card carrying the commander's actual tag is the strongest match there
    is, so the input slugs must outrank anything derived."""
    _seed(tag_db, {f"c{i}": ["payoff", "enabler"] for i in range(25)})
    cooccurrence.rebuild()

    partners = store.related_tags(["payoff"], min_lift=2.0)
    assert partners["payoff"] == float("inf")


def test_coincidental_overlap_is_rejected(tag_db):
    """Four shared cards between a rare tag and a common one is a coincidence,
    not a relationship — 23% of raw pairs sat at exactly four shared cards."""
    cards = {f"both{i}": ["rare", "common"] for i in range(4)}
    cards.update({f"rare{i}": ["rare"] for i in range(4)})
    cards.update({f"common{i}": ["common"] for i in range(200)})
    _seed(tag_db, cards)

    cooccurrence.rebuild()

    with tag_db.begin() as conn:
        rows = conn.execute(
            text("SELECT partner FROM tag_cooccurrence WHERE slug = 'rare'")
        ).fetchall()
    assert not rows


def test_colour_artifact_is_flagged(tag_db):
    """A tag whose neighbours are the colour tags describes colour, not
    behaviour. Torbran's `synergy-red` measured 80% against 0% for every real
    mechanic."""
    cards = {}
    for i in range(25):
        cards[f"multi{i}"] = [
            "synergy-red", "synergy-blue", "synergy-white",
            "synergy-black", "synergy-green",
        ]
    for i in range(40):
        cards[f"pad{i}"] = ["filler"]
    _seed(tag_db, cards)

    cooccurrence.rebuild()

    assert store.is_colour_artifact("synergy-red") is True


def test_real_mechanic_is_not_flagged(tag_db):
    cards = {f"c{i}": ["your-sacrifice-matters", "sacrifice-outlet"] for i in range(25)}
    cards.update({f"pad{i}": ["filler"] for i in range(40)})
    _seed(tag_db, cards)

    cooccurrence.rebuild()

    assert store.is_colour_artifact("your-sacrifice-matters") is False


def test_rebuild_is_idempotent(tag_db):
    _seed(tag_db, {f"c{i}": ["payoff", "enabler"] for i in range(25)})

    first = cooccurrence.rebuild()
    second = cooccurrence.rebuild()

    assert first == second


def test_empty_index_does_not_raise(tag_db):
    assert cooccurrence.rebuild() == 0


def test_related_tags_with_no_input(tag_db):
    assert store.related_tags([]) == {}


def test_lift_is_symmetric(tag_db):
    """Pairs are stored both ways so the query side is a single indexed lookup."""
    _seed(tag_db, {f"c{i}": ["payoff", "enabler"] for i in range(25)})
    cooccurrence.rebuild()

    with tag_db.begin() as conn:
        forward = conn.execute(text(
            "SELECT lift FROM tag_cooccurrence WHERE slug='payoff' AND partner='enabler'"
        )).scalar()
        backward = conn.execute(text(
            "SELECT lift FROM tag_cooccurrence WHERE slug='enabler' AND partner='payoff'"
        )).scalar()
    assert forward == backward
