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


def fold_into(
    selection: Any,
    missing: list[dict[str, Any]],
    request_roles: set[str],
    ctx: Any,
    commander: str | None,
) -> Any:
    """Put the missing staples that fill the requested role at the front of
    a role batch, keeping the batch its size.

    The chat model used to be told to propose staples itself, in their own
    batch, while the engine allows one batch per turn: a "give me ramp" turn
    had no legal path, and the model spent up to 5,000 reasoning tokens (65s)
    trying to find one. Folding them in here removes the decision.
    """
    if not missing or not request_roles or not selection.picks:
        return selection
    from app.cards import store
    from app.pipeline import roles, shaping
    from app.pipeline.selection import Pick, Selection

    wanted = roles.coarse_for_fine(request_roles)
    taken = {p.name.lower() for p in selection.picks}
    cards = store.by_names([m["name"] for m in missing])
    tags = store.tags_for_many([c["oracle_id"] for c in cards.values() if c.get("oracle_id")])
    staples: list[Pick] = []
    for m in missing:
        card = cards.get(m["name"].lower())
        if card is None or card["name"].lower() in taken:
            continue
        fine = roles.fine_roles_for_tags(tags.get(card.get("oracle_id"), set()), card.get("type_line"))
        if roles.LAND in fine or not roles.coarse_for_fine(fine) & wanted:
            continue
        if not shaping.legal_in_deck(card, ctx)[0]:
            continue
        staples.append(Pick(
            name=card["name"],
            reason=f"Format staple: {m.get('why') or 'played in most ' + (commander or '') + ' decks'}.",
        ))
    if not staples:
        return selection
    size = len(selection.picks)
    return Selection(
        picks=(staples + selection.picks)[:size],
        cuts=getattr(selection, "cuts", []),
        summary=selection.summary,
        raw=selection.raw,
    )
