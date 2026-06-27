import json
from unittest.mock import patch

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.chat.engine import run_chat_turn
from app.db import repository as repo
from app.llm.base import AssistantTurn, ToolCallRequest


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


class FakeProvider:
    """Scripted provider: returns a queued sequence of AssistantTurns."""

    def __init__(self, turns):
        self._turns = list(turns)
        self.sent_history_snapshots = []

    def send(self, system_prompt, history, tools):
        self.sent_history_snapshots.append(list(history))
        return self._turns.pop(0)

    def append_tool_results(self, history, assistant_turn, results):
        return [
            *history,
            {"role": "assistant", "fake_tool_call": True},
            {"role": "tool", "fake_tool_result": [r.content for r in results]},
        ]

    def append_user_message(self, history, text):
        return [*history, {"role": "user", "content": text}]


def _collect(events):
    return list(events)


def test_immediate_final_response_no_tools(session):
    provider = FakeProvider([AssistantTurn(text="Sure, let's talk about it!", tool_calls=[])])
    convo = repo.create_conversation(session)

    events = _collect(
        run_chat_turn(session, provider, convo.id, "hi there", deck_id=None)
    )

    assert any(e.startswith("event: token") for e in events)
    assert any(e.startswith("event: done") for e in events)

    messages = repo.list_messages(session, convo.id)
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[1].text_content == "Sure, let's talk about it!"


def test_tool_call_then_final_response(session):
    deck = repo.create_deck(session, name="Test Deck")
    provider = FakeProvider(
        [
            AssistantTurn(
                text=None,
                tool_calls=[ToolCallRequest(id="call_1", name="deck_get_current", arguments={})],
                raw_assistant_message={"role": "assistant", "tool_calls": ["call_1"]},
            ),
            AssistantTurn(text="Your deck is empty, let's start with a commander.", tool_calls=[]),
        ]
    )
    convo = repo.create_conversation(session)
    repo.set_conversation_deck(session, convo.id, deck.id)

    events = _collect(
        run_chat_turn(session, provider, convo.id, "what's in my deck?", deck_id=deck.id)
    )

    assert any(e.startswith("event: tool_call") for e in events)
    assert any("deck_get_current" in e for e in events)
    assert any(e.startswith("event: done") for e in events)

    messages = repo.list_messages(session, convo.id)
    assert [m.role for m in messages] == ["user", "assistant", "tool", "assistant"]
    assert messages[1].tool_calls[0]["name"] == "deck_get_current"


@patch("app.tools.deck_tools.get_scryfall_client")
def test_deck_mutation_emits_deck_updated_event(mock_get_client, session):
    mock_get_client.return_value.named.return_value = {
        "name": "Sol Ring",
        "cmc": 1.0,
        "color_identity": [],
    }
    deck = repo.create_deck(session, name="Test Deck")
    provider = FakeProvider(
        [
            AssistantTurn(
                text=None,
                tool_calls=[
                    ToolCallRequest(
                        id="call_1",
                        name="deck_add_card",
                        arguments={"card_name": "Sol Ring", "category": "ramp"},
                    )
                ],
                raw_assistant_message={"role": "assistant", "tool_calls": ["call_1"]},
            ),
            AssistantTurn(text="Added Sol Ring!", tool_calls=[]),
        ]
    )
    convo = repo.create_conversation(session)
    repo.set_conversation_deck(session, convo.id, deck.id)

    events = _collect(
        run_chat_turn(session, provider, convo.id, "add sol ring", deck_id=deck.id)
    )

    deck_updated_events = [e for e in events if e.startswith("event: deck_updated")]
    assert len(deck_updated_events) == 1
    payload = json.loads(deck_updated_events[0].split("data: ", 1)[1])
    assert payload["cards"][0]["name"] == "Sol Ring"


def test_tool_failure_does_not_crash_loop_and_model_sees_error(session):
    provider = FakeProvider(
        [
            AssistantTurn(
                text=None,
                tool_calls=[
                    ToolCallRequest(id="call_1", name="scryfall_card_by_name", arguments={"name": "x"})
                ],
                raw_assistant_message={"role": "assistant", "tool_calls": ["call_1"]},
            ),
            AssistantTurn(text="Couldn't find that card.", tool_calls=[]),
        ]
    )
    convo = repo.create_conversation(session)

    with patch("app.tools.dispatch.get_scryfall_client") as mock_client:
        mock_client.return_value.named.side_effect = ConnectionError("network down")
        events = _collect(
            run_chat_turn(session, provider, convo.id, "look up a card", deck_id=None)
        )

    assert any(e.startswith("event: done") for e in events)
    messages = repo.list_messages(session, convo.id)
    tool_message = [m for m in messages if m.role == "tool"][0]
    assert "failed" in tool_message.tool_results[0]["content"].lower()


def test_max_iterations_safety_valve_emits_error(session):
    looping_turn = AssistantTurn(
        text=None,
        tool_calls=[ToolCallRequest(id="call_1", name="deck_get_current", arguments={"deck_id": 1})],
        raw_assistant_message={"role": "assistant", "tool_calls": ["call_1"]},
    )
    deck = repo.create_deck(session)
    provider = FakeProvider([looping_turn] * 8)
    convo = repo.create_conversation(session)
    repo.set_conversation_deck(session, convo.id, deck.id)

    events = _collect(
        run_chat_turn(session, provider, convo.id, "loop forever", deck_id=deck.id)
    )

    assert any(e.startswith("event: error") for e in events)
    assert any("max tool-call iterations" in e for e in events)


def test_history_replay_reconstructs_provider_native_messages(session):
    provider = FakeProvider(
        [
            AssistantTurn(
                text="first reply",
                tool_calls=[],
                raw_assistant_message={"role": "assistant", "content": "first reply"},
            )
        ]
    )
    convo = repo.create_conversation(session)
    _collect(run_chat_turn(session, provider, convo.id, "first message", deck_id=None))

    provider2 = FakeProvider([AssistantTurn(text="second reply", tool_calls=[])])
    _collect(run_chat_turn(session, provider2, convo.id, "second message", deck_id=None))

    sent_history = provider2.sent_history_snapshots[0]
    assert sent_history[0] == {"role": "user", "content": "first message"}
    assert sent_history[1] == {"role": "assistant", "content": "first reply"}
    assert sent_history[2] == {"role": "user", "content": "second message"}
