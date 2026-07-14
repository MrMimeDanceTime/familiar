import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.db import repository as repo
from app.db.models import DeckProposal


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_create_and_get_conversation(session):
    convo = repo.create_conversation(session, title="Atraxa brainstorm")
    fetched = repo.get_conversation(session, convo.id)
    assert fetched is not None
    assert fetched.title == "Atraxa brainstorm"


def test_list_conversations_ordered_by_updated_desc(session):
    c1 = repo.create_conversation(session, title="First")
    c2 = repo.create_conversation(session, title="Second")
    repo.touch_conversation(session, c1.id)  # bump c1 to most-recently-updated

    results = repo.list_conversations(session)
    assert results[0].id == c1.id
    assert results[1].id == c2.id


def test_add_and_list_messages_in_sequence_order(session):
    convo = repo.create_conversation(session)
    repo.add_message(session, convo.id, role="user", sequence=0, text_content="hi")
    repo.add_message(session, convo.id, role="assistant", sequence=1, text_content="hello")

    messages = repo.list_messages(session, convo.id)
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[0].text_content == "hi"


def test_next_sequence_increments(session):
    convo = repo.create_conversation(session)
    assert repo.next_sequence(session, convo.id) == 0
    repo.add_message(session, convo.id, role="user", sequence=0, text_content="hi")
    assert repo.next_sequence(session, convo.id) == 1


def test_message_persists_provider_native_json_roundtrip(session):
    convo = repo.create_conversation(session)
    native = [{"role": "assistant", "content": [{"type": "tool_use", "id": "abc", "name": "x"}]}]
    msg = repo.add_message(
        session, convo.id, role="assistant", sequence=0, provider_native=native
    )
    fetched = repo.list_messages(session, convo.id)[0]
    assert fetched.provider_native == native
    assert fetched.id == msg.id


def test_create_deck_and_set_on_conversation(session):
    convo = repo.create_conversation(session)
    deck = repo.create_deck(session, name="Atraxa Superfriends", commander="Atraxa, Grand Unifier")
    updated_convo = repo.set_conversation_deck(session, convo.id, deck.id)
    assert updated_convo.deck_id == deck.id


def test_add_deck_card_and_snapshot(session):
    deck = repo.create_deck(session, name="Test Deck", commander="Korvold, Fae-Cursed King")
    repo.add_deck_card(
        session, deck.id, "Sol Ring", quantity=1, category="ramp", mana_value=1, color_identity=""
    )
    repo.add_deck_card(
        session, deck.id, "Sakura-Tribe Elder", quantity=1, category="ramp", mana_value=1, color_identity="G"
    )

    snapshot = repo.deck_snapshot(session, deck.id)
    assert snapshot["commander"] == "Korvold, Fae-Cursed King"
    assert len(snapshot["cards"]) == 2
    names = {c["name"] for c in snapshot["cards"]}
    assert names == {"Sol Ring", "Sakura-Tribe Elder"}


def test_add_deck_card_upserts_on_duplicate_name(session):
    deck = repo.create_deck(session)
    repo.add_deck_card(session, deck.id, "Sol Ring", quantity=1, category="ramp")
    repo.add_deck_card(session, deck.id, "Sol Ring", quantity=1, category="ramp", notes="updated")

    cards = repo.list_deck_cards(session, deck.id)
    assert len(cards) == 1
    assert cards[0].notes == "updated"


def test_remove_deck_card(session):
    deck = repo.create_deck(session)
    repo.add_deck_card(session, deck.id, "Sol Ring", quantity=1)
    removed = repo.remove_deck_card(session, deck.id, "Sol Ring")
    assert removed is True
    assert repo.list_deck_cards(session, deck.id) == []


def test_remove_nonexistent_deck_card_returns_false(session):
    deck = repo.create_deck(session)
    assert repo.remove_deck_card(session, deck.id, "Nonexistent Card") is False


def test_remove_deck_card_explicit_partial_quantity_shrinks_stack(session):
    deck = repo.create_deck(session)
    repo.add_deck_card(session, deck.id, "Swamp", quantity=34)
    removed = repo.remove_deck_card(session, deck.id, "Swamp", quantity=2)

    assert removed is True
    card = repo.get_deck_card(session, deck.id, "Swamp")
    assert card is not None
    assert card.quantity == 32


def test_remove_deck_card_quantity_covering_whole_stack_deletes_row(session):
    deck = repo.create_deck(session)
    repo.add_deck_card(session, deck.id, "Swamp", quantity=2)
    removed = repo.remove_deck_card(session, deck.id, "Swamp", quantity=2)

    assert removed is True
    assert repo.get_deck_card(session, deck.id, "Swamp") is None


def test_remove_deck_card_omitted_quantity_removes_whole_stack(session):
    deck = repo.create_deck(session)
    repo.add_deck_card(session, deck.id, "Swamp", quantity=34)
    removed = repo.remove_deck_card(session, deck.id, "Swamp")

    assert removed is True
    assert repo.get_deck_card(session, deck.id, "Swamp") is None


def test_update_deck_fields(session):
    deck = repo.create_deck(session, name="Old Name")
    updated = repo.update_deck(session, deck.id, name="New Name", power_level="casual")
    assert updated.name == "New Name"
    assert updated.power_level == "casual"


def test_delete_deck_cascades_cards(session):
    deck = repo.create_deck(session)
    repo.add_deck_card(session, deck.id, "Sol Ring", quantity=1)
    repo.delete_deck(session, deck.id)
    assert repo.get_deck(session, deck.id) is None
    assert repo.list_deck_cards(session, deck.id) == []


def test_deck_snapshot_raises_for_missing_deck(session):
    with pytest.raises(ValueError):
        repo.deck_snapshot(session, 9999)


def test_add_deck_card_explicit_quantity_adds_to_existing_stack(session):
    deck = repo.create_deck(session)
    repo.add_deck_card(session, deck.id, "Swamp", quantity=34)
    repo.add_deck_card(session, deck.id, "Swamp", quantity=6)

    card = repo.get_deck_card(session, deck.id, "Swamp")
    assert card.quantity == 40


def test_add_deck_card_omitted_quantity_does_not_clobber_existing_stack(session):
    deck = repo.create_deck(session)
    repo.add_deck_card(session, deck.id, "Swamp", quantity=34)
    repo.add_deck_card(session, deck.id, "Swamp", notes="metadata refresh only")

    card = repo.get_deck_card(session, deck.id, "Swamp")
    assert card.quantity == 34
    assert card.notes == "metadata refresh only"


def test_add_deck_card_new_card_defaults_to_quantity_one(session):
    deck = repo.create_deck(session)
    repo.add_deck_card(session, deck.id, "Sol Ring")

    card = repo.get_deck_card(session, deck.id, "Sol Ring")
    assert card.quantity == 1


def _make_proposal(session, deck_id, conversation_id, action="add", card_name="Sol Ring", **kwargs):
    proposal = DeckProposal(
        conversation_id=conversation_id,
        deck_id=deck_id,
        action=action,
        card_name=card_name,
        reasoning="test",
        **kwargs,
    )
    session.add(proposal)
    session.commit()
    session.refresh(proposal)
    return proposal


def test_apply_proposal_add_inserts_card_and_marks_approved(session, monkeypatch):
    deck = repo.create_deck(session)
    convo = repo.create_conversation(session)
    proposal = _make_proposal(
        session, deck.id, convo.id, action="add", card_name="Sol Ring", category="ramp", quantity=1
    )

    monkeypatch.setattr(
        "app.tools.deck_tools.get_scryfall_client",
        lambda: type("S", (), {"named": staticmethod(lambda name, fuzzy=True: {
            "name": "Sol Ring", "cmc": 1.0, "color_identity": [],
        })})(),
    )

    snapshot = repo.apply_proposal(session, proposal.id)
    assert snapshot is not None
    assert any(c["name"] == "Sol Ring" for c in snapshot["cards"])

    refreshed = repo.get_proposal(session, proposal.id)
    assert refreshed.status == "approved"


def test_apply_proposal_add_more_copies_adds_to_existing_stack(session, monkeypatch):
    deck = repo.create_deck(session)
    convo = repo.create_conversation(session)
    repo.add_deck_card(session, deck.id, "Swamp", quantity=8, category="land")
    proposal = _make_proposal(
        session, deck.id, convo.id, action="add", card_name="Swamp", category="land", quantity=6
    )

    monkeypatch.setattr(
        "app.tools.deck_tools.get_scryfall_client",
        lambda: type("S", (), {"named": staticmethod(lambda name, fuzzy=True: {
            "name": "Swamp", "cmc": 0.0, "color_identity": [],
        })})(),
    )

    snapshot = repo.apply_proposal(session, proposal.id)
    assert snapshot is not None
    swamp = next(c for c in snapshot["cards"] if c["name"] == "Swamp")
    assert swamp["quantity"] == 14


def test_apply_proposal_remove_deletes_card_and_marks_approved(session):
    deck = repo.create_deck(session)
    convo = repo.create_conversation(session)
    repo.add_deck_card(session, deck.id, "Sol Ring", quantity=1)
    proposal = _make_proposal(session, deck.id, convo.id, action="remove", card_name="Sol Ring", quantity=None)

    snapshot = repo.apply_proposal(session, proposal.id)
    assert snapshot is not None
    assert snapshot["cards"] == []
    assert repo.get_proposal(session, proposal.id).status == "approved"


def test_apply_proposal_remove_with_explicit_quantity_shrinks_stack_not_deletes(session):
    deck = repo.create_deck(session)
    convo = repo.create_conversation(session)
    repo.add_deck_card(session, deck.id, "Swamp", quantity=34)
    proposal = _make_proposal(
        session, deck.id, convo.id, action="remove", card_name="Swamp", quantity=2
    )

    snapshot = repo.apply_proposal(session, proposal.id)
    assert snapshot is not None
    swamp = next(c for c in snapshot["cards"] if c["name"] == "Swamp")
    assert swamp["quantity"] == 32
    assert repo.get_proposal(session, proposal.id).status == "approved"


def test_apply_proposal_remove_without_quantity_removes_whole_stack(session):
    deck = repo.create_deck(session)
    convo = repo.create_conversation(session)
    repo.add_deck_card(session, deck.id, "Swamp", quantity=34)
    proposal = _make_proposal(
        session, deck.id, convo.id, action="remove", card_name="Swamp", quantity=None
    )

    snapshot = repo.apply_proposal(session, proposal.id)
    assert snapshot is not None
    assert snapshot["cards"] == []


def test_apply_proposal_already_resolved_returns_none(session):
    deck = repo.create_deck(session)
    convo = repo.create_conversation(session)
    proposal = _make_proposal(session, deck.id, convo.id, action="remove", card_name="Sol Ring", status="denied")

    assert repo.apply_proposal(session, proposal.id) is None


def test_deny_proposal_marks_denied(session):
    deck = repo.create_deck(session)
    convo = repo.create_conversation(session)
    proposal = _make_proposal(session, deck.id, convo.id, action="remove", card_name="Sol Ring")

    assert repo.deny_proposal(session, proposal.id) is True
    assert repo.get_proposal(session, proposal.id).status == "denied"


def test_deny_proposal_already_resolved_returns_false(session):
    deck = repo.create_deck(session)
    convo = repo.create_conversation(session)
    proposal = _make_proposal(session, deck.id, convo.id, action="remove", card_name="Sol Ring", status="approved")

    assert repo.deny_proposal(session, proposal.id) is False


def test_list_proposals_ordered_by_created_at(session):
    deck = repo.create_deck(session)
    convo = repo.create_conversation(session)
    p1 = _make_proposal(session, deck.id, convo.id, card_name="Sol Ring")
    p2 = _make_proposal(session, deck.id, convo.id, card_name="Arcane Signet")

    proposals = repo.list_proposals(session, convo.id)
    assert [p.id for p in proposals] == [p1.id, p2.id]


def test_delete_conversation_cascades_proposals(session):
    deck = repo.create_deck(session)
    convo = repo.create_conversation(session)
    proposal = _make_proposal(session, deck.id, convo.id, card_name="Sol Ring")

    repo.delete_conversation(session, convo.id)

    assert repo.get_conversation(session, convo.id) is None
    assert repo.get_proposal(session, proposal.id) is None


def test_delete_deck_cascades_proposals(session):
    deck = repo.create_deck(session)
    convo = repo.create_conversation(session)
    proposal = _make_proposal(session, deck.id, convo.id, card_name="Sol Ring")

    repo.delete_deck(session, deck.id)

    assert repo.get_deck(session, deck.id) is None
    assert repo.get_proposal(session, proposal.id) is None


def test_additive_columns_patch_existing_db(tmp_path):
    """The idempotent ALTER-TABLE patch adds the power-nuance columns to a DB
    created before they existed, without touching data — the no-Alembic path."""
    from sqlalchemy import text

    from app.db.session import _apply_additive_columns

    db = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db}")
    # Simulate an old schema: a deck table lacking the nuance columns.
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE deck (id INTEGER PRIMARY KEY, name TEXT, commander TEXT, "
            "format TEXT)"
        ))
        conn.execute(text("INSERT INTO deck (id, name, format) VALUES (1, 'Old', 'commander')"))

    _apply_additive_columns(engine)
    # idempotent: a second run must not error
    _apply_additive_columns(engine)

    with engine.begin() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(deck)"))}
        assert {"power_nuance_adj", "power_nuance_reason", "power_nuance_key"} <= cols
        # existing row survived
        name = conn.execute(text("SELECT name FROM deck WHERE id=1")).scalar()
        assert name == "Old"
