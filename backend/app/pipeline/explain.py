"""Stage 4b: the language model explains picks it did not choose.

With Jev ranking the pool, selection no longer needs a model that thinks; it
still needs one that writes. This call gets the picks as a fixed list and
writes what Jev cannot: a reason per pick that names what it works with, a
summary, and cuts from the current deck. It cannot add, drop, or reorder
picks, so it cannot undo the ranking that measured best.

Thinking is off. Explaining ten given cards is a writing task, and the
selection stage's latency was dominated by thinking output.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.pipeline.selection import Pick, Selection, _parse_entries
from app.pipeline.shaping import ShapedCard

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are the explanation stage of a Magic: The Gathering Commander deckbuilding
assistant. The cards to add have ALREADY been chosen. Your job is to explain
them to the player, not to second-guess them.

For each chosen card write one sentence on why it fits THIS deck and this
request: name the commander or the card already in the deck it works with
when that is the point. Then suggest up to as many cuts as there are picks,
naming cards CURRENTLY IN THE DECK that are the weakest for its plan, each
with one sentence of why. Cuts may be empty.

Rules:
- Explain every chosen card, exactly as named. Do not add or drop cards.
- Cuts must be cards listed in the current deck, never a chosen card.
- Ground every sentence in the rules text and deck shown. No invented facts.
- Write every card name in double brackets, exactly as named: [[Sol Ring]].
  Basic lands too: [[Swamp]]. The app links cards by this form.

Output ONLY a JSON object:
{
  "summary": "<one or two sentences on the batch as a whole>",
  "picks": [{"name": "<chosen card>", "reason": "<one sentence>"}],
  "cuts": [{"name": "<card in the deck>", "reason": "<one sentence>"}]
}\
"""


def _render_picks(picks: list[Pick], by_name: dict[str, ShapedCard]) -> str:
    lines = []
    for pick in picks:
        card = by_name.get(pick.name.lower())
        header = pick.name
        if card and card.type_line:
            header += f" | {card.type_line}"
        lines.append(f"- {header}")
        if card and card.oracle_text:
            lines.append(f"    {' '.join(card.oracle_text.split())}")
        lines.append(f"    ranker's note: {pick.reason}")
    return "\n".join(lines)


def build_prompt(
    selection: Selection, pool: list[ShapedCard], user_intent: str, *,
    deck_context: str = "", player_message: str | None = None,
) -> tuple[str, str]:
    by_name = {c.name.lower(): c for c in pool if c.name}
    words = " ".join((player_message or "").split())
    said = f"Player's own words: {words[:600]}\n" if words else ""
    ctx = f"{deck_context.strip()}\n\n" if deck_context.strip() else ""
    user = (
        f"{ctx}Player intent: {user_intent}\n{said}\n"
        f"Chosen cards:\n{_render_picks(selection.picks, by_name)}"
    )
    return _SYSTEM_PROMPT, user


def apply_explanation(selection: Selection, raw_json: str) -> Selection:
    """Merge the model's prose into the selection. Picks keep their order and
    membership; a pick the model skipped keeps its fact-built reason."""
    data = json.loads(raw_json)
    if not isinstance(data, dict):
        raise ValueError("explanation was not a JSON object")
    chosen = {p.name.lower(): p.name for p in selection.picks}
    written = {p.name.lower(): p.reason for p in _parse_entries(data.get("picks"), chosen) if p.reason}
    picks = [Pick(name=p.name, reason=written.get(p.name.lower(), p.reason)) for p in selection.picks]
    cuts = [c for c in _parse_entries(data.get("cuts"), None) if c.name.lower() not in chosen]
    summary = data.get("summary")
    return Selection(
        picks=picks, cuts=cuts[: len(picks)],
        summary=summary if isinstance(summary, str) else selection.summary,
        raw={**selection.raw, "explained": True},
    )


def explain(
    provider: Any, selection: Selection, pool: list[ShapedCard], user_intent: str, *,
    model: str | None = None, deck_context: str = "", player_message: str | None = None,
) -> Selection:
    if not selection.picks:
        return selection
    system, user = build_prompt(
        selection, pool, user_intent, deck_context=deck_context, player_message=player_message,
    )
    raw = provider.complete_json(system, user, model=model, thinking=False)
    return apply_explanation(selection, raw)
