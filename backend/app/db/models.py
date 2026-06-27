from datetime import datetime, timezone

from sqlalchemy import Column, JSON
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Conversation(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    title: str = "New conversation"
    deck_id: int | None = Field(default=None, foreign_key="deck.id")
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Message(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    conversation_id: int = Field(foreign_key="conversation.id")
    role: str  # "user" | "assistant" | "tool"
    text_content: str | None = None
    provider_native: list = Field(default_factory=list, sa_column=Column(JSON))
    tool_calls: list | None = Field(default=None, sa_column=Column(JSON))
    tool_results: list | None = Field(default=None, sa_column=Column(JSON))
    sequence: int
    created_at: datetime = Field(default_factory=_utcnow)


class Deck(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = "Untitled Deck"
    commander: str | None = None
    partner_commander: str | None = None
    notes: str | None = None
    power_level: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class DeckCard(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    deck_id: int = Field(foreign_key="deck.id")
    card_name: str
    quantity: int = 1
    category: str | None = None
    mana_value: float | None = None
    color_identity: str | None = None
    notes: str | None = None
    added_at: datetime = Field(default_factory=_utcnow)
