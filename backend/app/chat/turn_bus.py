"""How a reader learns that new turn events exist.

The durable log in `turn_event` is always the source of truth. This module only
answers "has anything new landed?", which is a latency question, not a
correctness one. That split is deliberate: a missed notification degrades to
poll latency instead of losing an event.

Today there is one implementation, `PollingEventBus`, which re-queries the table
on an interval. It needs no broker and is entirely adequate for a single user on
a single worker. When either of those stops being true, a Redis or Postgres
LISTEN/NOTIFY bus implements the same protocol and nothing else changes.
"""

from __future__ import annotations

import time
from typing import Iterator, Protocol

from sqlmodel import Session

from app.db import repository as repo
from app.db.models import TURN_RUNNING, TurnEvent
from app.db.session import get_engine

# How often a reader re-checks for new events. 200ms keeps streaming visually
# smooth (tokens are flushed in ~120ms batches upstream) without a busy loop.
POLL_INTERVAL_SECONDS = 0.2

# Ceiling on a single subscribe() call, so a wedged turn can't pin a connection
# open forever. The client reconnects with its cursor and resumes seamlessly.
MAX_STREAM_SECONDS = 900


class TurnEventBus(Protocol):
    """Delivers a turn's events in seq order, starting after a cursor."""

    def subscribe(self, turn_id: str, after: int = 0) -> Iterator[TurnEvent]:
        """Yield events with seq > after, then follow the turn until it ends."""
        ...


class PollingEventBus:
    """Tails `turn_event` by polling.

    Each poll opens its own short-lived Session. Holding one open across the
    life of a stream would pin a read transaction, and under WAL that keeps the
    reader on a stale snapshot — it would never observe the writer's commits and
    would tail forever seeing nothing.
    """

    def __init__(
        self,
        poll_interval: float = POLL_INTERVAL_SECONDS,
        max_stream_seconds: float = MAX_STREAM_SECONDS,
    ) -> None:
        self._poll_interval = poll_interval
        self._max_stream_seconds = max_stream_seconds

    def subscribe(self, turn_id: str, after: int = 0) -> Iterator[TurnEvent]:
        cursor = after
        started = time.monotonic()

        while True:
            with Session(get_engine()) as session:
                events = repo.list_turn_events(session, turn_id, after=cursor)
                for event in events:
                    cursor = event.seq
                    # Detach so the caller can read attributes after the
                    # Session closes.
                    session.expunge(event)
                    yield event

                if not events:
                    turn = repo.get_turn(session, turn_id)
                    # A terminal turn with nothing left above the cursor means the
                    # client has seen everything there will ever be.
                    if turn is None or turn.status != TURN_RUNNING:
                        return

            if time.monotonic() - started > self._max_stream_seconds:
                return

            time.sleep(self._poll_interval)


_bus: TurnEventBus | None = None


def get_event_bus() -> TurnEventBus:
    global _bus
    if _bus is None:
        _bus = PollingEventBus()
    return _bus
