"""Runs a chat turn to completion, independent of any HTTP connection.

`run_chat_turn` is a lazy generator: it advances only when something pulls from
it. While it was consumed directly by StreamingResponse, the puller was the
client, so a backgrounded mobile tab stalled the turn mid-flight and Starlette
then closed it — leaving committed tool calls (including deck mutations) with no
final assistant message to explain them.

Here the puller is a worker thread instead. Nothing about execution depends on a
client being attached, so the turn always runs to completion; the client's only
job is to read the event log at whatever pace it can manage.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from typing import Any

from sqlmodel import Session

from app.chat.engine import run_chat_turn
from app.db import repository as repo
from app.db.models import TURN_DONE, TURN_ERROR
from app.db.session import get_engine
from app.llm.factory import get_provider

logger = logging.getLogger("app.chat.turn_runner")

# Tokens arrive far faster than a client needs them, and one row per token would
# bloat the log for no benefit. Buffer them and flush on whichever comes first.
# Every other event type flushes immediately: those carry meaning and ordering.
TOKEN_FLUSH_SECONDS = 0.12
TOKEN_FLUSH_CHARS = 200

_TERMINAL_EVENTS = {"done", "error"}


def new_turn_id() -> str:
    return uuid.uuid4().hex


def _parse_sse(chunk: str) -> tuple[str, dict[str, Any]] | None:
    """Recover (event, data) from the SSE string the engine yields.

    The engine formats SSE directly, and rewriting it to emit structured events
    would touch every yield site in the agentic loop. Parsing here keeps this
    change contained to the transport, which is the actual bug.
    """
    event = ""
    data = ""
    for line in chunk.split("\n"):
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: "):
            data = line[len("data: ") :]
    if not event or not data:
        return None
    try:
        return event, json.loads(data)
    except json.JSONDecodeError:
        logger.warning("Un-parseable SSE data in turn stream: %r", data[:200])
        return None


class _TokenBuffer:
    """Coalesces consecutive token events into one row."""

    def __init__(self, session: Session, turn_id: str) -> None:
        self._session = session
        self._turn_id = turn_id
        self._text: list[str] = []
        self._chars = 0
        self._first_at: float | None = None

    def add(self, text: str) -> None:
        if self._first_at is None:
            self._first_at = time.monotonic()
        self._text.append(text)
        self._chars += len(text)
        if (
            self._chars >= TOKEN_FLUSH_CHARS
            or time.monotonic() - self._first_at >= TOKEN_FLUSH_SECONDS
        ):
            self.flush()

    def flush(self) -> None:
        if not self._text:
            return
        repo.append_turn_event(
            self._session, self._turn_id, "token", {"text": "".join(self._text)}
        )
        self._text.clear()
        self._chars = 0
        self._first_at = None


def execute_turn(turn_id: str, conversation_id: int, message: str, deck_id: int | None) -> None:
    """Drive one turn to completion, appending every event to its log."""
    with Session(get_engine()) as session:
        buffer = _TokenBuffer(session, turn_id)
        status, error = TURN_DONE, None
        try:
            provider = get_provider()
            for chunk in run_chat_turn(session, provider, conversation_id, message, deck_id):
                parsed = _parse_sse(chunk)
                if parsed is None:
                    continue
                event, data = parsed
                if event == "token":
                    buffer.add(data.get("text", ""))
                    continue
                # Anything else is ordered relative to the tokens around it, so
                # the buffer has to land first.
                buffer.flush()
                repo.append_turn_event(session, turn_id, event, data)
                if event == "error":
                    status, error = TURN_ERROR, data.get("message")
        except Exception as exc:  # noqa: BLE001 - the turn is the failure boundary
            logger.exception("Turn %s failed", turn_id)
            buffer.flush()
            status, error = TURN_ERROR, str(exc)
            repo.append_turn_event(session, turn_id, "error", {"message": str(exc)})
        finally:
            buffer.flush()
            repo.finish_turn(session, turn_id, status, error)


def submit_turn(turn_id: str, conversation_id: int, message: str, deck_id: int | None) -> None:
    """Begin executing a turn.

    SINGLE-WORKER ASSUMPTION. The turn runs in a thread inside this process, so
    it is only reachable by the worker that accepted the POST. The Dockerfile's
    CMD passes no --workers, so there is exactly one, and readers necessarily
    hit the same process.

    If that ever changes, this function is the only thing that must change: the
    event log already lives in the database, so readers on another worker
    already work. Replace the thread with a real queue (and swap in a pub/sub
    TurnEventBus for latency); nothing else in the design depends on locality.

    A thread rather than asyncio.create_task because the engine, the provider
    SDKs, and SQLModel are all synchronous and blocking — running them on the
    event loop would stall every other request.
    """
    thread = threading.Thread(
        target=execute_turn,
        args=(turn_id, conversation_id, message, deck_id),
        name=f"turn-{turn_id[:8]}",
        daemon=True,
    )
    thread.start()
