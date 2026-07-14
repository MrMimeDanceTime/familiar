"""LLM nuance adjustment for the 1-10 power-level score.

The deterministic ``_estimate_power_level`` reads only quantities (land/ramp/draw/
interaction counts + curve). It is blind to the things that actually separate a
6 from an 8 at equal counts: card quality, commander synergy, and whether the
deck assembles a fast, focused win. This module adds a BOUNDED LLM judgment on
exactly those blind spots.

Design guarantees (this is a score users see, so noise is worse than nothing):
- The base score stays deterministic; the LLM only returns a clamped, quantized
  ADJUSTMENT in {-1.0, -0.5, 0, +0.5, +1.0}. It cannot move the number more than
  a point, and cannot return a continuous value that wobbles run to run.
- The prompt is a CLOSED rubric with a default of 0 — the model classifies
  against listed factors, it does not free-form "rate this deck". It is told the
  fundamentals are already counted, so it doesn't double-count curve/ramp.
- The result is cached against a content hash of the deck (cards + commander), so
  the call fires at most once per deck edit (see compute_deck_stats).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

_ALLOWED_ADJ = (-1.0, -0.5, 0.0, 0.5, 1.0)


def deck_content_hash(snapshot: dict[str, Any]) -> str:
    """Stable hash of the deck's power-relevant content: commander(s) plus the
    multiset of (card, quantity). Renaming the deck or editing notes does NOT
    change it; adding/removing a card or changing a commander does."""
    commanders = [
        (snapshot.get("commander") or "").lower(),
        (snapshot.get("partner_commander") or "").lower(),
    ]
    cards = sorted(
        (str(c.get("name") or "").lower(), int(c.get("quantity") or 0))
        for c in snapshot.get("cards", [])
    )
    payload = json.dumps({"commanders": commanders, "cards": cards}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_SYSTEM_PROMPT = """\
You adjust a Magic: The Gathering Commander deck's power-level score (1-10).

A deterministic system has ALREADY scored the deck from its fundamentals: land
count, ramp count, draw count, interaction count, and mana curve. Those are
counted — do NOT re-reward them. Your only job is to nudge the score for what
raw counts CANNOT see: card quality, commander synergy, and win-condition focus.

Return an adjustment from this exact set: -1.0, -0.5, 0, +0.5, +1.0.
Default to 0. Only move the score when a listed factor is CLEARLY present.

Adjust UP (+0.5 or +1.0) when:
- Tutors/pieces assemble a specific compact win the deck is actually built around
  (not just generic value).
- A fast, resilient combo or a low, focused clock the deck is dedicated to.
- The ramp/draw slots are premium staples (e.g. Rhystic Study, Sylvan Library,
  fast mana) rather than filler at the same count.
- Strong commander-to-deck synergy that raises the deck's ceiling.

Adjust DOWN (-0.5 or -1.0) when:
- A "good stuff" pile with high counts but no cohesive plan or win condition.
- Notable anti-synergy, or many cards that don't advance any win condition.
- The win condition is slow, durdly, or trivially disrupted.

Use +/-1.0 only for a strong, unambiguous case; +/-0.5 for a mild one; 0 when the
fundamentals already capture the deck's power.

Output ONLY this JSON object, nothing else:
{"adjustment": <one of -1.0,-0.5,0,0.5,1.0>, "reason": "<one sentence>"}\
"""


def _quantize(value: float) -> float:
    """Snap any numeric the model returns to the nearest allowed step, then
    clamp to +/-1.0. Guards against the model emitting 0.7, 2.0, etc."""
    clamped = max(-1.0, min(1.0, value))
    return min(_ALLOWED_ADJ, key=lambda a: abs(a - clamped))


def _render_deck(snapshot: dict[str, Any], base_score: int, base_factors: list[str]) -> str:
    commander = snapshot.get("commander") or "(none set)"
    partner = snapshot.get("partner_commander")
    header = f"Commander: {commander}" + (f" / {partner}" if partner else "")

    # Nonland cards with their functional roles; lands summarized as a count so
    # the model spends attention on the cards that carry the deck's plan.
    nonland: list[str] = []
    land_count = 0
    for c in snapshot.get("cards", []):
        type_line = (c.get("type_line") or "").lower()
        if "land" in type_line and (c.get("category") or "") != "Commander":
            land_count += 1
            continue
        roles = ", ".join(c.get("tags") or []) or "—"
        qty = c.get("quantity") or 1
        prefix = f"{qty}x " if qty > 1 else ""
        nonland.append(f"{prefix}{c.get('name')} [{c.get('category') or 'uncategorized'}]")

    base_line = f"Deterministic base score: {base_score}/10 (from counts: " + "; ".join(
        f for f in base_factors if not f.startswith("Raw:")
    ) + ")"

    return (
        f"{header}\n{base_line}\nLands: {land_count}\n"
        f"Nonland cards ({len(nonland)}):\n" + "\n".join(f"  {n}" for n in nonland)
    )


def compute_nuance(
    provider: Any,
    snapshot: dict[str, Any],
    base_score: int,
    base_factors: list[str],
    *,
    model: str | None = None,
) -> tuple[float, str]:
    """Return ``(adjustment, reason)`` for the deck. On any failure — bad JSON,
    provider error, out-of-range value — returns ``(0.0, "")`` so the base score
    stands; the nuance is a bonus, never a point of failure."""
    user_prompt = _render_deck(snapshot, base_score, base_factors)
    try:
        raw = provider.complete_json(_SYSTEM_PROMPT, user_prompt, model=model)
        data = json.loads(raw)
        adj = _quantize(float(data.get("adjustment", 0)))
        reason = data.get("reason")
        return adj, reason if isinstance(reason, str) else ""
    except Exception:
        return 0.0, ""
