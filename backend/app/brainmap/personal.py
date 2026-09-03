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
from app.db.models import DENIAL_SUPERSEDED, DENIAL_WITHDRAWN

logger = logging.getLogger(__name__)

# A denial for THIS deck is a direct instruction; a denial elsewhere is weaker
# evidence. Approvals are treated as the positive mirror of the same thing.
_THIS_DECK_WEIGHT = 1.0
_OTHER_DECK_WEIGHT = 0.4

# How much each denial reason says about the CARD, as opposed to the moment.
#
# This is the whole reason the review surface asks. "Don't need this role" is a
# statement about the deck's current shape — the card may be perfect next week
# once that slot opens, so holding it against the card is wrong. "Too generic"
# and "just don't like it" are statements about the card itself and should
# stick. Treating every denial identically throws that distinction away and
# slowly poisons the pool against cards that were only ever mistimed.
_REASON_WEIGHT: dict[str, float] = {
    # Written by the app, not the player: the model trimmed its own batch, or a
    # newer batch displaced this one. Neither is a verdict on the card.
    DENIAL_WITHDRAWN: 0.0,
    DENIAL_SUPERSEDED: 0.0,
    "don't need this role": 0.2,
    "don’t need this role": 0.2,   # curly apostrophe, as the UI sends it
    "off-theme": 0.5,
    "too expensive": 0.7,
    "too generic": 1.0,
    "just don't like it": 1.0,
    "just don’t like it": 1.0,
}

# An unlabelled denial ("Skip") sits between the two: it is a real rejection,
# but the player declined to say why, so it should not carry the full weight of
# an explicit "I dislike this card".
_UNLABELLED_DENIAL_WEIGHT = 0.6

# The curve preference is two aggregate joins over the whole proposal history
# and changes only when the player decides something, so it is memoised per
# database for a short while rather than recomputed on every suggestion.
_CURVE_CACHE_SECONDS = 120.0
_curve_cache: dict[str, tuple[float, tuple[float, float] | None]] = {}


def _reason_weight(reason: str | None) -> float:
    if not reason:
        return _UNLABELLED_DENIAL_WEIGHT
    return _REASON_WEIGHT.get(reason.strip().lower(), _UNLABELLED_DENIAL_WEIGHT)

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

        Weights sum across proposals, so a card denied three times counts more
        than one denied once, and each denial is scaled by how much its reason
        says about the CARD rather than the moment (see ``_REASON_WEIGHT``).

        Not grouped in SQL any more: the denial reason varies per row, so
        grouping would average away exactly the distinction this reads.
        """
        sql = """
            SELECT lower(card_name) AS name, status, deck_id, denial_reason
            FROM deck_proposals
            WHERE action = 'add' AND status IN ('approved', 'denied')
              AND card_name IS NOT NULL
        """
        out: dict[str, tuple[float, float]] = {}
        try:
            with self._engine.begin() as conn:
                rows = conn.execute(text(sql)).fetchall()
        except Exception as exc:  # noqa: BLE001 — history is a bonus, never a dependency
            logger.warning("personal layer could not read proposal history: %s", exc)
            return {}

        for name, status, row_deck_id, denial_reason in rows:
            if name in _BASIC_LANDS:
                continue
            weight = (
                _THIS_DECK_WEIGHT if deck_id is not None and row_deck_id == deck_id
                else _OTHER_DECK_WEIGHT
            )
            approvals, denials = out.get(name, (0.0, 0.0))
            if status == "approved":
                approvals += weight
            else:
                denials += weight * _reason_weight(denial_reason)
            out[name] = (approvals, denials)
        return out

    def curve_preference(self) -> tuple[float, float] | None:
        """Average mana value of cards taken vs passed on, across all decks.

        The generalising half of this layer. Exact-name history only helps for
        a card already seen, which is a small slice of any pool; a preference
        for cheap interaction over six-drops applies to cards never proposed
        before. Returns ``(approved_mv, denied_mv)`` or None without enough
        evidence to be worth acting on.

        Deliberately cross-deck: this is taste, not a property of one build.
        """
        import time

        cache_key = str(getattr(self._engine, "url", id(self._engine)))
        cached = _curve_cache.get(cache_key)
        if cached is not None and time.monotonic() - cached[0] < _CURVE_CACHE_SECONDS:
            return cached[1]
        result = self._curve_preference_uncached()
        _curve_cache[cache_key] = (time.monotonic(), result)
        return result

    def _curve_preference_uncached(self) -> tuple[float, float] | None:
        sql = """
            SELECT p.status, AVG(c.cmc) AS mv, COUNT(*) AS n
            FROM deck_proposals p
            JOIN cards c ON lower(c.name) = lower(p.card_name)
            WHERE p.action = 'add' AND p.status IN ('approved', 'denied')
              AND p.card_name IS NOT NULL AND c.cmc IS NOT NULL
              AND lower(c.name) NOT IN (
                  'plains','island','swamp','mountain','forest','wastes'
              )
            GROUP BY p.status
        """
        try:
            with self._engine.begin() as conn:
                rows = {r[0]: (r[1], r[2]) for r in conn.execute(text(sql))}
        except Exception:  # noqa: BLE001 — needs the card index; absent on a cold install
            return None

        approved = rows.get("approved")
        denied = rows.get("denied")
        # Both sides need enough rows to mean anything. A handful of denials is
        # noise, and reading a curve preference off it would be superstition.
        if not approved or not denied or approved[1] < 20 or denied[1] < 10:
            return None
        return (float(approved[0]), float(denied[0]))

    @staticmethod
    def _curve_score(
        card: dict, curve: tuple[float, float] | None
    ) -> LayerScore | None:
        """Score an unseen card against the player's demonstrated curve taste.

        Weak on purpose. It scores near the neutral midpoint and never reaches
        the confidence of a direct approve/deny, because "you tend to take
        cheaper cards" is a much softer claim than "you took this card twice".
        Returns None when the two averages are too close to mean anything.
        """
        if curve is None:
            return None
        approved_mv, denied_mv = curve
        spread = denied_mv - approved_mv
        # Under half a mana of difference is not a preference, it is noise.
        if abs(spread) < 0.5:
            return None

        cmc = card.get("cmc")
        if not isinstance(cmc, (int, float)):
            return None

        midpoint = (approved_mv + denied_mv) / 2
        # Positive when the card sits on the side of the midpoint the player
        # has been taking from.
        aligned = (midpoint - cmc) if spread > 0 else (cmc - midpoint)
        nudge = max(-0.15, min(0.15, aligned * 0.08))
        direction = "cheaper" if spread > 0 else "bigger"
        return LayerScore(
            layer=PERSONAL,
            score=0.5 + nudge,
            reason=f"you lean {direction} than this on average",
        )

    def score(
        self, cards: list[dict], context: ScoringContext
    ) -> dict[str, LayerScore]:
        history = self._history(context.deck_id)
        if not history:
            return {}

        curve = self.curve_preference()

        out: dict[str, LayerScore] = {}
        for card in cards:
            name = (card.get("name") or "").strip()
            if not name:
                continue
            entry = history.get(name.lower())
            if not entry:
                # No history for this exact card — but taste still generalises.
                # Without this the layer only ever speaks about cards already
                # proposed once, which is a small slice of any pool.
                inferred = self._curve_score(card, curve)
                if inferred is not None:
                    out[name.lower()] = inferred
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
