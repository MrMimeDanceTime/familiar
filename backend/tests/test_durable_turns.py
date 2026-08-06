"""Durable chat turns: execution outlives the connection.

The bug these pin: `run_chat_turn` is a lazy generator, so while it was consumed
directly by StreamingResponse a client that stopped reading (a backgrounded
mobile tab) stalled the turn and Starlette then closed it. Tool calls already
committed — including deck mutations — but `_finalize_turn` never ran, so no
assistant message explained them.

The load-bearing test is `test_turn_completes_with_no_client_attached`: it runs a
turn with nothing consuming it and asserts completion. That fails against the old
design by construction.
"""

import time

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.chat import turn_runner
from app.chat.turn_bus import HEARTBEAT, PollingEventBus
from app.db import repository as repo
from app.db.models import TURN_DONE, TURN_ERROR, TURN_RUNNING


@pytest.fixture
def session(tmp_path, monkeypatch):
    """An isolated DB, with the app's engine pointed at it.

    turn_runner and the bus each open their own Session from get_engine(), so
    the engine itself has to be swapped, not just this session.
    """
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False}
    )
    with engine.begin() as conn:
        from sqlalchemy import text

        conn.execute(text("PRAGMA journal_mode=WAL"))
    SQLModel.metadata.create_all(engine)

    monkeypatch.setattr("app.db.session.get_engine", lambda: engine)
    monkeypatch.setattr("app.chat.turn_runner.get_engine", lambda: engine)
    monkeypatch.setattr("app.chat.turn_bus.get_engine", lambda: engine)

    with Session(engine) as s:
        yield s


def _make_turn(session, events=(), status=TURN_RUNNING):
    conversation = repo.create_conversation(session)
    turn_id = turn_runner.new_turn_id()
    repo.create_turn(session, turn_id, conversation.id)
    for event, data in events:
        repo.append_turn_event(session, turn_id, event, data)
    if status != TURN_RUNNING:
        repo.finish_turn(session, turn_id, status)
    return turn_id


# --- the event log ---------------------------------------------------------


def test_seq_is_contiguous_and_ordered(session):
    turn_id = _make_turn(
        session,
        events=[("token", {"text": "a"}), ("tool_call", {"name": "x"}), ("done", {})],
    )
    events = repo.list_turn_events(session, turn_id)
    assert [e.seq for e in events] == [1, 2, 3]
    assert [e.event for e in events] == ["token", "tool_call", "done"]


def test_after_cursor_returns_only_newer_events(session):
    turn_id = _make_turn(
        session, events=[("token", {"text": str(i)}) for i in range(5)]
    )
    tail = repo.list_turn_events(session, turn_id, after=3)
    assert [e.seq for e in tail] == [4, 5]
    # Replaying from zero returns the whole turn, which is what a cold load or a
    # post-refresh resume does.
    assert len(repo.list_turn_events(session, turn_id, after=0)) == 5


def test_duplicate_seq_is_rejected(session):
    """The replay contract depends on (turn_id, seq) being unique."""
    from app.db.models import TurnEvent

    turn_id = _make_turn(session, events=[("token", {"text": "a"})])
    session.add(TurnEvent(turn_id=turn_id, seq=1, event="token", data={"text": "dup"}))
    with pytest.raises(Exception):
        session.commit()
    session.rollback()


# --- ownership seam --------------------------------------------------------


def test_turn_is_not_readable_by_another_owner(session):
    turn_id = _make_turn(session)
    assert repo.get_turn(session, turn_id, owner_id=1) is not None
    assert repo.get_turn(session, turn_id, owner_id=2) is None


# --- restart reconciliation ------------------------------------------------


def test_orphaned_running_turns_are_failed(session):
    running = _make_turn(session)
    done = _make_turn(session, status=TURN_DONE)

    assert repo.fail_orphaned_turns(session) == 1

    session.expire_all()
    assert repo.get_turn(session, running).status == TURN_ERROR
    assert repo.get_turn(session, running).error is not None
    # A turn that already finished is left alone.
    assert repo.get_turn(session, done).status == TURN_DONE


def test_purge_drops_only_old_terminal_turn_events(session):
    from datetime import datetime, timedelta, timezone

    fresh = _make_turn(session, events=[("token", {"text": "a"})], status=TURN_DONE)
    old = _make_turn(session, events=[("token", {"text": "b"})], status=TURN_DONE)
    running = _make_turn(session, events=[("token", {"text": "c"})])

    stale_turn = repo.get_turn(session, old)
    stale_turn.updated_at = datetime.now(timezone.utc) - timedelta(hours=48)
    session.add(stale_turn)
    session.commit()

    assert repo.purge_old_turn_events(session, max_age_hours=24) == 1

    assert repo.list_turn_events(session, old) == []
    assert len(repo.list_turn_events(session, fresh)) == 1
    # An in-flight turn is never purged, however long it has been running.
    assert len(repo.list_turn_events(session, running)) == 1


# --- execution independence: the actual bug --------------------------------


def test_turn_completes_with_no_client_attached(session, monkeypatch):
    """THE regression test.

    Runs a turn with nothing consuming its output and asserts it finished and
    persisted. Under the old design the generator only advanced when a client
    pulled, so with no client there was no progress at all.
    """
    conversation = repo.create_conversation(session)
    turn_id = turn_runner.new_turn_id()
    repo.create_turn(session, turn_id, conversation.id)

    def fake_run_chat_turn(session_, provider, conversation_id, message, deck_id):
        yield 'event: token\ndata: {"text": "Considering "}\n\n'
        yield 'event: tool_call\ndata: {"name": "scryfall_search", "arguments": {}}\n\n'
        yield 'event: token\ndata: {"text": "your deck."}\n\n'
        yield 'event: done\ndata: {"message_id": 7, "conversation_id": %d}\n\n' % conversation_id

    monkeypatch.setattr(turn_runner, "run_chat_turn", fake_run_chat_turn)
    monkeypatch.setattr(turn_runner, "get_provider", lambda: object())

    turn_runner.execute_turn(turn_id, conversation.id, "build me a deck", None)

    session.expire_all()
    assert repo.get_turn(session, turn_id).status == TURN_DONE

    events = repo.list_turn_events(session, turn_id)
    kinds = [e.event for e in events]
    assert "done" in kinds
    assert "tool_call" in kinds
    # Tokens are batched, but every character must survive the batching.
    text = "".join(e.data["text"] for e in events if e.event == "token")
    assert text == "Considering your deck."


def test_tokens_are_batched_but_ordered_against_other_events(session, monkeypatch):
    conversation = repo.create_conversation(session)
    turn_id = turn_runner.new_turn_id()
    repo.create_turn(session, turn_id, conversation.id)

    def fake_run_chat_turn(*args, **kwargs):
        for i in range(10):
            yield 'event: token\ndata: {"text": "%d"}\n\n' % i
        yield 'event: deck_updated\ndata: {"id": 1}\n\n'
        for i in range(10, 20):
            yield 'event: token\ndata: {"text": "%d"}\n\n' % i
        yield 'event: done\ndata: {"message_id": 1, "conversation_id": 1}\n\n'

    monkeypatch.setattr(turn_runner, "run_chat_turn", fake_run_chat_turn)
    monkeypatch.setattr(turn_runner, "get_provider", lambda: object())

    turn_runner.execute_turn(turn_id, conversation.id, "hi", None)

    session.expire_all()
    events = repo.list_turn_events(session, turn_id)
    # Far fewer rows than tokens: the batching is real.
    assert len([e for e in events if e.event == "token"]) < 20

    kinds = [e.event for e in events]
    deck_at = kinds.index("deck_updated")
    before = "".join(e.data["text"] for e in events[:deck_at] if e.event == "token")
    after = "".join(e.data["text"] for e in events[deck_at:] if e.event == "token")
    # A token buffer must flush before an ordered event, or tokens leak across it.
    assert before == "".join(str(i) for i in range(10))
    assert after == "".join(str(i) for i in range(10, 20))


def test_engine_failure_marks_the_turn_errored(session, monkeypatch):
    conversation = repo.create_conversation(session)
    turn_id = turn_runner.new_turn_id()
    repo.create_turn(session, turn_id, conversation.id)

    def exploding(*args, **kwargs):
        yield 'event: token\ndata: {"text": "partial"}\n\n'
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(turn_runner, "run_chat_turn", exploding)
    monkeypatch.setattr(turn_runner, "get_provider", lambda: object())

    turn_runner.execute_turn(turn_id, conversation.id, "hi", None)

    session.expire_all()
    turn = repo.get_turn(session, turn_id)
    assert turn.status == TURN_ERROR
    assert "provider exploded" in turn.error
    # The partial output before the failure is still replayable.
    events = repo.list_turn_events(session, turn_id)
    assert any(e.event == "token" for e in events)
    assert any(e.event == "error" for e in events)


# --- the reader ------------------------------------------------------------


def test_bus_replays_then_stops_on_terminal_turn(session):
    turn_id = _make_turn(
        session,
        events=[("token", {"text": "a"}), ("done", {})],
        status=TURN_DONE,
    )
    bus = PollingEventBus(poll_interval=0.01)
    seen = list(bus.subscribe(turn_id, after=0))
    assert [e.event for e in seen] == ["token", "done"]


def test_bus_resumes_from_cursor(session):
    turn_id = _make_turn(
        session,
        events=[("token", {"text": "a"}), ("token", {"text": "b"})],
        status=TURN_DONE,
    )
    bus = PollingEventBus(poll_interval=0.01)
    seen = list(bus.subscribe(turn_id, after=1))
    assert [e.data["text"] for e in seen] == ["b"]


def test_bus_picks_up_events_written_after_it_started(session):
    """The live-tail path: a reader attached mid-turn sees later writes."""
    import threading

    turn_id = _make_turn(session, events=[("token", {"text": "first"})])
    engine = session.get_bind()

    def write_later():
        time.sleep(0.05)
        with Session(engine) as s:
            repo.append_turn_event(s, turn_id, "token", {"text": "second"})
            repo.finish_turn(s, turn_id, TURN_DONE)

    writer = threading.Thread(target=write_later)
    writer.start()
    bus = PollingEventBus(poll_interval=0.01, max_stream_seconds=5)
    # Heartbeats interleave while the turn is quiet; only real events count.
    seen = [
        e.data.get("text")
        for e in bus.subscribe(turn_id, after=0)
        if e is not HEARTBEAT
    ]
    writer.join()

    assert seen == ["first", "second"]


# --- heartbeat -------------------------------------------------------------


def test_bus_emits_heartbeats_while_a_turn_is_silent(session):
    """A running turn that produces nothing must still signal liveness.

    Without this the socket carries no bytes during a slow tool call, and an
    intermediary or the browser can reap the connection — which reaches the user
    as an "error in input stream" rather than a quiet wait.
    """
    import threading

    from app.chat.turn_bus import HEARTBEAT

    turn_id = _make_turn(session)  # running, no events
    engine = session.get_bind()

    def finish_later():
        time.sleep(0.2)
        with Session(engine) as s:
            repo.append_turn_event(s, turn_id, "done", {"message_id": 1})
            repo.finish_turn(s, turn_id, TURN_DONE)

    worker = threading.Thread(target=finish_later)
    worker.start()
    bus = PollingEventBus(poll_interval=0.01, max_stream_seconds=5)
    seen = list(bus.subscribe(turn_id, after=0))
    worker.join()

    assert any(e is HEARTBEAT for e in seen), "no heartbeat during the silent window"
    # The real event still arrives, and the stream still terminates.
    assert [getattr(e, "event", None) for e in seen if e is not HEARTBEAT] == ["done"]


def test_no_heartbeat_once_the_turn_is_terminal(session):
    """A finished turn ends the stream rather than heartbeating forever."""
    from app.chat.turn_bus import HEARTBEAT

    turn_id = _make_turn(session, events=[("done", {})], status=TURN_DONE)
    bus = PollingEventBus(poll_interval=0.01, max_stream_seconds=5)
    seen = list(bus.subscribe(turn_id, after=0))

    assert not any(e is HEARTBEAT for e in seen)
    assert [e.event for e in seen] == ["done"]
