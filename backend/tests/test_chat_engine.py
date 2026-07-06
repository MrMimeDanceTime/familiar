import json
from unittest.mock import patch

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.chat.engine import _derive_title, run_chat_turn
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
        # Mirrors DeepSeek/OpenAI-style shape: one assistant entry, then one
        # tool-result entry per call (not bundled into a single message, as
        # Anthropic does) - this is what previously exposed the engine's
        # hardcoded appended[0]/appended[1] persistence bug.
        return [
            *history,
            {"role": "assistant", "fake_tool_call": True},
            *[{"role": "tool", "tool_call_id": r.call_id, "fake_tool_result": r.content} for r in results],
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


def test_engine_overrides_model_supplied_deck_id(session):
    """The model must not decide which deck a deck-scoped tool touches. deck_id
    is a required tool param so the model always supplies one; the engine forces
    it to the conversation's deck. Without this, a wrong guess read the wrong
    deck (or none)."""
    right_deck = repo.create_deck(session, name="Sakashima and Krark")
    wrong_deck = repo.create_deck(session, name="Korlash Swamptron")
    provider = FakeProvider(
        [
            AssistantTurn(
                text=None,
                tool_calls=[
                    ToolCallRequest(
                        id="call_1",
                        name="deck_get_current",
                        arguments={"deck_id": wrong_deck.id},
                    )
                ],
                raw_assistant_message={"role": "assistant", "tool_calls": ["call_1"]},
            ),
            AssistantTurn(text="Here's your deck.", tool_calls=[]),
        ]
    )
    convo = repo.create_conversation(session)
    repo.set_conversation_deck(session, convo.id, right_deck.id)

    captured: list[dict] = []

    import app.chat.engine as engine_mod

    original = engine_mod.dispatch

    def spy(name, arguments, sess):
        captured.append({"name": name, "arguments": dict(arguments)})
        return original(name, arguments, sess)

    with patch.object(engine_mod, "dispatch", spy):
        _collect(
            run_chat_turn(session, provider, convo.id, "show my deck", deck_id=right_deck.id)
        )

    deck_call = next(c for c in captured if c["name"] == "deck_get_current")
    assert deck_call["arguments"]["deck_id"] == right_deck.id


def test_multiple_tool_calls_in_one_turn_all_persisted(session):
    """Regression test: the engine used to assume append_tool_results always
    returns exactly [assistant, tool] (Anthropic's bundled shape) and hardcoded
    appended[0]/appended[1], silently dropping every tool-result entry past the
    first whenever a turn made more than one tool call (DeepSeek/OpenAI-style
    providers emit one tool message per call). That produced a persisted
    history missing tool results for some tool_call_ids, which the provider
    then rejected on the next turn with a 400 (mismatched tool_calls/tool
    messages)."""
    deck = repo.create_deck(session, name="Test Deck")
    provider = FakeProvider(
        [
            AssistantTurn(
                text=None,
                tool_calls=[
                    ToolCallRequest(id="call_1", name="deck_get_current", arguments={}),
                    ToolCallRequest(id="call_2", name="deck_get_current", arguments={}),
                ],
                raw_assistant_message={"role": "assistant", "tool_calls": ["call_1", "call_2"]},
            ),
            AssistantTurn(
                text="Got both results.",
                tool_calls=[],
                raw_assistant_message={"role": "assistant", "content": "Got both results."},
            ),
        ]
    )
    convo = repo.create_conversation(session)
    repo.set_conversation_deck(session, convo.id, deck.id)

    _collect(run_chat_turn(session, provider, convo.id, "check twice", deck_id=deck.id))

    messages = repo.list_messages(session, convo.id)
    tool_message = [m for m in messages if m.role == "tool"][0]
    assert len(tool_message.provider_native) == 2
    assert {pn["tool_call_id"] for pn in tool_message.provider_native} == {"call_1", "call_2"}

    history = _load_history_for_test(session, convo.id)
    tool_native_entries = [h for h in history if h.get("role") == "tool"]
    assert len(tool_native_entries) == 2


def _load_history_for_test(session, conversation_id):
    from app.chat.engine import _load_history

    return _load_history(session, conversation_id)


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


@patch("app.tools.deck_tools.get_scryfall_client")
def test_proposals_anchored_to_final_text_message(mock_get_client, session):
    """Proposals must anchor to the turn's *final* assistant text message — the
    bubble the player actually sees ("Proposed Risen Reef.") — not the internal
    text-less tool-call message, which the UI hides. Anchoring to the hidden
    message left every batch with no visible anchor, so they all pooled at the
    bottom of the transcript instead of rendering inline."""
    mock_get_client.return_value.named.return_value = {
        "name": "Risen Reef",
        "cmc": 3.0,
        "color_identity": ["G", "U"],
    }
    deck = repo.create_deck(session, name="Test Deck")
    provider = FakeProvider(
        [
            AssistantTurn(
                text=None,
                tool_calls=[
                    ToolCallRequest(
                        id="call_1",
                        name="propose_deck_changes",
                        arguments={
                            "summary": "A value elemental",
                            "changes": [{"action": "add", "card_name": "Risen Reef"}],
                        },
                    )
                ],
                raw_assistant_message={"role": "assistant", "tool_calls": ["call_1"]},
            ),
            AssistantTurn(text="Proposed Risen Reef.", tool_calls=[]),
        ]
    )
    convo = repo.create_conversation(session)
    repo.set_conversation_deck(session, convo.id, deck.id)

    _collect(
        run_chat_turn(session, provider, convo.id, "propose a value creature", deck_id=deck.id)
    )

    messages = repo.list_messages(session, convo.id)
    final_text_msg = next(
        m for m in messages
        if m.role == "assistant" and m.text_content == "Proposed Risen Reef."
    )
    tool_call_msg = next(m for m in messages if m.role == "assistant" and m.tool_calls)
    proposals = repo.list_proposals(session, convo.id)
    assert len(proposals) == 1
    # Anchored to the visible final-text bubble, not the hidden tool-call message.
    assert proposals[0].message_id == final_text_msg.id
    assert proposals[0].message_id != tool_call_msg.id


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
    provider = FakeProvider([looping_turn] * 12)
    convo = repo.create_conversation(session)
    repo.set_conversation_deck(session, convo.id, deck.id)

    events = _collect(
        run_chat_turn(session, provider, convo.id, "loop forever", deck_id=deck.id)
    )

    assert any(e.startswith("event: error") for e in events)
    assert any("max tool-call iterations" in e for e in events)


def test_derive_title_truncates_long_text():
    long_text = "x" * 100
    title = _derive_title(long_text, max_length=60)
    assert len(title) == 60
    assert title.endswith("…")


def test_derive_title_collapses_whitespace():
    assert _derive_title("hello   \n  world") == "hello world"


def test_first_message_sets_conversation_title(session):
    provider = FakeProvider([AssistantTurn(text="Sure thing!", tool_calls=[])])
    convo = repo.create_conversation(session)

    _collect(
        run_chat_turn(
            session, provider, convo.id,
            "Help me build a weird Korvold blink deck please",
            deck_id=None,
        )
    )

    refreshed = repo.get_conversation(session, convo.id)
    assert refreshed.title == "Help me build a weird Korvold blink deck please"


def test_second_message_does_not_overwrite_title(session):
    provider = FakeProvider(
        [
            AssistantTurn(text="first reply", tool_calls=[]),
            AssistantTurn(text="second reply", tool_calls=[]),
        ]
    )
    convo = repo.create_conversation(session)

    _collect(run_chat_turn(session, provider, convo.id, "first message", deck_id=None))
    _collect(run_chat_turn(session, provider, convo.id, "second message", deck_id=None))

    refreshed = repo.get_conversation(session, convo.id)
    assert refreshed.title == "first message"


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
