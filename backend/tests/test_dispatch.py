from unittest.mock import patch

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.db import repository as repo
from app.tools.dispatch import DECK_MUTATION_TOOLS, SESSION_TOOLS, STATELESS_TOOLS, dispatch
from app.tools.schemas import TOOL_SPECS
from app.tools.scryfall_client import ScryfallNotFoundError


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_unknown_tool_returns_error_result(session):
    result = dispatch("not_a_real_tool", {}, session)
    assert result.ok is False
    assert "Unknown tool" in result.content


@patch("app.tools.dispatch.get_scryfall_client")
def test_scryfall_search_dispatch(mock_get_client, session):
    mock_get_client.return_value.search.return_value = [{"name": "Sol Ring"}]
    result = dispatch("scryfall_search", {"query": "sol ring", "limit": 5}, session)
    assert result.ok is True
    assert result.content == [{"name": "Sol Ring"}]
    mock_get_client.return_value.search.assert_called_once_with("sol ring", limit=5)


@patch("app.tools.dispatch.get_scryfall_client")
def test_scryfall_card_by_name_not_found_becomes_error_result(mock_get_client, session):
    mock_get_client.return_value.named.side_effect = ScryfallNotFoundError("No card found")
    result = dispatch("scryfall_card_by_name", {"name": "Not A Real Card"}, session)
    assert result.ok is False
    assert "No card found" in result.content


@patch("app.tools.dispatch.get_edhrec_client")
def test_edhrec_commander_recs_dispatch(mock_get_client, session):
    mock_get_client.return_value.commander_recs.return_value = {"commander": "Atraxa", "categories": {}}
    result = dispatch("edhrec_commander_recs", {"commander_name": "Atraxa, Grand Unifier"}, session)
    assert result.ok is True
    assert result.content["commander"] == "Atraxa"


def test_deck_get_current_dispatch(session):
    deck = repo.create_deck(session, name="Test Deck")
    result = dispatch("deck_get_current", {"deck_id": deck.id}, session)
    assert result.ok is True
    assert result.content["name"] == "Test Deck"


@patch("app.tools.deck_tools.get_scryfall_client")
def test_deck_add_card_dispatch_validates_via_scryfall(mock_get_client, session):
    mock_get_client.return_value.named.return_value = {
        "name": "Sol Ring",
        "cmc": 1.0,
        "color_identity": [],
    }
    deck = repo.create_deck(session, name="Test Deck")
    result = dispatch(
        "deck_add_card",
        {"deck_id": deck.id, "card_name": "sol ring", "category": "ramp"},
        session,
    )
    assert result.ok is True
    assert result.content["cards"][0]["name"] == "Sol Ring"
    assert result.content["cards"][0]["mana_value"] == 1.0


@patch("app.tools.deck_tools.get_scryfall_client")
def test_deck_add_card_unknown_card_returns_error_result(mock_get_client, session):
    mock_get_client.return_value.named.side_effect = ScryfallNotFoundError("not found")
    deck = repo.create_deck(session, name="Test Deck")
    result = dispatch(
        "deck_add_card", {"deck_id": deck.id, "card_name": "Definitely Not A Card"}, session
    )
    assert result.ok is False
    assert "No Scryfall card found" in result.content


def test_deck_remove_card_dispatch(session):
    deck = repo.create_deck(session)
    repo.add_deck_card(session, deck.id, "Sol Ring", quantity=1)
    result = dispatch("deck_remove_card", {"deck_id": deck.id, "card_name": "Sol Ring"}, session)
    assert result.ok is True
    assert result.content["cards"] == []


def test_deck_update_notes_dispatch(session):
    deck = repo.create_deck(session)
    result = dispatch("deck_update_notes", {"deck_id": deck.id, "notes": "Go wide, sac theme"}, session)
    assert result.ok is True
    assert result.content["notes"] == "Go wide, sac theme"


def test_simulated_network_failure_does_not_crash_dispatch(session):
    with patch("app.tools.dispatch.get_scryfall_client") as mock_get_client:
        mock_get_client.return_value.search.side_effect = ConnectionError("network is down")
        result = dispatch("scryfall_search", {"query": "anything"}, session)
        assert result.ok is False
        assert "failed" in result.content.lower()


def test_deck_mutation_tools_set_is_accurate():
    assert DECK_MUTATION_TOOLS == {
        "deck_add_card",
        "deck_remove_card",
        "deck_set_commander",
        "deck_update_notes",
    }


def test_tool_schemas_and_dispatch_registry_in_sync():
    spec_names = {t.name for t in TOOL_SPECS}
    dispatch_names = set(STATELESS_TOOLS) | set(SESSION_TOOLS)
    assert spec_names == dispatch_names
