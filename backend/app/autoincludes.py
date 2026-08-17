"""Cards a deck should have regardless of what it is trying to do.

Sol Ring goes in ~90% of Commander decks. It does not need a ramp batch to
justify it, and it should not wait for one — reported from a real build, it took
five rounds to surface because it scored rank 11 in a pool where stage 4 takes
ten picks. It never lost on merit; it just sat outside the cut every time.

## What makes a card an auto-include

Not play rate alone. EDHREC's own numbers separate two kinds of popular card,
measured on a Myrkul pool:

    Sol Ring            78% play, synergy -0.01
    Command Tower       90% play, synergy -0.01
    Arcane Signet       66% play, synergy  0.00
    Eidolon of Blossoms 69% play, synergy +0.59
    Sanctum Weaver      67% play, synergy +0.57

The first three are format staples: everyone plays them, and playing them says
nothing about this deck. The last two are commander-specific — high play rate
BECAUSE of the commander, which is exactly what synergy measures.

Only the first kind belongs here. The second kind is what the suggestion
pipeline is for, and pulling it out would hollow the batches of their best
picks.

Basic lands are excluded: they clear both bars trivially and are handled by the
manabase, not by a card recommendation.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Played in at least this share of the commander's decks.
_MIN_PLAY_RATE = 0.55

# ...and no more commander-specific than this. A card with real synergy is a
# recommendation for THIS deck and belongs in a scored batch, not on a
# should-already-be-here list.
_MAX_SYNERGY = 0.10

_BASIC_LANDS = frozenset({
    "plains", "island", "swamp", "mountain", "forest", "wastes",
    "snow-covered plains", "snow-covered island", "snow-covered swamp",
    "snow-covered mountain", "snow-covered forest",
})


def find_missing(
    commander: str | None,
    deck_card_names: set[str],
    *,
    edhrec: Any,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Auto-include cards this deck does not have yet, most-played first.

    Degrades to an empty list on any EDHREC failure — a missing staple list is
    a lost convenience, never a reason for a deck read to fail.
    """
    if not commander:
        return []

    from app.pipeline import edhrec_source

    try:
        recs = edhrec.commander_recs(commander)
        candidates = edhrec_source.collect(recs, include_type_lists=True)
    except Exception as exc:  # noqa: BLE001 — advisory only
        logger.info("auto-includes unavailable for %r: %s", commander, exc)
        return []

    owned = {n.lower() for n in deck_card_names}
    out: list[dict[str, Any]] = []
    for card in candidates:
        name_lower = card.name.lower()
        if name_lower in owned or name_lower in _BASIC_LANDS:
            continue
        rate = card.inclusion_rate
        if rate is None or rate < _MIN_PLAY_RATE:
            continue
        # A card with real synergy is a recommendation, not a staple.
        if card.synergy is not None and card.synergy > _MAX_SYNERGY:
            continue
        out.append({
            "name": card.name,
            "play_rate": round(rate, 3),
            "why": f"in {rate * 100:.0f}% of {commander} decks",
        })

    out.sort(key=lambda c: c["play_rate"], reverse=True)
    return out[:limit]
