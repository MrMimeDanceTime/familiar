"""Card grounding: the app puts real card text in front of the model, and
withdraws a reply that describes a card the model never read.
"""

import json

import pytest
from sqlalchemy import text as sql_text
from sqlmodel import Session, SQLModel, create_engine

from app.cards import schema as card_schema
from app.chat import card_facts
from app.chat.engine import run_chat_turn
from app.db import repository as repo
from app.db.session import get_engine
from app.llm.base import AssistantTurn, ToolCallRequest
from tests.test_chat_engine import FakeProvider, _collect

CARDS = [
    {
        "oracle_id": "sol", "name": "Sol Ring", "mana_cost": "{1}", "cmc": 1.0,
        "type_line": "Artifact", "oracle_text": "{T}: Add {C}{C}.",
        "color_identity": "", "legal_commander": 1,
    },
    {
        "oracle_id": "cul", "name": "Cultivate", "mana_cost": "{2}{G}", "cmc": 3.0,
        "type_line": "Sorcery",
        "oracle_text": "Search your library for up to two basic land cards, reveal them, put one onto the battlefield tapped and the other into your hand, then shuffle.",
        "color_identity": "G", "legal_commander": 1,
    },
    {
        "oracle_id": "kro", "name": "Krosan Grip", "mana_cost": "{2}{G}", "cmc": 3.0,
        "type_line": "Instant",
        "oracle_text": "Split second\nDestroy target artifact or enchantment.",
        "color_identity": "G", "legal_commander": 1,
    },
]


@pytest.fixture
def card_index(tmp_path, monkeypatch):
    """A local card index holding a few real cards."""
    from app.config import settings
    from app.db import session as db_session

    monkeypatch.setattr(settings, "familiar_db_path", str(tmp_path / "cards.db"))
    db_session.get_engine.cache_clear()
    card_schema.ensure_schema()
    with get_engine().begin() as conn:
        for card in CARDS:
            conn.execute(sql_text(
                "INSERT INTO cards (oracle_id, name, mana_cost, cmc, type_line, oracle_text,"
                " color_identity, legal_commander, playable, raw) VALUES"
                " (:oracle_id, :name, :mana_cost, :cmc, :type_line, :oracle_text,"
                " :color_identity, :legal_commander, 1, :raw)"
            ), {**card, "raw": json.dumps(card)})
        # cards_fts is contentless-external, kept in sync by explicit writes
        # (see app/cards/schema.py), so a hand-built index needs the same.
        conn.execute(sql_text(
            "INSERT INTO cards_fts (rowid, name, oracle_text, type_line) "
            "SELECT rowid, name, oracle_text, type_line FROM cards"
        ))
    yield
    db_session.get_engine.cache_clear()


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'chat.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


# ── extraction and lookup ──────────────────────────────────────────────────

def test_names_come_from_brackets_only():
    text = "Play [[Sol Ring]] then [[Cultivate]]. Sol Ring is great. [[Sol Ring]] again."
    assert card_facts.names_in_text(text) == ["Sol Ring", "Cultivate"]


def test_basic_lands_are_not_grounded():
    assert card_facts.names_in_text("[[Forest]] and [[Sol Ring]]") == ["Sol Ring"]


def test_no_names_no_crash():
    assert card_facts.names_in_text(None) == []
    assert card_facts.names_in_text("no cards here") == []


def test_resolve_splits_real_cards_from_invented_ones(card_index):
    found, unknown = card_facts.resolve(["Sol Ring", "Blightsteel Colossuss"])
    assert set(found) == {"sol ring"}
    assert unknown == ["Blightsteel Colossuss"]


def test_block_carries_the_real_text_and_flags_inventions(card_index):
    found, unknown = card_facts.resolve(["Krosan Grip", "Mystic Fabrication"])
    block = card_facts.render_block(found, unknown)
    assert "Krosan Grip · {2}{G} · Instant — Split second Destroy target artifact" in block
    assert 'NO SUCH CARD: "Mystic Fabrication"' in block


def test_grounding_is_off_without_an_index(tmp_path, monkeypatch):
    from app.config import settings
    from app.db import session as db_session

    monkeypatch.setattr(settings, "familiar_db_path", str(tmp_path / "empty.db"))
    db_session.get_engine.cache_clear()
    try:
        assert card_facts.is_available() is False
    finally:
        db_session.get_engine.cache_clear()


# ── the turn ───────────────────────────────────────────────────────────────

def _deck(session):
    deck = repo.create_deck(session, name="Test")
    convo = repo.create_conversation(session)
    repo.set_conversation_deck(session, convo.id, deck.id)
    return deck, convo


def test_cards_the_player_names_are_grounded_before_the_first_send(card_index, session):
    deck, convo = _deck(session)
    provider = FakeProvider([AssistantTurn(text="Sure.", tool_calls=[])])

    _collect(run_chat_turn(session, provider, convo.id, "what does [[Cultivate]] do?", deck.id))

    prompt = provider.sent_system_prompts[0]
    assert "<card_facts>" in prompt
    assert "Search your library for up to two basic land cards" in prompt


def test_a_reply_about_an_unread_card_is_withdrawn_and_rewritten(card_index, session):
    deck, convo = _deck(session)
    provider = FakeProvider([
        AssistantTurn(text="[[Krosan Grip]] counters a spell.", tool_calls=[]),
        AssistantTurn(text="[[Krosan Grip]] destroys an artifact or enchantment.", tool_calls=[]),
    ])

    events = _collect(run_chat_turn(session, provider, convo.id, "what should I add?", deck.id))

    # The correction carried the real text and the draft.
    correction = provider.sent_history_snapshots[-1][-1]["content"]
    assert "HOLD" in correction and "Krosan Grip" in correction
    assert "counters a spell" in correction
    assert "Split second" in provider.sent_system_prompts[-1]

    # Only the corrected reply reaches the player and the transcript.
    messages = repo.list_messages(session, convo.id)
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[1].text_content == "[[Krosan Grip]] destroys an artifact or enchantment."
    streamed = "".join(e.data["text"] for e in events if e.event == "token")
    assert "counters a spell" not in streamed


def test_a_card_grounded_by_a_tool_result_is_left_alone(card_index, session):
    deck, convo = _deck(session)
    provider = FakeProvider([
        AssistantTurn(
            text=None,
            tool_calls=[ToolCallRequest(id="c1", name="search_card_index", arguments={"query": "grip"})],
            raw_assistant_message={"role": "assistant"},
        ),
        AssistantTurn(text="[[Krosan Grip]] destroys an artifact.", tool_calls=[]),
    ])

    _collect(run_chat_turn(session, provider, convo.id, "find me removal", deck.id))

    assert len(provider.thinking_flags) == 2  # no correction round
    assert repo.list_messages(session, convo.id)[-1].text_content == "[[Krosan Grip]] destroys an artifact."


def test_an_invented_card_is_named_as_such(card_index, session):
    deck, convo = _deck(session)
    provider = FakeProvider([
        AssistantTurn(text="Add [[Mystic Fabrication]], it draws three.", tool_calls=[]),
        AssistantTurn(text="I misremembered that name; nothing matches it.", tool_calls=[]),
    ])

    _collect(run_chat_turn(session, provider, convo.id, "suggest something", deck.id))

    assert 'NO SUCH CARD: "Mystic Fabrication"' in provider.sent_system_prompts[-1]
    assert repo.list_messages(session, convo.id)[-1].text_content.startswith("I misremembered")


def test_the_draft_is_withdrawn_at_most_once(card_index, session):
    deck, convo = _deck(session)
    provider = FakeProvider([
        AssistantTurn(text="[[Krosan Grip]] counters a spell.", tool_calls=[]),
        AssistantTurn(text="[[Cultivate]] counters a spell too.", tool_calls=[]),
    ])

    _collect(run_chat_turn(session, provider, convo.id, "tell me about removal", deck.id))

    # The second reply is still ungrounded on its own terms, but the turn does
    # not loop: one correction, then the reply stands.
    assert len(provider.thinking_flags) == 2
    assert repo.list_messages(session, convo.id)[-1].text_content == "[[Cultivate]] counters a spell too."


def test_a_streamed_draft_is_reset_on_the_client(card_index, session):
    deck, convo = _deck(session)

    class StreamingProvider(FakeProvider):
        def send_stream(self, system_prompt, history, tools, *, thinking=False):
            self.sent_system_prompts.append(system_prompt)
            self.sent_history_snapshots.append(list(history))
            self.thinking_flags.append(thinking)
            turn = self._turns.pop(0)
            for word in (turn.text or "").split(" "):
                yield word + " "
            yield turn

    provider = StreamingProvider([
        AssistantTurn(text="[[Krosan Grip]] counters a spell.", tool_calls=[]),
        AssistantTurn(text="[[Krosan Grip]] destroys an artifact.", tool_calls=[]),
    ])

    events = _collect(run_chat_turn(session, provider, convo.id, "removal?", deck.id))

    kinds = [e.event for e in events]
    assert "message_reset" in kinds
    reset_at = kinds.index("message_reset")
    before = "".join(e.data["text"] for e in events[:reset_at] if e.event == "token")
    after = "".join(e.data["text"] for e in events[reset_at:] if e.event == "token")
    assert "counters a spell" in before
    assert "destroys an artifact" in after and "counters" not in after


# ── the deck is grounded, so discussing it costs no correction ─────────────

def test_deck_cards_are_grounded_without_a_lookup(card_index, session):
    deck, convo = _deck(session)
    repo.add_deck_card(
        session, deck_id=deck.id, card_name="Krosan Grip", quantity=1,
        type_line="Instant", oracle_text="Split second\nDestroy target artifact or enchantment.",
        oracle_id="kro",
    )
    repo.add_deck_card(session, deck_id=deck.id, card_name="Forest", quantity=30,
                       type_line="Basic Land — Forest", oracle_text="")
    provider = FakeProvider([
        AssistantTurn(text="[[Krosan Grip]] is your uncounterable answer.", tool_calls=[]),
    ])

    _collect(run_chat_turn(session, provider, convo.id, "how do I handle artifacts?", deck.id))

    prompt = provider.sent_system_prompts[0]
    assert "In the deck (1 cards, basics omitted):" in prompt
    assert "Destroy target artifact or enchantment" in prompt
    assert "Forest" not in prompt.split("<card_facts>")[1]
    # One send: the reply was never withdrawn.
    assert len(provider.thinking_flags) == 1
    assert repo.list_messages(session, convo.id)[-1].text_content.startswith("[[Krosan Grip]]")


def test_a_staple_the_app_says_is_missing_is_grounded(card_index, session, monkeypatch):
    deck, convo = _deck(session)
    repo.add_deck_card(session, deck_id=deck.id, card_name="Cultivate", quantity=1,
                       type_line="Sorcery", oracle_text="Search your library...", oracle_id="cul")
    monkeypatch.setattr(
        "app.tools.deck_tools._missing_auto_includes",
        lambda snapshot: [{"name": "Sol Ring"}],
    )
    provider = FakeProvider([AssistantTurn(text="Add [[Sol Ring]] first.", tool_calls=[])])

    _collect(run_chat_turn(session, provider, convo.id, "what am I missing?", deck.id))

    assert "{T}: Add {C}{C}." in provider.sent_system_prompts[0]
    assert len(provider.thinking_flags) == 1


def test_a_card_from_the_last_reply_is_still_grounded_next_turn(card_index, session):
    deck, convo = _deck(session)
    _collect(run_chat_turn(
        session, FakeProvider([AssistantTurn(text="[[Krosan Grip]] handles it.", tool_calls=[])]),
        convo.id, "is [[Krosan Grip]] any good?", deck.id,
    ))
    provider = FakeProvider([AssistantTurn(text="Yes, [[Krosan Grip]] beats a counterspell.", tool_calls=[])])

    _collect(run_chat_turn(session, provider, convo.id, "is that uncounterable?", deck.id))

    assert "Split second" in provider.sent_system_prompts[0]
    assert len(provider.thinking_flags) == 1


def test_the_reset_says_why_the_text_vanished(card_index, session):
    deck, convo = _deck(session)

    class StreamingProvider(FakeProvider):
        def send_stream(self, system_prompt, history, tools, *, thinking=False):
            self.sent_system_prompts.append(system_prompt)
            self.sent_history_snapshots.append(list(history))
            self.thinking_flags.append(thinking)
            turn = self._turns.pop(0)
            yield turn.text or ""
            yield turn

    provider = StreamingProvider([
        AssistantTurn(text="[[Krosan Grip]] counters a spell.", tool_calls=[]),
        AssistantTurn(text="[[Krosan Grip]] destroys an artifact.", tool_calls=[]),
    ])
    events = _collect(run_chat_turn(session, provider, convo.id, "removal?", deck.id))

    reset = next(e for e in events if e.event == "message_reset")
    assert reset.data["note"] == "Rewritten after checking the real card text."


def test_correction_can_be_switched_off(card_index, session, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "chat_correct_ungrounded_replies", False)
    deck, convo = _deck(session)
    provider = FakeProvider([AssistantTurn(text="[[Krosan Grip]] counters a spell.", tool_calls=[])])

    _collect(run_chat_turn(session, provider, convo.id, "removal?", deck.id))

    assert len(provider.thinking_flags) == 1
    assert repo.list_messages(session, convo.id)[-1].text_content == "[[Krosan Grip]] counters a spell."
