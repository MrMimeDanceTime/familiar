"""Deck-state tool functions: let the LLM read/mutate the in-progress deck
during conversation, so the UI side panel stays in sync with what's agreed
on. Card additions are validated and enriched via Scryfall before insert.
"""

from __future__ import annotations

from sqlmodel import Session

from app.db import repository as repo
from app.tools.scryfall_client import ScryfallNotFoundError, get_scryfall_client


def deck_get_current(session: Session, deck_id: int) -> dict:
    return repo.deck_snapshot(session, deck_id)


def deck_add_card(
    session: Session,
    deck_id: int,
    card_name: str,
    qty: int = 1,
    category: str | None = None,
    notes: str | None = None,
) -> dict:
    scryfall = get_scryfall_client()
    try:
        card = scryfall.named(card_name, fuzzy=True)
    except ScryfallNotFoundError:
        raise ValueError(f"No Scryfall card found matching '{card_name}'")

    repo.add_deck_card(
        session,
        deck_id=deck_id,
        card_name=card["name"],
        quantity=qty,
        category=category,
        mana_value=card["cmc"],
        color_identity="".join(card["color_identity"] or []),
        notes=notes,
    )
    return repo.deck_snapshot(session, deck_id)


def deck_remove_card(session: Session, deck_id: int, card_name: str) -> dict:
    repo.remove_deck_card(session, deck_id, card_name)
    return repo.deck_snapshot(session, deck_id)


def deck_set_commander(session: Session, deck_id: int, commander_name: str) -> dict:
    scryfall = get_scryfall_client()
    try:
        card = scryfall.named(commander_name, fuzzy=True)
    except ScryfallNotFoundError:
        raise ValueError(f"No Scryfall card found matching '{commander_name}'")

    repo.update_deck(session, deck_id, commander=card["name"])
    return repo.deck_snapshot(session, deck_id)


def deck_update_notes(session: Session, deck_id: int, notes: str) -> dict:
    repo.update_deck(session, deck_id, notes=notes)
    return repo.deck_snapshot(session, deck_id)
