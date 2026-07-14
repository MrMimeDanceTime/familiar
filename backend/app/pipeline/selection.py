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
assistant. You are given a curated pool of legal candidate cards and the player's
intent. Choose the best cards for the deck and briefly justify each.

Rules:
- Pick ONLY from the candidate pool below. Never name a card that isn't listed.
- Never pick a card marked ILLEGAL.
- Prefer cards that directly serve the stated intent; note synergy when relevant.
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
    pool: list[ShapedCard], user_intent: str, *, max_picks: int = 10
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for the stage-4 call. The pool is
    rendered by the shaping layer so this stage and the golden tests see the same
    deterministic block."""
    system = _SYSTEM_PROMPT.format(max_picks=max_picks)
    block = render_pool(pool)
    user = f"Player intent: {user_intent}\n\nCandidate pool:\n{block}"
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
) -> Selection:
    """Run stage 4: prompt the provider with the curated pool, parse+repair its
    JSON into a validated Selection."""
    system, user = build_prompt(pool, user_intent, max_picks=max_picks)
    raw = provider.complete_json(system, user, model=model)
    return parse_selection(raw, pool, max_picks=max_picks)
