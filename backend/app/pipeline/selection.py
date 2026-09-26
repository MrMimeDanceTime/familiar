"""Stage 4 — selection. The second and final bounded LLM call.

The model is handed a pre-retrieved, cleaned, legality-annotated pool (from
stage 3) and asked only to CHOOSE from it and justify the choices. It never
retrieves, never invents cards: a pick that isn't in the pool is dropped here,
and stage 5 re-validates legality regardless. This is the inversion the whole
pipeline exists for — the model reasons over a curated shortlist instead of
driving the search.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.pipeline.shaping import ShapedCard, render_pool


@dataclass
class Pick:
    name: str
    reason: str = ""
    # Copies to add. Only basics go above 1 (a manabase fill), which is the
    # one card Commander allows more than one of.
    quantity: int = 1


@dataclass
class Selection:
    """Validated stage-4 output: cards to add, cards to consider cutting, and a
    short narration. Picks are guaranteed to be real, legal cards from the pool."""

    picks: list[Pick]
    cuts: list[Pick] = field(default_factory=list)
    summary: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


_SYSTEM_PROMPT = """\
You are the card-selection stage of a Magic: The Gathering Commander deckbuilding
assistant. You are given the deck being built (its commander and current cards),
the player's intent, and a curated pool of legal candidate cards. Choose the best
cards FOR THIS SPECIFIC DECK and briefly justify each.

Judge FIT, not just keyword-match to the intent. A card earns a pick when it
advances THIS deck's plan: it combos or synergizes with the commander or cards
already in the list, fills the stated role better than the alternatives, or
enables a line the deck is set up for. Prefer a card that clicks with the
commander over a generically-good card that doesn't. Say WHY it fits this deck in
the reason (name the commander/card it works with when that's the point), not just
what the card does in a vacuum.

Rules:
- Pick ONLY from the candidate pool below. Never name a card that isn't listed.
- Never pick a card marked ILLEGAL.
- Ground your fit reasoning in the commander and current deck shown below.
- Keep each justification to one sentence.

Output ONLY a JSON object of this shape:
{{
  "summary": "<one or two sentences on the overall shape of your picks>",
  "picks": [{{"name": "<exact card name from the pool>", "reason": "<one sentence>"}}],
  "cuts": [{{"name": "<card the player might cut to make room>", "reason": "<why>"}}]
}}
Pick at most {max_picks} cards. "cuts" may be empty. Nothing outside the JSON object.\
"""


def build_prompt(
    pool: list[ShapedCard], user_intent: str, *, max_picks: int = 10,
    deck_context: str = "", player_message: str | None = None,
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for the stage-4 call. The pool is
    rendered by the shaping layer so this stage and the golden tests see the same
    deterministic block. ``deck_context`` is a compact description of the
    commander + current deck (built by the service) so the model can judge fit
    against this specific deck rather than matching the intent in a vacuum."""
    system = _SYSTEM_PROMPT.format(max_picks=max_picks)
    block = render_pool(pool)
    ctx = f"{deck_context.strip()}\n\n" if deck_context.strip() else ""
    # The intent is the chat model's distillation; the player's own message
    # keeps the constraints that distillation drops ("cheap", "no green",
    # "something weird"). Capped so a pasted decklist cannot swamp the pool.
    words = " ".join((player_message or "").split())
    said = f"Player's own words: {words[:600]}\n" if words else ""
    user = f"{ctx}Player intent: {user_intent}\n{said}\nCandidate pool:\n{block}"
    return system, user


def _legal_names(pool: list[ShapedCard]) -> dict[str, str]:
    """Map lowercased legal card name -> canonical name, for pick validation.
    Illegal cards are excluded so a pick of one is dropped as out-of-pool."""
    return {c.name.lower(): c.name for c in pool if c.legal_in_deck and c.name}


def _parse_entries(raw: Any, valid: dict[str, str] | None) -> list[Pick]:
    """Coerce a list of {name, reason} dicts into Picks. When ``valid`` is given,
    only names present in it survive (canonicalized to the pool's spelling)."""
    picks: list[Pick] = []
    seen: set[str] = set()
    if not isinstance(raw, list):
        return picks
    for entry in raw:
        if isinstance(entry, str):
            name, reason = entry, ""
        elif isinstance(entry, dict):
            name = entry.get("name")
            reason = entry.get("reason") or ""
        else:
            continue
        if not isinstance(name, str) or not name.strip():
            continue
        key = name.strip().lower()
        if valid is not None:
            if key not in valid:
                continue
            canonical = valid[key]
        else:
            canonical = name.strip()
        if canonical.lower() in seen:
            continue
        seen.add(canonical.lower())
        picks.append(Pick(name=canonical, reason=reason if isinstance(reason, str) else ""))
    return picks


def parse_selection(
    raw_json: str, pool: list[ShapedCard], *, max_picks: int = 10
) -> Selection:
    """Parse and repair stage-4 JSON into a Selection.

    Hallucination guard: every pick must match a LEGAL card in the pool (by
    case-insensitive name) or it's dropped. Cuts are NOT constrained to the pool
    — they name cards already in the deck, which the pool doesn't contain — so
    they're passed through as free text for the caller to resolve. Raises
    ValueError only on unparseable JSON.
    """
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"stage-4 output was not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("stage-4 output was not a JSON object")

    valid = _legal_names(pool)
    picks = _parse_entries(data.get("picks"), valid)[:max_picks]
    cuts = _parse_entries(data.get("cuts"), None)

    summary = data.get("summary")
    return Selection(
        picks=picks,
        cuts=cuts,
        summary=summary if isinstance(summary, str) else "",
        raw=data,
    )


def select(
    provider: Any,
    pool: list[ShapedCard],
    user_intent: str,
    *,
    model: str | None = None,
    max_picks: int = 10,
    thinking: bool = True,
    deck_context: str = "",
    reasoning_effort: str | None = None,
    player_message: str | None = None,
) -> Selection:
    """Run stage 4: prompt the provider with the curated pool, parse+repair its
    JSON into a validated Selection.

    ``deck_context`` gives the model the commander + current deck so it can judge
    fit/synergy against this specific deck (the one thing Python can't do). This
    is the stage where deck-aware reasoning happens, so with real context present
    ``thinking=True`` earns its cost.

    ``reasoning_effort`` caps how long it thinks. Latency here is dominated by
    output volume (a thinking call emits ~9.5k completion tokens against ~490
    without), so this is the only lever that moves it — shrinking the prompt
    measured no faster. "low" roughly halves the wait for picks that graded the
    same."""
    system, user = build_prompt(
        pool, user_intent, max_picks=max_picks, deck_context=deck_context,
        player_message=player_message,
    )
    raw = provider.complete_json(
        system, user, model=model, thinking=thinking,
        reasoning_effort=reasoning_effort,
    )
    return parse_selection(raw, pool, max_picks=max_picks)
