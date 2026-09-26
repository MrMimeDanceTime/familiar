"""Standing deck restrictions: stated once, enforced on every later request."""

from unittest.mock import patch

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app import deckplan
from app.db import repository as repo
from app.pipeline.shaping import DeckContext, legal_in_deck
from app.tools.deck_tools import deck_set_plan
from app.tools.proposals import propose_deck_changes


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'restrict.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def deck(session):
    return repo.create_deck(session, name="Kaalia", commander="Kaalia of the Vast")


NO_DEMONS = {"exclude_types": ["Demon", "Dragons"], "exclude_cards": ["Sol Ring"]}


@pytest.mark.parametrize("name,type_line,expected", [
    ("Rune-Scarred Demon", "Creature — Demon", "the deck excludes Demon cards"),
    ("Utvara Hellkite", "Creature — Dragon", "the deck excludes Dragons cards"),
    ("Demonic Tutor", "Sorcery", None),
    ("Avacyn, Angel of Hope", "Legendary Creature — Angel", None),
    ("Sol Ring", "Artifact", "the deck excludes Sol Ring"),
])
def test_restriction_matches_type_words_and_names(name, type_line, expected):
    assert deckplan.restriction_broken(name, type_line, NO_DEMONS) == expected


def test_plural_forms_match_their_type():
    assert deckplan.restriction_broken("Llanowar Elves", "Creature — Elf Druid", {"exclude_types": ["Elves"]})


def test_excluded_cards_are_illegal_in_the_pool():
    ctx = DeckContext(identity=frozenset("WBR"), card_names_lower=frozenset(), restrictions=NO_DEMONS)
    ok, reasons = legal_in_deck(
        {"name": "Rune-Scarred Demon", "type_line": "Creature — Demon", "color_identity": ["B"]}, ctx,
    )
    assert not ok and reasons == ["the deck excludes Demon cards"]


def test_set_plan_stores_restrictions_and_keeps_the_other_list(session, deck):
    deck_set_plan(session, deck.id, exclude_types=["dragon", "Demon", "demon"])
    deck_set_plan(session, deck.id, exclude_cards=["Sol Ring"])
    snapshot = repo.deck_snapshot(session, deck.id)
    assert snapshot["restrictions"] == {"exclude_types": ["Dragon", "Demon"], "exclude_cards": ["Sol Ring"]}
    rendered = deckplan.render_plan(deckplan.build_plan(snapshot))
    assert "Excluded types: no Dragon, Demon cards" in rendered


def test_an_empty_list_clears_restrictions(session, deck):
    deck_set_plan(session, deck.id, exclude_types=["Demon"])
    deck_set_plan(session, deck.id, exclude_types=[])
    assert repo.deck_snapshot(session, deck.id)["restrictions"]["exclude_types"] == []


@patch("app.tools.deck_tools.get_scryfall_client")
def test_proposals_refuse_excluded_cards_unless_the_player_named_them(mock_client, session, deck):
    mock_client.return_value.named.return_value = {
        "name": "Rune-Scarred Demon", "type_line": "Creature — Demon", "cmc": 7.0, "color_identity": ["B"],
    }
    deck_set_plan(session, deck.id, exclude_types=["Demon"])
    convo = repo.create_conversation(session)
    change = {"action": "add", "card_name": "Rune-Scarred Demon"}
    with pytest.raises(ValueError, match="restrictions"):
        propose_deck_changes(session, deck.id, "x", [change], conversation_id=convo.id)
    result = propose_deck_changes(
        session, deck.id, "x", [{**change, "player_named": True}], conversation_id=convo.id,
    )
    assert result["proposals"][0]["card_name"] == "Rune-Scarred Demon"
