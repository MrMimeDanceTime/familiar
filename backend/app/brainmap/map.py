"""The brain map: assemble the layers, score a pool, explain the result.

One entry point (``score_pool``) so callers never have to know how many layers
exist or how they are weighted. Adding a fourth layer later is a change here and
nowhere else.
"""

from __future__ import annotations

import logging
from typing import Any

from app.brainmap.consensus import ConsensusLayer
from app.brainmap.layers import (
    CONSENSUS,
    MECHANICAL,
    PERSONAL,
    CardScore,
    ScoringContext,
    blend,
)
from app.brainmap.mechanical import MechanicalLayer
from app.brainmap.personal import PersonalLayer

logger = logging.getLogger(__name__)


def build_layers(store: Any, engine: Any) -> list[Any]:
    """The default layer stack."""
    return [ConsensusLayer(), MechanicalLayer(store), PersonalLayer(engine)]


def score_pool(
    cards: list[dict],
    context: ScoringContext,
    *,
    store: Any,
    engine: Any,
    off_meta: float = 0.0,
    layers: list[Any] | None = None,
) -> list[CardScore]:
    """Score a candidate pool across every layer and return it ranked.

    A layer that raises is dropped with a warning rather than sinking the
    suggestion: a scoring layer is an improvement over unranked candidates, not
    a precondition for producing any.
    """
    layers = layers if layers is not None else build_layers(store, engine)

    scores: dict[str, dict] = {}
    for layer in layers:
        try:
            scores[layer.name] = layer.score(cards, context)
        except Exception as exc:  # noqa: BLE001
            logger.warning("brain map layer %r failed: %s", layer.name, exc)
            scores[layer.name] = {}

    active = [name for name, entries in scores.items() if entries]
    logger.info(
        "brain map scored %d cards; layers with signal: %s (off_meta=%.2f)",
        len(cards), ", ".join(active) or "none", off_meta,
    )
    return blend(scores, off_meta=off_meta)


def annotate_pool(
    cards: list[dict], ranked: list[CardScore]
) -> list[dict]:
    """Attach each card's scores back onto the pool, ordered by total.

    Cards the map has no opinion about keep their original relative order at the
    end rather than being dropped — an unscored card is not a bad card, it is
    one no layer had anything to say about.
    """
    by_name = {score.name: score for score in ranked}
    order = {score.name: i for i, score in enumerate(ranked)}

    annotated: list[dict] = []
    for card in cards:
        name = (card.get("name") or "").strip().lower()
        entry = dict(card)
        score = by_name.get(name)
        if score is not None:
            entry["brainmap"] = {
                "total": round(score.total, 3),
                "consensus": round(score.get(CONSENSUS), 3),
                "mechanical": round(score.get(MECHANICAL), 3),
                "personal": round(score.get(PERSONAL), 3),
                "explain": score.explain(),
            }
        annotated.append(entry)

    annotated.sort(
        key=lambda c: order.get((c.get("name") or "").strip().lower(), len(order))
    )
    return annotated
