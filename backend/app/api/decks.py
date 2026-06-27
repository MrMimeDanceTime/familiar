from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from app.db import repository as repo
from app.db.session import get_engine

router = APIRouter(prefix="/api/decks", tags=["decks"])


class CreateDeckIn(BaseModel):
    name: str = "Untitled Deck"
    commander: str | None = None


class UpdateDeckIn(BaseModel):
    name: str | None = None
    commander: str | None = None
    partner_commander: str | None = None
    notes: str | None = None
    power_level: str | None = None


@router.post("")
def create_deck(body: CreateDeckIn):
    with Session(get_engine()) as session:
        deck = repo.create_deck(session, name=body.name, commander=body.commander)
        return repo.deck_snapshot(session, deck.id)


@router.get("")
def list_decks():
    with Session(get_engine()) as session:
        decks = repo.list_decks(session)
        return [repo.deck_snapshot(session, d.id) for d in decks]


@router.get("/{deck_id}")
def get_deck(deck_id: int):
    with Session(get_engine()) as session:
        try:
            return repo.deck_snapshot(session, deck_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))


@router.patch("/{deck_id}")
def update_deck(deck_id: int, body: UpdateDeckIn):
    with Session(get_engine()) as session:
        try:
            repo.update_deck(
                session,
                deck_id,
                name=body.name,
                commander=body.commander,
                partner_commander=body.partner_commander,
                notes=body.notes,
                power_level=body.power_level,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return repo.deck_snapshot(session, deck_id)


@router.delete("/{deck_id}")
def delete_deck(deck_id: int):
    with Session(get_engine()) as session:
        repo.delete_deck(session, deck_id)
        return {"ok": True}


@router.get("/{deck_id}/cards")
def get_deck_cards(deck_id: int):
    with Session(get_engine()) as session:
        try:
            return repo.deck_snapshot(session, deck_id)["cards"]
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
