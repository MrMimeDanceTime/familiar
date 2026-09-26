"""Proposals: the only way the model changes a deck.

``propose_deck_changes`` validates a batch (names against Scryfall, the
banned list, singleton, cards already in the deck, the budget ceiling) and
stores pending ``DeckProposal`` rows for the player to approve or deny; the
deck itself is never edited here. ``withdraw_pending_proposals`` lets the
model trim a batch it reconsiders.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.db import repository as repo
from app.db.models import DENIAL_WITHDRAWN, DeckProposal
from app.tools import deck_tools
from app.tools.card_lists import _BANNED_COMMANDER
from app.tools.deck_tools import _BASIC_LAND_NAMES
from app.tools.scryfall_client import ScryfallNotFoundError


def propose_deck_changes(
    session: Session,
    deck_id: int,
    summary: str,
    changes: list[dict[str, Any]],
    *,
    conversation_id: int | None = None,
    message_id: int | None = None,
) -> dict:
    """Validate and store proposed deck changes.

    For ``add`` actions the card name is fuzzy-matched against Scryfall to
    get the canonical name.  The deck is *not* modified — each change is
    stored as a pending ``DeckProposal`` row for the player to approve or
    deny.
    """
    if conversation_id is None:
        raise ValueError("propose_deck_changes requires a conversation_id")
    scryfall = deck_tools.get_scryfall_client()
    proposals: list[dict[str, Any]] = []
    deck_cards_by_lower = {c.card_name.lower(): c.card_name for c in repo.list_deck_cards(session, deck_id)}
    deck = repo.get_deck(session, deck_id)
    is_commander = deck is not None and deck.format == "commander"
    banned_lower = {n.lower() for n in _BANNED_COMMANDER}

    # The whole batch is validated before any row is written. Committing per
    # change meant a banned or duplicate card in position four left the first
    # three as pending rows the engine never learned about: not anchored to a
    # message, not revealed in the deck_proposal event, but sitting in
    # pending_proposals on every deck read and resurfacing as a stray batch on
    # reload. A batch either lands whole or not at all.
    rows: list[DeckProposal] = []
    # lower(name) -> the card's real text, kept from the resolution each add
    # already does. It rides back on the returned batch (not the stored row)
    # so the model describes what it just proposed from the card, not from
    # memory. See app/chat/card_facts.py.
    facts: dict[str, dict[str, Any]] = {}
    for change in changes:
        action = change.get("action", "add")
        card_name = change.get("card_name", "")
        # "remove" defaults to the whole stack (None) unless a quantity is
        # given explicitly — e.g. cutting 2 of 34 Swamps.  "add" defaults to 1.
        quantity = change.get("quantity") if action == "remove" else change.get("quantity", 1)
        category = change.get("category")
        reasoning = change.get("reasoning", "")
        # The brain map's verdict, when the suggestion pipeline produced one.
        # Carried on the row so the review surface can show WHY a card scored
        # the way it did — the pool it was scored against is gone by then.
        scores = change.get("scores")

        if action in ("add", "remove", "set_commander") and not card_name:
            raise ValueError(f"'{action}' requires a card_name.")

        canonical_name: str | None = None
        if action in ("add", "set_commander"):
            try:
                card = deck_tools.lookup_card(scryfall, card_name)
                canonical_name = card["name"]
                facts[canonical_name.lower()] = {
                    "mana_cost": card.get("mana_cost"),
                    "type_line": card.get("type_line"),
                    "oracle_text": card.get("oracle_text") or "",
                }
            except ScryfallNotFoundError:
                raise ValueError(f"No Scryfall card found matching '{card_name}'")
            # Commander is a banned-list format — refuse to propose an
            # illegal card rather than relying on the model to self-police.
            if is_commander and canonical_name.lower() in banned_lower:
                raise ValueError(
                    f"'{canonical_name}' is banned in Commander — cannot propose it. "
                    "Suggest a legal alternative instead."
                )
            # Singleton: a card already in the deck cannot be added again.
            # The suggestion pipeline filters owned cards during retrieval, but
            # the direct path had no such check, so a card approved in an
            # earlier batch could be re-proposed and approved a second time —
            # observed on a real build, with two copies each of Ashnod's Altar
            # and Phyrexian Altar reaching the deck.
            if (
                action == "add"
                and is_commander
                and canonical_name.lower() in deck_cards_by_lower
                and canonical_name.lower() not in _BASIC_LAND_NAMES
            ):
                raise ValueError(
                    f"'{canonical_name}' is already in the deck — Commander is "
                    "singleton, so it cannot be added again. Propose a different "
                    "card, or remove it first if you meant to replace it."
                )
        elif action == "remove":
            canonical_name = deck_cards_by_lower.get(card_name.lower())
            if canonical_name is None:
                raise ValueError(f"'{card_name}' is not in the deck — cannot propose removing it.")

        rows.append(DeckProposal(
            conversation_id=conversation_id,
            deck_id=deck_id,
            message_id=message_id,
            status="pending",
            action=action,
            card_name=canonical_name or card_name or None,
            quantity=quantity,
            category=category,
            scores=scores if isinstance(scores, dict) else None,
            commander_name=canonical_name if action == "set_commander" else None,
            reasoning=reasoning,
        ))

    prices = _prices_for([r.card_name for r in rows if r.action != "remove" and r.card_name])
    for row in rows:
        if row.card_name:
            row.price_usd = prices.get(row.card_name.lower())

    session.add_all(rows)
    session.commit()
    for proposal in rows:
        session.refresh(proposal)
        proposals.append({
            "id": proposal.id,
            "deck_id": proposal.deck_id,
            "status": proposal.status,
            "action": proposal.action,
            "card_name": proposal.card_name,
            "quantity": proposal.quantity,
            "category": proposal.category,
            "commander_name": proposal.commander_name,
            "reasoning": proposal.reasoning,
            "scores": proposal.scores,
            "price_usd": proposal.price_usd,
            **facts.get((proposal.card_name or "").lower(), {}),
        })

    return {"ok": True, "summary": summary, "proposals": proposals}


def _prices_for(names: list[str]) -> dict[str, float]:
    """lower(name) -> USD price from the card index. Empty when the index is
    absent; a missing price is a missing price, never a reason to fail."""
    if not names:
        return {}
    try:
        from app.cards import store as card_store

        found = card_store.by_names(names)
    except Exception:  # noqa: BLE001 - the index is optional
        return {}
    return {
        key: card["price_usd"]
        for key, card in found.items()
        if isinstance(card.get("price_usd"), (int, float))
    }


def withdraw_pending_proposals(
    session: Session,
    deck_id: int,
    include_commander: bool = False,
    card_names: list[str] | None = None,
) -> dict:
    """Mark pending proposals for *deck_id* as denied.

    Two modes:
    - ``card_names`` given: withdraw ONLY those cards (case-insensitive match on
      the proposal's card name), leaving the rest of the batch pending. This is
      how the model trims a card or two out of a batch it just proposed without
      nuking the whole thing.
    - ``card_names`` omitted: withdraw the whole pending batch — used when the
      player changes direction entirely.

    A pending ``set_commander`` proposal is the deck's identity, not a card
    batch that gets churned during review — so it is preserved by default.
    Clearing a stale card batch before proposing the next one must never
    silently cancel an unapproved commander. Pass ``include_commander=True``
    only when the player has actually decided against the proposed commander.
    (A commander named explicitly in ``card_names`` is only withdrawn when
    ``include_commander`` is also true.)

    Returns a count of how many were withdrawn, how many commander proposals
    were preserved, and the names that matched nothing (so the model can tell
    it misnamed a card rather than silently no-op).
    """
    from sqlmodel import select

    statement = select(DeckProposal).where(
        DeckProposal.deck_id == deck_id,
        DeckProposal.status == "pending",
    )
    proposals = list(session.exec(statement))

    target_lower: set[str] | None = (
        {n.strip().lower() for n in card_names if n and n.strip()}
        if card_names is not None
        else None
    )
    matched: set[str] = set()

    withdrawn = 0
    preserved_commander = 0
    for p in proposals:
        name_lower = (p.card_name or p.commander_name or "").lower()
        if target_lower is not None and name_lower not in target_lower:
            continue  # selective withdraw: skip cards not named
        if p.action == "set_commander" and not include_commander:
            preserved_commander += 1
            continue
        p.status = "denied"
        # The model changed its mind, not the player. Without the marker this
        # counted as a player denial and slowly taught the personal layer to
        # dislike cards the player never saw.
        p.denial_reason = DENIAL_WITHDRAWN
        session.add(p)
        withdrawn += 1
        matched.add(name_lower)
    session.commit()

    not_found = (
        sorted(target_lower - matched) if target_lower is not None else []
    )
    return {
        "ok": True,
        "withdrawn": withdrawn,
        "preserved_commander": preserved_commander,
        "not_found": not_found,
    }
