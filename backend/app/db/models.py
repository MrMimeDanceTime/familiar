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
    format: str = "commander"
    # Cached LLM power-level nuance: the clamped ±adjustment, its one-line reason,
    # and the deck-content hash it was computed for. When the hash matches the
    # deck's current cards + commander, the adjustment is reused for free; a
    # card/commander change flips the hash and forces a recompute. See
    # app/tools/power_nuance.py.
    power_nuance_adj: float | None = None
    power_nuance_reason: str | None = None
    power_nuance_key: str | None = None
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
    type_line: str | None = None
    oracle_text: str | None = None
    oracle_id: str | None = None
    tags: list | None = Field(default=None, sa_column=Column(JSON))
    notes: str | None = None
    added_at: datetime = Field(default_factory=_utcnow)


class DeckProposal(SQLModel, table=True):
    __tablename__ = "deck_proposals"
    id: int | None = Field(default=None, primary_key=True)
    conversation_id: int = Field(foreign_key="conversation.id")
    deck_id: int = Field(foreign_key="deck.id")
    message_id: int | None = None
    status: str = "pending"  # pending | approved | denied
    action: str  # add | remove | set_commander
    card_name: str | None = None
    # None means "the whole stack" for remove proposals (the common case);
    # add proposals always get an explicit quantity (default 1).  Must use
    # Field(default=None) rather than a bare `= 1` class attribute — SQLModel
    # silently coerces an explicit quantity=None back to 1 on commit otherwise.
    quantity: int | None = Field(default=None)
    category: str | None = None
    commander_name: str | None = None
    reasoning: str = ""
    created_at: datetime = Field(default_factory=_utcnow)


class UserPreferences(SQLModel, table=True):
    __tablename__ = "user_preferences"
    id: int = Field(default=1, primary_key=True)
    preferred_bracket: str | None = None
    preferred_power: str | None = None
    budget: str | None = None
    rule0_notes: str | None = None
    build_preferences: str | None = None
