import json
from unittest.mock import patch

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.chat.engine import _derive_title, run_chat_turn
from app.db import repository as repo
from app.llm.base import AssistantTurn, ToolCallRequest
from app.tools.schemas import TOOL_SPECS


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
        self.thinking_flags = []
        self.tools_per_send = []

    def send(self, system_prompt, history, tools, *, thinking=True):
        self.sent_history_snapshots.append(list(history))
        self.thinking_flags.append(thinking)
        self.tools_per_send.append(tools)
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


def test_engine_disables_thinking_for_the_chat_loop(session):
    # Thinking mode is the biggest lever on turn latency; the loop turns it off
    # (deep reasoning lives in tool choices + the pipeline/nuance calls).
    provider = FakeProvider([AssistantTurn(text="hi", tool_calls=[])])
    convo = repo.create_conversation(session)
    _collect(run_chat_turn(session, provider, convo.id, "hey", deck_id=None))
    assert provider.thinking_flags == [False]


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


def test_max_iterations_forces_a_final_text_response(session):
    """When the loop exhausts its tool-call budget, the engine must force one
    toolless wrap-up send so the player gets a real summary — not the old bare
    'reached max iterations' error that discarded a nearly-finished turn."""

    class NeverStopsProvider:
        """Returns a tool call on every tool-bearing send (so the loop never
        terminates naturally), but a final text response when sent no tools —
        exactly the wrap-up call the engine makes on exhaustion."""

        def __init__(self):
            self.toolless_sends = 0
            self.thinking_on_wrap = None

        def send(self, system_prompt, history, tools, *, thinking=True):
            if not tools:
                self.toolless_sends += 1
                self.thinking_on_wrap = thinking
                return AssistantTurn(text="Here's the summary of what I found.", tool_calls=[])
            return AssistantTurn(
                text=None,
                tool_calls=[ToolCallRequest(id="c", name="deck_get_current", arguments={})],
                raw_assistant_message={"role": "assistant", "tool_calls": ["c"]},
            )

        def append_tool_results(self, history, assistant_turn, results):
            return [*history, {"role": "assistant"}, {"role": "tool"}]

        def append_user_message(self, history, text):
            return [*history, {"role": "user", "content": text}]

    deck = repo.create_deck(session, name="Loopy")
    convo = repo.create_conversation(session)
    repo.set_conversation_deck(session, convo.id, deck.id)
    provider = NeverStopsProvider()

    events = _collect(
        run_chat_turn(session, provider, convo.id, "help", deck_id=deck.id)
    )

    # The player gets a real answer and a clean close, not an error.
    assert not any(e.startswith("event: error") for e in events)
    assert any(e.startswith("event: done") for e in events)
    assert any("summary of what I found" in e for e in events)
    # Exactly one forced wrap-up, and it ran with thinking on (synthesis turn).
    assert provider.toolless_sends == 1
    # Thinking must stay OFF here. Turning it on broke the turn outright:
    # DeepSeek requires every assistant message in the history to carry
    # `reasoning_content` when a call runs in thinking mode, and the loop's own
    # messages are produced with thinking off, so the wrap-up 400'd and lost the
    # very turn this fallback exists to rescue.
    assert provider.thinking_on_wrap is False

    messages = repo.list_messages(session, convo.id)
    assert messages[-1].role == "assistant"
    assert messages[-1].text_content == "Here's the summary of what I found."


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

    def spy(name, arguments, sess, provider=None):
        captured.append({"name": name, "arguments": dict(arguments)})
        return original(name, arguments, sess, provider=provider)

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


@patch("app.tools.deck_tools.get_scryfall_client")
def test_only_withdraw_tool_offered_after_proposals(mock_get_client, session):
    """After a proposal batch, the engine must offer ONLY withdraw_pending_
    proposals — so the model can trim but not add another batch or churn."""
    mock_get_client.return_value.named.return_value = {
        "name": "Sol Ring", "cmc": 1.0, "color_identity": [],
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
                            "summary": "Ramp",
                            "changes": [{"action": "add", "card_name": "Sol Ring"}],
                        },
                    )
                ],
                raw_assistant_message={"role": "assistant", "tool_calls": ["call_1"]},
            ),
            AssistantTurn(text="Here's your ramp batch.", tool_calls=[]),
        ]
    )
    convo = repo.create_conversation(session)
    repo.set_conversation_deck(session, convo.id, deck.id)

    _collect(
        run_chat_turn(session, provider, convo.id, "suggest ramp", deck_id=deck.id)
    )

    # First send offered the full tool set; the send AFTER proposals offered
    # only the withdraw tool.
    assert provider.tools_per_send[0] == TOOL_SPECS
    assert [t.name for t in provider.tools_per_send[1]] == ["withdraw_pending_proposals"]


@patch("app.tools.deck_tools.get_scryfall_client")
def test_proposals_revealed_only_after_trims_settle(mock_get_client, session):
    """The deck_proposal event must fire ONCE, at turn end, carrying only the
    proposals that survived trimming — never mid-loop (so trimmed cards don't
    flash into the UI and back out)."""
    mock_get_client.return_value.named.side_effect = lambda name, **k: {
        "name": name, "cmc": 1.0, "color_identity": [],
    }
    deck = repo.create_deck(session, name="Test Deck")
    provider = FakeProvider(
        [
            # Propose three cards.
            AssistantTurn(
                text=None,
                tool_calls=[
                    ToolCallRequest(
                        id="call_1",
                        name="propose_deck_changes",
                        arguments={
                            "summary": "Two rocks the player named",
                            "changes": [
                                {"action": "add", "card_name": "Sol Ring"},
                                {"action": "add", "card_name": "Mind Stone"},
                            ],
                        },
                    )
                ],
                raw_assistant_message={"role": "assistant", "tool_calls": ["call_1"]},
            ),
            # Trim one of them.
            AssistantTurn(
                text=None,
                tool_calls=[
                    ToolCallRequest(
                        id="call_2",
                        name="withdraw_pending_proposals",
                        arguments={"card_names": ["Mind Stone"]},
                    )
                ],
                raw_assistant_message={"role": "assistant", "tool_calls": ["call_2"]},
            ),
            AssistantTurn(text="Two ramp rocks for you.", tool_calls=[]),
        ]
    )
    convo = repo.create_conversation(session)
    repo.set_conversation_deck(session, convo.id, deck.id)

    events = _collect(
        run_chat_turn(session, provider, convo.id, "suggest ramp", deck_id=deck.id)
    )

    proposal_events = [e for e in events if e.startswith("event: deck_proposal")]
    # Exactly one reveal, at the end.
    assert len(proposal_events) == 1
    # It carries only the survivor, not the trimmed Mind Stone.
    payload = proposal_events[0]
    assert "Sol Ring" in payload
    assert "Mind Stone" not in payload
    # The reveal comes before done.
    done_idx = next(i for i, e in enumerate(events) if e.startswith("event: done"))
    reveal_idx = next(i for i, e in enumerate(events) if e.startswith("event: deck_proposal"))
    assert reveal_idx < done_idx


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


def test_max_iterations_safety_valve_wraps_up_instead_of_erroring(session):
    # Exhausting the tool-call budget must NOT drop the turn with a bare error.
    # The engine forces a toolless wrap-up send (the 13th here) so the model
    # delivers a real final response. See test_max_iterations_forces_a_final_
    # text_response for the thinking-flag/side-effect assertions.
    looping_turn = AssistantTurn(
        text=None,
        tool_calls=[ToolCallRequest(id="call_1", name="deck_get_current", arguments={"deck_id": 1})],
        raw_assistant_message={"role": "assistant", "tool_calls": ["call_1"]},
    )
    wrap_up = AssistantTurn(text="Here's where I got to.", tool_calls=[])
    deck = repo.create_deck(session)
    provider = FakeProvider([looping_turn] * 12 + [wrap_up])
    convo = repo.create_conversation(session)
    repo.set_conversation_deck(session, convo.id, deck.id)

    events = _collect(
        run_chat_turn(session, provider, convo.id, "loop forever", deck_id=deck.id)
    )

    assert not any(e.startswith("event: error") for e in events)
    assert any(e.startswith("event: done") for e in events)
    assert any("Here's where I got to." in e for e in events)


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


# ── Routing hand-picked batches to the scoring pipeline ──────────────────
#
# The prompt asked the model to use suggest_cards for role batches and was
# ignored: on a live deployed deck, 68 proposals were built with
# propose_deck_changes and suggest_cards was called ZERO times. Every card
# reached the player with no play rate, no mechanical fit, and nothing for the
# deck to learn from. More prompt text did not move it, because the direct path
# stayed available for the job.


from app.chat.engine import _redirect_to_pipeline  # noqa: E402 - section import


def _changes(*specs):
    return [{"action": a, "card_name": n} for a, n in specs]


def test_multi_card_handpicked_batch_is_refused():
    msg = _redirect_to_pipeline("propose_deck_changes", {
        "changes": _changes(("add", "Sol Ring"), ("add", "Arcane Signet"),
                            ("add", "Fellwar Stone")),
    })
    assert msg is not None
    assert "suggest_cards" in msg


def test_single_named_card_is_allowed():
    """"Add Sol Ring" is exactly what the direct path is for."""
    assert _redirect_to_pipeline("propose_deck_changes", {
        "changes": _changes(("add", "Sol Ring")),
    }) is None


def test_cuts_are_allowed():
    """A cut names cards already in the deck; there is nothing to score."""
    assert _redirect_to_pipeline("propose_deck_changes", {
        "changes": _changes(("remove", "Bad Card"), ("remove", "Worse Card"),
                            ("remove", "Filler")),
    }) is None


def test_commander_proposal_is_allowed():
    assert _redirect_to_pipeline("propose_deck_changes", {
        "changes": [{"action": "set_commander", "card_name": "Korvold"}],
    }) is None


def test_commander_plus_one_card_is_allowed():
    """Opening a deck with the commander and a single card is a normal move and
    must not be blocked."""
    assert _redirect_to_pipeline("propose_deck_changes", {
        "changes": [
            {"action": "set_commander", "card_name": "Korvold"},
            {"action": "add", "card_name": "Sol Ring"},
        ],
    }) is None


def test_mixed_batch_counts_only_the_adds():
    """Cuts alongside adds must not push a small add batch over the limit."""
    allowed = _redirect_to_pipeline("propose_deck_changes", {
        "changes": _changes(("remove", "Cut This"), ("remove", "Cut That"),
                            ("add", "Card A"), ("add", "Card B")),
    })
    assert allowed is None

    refused = _redirect_to_pipeline("propose_deck_changes", {
        "changes": _changes(("remove", "Cut This"), ("add", "Card A"),
                            ("add", "Card B"), ("add", "Card C")),
    })
    assert refused is not None


def test_suggest_cards_is_never_redirected():
    """The pipeline path is the destination, not a candidate for redirection."""
    assert _redirect_to_pipeline("suggest_cards", {"intent": "ramp"}) is None


def test_other_tools_pass_through():
    assert _redirect_to_pipeline("deck_get_current", {"deck_id": 1}) is None


def test_malformed_changes_do_not_crash():
    for changes in (None, "not a list", [], [None, 7]):
        assert _redirect_to_pipeline(
            "propose_deck_changes", {"changes": changes}
        ) is None


def test_refusal_names_the_cards_so_the_model_can_reissue():
    msg = _redirect_to_pipeline("propose_deck_changes", {
        "changes": _changes(("add", "Sol Ring"), ("add", "Arcane Signet"),
                            ("add", "Fellwar Stone")),
    })
    assert "Sol Ring" in msg
    assert "Arcane Signet" in msg


def test_two_named_cards_are_allowed():
    """Measured on the live deck: real hand-picked batches were 3, 4, and 6
    adds (ten of thirteen at 6). Nothing at 1 or 2, so a player naming two
    cards stays on the direct path."""
    assert _redirect_to_pipeline("propose_deck_changes", {
        "changes": _changes(("add", "Sol Ring"), ("add", "Arcane Signet")),
    }) is None


# ── The reveal payload must carry the scores ─────────────────────────────
#
# Found in production: proposals had full scores in the database and every card
# still rendered "no scoring data" in the review UI. The deck_proposal SSE
# payload — the ONLY thing that UI reads — was hand-built and omitted the field.
# Three other serialisation sites had been updated; this one was missed, and no
# test covered it because every test asserted against the database.


def test_settled_batch_includes_scores(session):
    """The reveal payload is what the review UI renders, so a score in the row
    that never reaches the wire is invisible."""
    from app.chat.engine import _settled_proposal_batch
    from app.db.models import DeckProposal

    deck = repo.create_deck(session, name="Test Deck")
    convo = repo.create_conversation(session)
    verdict = {
        "total": 0.78, "consensus": 0.85, "mechanical": 0.75,
        "personal": 0.0, "explain": "payoff for sacrifice",
    }
    proposal = DeckProposal(
        conversation_id=convo.id, deck_id=deck.id, action="add",
        card_name="Mayhem Devil", quantity=1, reasoning="pings on sacrifice",
        scores=verdict,
    )
    session.add(proposal)
    session.commit()
    session.refresh(proposal)

    batch = _settled_proposal_batch(session, [proposal.id], "a batch")

    assert batch["proposals"][0]["scores"] == verdict


def test_settled_batch_tolerates_an_unscored_proposal(session):
    """propose_deck_changes for a named card legitimately has no scores; the
    key must still be present so the UI can distinguish "no data" from a
    missing field."""
    from app.chat.engine import _settled_proposal_batch
    from app.db.models import DeckProposal

    deck = repo.create_deck(session, name="Test Deck")
    convo = repo.create_conversation(session)
    proposal = DeckProposal(
        conversation_id=convo.id, deck_id=deck.id, action="add",
        card_name="Sol Ring", quantity=1, reasoning="player asked for it",
    )
    session.add(proposal)
    session.commit()
    session.refresh(proposal)

    batch = _settled_proposal_batch(session, [proposal.id], "a batch")

    assert "scores" in batch["proposals"][0]
    assert batch["proposals"][0]["scores"] is None


def test_every_ui_facing_serialisation_carries_scores():
    """Guards the class of bug rather than the instance: three sites were
    updated and a fourth was missed. Any new proposal serialiser feeding the UI
    must include the field."""
    import inspect

    from app.api import conversations as conversations_api
    from app.chat import engine as engine_module
    from app.tools import deck_tools

    for module, marker in (
        (engine_module, "_settled_proposal_batch"),
        (conversations_api, "get_conversation"),
        (deck_tools, "propose_deck_changes"),
    ):
        source = inspect.getsource(module)
        assert '"reasoning"' in source, marker
        assert '"scores"' in source, f"{marker} serialises proposals without scores"


def test_failed_turn_still_reveals_its_proposals(session):
    """A turn that errors after building proposals must still show them.

    They are real pending rows, so without this the player is told something
    failed while approvable cards sit invisible until a page refresh — the work
    is done and unreachable, which reads as data loss.
    """
    from app.db.models import DeckProposal

    deck = repo.create_deck(session, name="Test Deck")
    convo = repo.create_conversation(session)
    repo.set_conversation_deck(session, convo.id, deck.id)

    class ExplodingProvider(FakeProvider):
        def __init__(self):
            super().__init__([])

        def send(self, *a, **kw):
            # Create a proposal, then fail — the shape of the real bug, where a
            # batch landed and the wrap-up call 400'd.
            proposal = DeckProposal(
                conversation_id=convo.id, deck_id=deck.id, action="add",
                card_name="Sol Ring", quantity=1, reasoning="ramp",
            )
            session.add(proposal)
            session.commit()
            session.refresh(proposal)
            self.created_id = proposal.id
            raise RuntimeError("DeepSeek API call failed")

    provider = ExplodingProvider()
    events = _collect(
        run_chat_turn(session, provider, convo.id, "build me a deck", deck_id=deck.id)
    )

    assert any(e.startswith("event: error") for e in events)
    # The proposal is pending in the database...
    assert repo.get_proposal(session, provider.created_id).status == "pending"


# ── The redirect must not swallow the commander ──────────────────────────
#
# The prompt tells the model to batch set_commander WITH its opening cards.
# Refusing the whole call discarded the commander too, and a deck with no
# commander blocks everything downstream — so the model followed its
# instructions, got refused, and had no way forward.


def test_commander_survives_a_redirected_batch():
    from app.chat.engine import _split_handpicked_adds

    split = _split_handpicked_adds("propose_deck_changes", {"changes": [
        {"action": "set_commander", "card_name": "Myrkul, Lord of Bones"},
        {"action": "add", "card_name": "A"},
        {"action": "add", "card_name": "B"},
        {"action": "add", "card_name": "C"},
    ]})

    assert split is not None
    kept, stripped = split
    assert kept["changes"] == [
        {"action": "set_commander", "card_name": "Myrkul, Lord of Bones"}
    ]
    assert len(stripped) == 3


def test_cuts_survive_a_redirected_batch():
    """A cut names a card already in the deck; there is nothing to score, and
    re-sending it through suggest_cards is not possible."""
    from app.chat.engine import _split_handpicked_adds

    kept, stripped = _split_handpicked_adds("propose_deck_changes", {"changes": [
        {"action": "remove", "card_name": "Bad Card"},
        {"action": "add", "card_name": "A"},
        {"action": "add", "card_name": "B"},
        {"action": "add", "card_name": "C"},
    ]})

    assert kept["changes"] == [{"action": "remove", "card_name": "Bad Card"}]
    assert len(stripped) == 3


def test_pure_add_batch_keeps_nothing():
    from app.chat.engine import _split_handpicked_adds

    kept, stripped = _split_handpicked_adds("propose_deck_changes", {"changes": [
        {"action": "add", "card_name": "A"},
        {"action": "add", "card_name": "B"},
        {"action": "add", "card_name": "C"},
    ]})

    assert kept["changes"] == []
    assert len(stripped) == 3


def test_commander_alone_is_never_split():
    from app.chat.engine import _split_handpicked_adds

    assert _split_handpicked_adds("propose_deck_changes", {"changes": [
        {"action": "set_commander", "card_name": "Myrkul, Lord of Bones"},
    ]}) is None


def test_redirect_tells_the_model_what_was_already_applied():
    """Without this the model re-sends the commander it already got, which the
    proposal path then rejects as a duplicate."""
    msg = _redirect_to_pipeline("propose_deck_changes", {"changes": [
        {"action": "set_commander", "card_name": "Myrkul, Lord of Bones"},
        {"action": "add", "card_name": "A"},
        {"action": "add", "card_name": "B"},
        {"action": "add", "card_name": "C"},
    ]})
    assert msg is not None
    assert "suggest_cards" in msg


# ── The player must be able to name cards ────────────────────────────────
#
# The guard trapped the model: asked for four specific staples by name, it
# could not propose them as a batch and offered to smuggle them through one at
# a time instead. A rule with no legitimate way past it gets worked around,
# which is worse than the behaviour it prevents.


def test_player_named_batch_is_allowed():
    named = [
        {"action": "add", "card_name": n, "player_named": True}
        for n in ["Necropotence", "Rampant Growth", "Nature's Lore", "Three Visits"]
    ]
    assert _redirect_to_pipeline("propose_deck_changes", {"changes": named}) is None


def test_model_picked_batch_is_still_refused():
    """The flag is an escape hatch for the player, not for the model."""
    mine = [{"action": "add", "card_name": n} for n in ["A", "B", "C", "D"]]
    assert _redirect_to_pipeline("propose_deck_changes", {"changes": mine}) is not None


def test_mixed_batch_keeps_named_and_strips_the_rest():
    from app.chat.engine import _split_handpicked_adds

    kept, stripped = _split_handpicked_adds("propose_deck_changes", {"changes": [
        {"action": "add", "card_name": "Necropotence", "player_named": True},
        {"action": "add", "card_name": "Rampant Growth", "player_named": True},
        {"action": "add", "card_name": "Mine A"},
        {"action": "add", "card_name": "Mine B"},
        {"action": "add", "card_name": "Mine C"},
    ]})

    assert [c["card_name"] for c in kept["changes"]] == [
        "Necropotence", "Rampant Growth",
    ]
    assert len(stripped) == 3


def test_two_named_cards_do_not_need_the_flag():
    """Below the limit nothing changes; the flag only matters at 3+."""
    assert _redirect_to_pipeline("propose_deck_changes", {"changes": [
        {"action": "add", "card_name": "A"},
        {"action": "add", "card_name": "B"},
    ]}) is None


def test_player_named_is_in_the_tool_schema():
    """The model can only set what the schema exposes."""
    from app.tools.schemas import TOOL_SPECS

    spec = next(s for s in TOOL_SPECS if s.name == "propose_deck_changes")
    item = spec.parameters["properties"]["changes"]["items"]
    assert "player_named" in item["properties"]


# ── A redirect that applied the commander must let suggest_cards run ─────


@patch("app.tools.deck_tools.get_scryfall_client")
def test_redirect_with_kept_commander_still_offers_suggest_cards(mock_get_client, session):
    """The refusal text tells the model to re-issue the adds via suggest_cards.
    The tool list on the very next send used to be withdraw-only, so the model
    was instructed to do something it could not."""
    mock_get_client.return_value.named.side_effect = lambda name, **k: {
        "name": name, "cmc": 1.0, "color_identity": [],
    }
    deck = repo.create_deck(session, name="Test Deck")
    provider = FakeProvider([
        AssistantTurn(
            text=None,
            tool_calls=[ToolCallRequest(
                id="call_1", name="propose_deck_changes",
                arguments={"summary": "open", "changes": [
                    {"action": "set_commander", "card_name": "Myrkul, Lord of Bones"},
                    {"action": "add", "card_name": "A"},
                    {"action": "add", "card_name": "B"},
                    {"action": "add", "card_name": "C"},
                ]},
            )],
            raw_assistant_message={"role": "assistant", "tool_calls": ["call_1"]},
        ),
        AssistantTurn(
            text=None,
            tool_calls=[ToolCallRequest(
                id="call_2", name="propose_deck_changes",
                arguments={"summary": "one", "changes": [
                    {"action": "add", "card_name": "Sol Ring", "player_named": True},
                ]},
            )],
            raw_assistant_message={"role": "assistant", "tool_calls": ["call_2"]},
        ),
        AssistantTurn(text="Done.", tool_calls=[]),
    ])
    convo = repo.create_conversation(session)
    repo.set_conversation_deck(session, convo.id, deck.id)

    _collect(run_chat_turn(session, provider, convo.id, "build it", deck_id=deck.id))

    names_per_send = [[t.name for t in tools] for tools in provider.tools_per_send]
    assert "suggest_cards" in names_per_send[1], "the follow-up send must allow the pipeline"
    assert names_per_send[2] == ["withdraw_pending_proposals"], "one iteration only"
    assert "call now" in provider.sent_history_snapshots[1][-1]["fake_tool_result"] \
        or "may call now" in provider.sent_history_snapshots[1][-1]["fake_tool_result"]


# ── Bounded context ──────────────────────────────────────────────────────


def test_bound_history_elides_old_large_tool_results_only():
    from app.chat.engine import bound_history

    big = "x" * 5000
    small = "short"
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "tool_calls": ["a"]},
        {"role": "tool", "tool_call_id": "a", "content": big},        # old, large: elided
        {"role": "tool", "tool_call_id": "b", "content": small},      # old, small: kept
        {"role": "tool", "tool_call_id": "c", "content": big},        # recent: kept
        {"role": "tool", "tool_call_id": "d", "content": big},        # recent: kept
        {"role": "assistant", "content": big},                        # never touched
    ]

    bounded = bound_history(history, keep_recent=2)

    assert "elided" in bounded[2]["content"] and len(bounded[2]["content"]) < 500
    assert bounded[3]["content"] == small
    assert bounded[4]["content"] == big and bounded[5]["content"] == big
    assert bounded[6]["content"] == big
    # The original is untouched: this is a send-time view, not the record.
    assert history[2]["content"] == big


@patch("app.tools.deck_tools.get_scryfall_client")
def test_provider_sees_bounded_history_but_db_keeps_everything(mock_get_client, session):
    mock_get_client.return_value.named.return_value = {
        "name": "Sol Ring", "cmc": 1.0, "color_identity": [],
    }
    deck = repo.create_deck(session, name="Test Deck")
    convo = repo.create_conversation(session)
    repo.set_conversation_deck(session, convo.id, deck.id)
    # Six earlier tool results, all large.
    seq = 0
    for i in range(6):
        repo.add_message(session, convo.id, role="assistant", sequence=seq,
                         provider_native=[{"role": "assistant", "tool_calls": [f"c{i}"]}])
        repo.add_message(session, convo.id, role="tool", sequence=seq + 1,
                         provider_native=[{"role": "tool", "tool_call_id": f"c{i}", "content": "y" * 3000}])
        seq += 2

    provider = FakeProvider([AssistantTurn(text="ok", tool_calls=[])])
    _collect(run_chat_turn(session, provider, convo.id, "next", deck_id=deck.id))

    sent = provider.sent_history_snapshots[0]
    tool_msgs = [m for m in sent if m.get("role") == "tool"]
    assert sum("elided" in m["content"] for m in tool_msgs) == 2
    assert sum(m["content"] == "y" * 3000 for m in tool_msgs) == 4
    stored = [m for m in repo.list_messages(session, convo.id) if m.role == "tool"]
    assert all(m.provider_native[0]["content"] == "y" * 3000 for m in stored)


# ── A new batch supersedes the old one ───────────────────────────────────


@patch("app.tools.deck_tools.get_scryfall_client")
def test_new_batch_supersedes_stale_pending_cards_but_not_the_commander(mock_get_client, session):
    from app.db.models import DENIAL_SUPERSEDED

    mock_get_client.return_value.named.side_effect = lambda name, **k: {
        "name": name, "cmc": 1.0, "color_identity": [],
    }
    deck = repo.create_deck(session, name="Test Deck")
    convo = repo.create_conversation(session)
    repo.set_conversation_deck(session, convo.id, deck.id)

    from app.tools.deck_tools import propose_deck_changes
    stale = propose_deck_changes(session, deck.id, "old", [
        {"action": "set_commander", "card_name": "Myrkul, Lord of Bones"},
        {"action": "add", "card_name": "Old Pick"},
    ], conversation_id=convo.id)
    stale_ids = {p["card_name"]: p["id"] for p in stale["proposals"]}

    provider = FakeProvider([
        AssistantTurn(
            text=None,
            tool_calls=[ToolCallRequest(
                id="call_1", name="propose_deck_changes",
                arguments={"summary": "new", "changes": [
                    {"action": "add", "card_name": "New Pick"},
                ]},
            )],
            raw_assistant_message={"role": "assistant", "tool_calls": ["call_1"]},
        ),
        AssistantTurn(text="Here.", tool_calls=[]),
    ])
    _collect(run_chat_turn(session, provider, convo.id, "something else", deck_id=deck.id))

    old_pick = repo.get_proposal(session, stale_ids["Old Pick"])
    assert old_pick.status == "denied"
    assert old_pick.denial_reason == DENIAL_SUPERSEDED
    commander = repo.get_proposal(session, stale_ids["Myrkul, Lord of Bones"])
    assert commander.status == "pending"
    new = [p for p in repo.list_proposals(session, convo.id) if p.card_name == "New Pick"]
    assert new and new[0].status == "pending"


# ── Streaming ────────────────────────────────────────────────────────────


class StreamingFakeProvider(FakeProvider):
    """A provider that streams: yields the turn's text in pieces, then the turn."""

    def send_stream(self, system_prompt, history, tools, *, thinking=False):
        turn = self.send(system_prompt, history, tools, thinking=thinking)
        if turn.text:
            for i in range(0, len(turn.text), 4):
                yield turn.text[i:i + 4]
        yield turn


def test_streaming_provider_text_reaches_the_client_once(session):
    provider = StreamingFakeProvider([AssistantTurn(text="Sure, let's talk.", tool_calls=[])])
    convo = repo.create_conversation(session)

    events = _collect(run_chat_turn(session, provider, convo.id, "hi", deck_id=None))

    tokens = [json.loads(e.split("data: ", 1)[1])["text"] for e in events if e.startswith("event: token")]
    assert "".join(tokens) == "Sure, let's talk."
    assert len(tokens) > 1, "text should arrive in pieces, not one block"
    assert repo.list_messages(session, convo.id)[-1].text_content == "Sure, let's talk."


def test_text_alongside_a_tool_call_streams_with_a_break_before_the_reply(session):
    deck = repo.create_deck(session, name="Test Deck")
    provider = StreamingFakeProvider([
        AssistantTurn(
            text="Checking the deck.",
            tool_calls=[ToolCallRequest(id="c1", name="deck_get_current", arguments={})],
            raw_assistant_message={"role": "assistant", "tool_calls": ["c1"]},
        ),
        AssistantTurn(text="It is empty.", tool_calls=[]),
    ])
    convo = repo.create_conversation(session)
    repo.set_conversation_deck(session, convo.id, deck.id)

    events = _collect(run_chat_turn(session, provider, convo.id, "what's in it?", deck_id=deck.id))

    text = "".join(
        json.loads(e.split("data: ", 1)[1])["text"] for e in events if e.startswith("event: token")
    )
    assert text == "Checking the deck.\n\nIt is empty."
