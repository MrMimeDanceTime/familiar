"""Stage 5 — validation and proposal. The hard legality gate, and the bridge
back to the app's existing approval workflow.

The model never mutates the deck. Stage-4 picks are translated into the same
``changes`` shape ``propose_deck_changes`` already consumes, and that function
does the real gate: fuzzy-matches each name to a canonical Scryfall card, refuses
banned cards, and stores pending ``DeckProposal`` rows the player approves or
denies. Reusing it (rather than hand-building rows) means the pipeline can't
drift from the invariant that suggestions are proposals, not edits — and it
inherits the banned-list check for free.

Cuts are proposed as ``remove`` actions; ``propose_deck_changes`` already refuses
to remove a card that isn't in the deck, so a hallucinated cut is rejected there.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.pipeline.selection import Selection
from app.tools.deck_tools import propose_deck_changes


def selection_to_changes(selection: Selection) -> list[dict[str, Any]]:
    """Translate a Selection into the change dicts propose_deck_changes expects.

    Picks -> add (qty 1), cuts -> remove (whole stack). The reasoning carries the
    model's one-line justification through to the proposal row so the UI can show
    why each card was suggested.
    """
    changes: list[dict[str, Any]] = []
    for pick in selection.picks:
        changes.append({
            "action": "add",
            "card_name": pick.name,
            "quantity": 1,
            "reasoning": pick.reason,
        })
    for cut in selection.cuts:
        changes.append({
            "action": "remove",
            "card_name": cut.name,
            "reasoning": cut.reason,
        })
    return changes


def validate_to_proposals(
    session: Session,
    deck_id: int,
    selection: Selection,
    *,
    conversation_id: int | None = None,
    message_id: int | None = None,
    summary: str | None = None,
) -> dict:
    """Turn a Selection into pending proposals via the shared proposal path.

    Returns propose_deck_changes' result: ``{ok, summary, proposals}``. When the
    selection is empty, returns an empty batch without calling through (nothing
    to propose). ``summary`` defaults to the selection's own summary line.
    """
    changes = selection_to_changes(selection)
    if not changes:
        return {"ok": True, "summary": summary or selection.summary, "proposals": []}

    return propose_deck_changes(
        session,
        deck_id,
        summary or selection.summary,
        changes,
        conversation_id=conversation_id,
        message_id=message_id,
    )
