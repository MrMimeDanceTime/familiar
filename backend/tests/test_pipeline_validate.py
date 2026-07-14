from unittest.mock import patch

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.db import repository as repo
from app.pipeline.selection import Pick, Selection
from app.pipeline.validate import selection_to_changes, validate_to_proposals


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


# ── selection_to_changes (pure) ────────────────────────────────────────────

def test_picks_become_add_actions_with_reasoning():
    sel = Selection(picks=[Pick("Sol Ring", "best ramp")], cuts=[])
    changes = selection_to_changes(sel)
    assert changes == [
        {"action": "add", "card_name": "Sol Ring", "quantity": 1, "reasoning": "best ramp"},
    ]


def test_cuts_become_remove_actions():
    sel = Selection(picks=[], cuts=[Pick("Filler", "weak")])
    changes = selection_to_changes(sel)
    assert changes == [
        {"action": "remove", "card_name": "Filler", "reasoning": "weak"},
    ]


# ── validate_to_proposals (delegates to propose_deck_changes) ──────────────

def test_empty_selection_returns_empty_batch_without_scryfall(session):
    deck = repo.create_deck(session)
    convo = repo.create_conversation(session)
    # No patching: if it tried to hit Scryfall this would fail, proving the
    # short-circuit avoids the call for an empty selection.
    result = validate_to_proposals(
        session, deck.id, Selection(picks=[], cuts=[], summary="nothing fit"),
        conversation_id=convo.id,
    )
    assert result == {"ok": True, "summary": "nothing fit", "proposals": []}


@patch("app.tools.deck_tools.get_scryfall_client")
def test_picks_flow_through_to_pending_proposals(mock_get_client, session):
    mock_get_client.return_value.named.return_value = {"name": "Sol Ring"}
    deck = repo.create_deck(session, format="commander")
    convo = repo.create_conversation(session)

    sel = Selection(picks=[Pick("sol ring", "ramp")], cuts=[], summary="added ramp")
    result = validate_to_proposals(
        session, deck.id, sel, conversation_id=convo.id,
    )

    assert result["ok"] is True
    assert result["summary"] == "added ramp"
    assert len(result["proposals"]) == 1
    prop = result["proposals"][0]
    assert prop["action"] == "add"
    assert prop["card_name"] == "Sol Ring"  # canonicalized by propose_deck_changes
    assert prop["reasoning"] == "ramp"
    assert prop["status"] == "pending"


@patch("app.tools.deck_tools.get_scryfall_client")
def test_banned_pick_is_rejected_by_the_shared_gate(mock_get_client, session):
    # propose_deck_changes owns the banned-list check; validate_to_proposals
    # inherits it by delegating rather than reimplementing legality.
    mock_get_client.return_value.named.return_value = {"name": "Black Lotus"}
    deck = repo.create_deck(session, format="commander")
    convo = repo.create_conversation(session)

    sel = Selection(picks=[Pick("Black Lotus", "fast mana")], cuts=[])
    with pytest.raises(ValueError, match="banned"):
        validate_to_proposals(session, deck.id, sel, conversation_id=convo.id)


def test_summary_override_wins_over_selection_summary(session):
    deck = repo.create_deck(session)
    convo = repo.create_conversation(session)
    sel = Selection(picks=[], cuts=[], summary="original")
    result = validate_to_proposals(
        session, deck.id, sel, conversation_id=convo.id, summary="overridden",
    )
    assert result["summary"] == "overridden"
