import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session

from app.chat.streaming import error_event, format_sse
from app.chat.turn_bus import HEARTBEAT, get_event_bus
from app.chat.turn_runner import new_turn_id, submit_turn
from app.db import repository as repo
from app.db.models import SINGLE_USER_ID, TURN_RUNNING
from app.db.session import get_engine

router = APIRouter(prefix="/api/chat", tags=["chat"])

# How often to write a keep-alive comment on an otherwise silent stream. Well
# under the 60s idle timeouts intermediaries typically apply.
HEARTBEAT_SECONDS = 15.0


def current_owner_id(request: Request) -> int:
    """Who owns the turns this request may see.

    Familiar has no auth, so this is a constant. It exists as a function, and is
    called on the paths that matter, so that adding real users means changing
    this one body rather than auditing every query for the one that forgot to
    scope itself. See models.SINGLE_USER_ID.
    """
    return SINGLE_USER_ID


class ChatIn(BaseModel):
    conversation_id: int | str  # "new" or an existing conversation id
    message: str
    deck_id: int | None = None  # when "new", optionally link to an existing deck


class ChatAccepted(BaseModel):
    turn_id: str
    conversation_id: int


@router.post("", response_model=ChatAccepted)
def post_chat(body: ChatIn, request: Request):
    """Start a turn and return its id. Does not stream.

    The turn executes independently of this request; the client reads it back
    from the events endpoint below. That separation is the fix for turns dying
    when a mobile browser suspends a backgrounded tab.
    """
    with Session(get_engine()) as session:
        if body.conversation_id == "new":
            if body.deck_id is not None:
                deck = repo.get_deck(session, body.deck_id)
                if not deck:
                    raise HTTPException(status_code=404, detail=f"Deck {body.deck_id} not found")
            else:
                deck = repo.create_deck(session)
            conversation = repo.create_conversation(session)
            repo.set_conversation_deck(session, conversation.id, deck.id)
            conversation_id = conversation.id
            deck_id = deck.id
        else:
            conversation_id = int(body.conversation_id)
            conversation = repo.get_conversation(session, conversation_id)
            if not conversation:
                raise HTTPException(status_code=404, detail="Conversation not found")
            deck_id = conversation.deck_id
            # One turn at a time per conversation. Two concurrent turns would
            # interleave message sequence numbers and replay a corrupted history
            # on the next call; the UI disables the composer while streaming,
            # but a refresh or a second tab does not know that.
            running = repo.running_turn_for_conversation(session, conversation_id)
            if running is not None:
                raise HTTPException(
                    status_code=409,
                    detail=f"A turn is already running for this conversation (turn {running.id}).",
                )

        turn_id = new_turn_id()
        repo.create_turn(
            session,
            turn_id,
            conversation_id,
            owner_id=current_owner_id(request),
        )

    submit_turn(turn_id, conversation_id, body.message, deck_id)
    return ChatAccepted(turn_id=turn_id, conversation_id=conversation_id)


@router.get("/turns/{turn_id}/events")
def get_turn_events(turn_id: str, request: Request, after: int = 0):
    """Replay a turn's events from `after`, then follow it live.

    Pure reader: no side effects, safe to call repeatedly. A client that
    disconnects and comes back sends the last seq it saw and receives exactly
    what it missed, so reconnect, refresh, and cold load are one code path.
    """
    owner_id = current_owner_id(request)
    with Session(get_engine()) as session:
        turn = repo.get_turn(session, turn_id, owner_id=owner_id)
        if turn is None:
            raise HTTPException(status_code=404, detail="Turn not found")

    bus = get_event_bus()

    def event_stream():
        # An SSE comment, which clients ignore. A turn can be silent for a long
        # time (a slow tool call, a thinking-mode provider request), and a
        # connection with no bytes on it is liable to be reaped by an
        # intermediary or the browser itself — surfacing to the client as a
        # dropped stream rather than a quiet one. This keeps it demonstrably
        # alive.
        yield ": open\n\n"
        last_beat = time.monotonic()

        for event in bus.subscribe(turn_id, after=after):
            if event is HEARTBEAT:
                now = time.monotonic()
                if now - last_beat >= HEARTBEAT_SECONDS:
                    last_beat = now
                    yield ": ping\n\n"
                continue
            last_beat = time.monotonic()
            yield format_sse(event.event, {**event.data, "seq": event.seq})

        # A turn that failed before writing its own error event (a crash between
        # the last event and finish_turn) would otherwise end the stream with no
        # explanation. Re-read the row so the client always learns the outcome.
        with Session(get_engine()) as session:
            final = repo.get_turn(session, turn_id, owner_id=owner_id)
        if final is not None and final.status != TURN_RUNNING and final.error:
            yield error_event(final.error)

    # no-transform stops a proxy from buffering the stream and defeating SSE.
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.post("/turns/{turn_id}/cancel")
def cancel_turn(turn_id: str, request: Request):
    """Stop a running turn at its next checkpoint.

    The engine finishes the turn with whatever it has written so far, so the
    transcript stays consistent and the player can redirect immediately.
    """
    with Session(get_engine()) as session:
        turn = repo.get_turn(session, turn_id, owner_id=current_owner_id(request))
        if turn is None:
            raise HTTPException(status_code=404, detail="Turn not found")
        if turn.status != TURN_RUNNING:
            return {"turn_id": turn.id, "status": turn.status, "cancel_requested": False}
        repo.request_turn_cancel(session, turn_id, owner_id=current_owner_id(request))
        return {"turn_id": turn.id, "status": turn.status, "cancel_requested": True}


@router.get("/turns/{turn_id}")
def get_turn_status(turn_id: str, request: Request):
    """Cheap status check, for a client deciding whether to resume a turn."""
    with Session(get_engine()) as session:
        turn = repo.get_turn(session, turn_id, owner_id=current_owner_id(request))
        if turn is None:
            raise HTTPException(status_code=404, detail="Turn not found")
        return {
            "turn_id": turn.id,
            "conversation_id": turn.conversation_id,
            "status": turn.status,
            "error": turn.error,
            "usage": {
                "llm_calls": turn.llm_calls,
                "prompt_tokens": turn.prompt_tokens,
                "completion_tokens": turn.completion_tokens,
                "reasoning_tokens": turn.reasoning_tokens,
            },
        }
