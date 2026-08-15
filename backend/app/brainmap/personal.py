"""Layer 3 — what this player actually keeps.

The only layer not derived from what everyone else built. Consensus reports the
crowd and mechanical reports the rules; neither knows that this player takes
cheap interaction and cuts six-drops. Without this layer the map is a very
well-informed conformist.

Reads the existing proposal history: every suggestion ever approved or denied is
already stored in ``deck_proposals`` with its status. That history has been
accumulating and going unread.

Two scopes, deliberately:

- **This deck** — a card denied for this deck should stop surfacing for it. That
  is a direct instruction and is weighted hardest.
- **Across decks** — a card the player takes whenever it appears is a genuine
  preference, and one they never take is a genuine dislike, independent of any
  single deck.

Returning an empty dict when there is no history is the correct behaviour, not a
failure: the blend renormalises over active layers, so an empty personal layer
means "no opinion" rather than "everything scores zero".
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

from app.brainmap.layers import PERSONAL, LayerScore, ScoringContext

logger = logging.getLogger(__name__)

# A denial for THIS deck is a direct instruction; a denial elsewhere is weaker
# evidence. Approvals are treated as the positive mirror of the same thing.
_THIS_DECK_WEIGHT = 1.0
_OTHER_DECK_WEIGHT = 0.4

# Basic lands are approved constantly and carry no preference information: a
# deck needs them regardless of taste, so counting them would make the layer's
# strongest opinions be about Swamp. Measured on the real history, they were the
# top three cards by approval count.
_BASIC_LANDS = frozenset({
    "plains", "island", "swamp", "mountain", "forest", "wastes",
    "snow-covered plains", "snow-covered island", "snow-covered swamp",
    "snow-covered mountain", "snow-covered forest",
})


class PersonalLayer:
    """Scores cards by the player's own approve/deny history."""

    name = PERSONAL

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    def _history(self, deck_id: int | None) -> dict[str, tuple[float, float]]:
        """Return ``lower(card_name) -> (approve_weight, deny_weight)``.

        Weights are summed across proposals so a card denied three times counts
        more than one denied once.
        """
        sql = """
            SELECT lower(card_name) AS name, status, deck_id, count(*) AS n
            FROM deck_proposals
            WHERE action = 'add' AND status IN ('approved', 'denied')
              AND card_name IS NOT NULL
            GROUP BY lower(card_name), status, deck_id
        """
        out: dict[str, tuple[float, float]] = {}
        try:
            with self._engine.begin() as conn:
                rows = conn.execute(text(sql)).fetchall()
        except Exception as exc:  # noqa: BLE001 — history is a bonus, never a dependency
            logger.warning("personal layer could not read proposal history: %s", exc)
            return {}

        for name, status, row_deck_id, count in rows:
            if name in _BASIC_LANDS:
                continue
            weight = (
                _THIS_DECK_WEIGHT if deck_id is not None and row_deck_id == deck_id
                else _OTHER_DECK_WEIGHT
            )
            approvals, denials = out.get(name, (0.0, 0.0))
            if status == "approved":
                approvals += weight * count
            else:
                denials += weight * count
            out[name] = (approvals, denials)
        return out

    def score(
        self, cards: list[dict], context: ScoringContext
    ) -> dict[str, LayerScore]:
        history = self._history(context.deck_id)
        if not history:
            return {}

        out: dict[str, LayerScore] = {}
        for card in cards:
            name = (card.get("name") or "").strip()
            if not name:
                continue
            entry = history.get(name.lower())
            if not entry:
                continue

            approvals, denials = entry
            total = approvals + denials
            if total <= 0:
                continue

            score = approvals / total
            # Weights are fractional (a decision on another deck counts 0.4), so
            # rounding them to whole "times" produces contradictions like
            # "approved 2x, denied 0x" for a card that WAS denied once
            # elsewhere. Describe the balance instead of inventing counts.
            if approvals and denials:
                reason = (
                    "you usually take this" if score >= 0.5
                    else "you usually pass on this"
                )
            elif approvals:
                reason = "you have taken this before"
            else:
                reason = "you have passed on this before"

            out[name.lower()] = LayerScore(
                layer=PERSONAL, score=score, reason=reason,
            )
        return out

    def has_history(self) -> bool:
        """Whether any usable history exists yet.

        Worth exposing because "no opinion" and "scored everything zero" are
        very different states and the difference should be visible.
        """
        return bool(self._history(None))
