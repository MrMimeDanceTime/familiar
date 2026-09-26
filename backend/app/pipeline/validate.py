"""Stage 5 — validation and proposal. The hard legality gate, and the bridge
back to the app's existing approval workflow.

The model never mutates the deck. Stage-4 picks are translated into the same
``changes`` shape ``propose_deck_changes`` already consumes, and that function
does the real gate: fuzzy-matches each name to a canonical Scryfall card, refuses
banned cards, and stores pending ``DeckProposal`` rows the player approves or
denies. Reusing it (rather than hand-building rows) means the pipeline can't
drift from the invariant that suggestions are proposals, not edits — and it
inherits the banned-list check for free.

Cuts are proposed as ``remove`` actions, but a cut naming a card not in the deck
is dropped here first (see ``selection_to_changes``): if it reached
``propose_deck_changes`` it would raise and sink the whole batch of good adds.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.pipeline.selection import Selection
from app.tools.proposals import propose_deck_changes


def selection_to_changes(
    selection: Selection,
    deck_card_names: set[str] | None = None,
    scores_by_name: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Translate a Selection into the change dicts propose_deck_changes expects.

    Picks -> add (qty 1), cuts -> remove (whole stack). The reasoning carries the
    model's one-line justification through to the proposal row so the UI can show
    why each card was suggested.

    ``scores_by_name`` (lowercased card name -> brain-map verdict) rides along so
    the review surface can show WHY a card scored well, not just that the model
    liked it. It is attached here rather than recomputed at render time because
    the pool the card was scored against no longer exists by then.

    Cuts are hallucination-guarded: when ``deck_card_names`` (lowercased) is given,
    a cut naming a card not in the deck is dropped rather than forwarded. Without
    this guard a single bogus cut makes propose_deck_changes raise and sinks the
    entire batch of good adds — so the guard is what keeps one bad remove from
    collapsing the whole suggestion.
    """
    scores_by_name = scores_by_name or {}
    changes: list[dict[str, Any]] = []
    for pick in selection.picks:
        change: dict[str, Any] = {
            "action": "add",
            "card_name": pick.name,
            "quantity": pick.quantity,
            "reasoning": pick.reason,
        }
        verdict = scores_by_name.get(pick.name.lower())
        if verdict:
            change["scores"] = verdict
        changes.append(change)
    for cut in selection.cuts:
        if deck_card_names is not None and cut.name.lower() not in deck_card_names:
            continue
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
    scores_by_name: dict[str, dict[str, Any]] | None = None,
) -> dict:
    """Turn a Selection into pending proposals via the shared proposal path.

    Returns propose_deck_changes' result: ``{ok, summary, proposals}``. When the
    selection is empty, returns an empty batch without calling through (nothing
    to propose). ``summary`` defaults to the selection's own summary line.

    Cuts that name a card not currently in the deck are dropped before proposing,
    so a hallucinated cut can't make the whole batch fail.
    """
    from app.db import repository as repo

    deck_names = {c.card_name.lower() for c in repo.list_deck_cards(session, deck_id)}
    changes = selection_to_changes(selection, deck_names, scores_by_name)
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
