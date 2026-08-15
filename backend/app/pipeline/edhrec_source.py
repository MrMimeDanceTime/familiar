"""EDHREC as a candidate SOURCE, not a garnish.

Stage 2 fetched the commander's EDHREC page and used it only to annotate cards
Scryfall had already returned. A card EDHREC recommends that no query happened
to match could not enter the pool at all — the best available signal for "what
actually goes in this deck" was structurally unable to contribute a candidate.
And the annotation it did produce was dropped by ``shaping._STRIP_FIELDS``
before stage 4 ever saw it, so the selection model never knew a play rate.

This module makes the page a real source: pull its cardlists, hydrate the names
through the local card index, and hand them back as pool entries.

## The two numbers, and why they are not interchangeable

EDHREC gives each card ``synergy`` and ``inclusion``. They pull in opposite
directions and only one of them is what its name suggests.

``inclusion`` is **a raw deck count, not a rate** — verified 2026-08-15, it
equals ``num_decks`` exactly. The rate is ``num_decks / potential_decks``, and
the denominator varies per card because a newer card has had fewer eligible
decks (2,893 for a recent printing vs 20,489 for an established one). Sorting on
``inclusion`` therefore ranks by raw popularity AND quietly buries new cards.
This module computes the rate instead.

``synergy`` is a true differential: inclusion in THIS commander's decks minus
inclusion in decks of the same colours. It is the interesting number. A card at
75% play and +0.02 synergy is a staple every deck in these colours runs; a card
at 20% play and +0.18 is doing something specific to this commander.

Measured on Korvold: the "High Synergy Cards" list averages ~+0.42 synergy while
"Top Cards" averages ~+0.15 at comparable play rates. Same popularity, opposite
meaning. Ranking on popularity alone is exactly how every deck converges on the
same hundred cards.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# EDHREC cardlist tags that carry a meaningful signal, mapped to how we label
# them. Type buckets (creatures, instants, lands…) are deliberately excluded:
# they are just the same cards re-sorted, so pulling them in would flood the
# pool with duplicates of what these three lists already surface.
SIGNAL_LISTS: dict[str, str] = {
    "highsynergycards": "high-synergy",
    "topcards": "top-card",
    "gamechangers": "game-changer",
    "newcards": "new-card",
}

# Lists to pull from when taking a broader sweep (a deck early in its build
# wants breadth, not just the top of each signal list).
TYPE_LISTS: frozenset[str] = frozenset({
    "creatures", "instants", "sorceries", "enchantments", "planeswalkers",
    "utilityartifacts", "manaartifacts", "utilitylands", "lands",
})


@dataclass(frozen=True)
class EdhrecCard:
    """One EDHREC recommendation, with the rate computed rather than assumed."""

    name: str
    synergy: float | None
    num_decks: int | None
    potential_decks: int | None
    category: str
    signal: str

    @property
    def inclusion_rate(self) -> float | None:
        """Fraction of eligible decks running this card, or None if unknowable.

        This is the number ``inclusion`` looks like but is not. Computed per
        card because ``potential_decks`` differs between an old card and a new
        one, which is what makes a raw count misleading.
        """
        if not self.num_decks or not self.potential_decks:
            return None
        return self.num_decks / self.potential_decks


def _card_from_view(view: dict[str, Any], category: str, signal: str) -> EdhrecCard | None:
    name = (view.get("name") or "").strip()
    if not name:
        return None
    synergy = view.get("synergy")
    return EdhrecCard(
        name=name,
        synergy=float(synergy) if isinstance(synergy, (int, float)) else None,
        num_decks=view.get("num_decks"),
        potential_decks=view.get("potential_decks"),
        category=category,
        signal=signal,
    )


def collect(
    recs: dict[str, Any],
    *,
    include_type_lists: bool = True,
    per_list_cap: int = 25,
) -> list[EdhrecCard]:
    """Flatten a commander's EDHREC page into deduped recommendations.

    Signal lists come first so that when a card appears in several lists it
    keeps its most informative label — being a "high-synergy" card says more
    than being one of fifty creatures.
    """
    out: list[EdhrecCard] = []
    seen: set[str] = set()
    categories = recs.get("categories") or {}

    def _take(tag: str, signal: str) -> None:
        entry = categories.get(tag)
        if not isinstance(entry, dict):
            return
        header = entry.get("header") or tag
        for view in (entry.get("cards") or [])[:per_list_cap]:
            if not isinstance(view, dict):
                continue
            card = _card_from_view(view, header, signal)
            if card is None or card.name.lower() in seen:
                continue
            seen.add(card.name.lower())
            out.append(card)

    for tag, signal in SIGNAL_LISTS.items():
        _take(tag, signal)

    if include_type_lists:
        for tag in categories:
            if tag in TYPE_LISTS:
                _take(tag, "by-type")

    return out


def rank(cards: list[EdhrecCard], *, off_meta: float = 0.0) -> list[EdhrecCard]:
    """Order recommendations, trading popularity against commander-specificity.

    ``off_meta`` runs 0.0 to 1.0. At 0 the ranking follows play rate, which
    reproduces the consensus list. At 1 it follows synergy alone, surfacing
    cards that are specific to this commander even when few decks run them.

    This is the knob that stops every deck becoming the same deck. It exists
    because the two signals genuinely disagree: ranking on play rate alone
    returns the colour-pair staples, which is what "EDHREC statistical copy"
    means in practice.
    """
    weight = max(0.0, min(1.0, off_meta))

    def key(card: EdhrecCard) -> float:
        rate = card.inclusion_rate or 0.0
        synergy = card.synergy or 0.0
        # Synergy is roughly [-0.2, 0.5] while rate is [0, 1]; scaling synergy
        # keeps the two comparable so the weight behaves like a real blend
        # rather than one term always dominating.
        return (1.0 - weight) * rate + weight * (synergy * 2.0)

    return sorted(cards, key=key, reverse=True)


def hydrate(
    cards: list[EdhrecCard], store: Any, identity: frozenset[str] | None = None
) -> list[dict[str, Any]]:
    """Turn recommendations into pool entries via the local card index.

    EDHREC gives names, and the pipeline needs full card objects. The local
    index resolves the whole list in one query instead of N API calls, which is
    what makes EDHREC usable as a source rather than only as an annotation.

    A name the index cannot resolve is dropped with a log line rather than
    guessed at: EDHREC occasionally lists a card under a name spelling the
    index does not carry, and a wrong card is worse than a missing one.
    """
    if not cards:
        return []

    resolved = store.by_names([c.name for c in cards])
    out: list[dict[str, Any]] = []
    missing: list[str] = []

    for card in cards:
        hit = resolved.get(card.name.lower())
        if hit is None:
            missing.append(card.name)
            continue
        if identity is not None:
            card_identity = {c for c in (hit.get("color_identity") or []) if c}
            if not card_identity.issubset(identity):
                continue
        entry = dict(hit)
        entry["edhrec"] = {
            "synergy": card.synergy,
            "inclusion_rate": card.inclusion_rate,
            "num_decks": card.num_decks,
            "category": card.category,
            "signal": card.signal,
        }
        out.append(entry)

    if missing:
        logger.info(
            "EDHREC: %d recommendation(s) not in the card index (e.g. %s)",
            len(missing), ", ".join(missing[:3]),
        )
    return out
