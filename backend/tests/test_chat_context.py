"""The per-turn context blocks: deck state, since-last-turn, player history."""

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.chat import context
from app.db import repository as repo
from app.db.models import DENIAL_SUPERSEDED, DeckProposal


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'ctx.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _card(session, deck_id, name, *, type_line="Creature — Elf", mv=2.0, tags=None, ci="G", qty=1):
    return repo.add_deck_card(
        session, deck_id=deck_id, card_name=name, quantity=qty, mana_value=mv,
        color_identity=ci, type_line=type_line, oracle_text="", oracle_id=f"oid-{name}",
        tags=tags if tags is not None else [],
    )


def _proposal(session, deck_id, convo_id, name, status="pending", reason=None, action="add", reported=None):
    p = DeckProposal(
        conversation_id=convo_id, deck_id=deck_id, action=action,
        card_name=name if action != "set_commander" else None,
        commander_name=name if action == "set_commander" else None,
        status=status, denial_reason=reason, reported_status=reported,
    )
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


# ── deck state ─────────────────────────────────────────────────────────────

def test_no_deck_gives_empty_state(session):
    state = context.deck_state(session, None)
    assert state.block == "" and state.plan_is_set is None and state.total_cards is None


def test_empty_deck_header_names_the_gap(session):
    deck = repo.create_deck(session, name="Fresh")
    state = context.deck_state(session, deck.id)
    assert "0 of 100 cards" in state.block
    assert "Commander: not set." in state.block
    assert "Plan: not set" in state.block
    assert state.plan_is_set is False and state.total_cards == 0
    assert "Roles:" not in state.block


def test_pending_commander_is_named_in_the_header(session):
    deck = repo.create_deck(session, name="Fresh")
    convo = repo.create_conversation(session)
    _proposal(session, deck.id, convo.id, "Korvold, Fae-Cursed King", action="set_commander")
    state = context.deck_state(session, deck.id)
    assert "Your proposal of Korvold, Fae-Cursed King is awaiting" in state.block


def test_header_carries_count_plan_roles_scores_and_pending(session):
    deck = repo.create_deck(session, name="Elves")
    repo.update_deck(session, deck.id, commander="Ezuri, Renegade Leader", themes=["elves"], power_level="6")
    _card(session, deck.id, "Ezuri, Renegade Leader", type_line="Legendary Creature — Elf", mv=3.0)
    for i in range(10):
        _card(session, deck.id, f"Forest {i}", type_line="Basic Land — Forest", mv=0, ci="")
    _card(session, deck.id, "Llanowar Elves", tags=["ramp"], mv=1.0)
    convo = repo.create_conversation(session)
    _proposal(session, deck.id, convo.id, "Elvish Mystic")
    _proposal(session, deck.id, convo.id, "Old Pick", status="denied", reason=DENIAL_SUPERSEDED)

    state = context.deck_state(session, deck.id)
    block = state.block
    assert "12 of 100 cards" in block
    assert "Commander: Ezuri, Renegade Leader (identity G)." in block
    assert "Plan: set · themes: elves · power 6" in block
    assert "still needs:" in block
    assert "Roles: land 10 · ramp 1" in block
    assert "Bracket " in block and "power " in block
    assert "Awaiting the player's decision: 1 proposal(s) (Elvish Mystic)" in block
    assert state.plan_is_set is True and state.total_cards == 12


def test_header_lists_missing_staples(session, monkeypatch):
    deck = repo.create_deck(session, name="Elves")
    repo.update_deck(session, deck.id, commander="Ezuri, Renegade Leader")
    _card(session, deck.id, "Ezuri, Renegade Leader", type_line="Legendary Creature — Elf")
    monkeypatch.setattr(
        "app.tools.deck_tools._missing_auto_includes",
        lambda snapshot: [{"name": "Sol Ring"}, {"name": "Arcane Signet"}],
    )
    state = context.deck_state(session, deck.id)
    assert "Missing format staples:" in state.block
    assert "Sol Ring" in state.block


# ── since last turn ────────────────────────────────────────────────────────

def test_since_last_turn_reports_decisions_once(session):
    deck = repo.create_deck(session)
    convo = repo.create_conversation(session)
    _proposal(session, deck.id, convo.id, "Sol Ring", status="approved")
    _proposal(session, deck.id, convo.id, "Cultivate", status="denied", reason="too expensive")
    _proposal(session, deck.id, convo.id, "Farseek", status="denied")
    _proposal(session, deck.id, convo.id, "Withdrawn One", status="denied", reason="withdrawn")
    _proposal(session, deck.id, convo.id, "Still Pending")
    _proposal(session, deck.id, convo.id, "Korvold", status="approved", action="set_commander")

    note = context.since_last_turn(session, deck.id)
    assert "approved Sol Ring, commander Korvold" in note
    assert "denied Cultivate (too expensive), Farseek (no reason given)" in note
    assert "Withdrawn One" not in note
    assert "Still Pending" not in note
    assert context.since_last_turn(session, deck.id) == ""


def test_since_last_turn_reports_an_undo(session):
    deck = repo.create_deck(session)
    convo = repo.create_conversation(session)
    p = _proposal(session, deck.id, convo.id, "Sol Ring", status="approved")
    assert "approved Sol Ring" in context.since_last_turn(session, deck.id)
    p.status = "pending"
    session.add(p)
    session.commit()
    note = context.since_last_turn(session, deck.id)
    assert "undid the approval of Sol Ring" in note
    assert context.since_last_turn(session, deck.id) == ""


def test_since_last_turn_is_scoped_to_the_deck(session):
    deck = repo.create_deck(session)
    other = repo.create_deck(session)
    convo = repo.create_conversation(session)
    _proposal(session, other.id, convo.id, "Elsewhere", status="approved")
    assert context.since_last_turn(session, deck.id) == ""
    assert context.since_last_turn(session, None) == ""


# ── player history ─────────────────────────────────────────────────────────

def test_player_history_needs_enough_decisions(session):
    deck = repo.create_deck(session)
    convo = repo.create_conversation(session)
    for i in range(3):
        _proposal(session, deck.id, convo.id, f"Card {i}", status="approved")
    assert context.player_history(session) == ""


def test_player_history_summarises_reasons_and_repeats(session):
    deck = repo.create_deck(session)
    convo = repo.create_conversation(session)
    for i in range(6):
        _proposal(session, deck.id, convo.id, f"Card {i}", status="approved")
    _proposal(session, deck.id, convo.id, "Cultivate", status="denied", reason="too expensive")
    _proposal(session, deck.id, convo.id, "Cultivate", status="denied", reason="too expensive")
    _proposal(session, deck.id, convo.id, "Farseek", status="denied", reason="off-theme")
    _proposal(session, deck.id, convo.id, "Superseded", status="denied", reason=DENIAL_SUPERSEDED)

    block = context.player_history(session)
    assert "approved 6 of your card proposals and denied 3" in block
    assert '"too expensive" (2)' in block
    assert "passed on more than once: Cultivate" in block
    assert "Superseded" not in block
