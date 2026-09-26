"""Draft a deck's gameplan from its commander, its cards, and the player's notes.

Every stage that chooses cards reads the plan (``deckplan.render_plan`` feeds
Jev and the chat; stage 1 and local retrieval search with its themes), but no
deck had one: ``plan_notes`` and ``themes`` were empty on all eleven. Without
it a request like "what fits my deck" gave search nothing but its own words.

The draft is one non-thinking call. Themes are written as short phrases that
appear in rules text ("-1/-1 counter", "proliferate", "whenever a creature
dies") so the same list serves as the plan's direction and as search terms.
It is asked to describe mechanics rather than name cards. It does sometimes
name cards anyway; those are cards already in the deck, which cannot be
suggested, so a plan cannot smuggle specific picks into search.
"""

from __future__ import annotations

import json
from typing import Any

_SYSTEM_PROMPT = """\
You write the gameplan for a Magic: The Gathering Commander deck, from its
commander's rules text, its current cards, and the player's notes.

Output ONLY a JSON object:
{
  "plan": "<3 to 5 sentences: how this deck wins, the engine it builds, and what it wants to be doing early, mid, and late game>",
  "themes": ["<3 to 8 short phrases that appear in the RULES TEXT of cards this plan wants, e.g. \\"-1/-1 counter\\", \\"proliferate\\", \\"whenever a creature dies\\", \\"create a treasure\\">"]
}

Rules:
- Describe mechanics and strategy. Never name a card other than the commander.
- Themes are rules-text phrases a search could match, not archetype labels:
  "sacrifice another creature", not "aristocrats".
- Ground everything in the commander's text and the cards listed.\
"""


def build_prompt(snapshot: dict[str, Any]) -> tuple[str, str]:
    commanders = [n for n in (snapshot.get("commander"), snapshot.get("partner_commander")) if n]
    cards = snapshot.get("cards", [])
    by_name = {(c.get("name") or "").lower(): c for c in cards}
    lines = []
    for name in commanders:
        text = " ".join(str((by_name.get(name.lower()) or {}).get("oracle_text") or "").split())
        lines.append(f"Commander: {name}" + (f" | {text}" if text else ""))
    by_category: dict[str, list[str]] = {}
    for c in cards:
        if (c.get("name") or "").lower() in {n.lower() for n in commanders}:
            continue
        by_category.setdefault(c.get("category") or "Other", []).append(c.get("name") or "")
    lines.append("")
    lines.append("Current cards:")
    for category in sorted(by_category):
        lines.append(f"- {category}: {', '.join(n for n in by_category[category] if n)}")
    notes = (snapshot.get("notes") or "").strip()
    if notes:
        lines.append("")
        lines.append(f"Player's notes: {' '.join(notes.split())[:1500]}")
    return _SYSTEM_PROMPT, "\n".join(lines)


def parse(raw_json: str) -> dict[str, Any]:
    data = json.loads(raw_json)
    plan = data.get("plan") if isinstance(data, dict) else None
    themes = data.get("themes") if isinstance(data, dict) else None
    if not isinstance(plan, str) or not plan.strip():
        raise ValueError("gameplan draft had no plan")
    clean = []
    for theme in themes if isinstance(themes, list) else []:
        if isinstance(theme, str) and theme.strip() and theme.strip().lower() not in {t.lower() for t in clean}:
            clean.append(theme.strip())
    return {"plan": plan.strip(), "themes": clean[:8]}


def draft_in_background(deck_id: int, commander: str) -> None:
    """Draft and store the plan for a deck whose commander was just proposed,
    off the chat turn's thread, when the deck has no plan yet.

    The chat model used to compose the plan's themes and notes itself in the
    same thinking send that proposed the commander: 1,800-1,900 reasoning
    tokens, 16-18s, on every setup turn. This is one non-thinking call nobody
    waits for. A plan the player or the model already set is never replaced.
    """
    import threading

    threading.Thread(
        target=_draft_and_store, args=(deck_id, commander), daemon=True,
        name=f"gameplan-{deck_id}",
    ).start()


def _draft_and_store(deck_id: int, commander: str) -> None:
    import logging

    from sqlmodel import Session

    from app.db import repository as repo
    from app.db.session import get_engine
    from app.llm.factory import get_fast_model, get_provider
    from app.tools import deck_tools

    log = logging.getLogger(__name__)
    try:
        with Session(get_engine()) as session:
            snapshot = repo.deck_snapshot(session, deck_id)
            if snapshot.get("plan_notes") or snapshot.get("themes"):
                return
            if not snapshot.get("commander"):
                # Proposed, not yet approved: draft against the proposed one.
                card = deck_tools.lookup_card(deck_tools.get_scryfall_client(), commander)
                snapshot["commander"] = card.get("name") or commander
                snapshot["cards"] = [
                    {"name": snapshot["commander"], "oracle_text": card.get("oracle_text")},
                    *snapshot.get("cards", []),
                ]
            plan = draft(get_provider(), snapshot, model=get_fast_model())
            fresh = repo.deck_snapshot(session, deck_id)
            if fresh.get("plan_notes") or fresh.get("themes"):
                return  # set meanwhile; theirs wins
            repo.update_deck(session, deck_id, plan_notes=plan["plan"], themes=plan["themes"])
    except Exception as exc:  # noqa: BLE001 - a missing plan only means generic defaults
        log.warning("gameplan draft for deck %s failed: %s", deck_id, exc)


def draft(provider: Any, snapshot: dict[str, Any], *, model: str | None = None) -> dict[str, Any]:
    """``{"plan": str, "themes": [str, ...]}`` for the deck as it stands."""
    system, user = build_prompt(snapshot)
    return parse(provider.complete_json(system, user, model=model, thinking=False))
