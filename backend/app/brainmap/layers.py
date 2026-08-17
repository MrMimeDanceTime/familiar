"""The three scoring layers, kept deliberately separate.

A card's fitness for a deck is not one number, and collapsing it into one is
how a recommender becomes a conformist. Three independent signals, each with a
different blind spot:

1. **Consensus** — EDHREC play rate and synergy. What people actually run.
   Reliable and well-evidenced, but it is an aggregate of what has already been
   built, so it reports consensus and lags discovery. It cannot tell you a card
   is good, only that it is popular.

2. **Mechanical** — oracle-tag relationships from the local index. What *works*
   with the commander, whether or not anyone plays it. Measured on Korvold:
   326 free/repeatable sacrifice outlets exist in Jund identity and 305 of them
   are absent from EDHREC's page, including Carrion Feeder, Greater Good,
   Altar of Dementia, and Krark-Clan Ironworks. This is where finds come from.

3. **Personal** — the player's own accept/deny history. The only signal not
   derived from what everyone else built, and the counterweight that keeps the
   map from producing a very well-informed conformist.

They stay separate for three reasons. The off-meta control is a mix weight
*across* layers, not a single dial. The system can explain itself ("high
mechanical, low consensus" is a sentence worth showing, and it is exactly the
underplayed-but-correct case). And a layer that has no data yet — layer 3 on a
fresh install — contributes nothing rather than silently biasing a blended
score.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

CONSENSUS = "consensus"
MECHANICAL = "mechanical"
PERSONAL = "personal"

LAYER_NAMES: tuple[str, ...] = (CONSENSUS, MECHANICAL, PERSONAL)


@dataclass(frozen=True)
class LayerScore:
    """One layer's opinion about one card.

    ``score`` is normalised to 0.0-1.0 so layers are comparable. ``reason`` is
    the human-readable why, which is the part that makes a suggestion
    inspectable rather than an oracle.
    """

    layer: str
    score: float
    reason: str = ""


@dataclass
class CardScore:
    """Every layer's opinion about one card, plus the blended result."""

    name: str
    oracle_id: str | None = None
    layers: dict[str, LayerScore] = field(default_factory=dict)
    total: float = 0.0

    def get(self, layer: str) -> float:
        entry = self.layers.get(layer)
        return entry.score if entry else 0.0

    def reasons(self) -> list[str]:
        return [s.reason for s in self.layers.values() if s.reason]

    def explain(self) -> str:
        """Why this card scored what it did, as one line.

        Names the layers that actually contributed. A card that is
        high-mechanical and low-consensus reads differently from a staple, and
        that difference is the most useful thing the map knows.
        """
        parts = [
            f"{name} {self.layers[name].score:.2f}"
            for name in LAYER_NAMES
            if name in self.layers and self.layers[name].score > 0
        ]
        detail = "; ".join(self.reasons())
        head = ", ".join(parts) if parts else "no signal"
        return f"{head}{' — ' + detail if detail else ''}"


class Layer(Protocol):
    """A scoring layer.

    Implementations return ``{lowercased card name: LayerScore}``. A layer with
    nothing to say returns an empty dict — that is the normal state for the
    personal layer on a fresh install, and it must not be confused with scoring
    everything zero.
    """

    name: str

    def score(self, cards: list[dict], context: "ScoringContext") -> dict[str, LayerScore]:
        ...


@dataclass
class ScoringContext:
    """What the layers need to know about the deck being built."""

    commander: str | None = None
    identity: frozenset[str] = frozenset()
    themes: list[str] = field(default_factory=list)
    deck_card_names: frozenset[str] = frozenset()
    deck_id: int | None = None


# Default mix. Consensus leads because it is the best-evidenced signal, but
# mechanical carries real weight because it is the only layer that can surface
# a card nobody has thought to play yet. Personal is small by default and grows
# in influence naturally as history accumulates — it scores nothing when there
# is no history, so its weight is simply unused rather than diluting the rest.
# Floor on the confidence multiplier. A card scored by one layer keeps most of
# its value — the layer that spoke may well be right — but still ranks below an
# equally-scored card several layers agree on.
#
# 0.70 is the highest value that preserves that ordering: at 0.75 a lone 1.00
# ties a three-layer 0.80, which is the "overconfident from thin evidence" bug
# this floor exists to prevent.
_MIN_CONFIDENCE = 0.70

DEFAULT_WEIGHTS: dict[str, float] = {
    CONSENSUS: 0.5,
    MECHANICAL: 0.3,
    PERSONAL: 0.2,
}


def blend(
    scores: dict[str, dict[str, LayerScore]],
    *,
    weights: dict[str, float] | None = None,
    off_meta: float = 0.0,
) -> list[CardScore]:
    """Combine per-layer scores into a ranked list.

    ``off_meta`` (0.0-1.0) shifts weight from consensus toward mechanical. At 0
    the map recommends what people play; at 1 it recommends what works with this
    commander regardless of popularity. This is the same knob EDHREC ranking
    uses, applied one level up: it is what stops two decks on the same commander
    returning the same list.

    Weights are renormalised over the layers that actually produced a score, so
    an absent layer does not silently shrink every card's total. Without that, a
    fresh install (no personal history) would score every card at most 0.8 and
    the numbers would stop meaning anything.
    """
    weights = dict(weights or DEFAULT_WEIGHTS)

    shift = max(0.0, min(1.0, off_meta))
    consensus_weight = weights.get(CONSENSUS, 0.0)
    mechanical_weight = weights.get(MECHANICAL, 0.0)
    movable = consensus_weight * shift
    weights[CONSENSUS] = consensus_weight - movable
    weights[MECHANICAL] = mechanical_weight + movable

    by_card: dict[str, CardScore] = {}
    for layer_name, entries in scores.items():
        for key, entry in entries.items():
            card = by_card.setdefault(key, CardScore(name=key))
            card.layers[layer_name] = entry

    # Renormalise PER CARD over the layers that scored THAT card, not globally
    # over the layers that scored anything.
    #
    # Global renormalisation silently punishes a card the other layers know
    # nothing about. Measured on a real Rin and Seri pool: Cat Collector scored
    # mechanical 1.00 with no EDHREC entry (it is a fine card that simply is not
    # on the commander's page) and came out at 0.42 — below staples scoring 0.62
    # on consensus alone. 43 of 60 cards landed at exactly 0.00, so most of the
    # pool reached the selection model effectively unranked.
    #
    # "Unlisted" is not "bad". A layer with no opinion about a card must not
    # drag that card's total down.
    # How much of the total weight has to back a score before it is taken at
    # face value. Below this, the score is shaded toward the neutral midpoint in
    # proportion to how thin the evidence is.
    #
    # This is the counterweight to per-card renormalisation. Without it, one
    # layer scoring 1.0 outranks a card all three layers agree is 0.8 — trading
    # the "unlisted is punished" bug for an "overconfident from thin evidence"
    # bug. A card is not better than a broadly-endorsed one just because only
    # one layer happened to have an opinion.
    # Shading pulls a thinly-evidenced score DOWN toward neutral, never up.
    #
    # Blending toward a fixed midpoint moved thin scores in whichever direction
    # the midpoint lay, which quietly rewarded ignorance: a card scoring 0.70 on
    # mechanical ALONE was lifted toward 0.35 from below... but a card scoring
    # 0.70 mechanical AND 0.30 consensus averaged to 0.43 and then got shaded
    # too. Measured on the real Myrkul pool, Font of Fertility (EDHREC rank
    # 6,410, mechanical only) beat Rampant Growth (rank 26, mechanical AND
    # consensus) 0.455 to 0.430 — being known by more layers made a card score
    # WORSE.
    #
    # More evidence must never cost a card. So confidence can only reduce a
    # score, and a fully-evidenced card keeps its raw value.
    full_weight = sum(weights.get(name, 0.0) for name in LAYER_NAMES) or 1.0

    for card in by_card.values():
        contributing = [name for name in card.layers if name in weights]
        card_weight = sum(weights[name] for name in contributing)
        if card_weight <= 0:
            card.total = 0.0
            continue
        raw = sum(
            card.get(name) * weights[name] for name in contributing
        ) / card_weight
        # Floored at a partial credit rather than scaling linearly, so a card
        # known to one layer is discounted for thin evidence without being
        # buried beneath cards no layer has an opinion about.
        confidence = _MIN_CONFIDENCE + (1.0 - _MIN_CONFIDENCE) * (
            card_weight / full_weight
        )
        card.total = raw * confidence

    # Consensus breaks ties. The mechanical layer scores in a few discrete bands
    # (engine piece / payoff / enabler), so at high off_meta many cards land on
    # an identical total and their order would otherwise be arbitrary. Falling
    # back to play rate keeps the ordering stable and sensible without letting
    # consensus back into the weighting itself.
    return sorted(
        by_card.values(),
        key=lambda c: (c.total, c.get(CONSENSUS)),
        reverse=True,
    )
