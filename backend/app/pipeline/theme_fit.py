"""Archetype-conditioned EDHREC play rates.

The commander page's rates are averaged over every build of the commander:
"40% of Korlash decks run this" mixes the Voltron Korlash with the Swamps-ramp
Korlash, and a card that half of one archetype runs and none of the other
looks lukewarm to both. EDHREC also publishes each commander's themes with
their own pages (``commander_themes``), computed over only that theme's decks.

This module matches the deck being built to those themes and reads a
candidate's play rate through the match:

  * each theme is weighted by how much of the deck is that theme's
    DISTINCTIVE cards: the mean, over the deck's cards, of how far their play
    rate on the theme page sits above the commander page. Raw play rates were
    tried first and matched every deck to every theme about equally, because
    the staples all themes share dominate them. Weights are a softmax over
    that lift, sharp enough to commit to the themes the deck leans into;
  * ``theme_rate`` is a candidate's play rate averaged over the themes by
    that weight, and ``theme_lift`` is how far it sits above or below the
    commander-wide rate. A rate resting on fewer than ``MIN_DECKS`` decks
    falls back to the commander-wide one.

Everything comes from EDHREC's own aggregates; no decklist is fetched.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from app.pipeline import edhrec_source

logger = logging.getLogger(__name__)

MAX_THEMES = 6
MIN_DECKS = 20
# Softmax temperature over the deck's mean theme lift (a difference of play
# rates, typically 0.00-0.10 between themes).
TEMPERATURE = 0.02


@dataclass
class ThemeProfile:
    """Which of the commander's themes this deck leans into, and every page
    card's play rate under them."""

    weights: dict[str, float] = field(default_factory=dict)
    base: dict[str, float] = field(default_factory=dict)
    themed: dict[str, dict[str, float]] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not self.weights

    def rate(self, name: str) -> float | None:
        key = name.lower()
        if self.is_empty():
            return None
        total = 0.0
        seen = False
        for theme, weight in self.weights.items():
            value = self.themed[theme].get(key)
            if value is None:
                value = self.base.get(key)
            if value is not None:
                seen = True
                total += weight * value
        return total if seen else None

    def lift(self, name: str) -> float | None:
        rate = self.rate(name)
        base = self.base.get(name.lower())
        if rate is None or base is None:
            return None
        return rate - base

    def top_theme(self) -> str | None:
        return max(self.weights, key=self.weights.get) if self.weights else None


def _rates(recs: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for card in edhrec_source.collect(recs, per_list_cap=10_000):
        if card.inclusion_rate is None:
            continue
        if card.potential_decks is not None and card.potential_decks < MIN_DECKS:
            continue
        out[card.name.lower()] = card.inclusion_rate
    return out


def _deck_lift(rates: dict[str, float], base: dict[str, float], deck: list[str]) -> float:
    lifts = [rates[n] - base.get(n, 0.0) for n in deck if n in rates]
    return sum(lifts) / max(1, len(deck))


def build_profile(commander: str | None, deck_card_names: list[str], client: Any) -> ThemeProfile:
    """Match the deck to the commander's themes. Empty on any failure, or when
    the commander has no themes; callers treat that as "no theme signal"."""
    if not commander:
        return ThemeProfile()
    try:
        base = _rates(client.commander_recs(commander))
        themes = client.commander_themes(commander)[:MAX_THEMES]
        deck = [n.lower() for n in deck_card_names if n and n.lower() != commander.lower()]
        themed: dict[str, dict[str, float]] = {}
        raw_weights: dict[str, float] = {}
        for theme in themes:
            try:
                rates = _rates(client.commander_recs(commander, theme=theme["slug"]))
            except Exception as exc:  # noqa: BLE001 - one missing theme page is fine
                logger.info("theme page %s/%s unavailable: %s", commander, theme["slug"], exc)
                continue
            if not rates:
                continue
            themed[theme["slug"]] = rates
            raw_weights[theme["slug"]] = _deck_lift(rates, base, deck)
        if not raw_weights:
            return ThemeProfile()
        top = max(raw_weights.values())
        exp = {slug: math.exp((w - top) / TEMPERATURE) for slug, w in raw_weights.items()}
        total = sum(exp.values())
        weights = {slug: e / total for slug, e in exp.items()}
        return ThemeProfile(weights=weights, base=base, themed=themed)
    except Exception as exc:  # noqa: BLE001 - a bonus signal, never a dependency
        logger.warning("theme profile for %r failed: %s", commander, exc)
        return ThemeProfile()


def annotate(pool: list[dict[str, Any]], profile: ThemeProfile) -> None:
    """Add ``theme_rate`` and ``theme_lift`` to each pool card's ``edhrec``
    block, creating it for cards EDHREC's commander page did not carry."""
    if profile.is_empty():
        return
    for card in pool:
        name = card.get("name") or ""
        rate = profile.rate(name)
        if rate is None:
            continue
        block = dict(card.get("edhrec") or {})
        block["theme_rate"] = rate
        block["theme_lift"] = profile.lift(name)
        card["edhrec"] = block
