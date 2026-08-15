"""Layer 1 — what people actually play.

Reads the EDHREC annotation stage 2 attaches. Well-evidenced and the most
reliable single signal available, with one structural blind spot: it aggregates
decks that have already been built, so it reports consensus and lags discovery.
It can tell you a card is popular; it cannot tell you a card is good.

Both EDHREC numbers are used, because they mean different things and the
difference is the entire point. Play rate says "most decks run this". Synergy
says "this is more common here than in other decks of these colours". A card at
75% play and +0.02 synergy is a colour-pair staple; one at 20% and +0.18 is
doing something specific to this commander.
"""

from __future__ import annotations

from app.brainmap.layers import CONSENSUS, LayerScore, ScoringContext

# Synergy is roughly [-0.2, +0.5] in practice. Normalising against a 0.5 ceiling
# keeps a strong-synergy card near 1.0 without letting an outlier dominate.
_SYNERGY_CEILING = 0.5


def _normalise_synergy(value: float) -> float:
    return max(0.0, min(1.0, value / _SYNERGY_CEILING))


class ConsensusLayer:
    """Scores cards on EDHREC play rate blended with commander-specific synergy."""

    name = CONSENSUS

    def __init__(self, *, synergy_weight: float = 0.5) -> None:
        # An even split by default: popularity alone reproduces the colour-pair
        # staple list, and synergy alone over-rewards narrow cards that only a
        # handful of decks run.
        self._synergy_weight = max(0.0, min(1.0, synergy_weight))

    def score(
        self, cards: list[dict], context: ScoringContext
    ) -> dict[str, LayerScore]:
        """Score the pool on EDHREC data.

        A card with no EDHREC entry is skipped, NOT scored zero. "Unlisted" and
        "unpopular" are different claims: EDHREC only covers cards that appear
        on the commander's page, so a perfectly good card that no query surfaced
        into that page has no consensus opinion at all. Scoring it zero would
        rank it below cards with a genuinely terrible play rate.

        The blend already handles this correctly — it renormalises over the
        layers that produced a score for each card — so skipping means "no
        opinion" and lets the other layers decide.
        """
        out: dict[str, LayerScore] = {}
        for card in cards:
            data = card.get("edhrec")
            name = (card.get("name") or "").strip()
            if not data or not name:
                continue

            rate = data.get("inclusion_rate")
            synergy = data.get("synergy")
            rate_value = float(rate) if isinstance(rate, (int, float)) else 0.0
            synergy_value = (
                _normalise_synergy(float(synergy))
                if isinstance(synergy, (int, float)) else 0.0
            )

            score = (
                (1.0 - self._synergy_weight) * rate_value
                + self._synergy_weight * synergy_value
            )
            if score <= 0:
                continue

            bits: list[str] = []
            if isinstance(rate, (int, float)):
                bits.append(f"{rate * 100:.0f}% of decks")
            if isinstance(synergy, (int, float)):
                bits.append(f"synergy {synergy:+.2f}")

            out[name.lower()] = LayerScore(
                layer=CONSENSUS, score=score, reason=", ".join(bits),
            )
        return out
