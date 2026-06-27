import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.db import repository as repo


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
