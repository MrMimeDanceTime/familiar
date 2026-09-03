"""The player's own deckbuilding knowledge.

The seeded entries are the model's grounding for deckbuilding advice, and
the search tool treats the knowledge base as authoritative. Letting the
player add entries of their own (house rules, a playgroup's power
expectations, a pet theory about land counts) makes that advice theirs
rather than generic. Seeded entries stay read-only here: they are replaced
wholesale whenever the seed changes, so an edit to one would not survive.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.db.session import get_engine
from app.knowledge.models import SOURCE_USER, KnowledgeEntry

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

CATEGORIES = (
    "mana-curve", "ramp", "removal", "card-draw", "land-base", "color-pie",
    "commander", "synergy", "format-specific", "power-level", "playgroup",
)


class KnowledgeIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=4000)
    category: str = "playgroup"
    format: str = "any"


class KnowledgeOut(BaseModel):
    id: int
    title: str
    body: str
    category: str
    format: str
    source: str


def _out(entry: KnowledgeEntry) -> KnowledgeOut:
    return KnowledgeOut(
        id=entry.id, title=entry.title, body=entry.body,
        category=entry.category, format=entry.format, source=entry.source or "seed",
    )


def _validated(body: KnowledgeIn) -> KnowledgeIn:
    if body.category not in CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"category must be one of: {', '.join(CATEGORIES)}",
        )
    return body


@router.get("", response_model=list[KnowledgeOut])
def list_entries(source: str | None = None):
    """Every entry, or only the player's own with ``source=user``."""
    with Session(get_engine()) as session:
        statement = select(KnowledgeEntry).order_by(KnowledgeEntry.id)
        if source:
            statement = statement.where(KnowledgeEntry.source == source)
        return [_out(e) for e in session.exec(statement)]


@router.get("/categories")
def list_categories():
    return {"categories": list(CATEGORIES)}


@router.post("", response_model=KnowledgeOut)
def create_entry(body: KnowledgeIn):
    body = _validated(body)
    with Session(get_engine()) as session:
        entry = KnowledgeEntry(
            title=body.title.strip(), body=body.body.strip(),
            category=body.category, format=body.format, source=SOURCE_USER,
        )
        session.add(entry)
        session.commit()
        session.refresh(entry)
        return _out(entry)


@router.put("/{entry_id}", response_model=KnowledgeOut)
def update_entry(entry_id: int, body: KnowledgeIn):
    body = _validated(body)
    with Session(get_engine()) as session:
        entry = session.get(KnowledgeEntry, entry_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="Entry not found")
        if entry.source != SOURCE_USER:
            raise HTTPException(
                status_code=403,
                detail="Seeded entries are read-only; add your own entry instead.",
            )
        entry.title = body.title.strip()
        entry.body = body.body.strip()
        entry.category = body.category
        entry.format = body.format
        session.add(entry)
        session.commit()
        session.refresh(entry)
        return _out(entry)


@router.delete("/{entry_id}")
def delete_entry(entry_id: int):
    with Session(get_engine()) as session:
        entry = session.get(KnowledgeEntry, entry_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="Entry not found")
        if entry.source != SOURCE_USER:
            raise HTTPException(status_code=403, detail="Seeded entries cannot be deleted.")
        session.delete(entry)
        session.commit()
        return {"ok": True}
