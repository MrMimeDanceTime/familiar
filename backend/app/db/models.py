from datetime import datetime, timezone

from sqlalchemy import Column, JSON, UniqueConstraint
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Familiar is single-user today: there is no auth and no per-user ownership on
# Conversation/Deck/Message. Rather than pretend ownership doesn't exist, every
# owner-scoped row carries this constant, and reads filter on it. The filter is
# a no-op now but is exercised on every request, so when real users arrive only
# `current_owner_id()` changes — not every query that forgot to scope itself.
SINGLE_USER_ID = 1


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
    # The deck plan: what this deck is TRYING to be, as opposed to what it
    # currently is. Without it every suggestion starts cold — the model saw a
    # list of card names and had to re-infer the plan from them each call.
    #
    # `role_targets` is {role: count} ("ramp": 10, "interaction": 8). `themes`
    # is a list of direction strings the deck is built around. Both are JSON
    # because they are read and written whole, never queried by element.
    role_targets: dict | None = Field(default=None, sa_column=Column(JSON))
    themes: list | None = Field(default=None, sa_column=Column(JSON))
    plan_notes: str | None = None
    # How far off-consensus to build, 0.0 to 1.0. Trades EDHREC play rate
    # against commander-specific synergy when ranking recommendations: at 0 the
    # deck gets what everyone plays, at 1 it gets what is specific to this
    # commander even when few decks run it. Defaults to 0.25 when unset.
    off_meta: float | None = None
    # Budget ceiling per card in USD. Null means "use the player's standing
    # budget preference", which maps to a ceiling in deckplan.
    max_card_price: float | None = None
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
    # The brain map's verdict for this card: per-layer scores plus the
    # one-line explanation. Stored on the proposal rather than recomputed at
    # render time because the pool it was scored against is gone by the time
    # the player reviews, and "why did it suggest this" has to answer with what
    # the model actually saw.
    scores: dict | None = Field(default=None, sa_column=Column(JSON))
    # Why a denial happened. A bare boolean is a weak signal — "no" and "not
    # this one, wrong slot" mean different things to the learning loop, and
    # only the second is worth generalising from.
    denial_reason: str | None = None
    # Price at proposal time, from the card index, so the review card can show
    # what a pick costs without a lookup per render.
    price_usd: float | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class UserPreferences(SQLModel, table=True):
    __tablename__ = "user_preferences"
    id: int = Field(default=1, primary_key=True)
    preferred_bracket: str | None = None
    preferred_power: str | None = None
    budget: str | None = None
    rule0_notes: str | None = None
    build_preferences: str | None = None


# Denial reasons the APP writes, as opposed to the ones the player picks in the
# review UI. Both mean "this proposal went away without the player passing on
# the card", and the personal scoring layer must weight them at zero — a card
# withdrawn by the model or displaced by a newer batch says nothing about the
# player's taste.
DENIAL_WITHDRAWN = "withdrawn"
DENIAL_SUPERSEDED = "superseded"

# Turn statuses. A turn is terminal when it is not RUNNING.
TURN_RUNNING = "running"
TURN_DONE = "done"
TURN_ERROR = "error"


class Turn(SQLModel, table=True):
    """One execution of the agentic chat loop.

    A turn exists independently of the HTTP request that started it. That is the
    whole point: `run_chat_turn` is a lazy generator, so when it was consumed
    directly by StreamingResponse a client that stopped reading (a backgrounded
    mobile tab) stalled the turn mid-flight and Starlette then closed it. The
    turn row plus its event log let execution outlive any one connection.

    `status` is what lets a reader tell "nothing has happened yet" from "the
    turn is over", without inferring it from a timeout.
    """

    id: str = Field(primary_key=True)  # uuid4 hex
    conversation_id: int = Field(foreign_key="conversation.id", index=True)
    owner_id: int = Field(default=SINGLE_USER_ID, index=True)
    status: str = Field(default=TURN_RUNNING, index=True)
    error: str | None = None
    # What the turn cost, summed over every provider call it made (the chat
    # loop plus any pipeline or nuance call from inside a tool). Null on turns
    # that predate the columns.
    llm_calls: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class TurnEvent(SQLModel, table=True):
    """A single replayable event within a turn.

    Ordered by `seq`, which is per-turn and monotonic from 1. Clients resume by
    sending the last seq they saw; the reader returns everything above it. That
    one cursor covers reconnect, refresh, and cold load identically.

    This is a replay buffer, not history — durable conversation history lives in
    `message`. Rows are swept once their turn is terminal and old (see
    `repository.purge_old_turn_events`).
    """

    __tablename__ = "turn_event"
    __table_args__ = (
        # The replay contract depends on (turn_id, seq) being unique and gapless.
        # As an index it also serves the `WHERE turn_id = ? AND seq > ?` read,
        # which is the only query this table ever serves.
        UniqueConstraint("turn_id", "seq", name="uq_turn_event_turn_seq"),
    )

    id: int | None = Field(default=None, primary_key=True)
    turn_id: str = Field(foreign_key="turn.id", index=True)
    seq: int
    event: str
    data: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow)
