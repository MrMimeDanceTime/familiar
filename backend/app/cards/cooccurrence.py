"""Derive which oracle tags relate, from the cards that carry them.

The brain map needs to answer "does this card do something the commander cares
about". The previous answer was a hand-written list of eight themes, which is a
closed vocabulary someone must keep extending — and every commander outside it
scored zero. Rin and Seri exposed that: a Cat/Dog deck matched nothing until
tribal was special-cased.

Tagger already encodes the relationships. A commander carries tags naming what
it cares about (`your-sacrifice-matters`, `synergy-exile-cast`, `typal-cat`,
`synergy-room`), and the cards that serve it carry related tags. What was
missing is a way to know WHICH tags relate.

## Why co-occurrence rather than slug names

Slug names look like they encode it and do not. Substring matching pairs only
235 of 360 relationship slugs (65%), and the failures are not near-misses:
``draw-matters`` -> ``drawback``, ``name-matters`` -> ``punny-name``. Requiring
hyphen-token boundaries scored *worse* (63%) and still pulled ``punny-name``.
Morphology is an accident of naming.

Co-occurrence measures the real thing. For a pair of tags, ``lift`` is
``P(partner | slug) / P(partner)``: how much more often they share a card than
chance would give. Measured on the live index:

    synergy-exile-cast  -> repeatable-impulsive-draw   57.5x
    your-sacrifice-matters -> hate-removal-sacrifice  249.4x
    damage-increaser    -> synergy-burn               292.9x

That last one is why this generalises. Torbran's only relationship tag is
``synergy-red``, which describes his colour rather than his deck — but his
``damage-increaser`` tag points straight at burn payoffs.

## Colour artifacts

``synergy-red`` is not too broad (171 cards, narrower than ``synergy-artifact``
at 658). It is the wrong KIND of tag: it describes colour, not behaviour. That
is detectable without a maintained blocklist — a colour tag's nearest
neighbours are the other four colour tags (80% of Torbran's top partners),
where every genuine mechanic measures 0%.
"""

from __future__ import annotations

import collections
import logging
from typing import Any

from sqlalchemy import text

from app.db.session import get_engine

logger = logging.getLogger(__name__)

# The five colour-identity tags. A tag surrounded by these is describing colour.
COLOUR_TAGS = frozenset({
    "synergy-white", "synergy-blue", "synergy-black",
    "synergy-red", "synergy-green",
})

# A partner needs to share this many cards before its lift means anything. With
# fewer, one or two coincidental cards produce an enormous lift off tiny counts.
MIN_SHARED = 4

# ...but an absolute floor alone is the wrong instrument. 23% of raw pairs sit
# at exactly 4 shared cards, and raising the floor globally would delete small
# real relationships (`typal-cat` covers only 24 cards) while keeping large
# coincidental ones. So the overlap must also be a meaningful FRACTION of the
# rarer tag: 4 shared cards out of 24 is a relationship, 4 out of 900 is a
# coincidence. This is what removes `landfall` from `synergy-exile-cast`.
MIN_OVERLAP_RATIO = 0.10

# Ignore partners rarer than this: a 2-card tag will co-occur with anything it
# touches at absurd lift, which is noise rather than signal.
MIN_PARTNER_CARDS = 20

# Lift below this is not a relationship worth recording. 2.0 means "twice as
# often as chance", which is a low bar deliberately — filtering happens at query
# time where the caller knows how selective it needs to be.
MIN_LIFT = 2.0

# How many partners to keep per slug. Beyond this the tail is weak relationships
# that only add noise to a pool.
MAX_PARTNERS = 40

# Top-N partners examined when deciding whether a tag is a colour artifact.
_COLOUR_PROBE_DEPTH = 5


def _tag_sets(conn: Any) -> dict[str, set[str]]:
    """oracle_id -> its tags, over commander-legal playable cards only.

    Scoped deliberately: relationships are being derived for deckbuilding, so
    tokens, art cards, and format-illegal cards would only blur the statistics.
    """
    rows = conn.execute(text("""
        SELECT t.oracle_id, t.slug
        FROM card_tags t
        JOIN cards c ON c.oracle_id = t.oracle_id
        WHERE c.legal_commander = 1 AND c.playable = 1
    """)).fetchall()

    by_card: dict[str, set[str]] = collections.defaultdict(set)
    for oracle_id, slug in rows:
        by_card[oracle_id].add(slug)
    return by_card


def compute(conn: Any) -> tuple[list[dict], list[dict]]:
    """Return ``(cooccurrence_rows, trait_rows)`` computed from the card index.

    Pure computation over the tag data — no writes — so it can be tested and
    inspected without touching the database.
    """
    by_card = _tag_sets(conn)
    total_cards = len(by_card)
    if total_cards == 0:
        return [], []

    counts: collections.Counter[str] = collections.Counter()
    for tags in by_card.values():
        counts.update(tags)

    # Pair counts. Only tags on at least MIN_SHARED cards can produce a usable
    # pair, so rare tags are dropped before the quadratic step — without that
    # this is ~4,400 tags of pairwise work for rows that get filtered anyway.
    eligible = {slug for slug, n in counts.items() if n >= MIN_SHARED}
    pair_counts: collections.Counter[tuple[str, str]] = collections.Counter()
    for tags in by_card.values():
        present = sorted(tags & eligible)
        for i, a in enumerate(present):
            for b in present[i + 1:]:
                pair_counts[(a, b)] += 1

    # Lift is symmetric, so each pair is emitted in both directions to make the
    # query side a simple indexed lookup on one column.
    by_slug: dict[str, list[tuple[float, str, int]]] = collections.defaultdict(list)
    for (a, b), shared in pair_counts.items():
        if shared < MIN_SHARED:
            continue
        count_a, count_b = counts[a], counts[b]
        # The overlap has to be a real share of the rarer tag, not just a
        # handful of cards that happen to carry both.
        if shared < min(count_a, count_b) * MIN_OVERLAP_RATIO:
            continue
        expected = (count_a / total_cards) * (count_b / total_cards) * total_cards
        if expected <= 0:
            continue
        lift = shared / expected
        if lift < MIN_LIFT:
            continue
        if count_b >= MIN_PARTNER_CARDS:
            by_slug[a].append((lift, b, shared))
        if count_a >= MIN_PARTNER_CARDS:
            by_slug[b].append((lift, a, shared))

    cooc_rows: list[dict] = []
    for slug, partners in by_slug.items():
        partners.sort(reverse=True)
        for lift, partner, shared in partners[:MAX_PARTNERS]:
            cooc_rows.append({
                "slug": slug, "partner": partner,
                "shared": shared, "lift": round(lift, 3),
            })

    # A tag whose nearest neighbours are the colour tags is describing colour.
    trait_rows: list[dict] = []
    for slug, partners in by_slug.items():
        top = [p for _lift, p, _shared in sorted(partners, reverse=True)[:_COLOUR_PROBE_DEPTH]]
        colour_share = (
            sum(1 for p in top if p in COLOUR_TAGS) / len(top) if top else 0.0
        )
        trait_rows.append({
            "slug": slug,
            "card_count": counts[slug],
            "colour_share": round(colour_share, 3),
        })

    return cooc_rows, trait_rows


def rebuild(batch_size: int = 5000) -> int:
    """Recompute both derived tables. Returns co-occurrence rows written.

    Called after a card-index import, since it is derived entirely from
    ``card_tags`` and ``cards``. Writes in short transactions for the same
    reason the importer does: never hold SQLite's single write lock long enough
    to block a live request.
    """
    engine = get_engine()
    with engine.begin() as conn:
        cooc_rows, trait_rows = compute(conn)

    if not cooc_rows:
        logger.warning("tag co-occurrence: nothing computed (empty tag index?)")
        return 0

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM tag_cooccurrence"))
        conn.execute(text("DELETE FROM tag_traits"))

    insert_cooc = text(
        "INSERT OR REPLACE INTO tag_cooccurrence (slug, partner, shared, lift) "
        "VALUES (:slug, :partner, :shared, :lift)"
    )
    for i in range(0, len(cooc_rows), batch_size):
        with engine.begin() as conn:
            conn.execute(insert_cooc, cooc_rows[i : i + batch_size])

    insert_trait = text(
        "INSERT OR REPLACE INTO tag_traits (slug, card_count, colour_share) "
        "VALUES (:slug, :card_count, :colour_share)"
    )
    for i in range(0, len(trait_rows), batch_size):
        with engine.begin() as conn:
            conn.execute(insert_trait, trait_rows[i : i + batch_size])

    logger.info(
        "tag co-occurrence: %d pairs across %d slugs",
        len(cooc_rows), len(trait_rows),
    )
    return len(cooc_rows)
