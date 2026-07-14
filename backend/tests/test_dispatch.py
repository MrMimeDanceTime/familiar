from unittest.mock import patch

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.db import repository as repo
from app.tools import deck_tools
from app.tools.dispatch import (
    DECK_MUTATION_TOOLS,
    PROVIDER_SESSION_TOOLS,
    SESSION_TOOLS,
    STATELESS_TOOLS,
    dispatch,
)
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


def test_search_knowledge_executes_and_filters(tmp_path):
    """Exercises the real FTS query (no mock) — regression guard for the
    Session.exec params-must-be-keyword bug that made the RAG throw at
    runtime. Builds a self-contained DB (patching get_engine in every module
    that imported it) so the store and this test share one engine, then checks
    the query runs and the category filter isolates topics."""
    from sqlmodel import SQLModel, create_engine
    from app.knowledge import models as kb_models, store as kb_store
    from app.knowledge.models import KnowledgeEntry, _ensure_fts

    engine = create_engine(f"sqlite:///{tmp_path / 'kb.db'}")
    with patch.object(kb_models, "get_engine", lambda: engine), \
         patch.object(kb_store, "get_engine", lambda: engine):
        SQLModel.metadata.create_all(engine)
        _ensure_fts()
        with Session(engine) as s:
            s.add(KnowledgeEntry(title="Ramp sizing",
                                 body="Run 10-12 ramp pieces in commander.",
                                 category="ramp", format="commander"))
            s.add(KnowledgeEntry(title="Removal sizing",
                                 body="Run 10-15 removal answers in commander.",
                                 category="removal", format="commander"))
            s.commit()

        hits = kb_store.search_knowledge("ramp", top_k=5)  # must not raise
        assert any("ramp" in h["body"].lower() for h in hits)
        ramp_only = kb_store.search_knowledge("ramp removal", top_k=5, category="ramp")
        assert ramp_only and all(h["category"] == "ramp" for h in ramp_only)


def test_detect_roles_uses_tags_not_oracle_text():
    from app.tools.deck_tools import _detect_roles

    # A ramp mana-rock tag makes it ramp regardless of (empty) oracle text.
    assert _detect_roles("Artifact", "", None, None, ["ramp", "mana-rock"]) == {"ramp"}
    # Counterspells/sweepers map to removal via tags.
    assert _detect_roles("Instant", "", None, None, ["counterspell"]) == {"removal"}


def test_detect_roles_land_ramp_guards():
    from app.tools.deck_tools import _detect_roles

    # A land with a generic non-ramp tag is only a land, never ramp.
    assert _detect_roles("Land", "", None, None, ["graveyard-hate"]) == {"land"}
    # A land explicitly tagged as land-ramp counts as both.
    assert _detect_roles("Land", "", None, None, ["land-ramp"]) == {"ramp", "land"}


def test_detect_roles_untagged_card_is_not_guessed_from_text():
    from app.tools.deck_tools import _detect_roles

    # No tags, no oracle_id: a nonland card gets NO role (no regex guessing).
    assert _detect_roles("Creature", "draw a card. add {G}.", None, None, []) == set()
    # A land with no tags still gets its land role from the type line.
    assert _detect_roles("Basic Land — Forest", "", None, None, []) == {"land"}


@patch("app.knowledge.tag_lookup.get_tag_lookup", lambda: {})
@patch("app.tools.deck_tools.get_scryfall_client")
def test_compute_deck_stats_surfaces_untagged_cards(mock_scry, session):
    from app.tools.deck_tools import compute_deck_stats

    # Untagged cards trigger a backfill lookup; stub it to find nothing so
    # they stay untagged and the test makes no network call.
    mock_scry.return_value.collection.return_value = {"found": []}

    deck = repo.create_deck(session)
    repo.add_deck_card(session, deck.id, "Sol Ring", type_line="Artifact",
                       mana_value=1, oracle_id="oid-sol", tags=["ramp", "mana-rock"])
    # Nonland card with no tags -> should be reported as untagged.
    repo.add_deck_card(session, deck.id, "Brand New Card", type_line="Creature",
                       mana_value=3, oracle_id="oid-new", tags=[])
    # Basic land with no tags -> counted as land, NOT reported as untagged.
    repo.add_deck_card(session, deck.id, "Forest", type_line="Basic Land — Forest",
                       oracle_id="oid-forest", tags=[])

    stats = compute_deck_stats(session, deck.id)
    assert stats["ramp_count"] == 1
    assert stats["untagged"] == ["Brand New Card"]


@patch("app.knowledge.tag_lookup.get_tag_lookup", lambda: {})
@patch("app.tools.deck_tools.get_scryfall_client")
def test_compute_deck_stats_reports_deficiencies(mock_scry, session):
    from app.tools.deck_tools import compute_deck_stats

    mock_scry.return_value.collection.return_value = {"found": []}
    deck = repo.create_deck(session, format="commander")
    # A tiny commander deck: everything will be LOW vs targets.
    repo.add_deck_card(session, deck.id, "Forest", quantity=10,
                       type_line="Basic Land — Forest", oracle_id="oid-forest", tags=[])
    repo.add_deck_card(session, deck.id, "Sol Ring", type_line="Artifact",
                       mana_value=1, oracle_id="oid-sol", tags=["ramp"])

    stats = compute_deck_stats(session, deck.id)
    by_cat = {d["category"]: d for d in stats["deficiencies"]}
    assert by_cat["lands"]["status"] == "LOW"   # 10 < 33
    assert by_cat["ramp"]["status"] == "LOW"    # 1 < 10
    assert by_cat["draw"]["status"] == "LOW"    # 0 < 10


@patch("app.knowledge.tag_lookup.get_tag_lookup", lambda: {})
def test_deck_snapshot_category_derived_from_tags_not_stored(session):
    # Display category is recomputed from tags on read, so a stale/wrong
    # stored category is ignored — the drift that manual tagging caused.
    deck = repo.create_deck(session)
    repo.add_deck_card(session, deck.id, "Cultivate", type_line="Sorcery",
                       oracle_id="oid-cult", tags=["ramp"], category="removal")
    snap = repo.deck_snapshot(session, deck.id)
    assert snap["cards"][0]["category"] == "ramp"


@patch("app.knowledge.tag_lookup.get_tag_lookup", lambda: {})
def test_deck_snapshot_commander_category_overrides_tags(session):
    # A designated commander shows as "Commander" even without functional tags.
    deck = repo.create_deck(session, commander="Krark, the Thumbless")
    repo.add_deck_card(session, deck.id, "Krark, the Thumbless",
                       type_line="Legendary Creature — Goblin", oracle_id="oid-k", tags=[])
    snap = repo.deck_snapshot(session, deck.id)
    assert snap["cards"][0]["category"] == "Commander"


@patch("app.knowledge.tag_lookup.get_tag_lookup", lambda: {})
@patch("app.tools.deck_tools.get_scryfall_client")
def test_compute_deck_stats_no_deficiencies_for_noncommander(mock_scry, session):
    from app.tools.deck_tools import compute_deck_stats

    mock_scry.return_value.collection.return_value = {"found": []}
    deck = repo.create_deck(session, format="modern")
    repo.add_deck_card(session, deck.id, "Island", quantity=20,
                       type_line="Basic Land — Island", oracle_id="oid-isl", tags=[])
    stats = compute_deck_stats(session, deck.id)
    assert stats["deficiencies"] == []


def _cat(session, deck_id, name):
    return repo.get_deck_card(session, deck_id, name).category


def test_set_deck_commanders_partner_and_demote(session):
    deck = repo.create_deck(session)
    repo.add_deck_card(session, deck.id, "Sakashima of a Thousand Faces",
                       type_line="Legendary Creature — Human", category="synergy-piece")
    repo.add_deck_card(session, deck.id, "Krark, the Thumbless",
                       type_line="Legendary Creature — Goblin", category="synergy-piece")
    repo.add_deck_card(session, deck.id, "Sol Ring", type_line="Artifact", category="ramp")

    snap = deck_tools.set_deck_commanders(
        session, deck.id,
        commander_name="Sakashima of a Thousand Faces",
        partner_commander_name="Krark, the Thumbless",
    )
    assert snap["commander"] == "Sakashima of a Thousand Faces"
    assert snap["partner_commander"] == "Krark, the Thumbless"
    assert _cat(session, deck.id, "Sakashima of a Thousand Faces") == "Commander"
    assert _cat(session, deck.id, "Krark, the Thumbless") == "Commander"

    # Reassign to a single commander: the dropped partner is demoted out of
    # the Commander category, not left dangling.
    deck_tools.set_deck_commanders(
        session, deck.id, commander_name="Sakashima of a Thousand Faces",
    )
    assert repo.get_deck(session, deck.id).partner_commander is None
    assert _cat(session, deck.id, "Krark, the Thumbless") != "Commander"


def test_set_deck_commanders_clear(session):
    deck = repo.create_deck(session, commander="Old Cmdr")
    repo.add_deck_card(session, deck.id, "Old Cmdr",
                       type_line="Legendary Creature", category="Commander")

    deck_tools.set_deck_commanders(session, deck.id, commander_name=None)
    assert repo.get_deck(session, deck.id).commander is None
    assert _cat(session, deck.id, "Old Cmdr") != "Commander"


def _pending(session, conversation_id, deck_id, action, name):
    from app.db.models import DeckProposal

    p = DeckProposal(
        conversation_id=conversation_id,
        deck_id=deck_id, status="pending", action=action,
        card_name=name,
        commander_name=name if action == "set_commander" else None,
    )
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def _status(session, proposal_id):
    from app.db.models import DeckProposal

    return session.get(DeckProposal, proposal_id).status


def test_withdraw_preserves_pending_commander(session):
    # Regression: withdrawing a stale card batch before proposing the next one
    # must not silently cancel a still-unapproved set_commander proposal.
    deck = repo.create_deck(session)
    convo = repo.create_conversation(session)
    cmd = _pending(session, convo.id, deck.id, "set_commander", "Mass of Mysteries")
    card = _pending(session, convo.id, deck.id, "add", "Risen Reef")

    result = deck_tools.withdraw_pending_proposals(session, deck.id)

    assert result["withdrawn"] == 1
    assert result["preserved_commander"] == 1
    assert _status(session, cmd.id) == "pending"
    assert _status(session, card.id) == "denied"


def test_withdraw_include_commander_cancels_it(session):
    # The explicit opt-in still lets the player back out of a commander.
    deck = repo.create_deck(session)
    convo = repo.create_conversation(session)
    cmd = _pending(session, convo.id, deck.id, "set_commander", "Mass of Mysteries")

    result = deck_tools.withdraw_pending_proposals(
        session, deck.id, include_commander=True
    )

    assert result["withdrawn"] == 1
    assert result["preserved_commander"] == 0
    assert _status(session, cmd.id) == "denied"


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


@patch("app.tools.deck_tools.get_scryfall_client")
def test_propose_deck_changes_add_validates_via_scryfall(mock_get_client, session):
    mock_get_client.return_value.named.return_value = {"name": "Sol Ring"}
    deck = repo.create_deck(session)
    convo = repo.create_conversation(session)
    result = dispatch(
        "propose_deck_changes",
        {
            "deck_id": deck.id,
            "summary": "Add ramp",
            "changes": [{"action": "add", "card_name": "sol ring", "category": "ramp", "reasoning": "fast mana"}],
            "conversation_id": convo.id,
        },
        session,
    )
    assert result.ok is True
    assert result.content["proposals"][0]["card_name"] == "Sol Ring"
    assert result.content["proposals"][0]["status"] == "pending"


@patch("app.tools.deck_tools.get_scryfall_client")
def test_propose_deck_changes_rejects_banned_card_in_commander(mock_get_client, session):
    mock_get_client.return_value.named.return_value = {"name": "Mana Crypt"}
    deck = repo.create_deck(session, format="commander")
    convo = repo.create_conversation(session)
    result = dispatch(
        "propose_deck_changes",
        {
            "deck_id": deck.id,
            "summary": "Add fast mana",
            "changes": [{"action": "add", "card_name": "mana crypt", "reasoning": "ramp"}],
            "conversation_id": convo.id,
        },
        session,
    )
    assert result.ok is False
    assert "banned in Commander" in result.content


@patch("app.tools.deck_tools.get_scryfall_client")
def test_propose_deck_changes_allows_banned_card_in_nonsingleton_format(mock_get_client, session):
    # Mana Crypt is only banned in Commander; a non-commander deck may run it.
    mock_get_client.return_value.named.return_value = {"name": "Mana Crypt"}
    deck = repo.create_deck(session, format="legacy")
    convo = repo.create_conversation(session)
    result = dispatch(
        "propose_deck_changes",
        {
            "deck_id": deck.id,
            "summary": "Add fast mana",
            "changes": [{"action": "add", "card_name": "mana crypt", "reasoning": "ramp"}],
            "conversation_id": convo.id,
        },
        session,
    )
    assert result.ok is True
    assert result.content["proposals"][0]["card_name"] == "Mana Crypt"


@patch("app.tools.deck_tools.get_scryfall_client")
def test_propose_deck_changes_add_unknown_card_is_error_result(mock_get_client, session):
    mock_get_client.return_value.named.side_effect = ScryfallNotFoundError("not found")
    deck = repo.create_deck(session)
    convo = repo.create_conversation(session)
    result = dispatch(
        "propose_deck_changes",
        {
            "deck_id": deck.id,
            "summary": "Add ramp",
            "changes": [{"action": "add", "card_name": "Definitely Not A Card", "reasoning": "x"}],
            "conversation_id": convo.id,
        },
        session,
    )
    assert result.ok is False
    assert "No Scryfall card found" in result.content


def test_propose_deck_changes_remove_validates_against_deck_contents(session):
    deck = repo.create_deck(session)
    convo = repo.create_conversation(session)
    repo.add_deck_card(session, deck.id, "Sol Ring", quantity=1)

    result = dispatch(
        "propose_deck_changes",
        {
            "deck_id": deck.id,
            "summary": "Cut Sol Ring",
            "changes": [{"action": "remove", "card_name": "sol ring", "reasoning": "too good"}],
            "conversation_id": convo.id,
        },
        session,
    )
    assert result.ok is True
    assert result.content["proposals"][0]["card_name"] == "Sol Ring"


def test_propose_deck_changes_remove_without_quantity_defaults_to_whole_stack(session):
    deck = repo.create_deck(session)
    convo = repo.create_conversation(session)
    repo.add_deck_card(session, deck.id, "Swamp", quantity=34)

    result = dispatch(
        "propose_deck_changes",
        {
            "deck_id": deck.id,
            "summary": "Cut Swamp",
            "changes": [{"action": "remove", "card_name": "Swamp", "reasoning": "too many lands"}],
            "conversation_id": convo.id,
        },
        session,
    )
    assert result.ok is True
    assert result.content["proposals"][0]["quantity"] is None


def test_propose_deck_changes_remove_with_explicit_quantity_is_preserved(session):
    deck = repo.create_deck(session)
    convo = repo.create_conversation(session)
    repo.add_deck_card(session, deck.id, "Swamp", quantity=34)

    result = dispatch(
        "propose_deck_changes",
        {
            "deck_id": deck.id,
            "summary": "Cut 2 Swamp",
            "changes": [
                {"action": "remove", "card_name": "Swamp", "quantity": 2, "reasoning": "trim lands"}
            ],
            "conversation_id": convo.id,
        },
        session,
    )
    assert result.ok is True
    assert result.content["proposals"][0]["quantity"] == 2


def test_propose_deck_changes_remove_card_not_in_deck_is_error_result(session):
    deck = repo.create_deck(session)
    convo = repo.create_conversation(session)

    result = dispatch(
        "propose_deck_changes",
        {
            "deck_id": deck.id,
            "summary": "Cut something",
            "changes": [{"action": "remove", "card_name": "Sol Ring", "reasoning": "too good"}],
            "conversation_id": convo.id,
        },
        session,
    )
    assert result.ok is False
    assert "is not in the deck" in result.content


def test_propose_deck_changes_missing_card_name_is_error_result(session):
    deck = repo.create_deck(session)
    convo = repo.create_conversation(session)

    result = dispatch(
        "propose_deck_changes",
        {
            "deck_id": deck.id,
            "summary": "Bad proposal",
            "changes": [{"action": "remove", "card_name": "", "reasoning": "x"}],
            "conversation_id": convo.id,
        },
        session,
    )
    assert result.ok is False
    assert "requires a card_name" in result.content


def test_propose_deck_changes_without_conversation_id_is_error_result(session):
    deck = repo.create_deck(session)

    result = dispatch(
        "propose_deck_changes",
        {
            "deck_id": deck.id,
            "summary": "Orphan proposal",
            "changes": [{"action": "remove", "card_name": "Sol Ring", "reasoning": "x"}],
        },
        session,
    )
    assert result.ok is False
    assert "conversation_id" in result.content


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


# Dispatch-registered tools intentionally not exposed to the model as a
# ToolSpec — invoked internally only (e.g. via repository.apply_proposal).
DISPATCH_ONLY_TOOLS = {
    "deck_add_card",
    "deck_remove_card",
    "deck_set_commander",
}


def test_suggest_cards_requires_a_provider(session):
    deck = repo.create_deck(session)
    # no provider passed -> guarded, not a crash
    result = dispatch("suggest_cards", {"deck_id": deck.id, "intent": "ramp"}, session)
    assert result.ok is False
    assert "requires an LLM provider" in result.content


@patch("app.pipeline.service.build_suggestions")
def test_suggest_cards_threads_provider_and_returns_proposal_shape(mock_build, session):
    from app.pipeline.service import SuggestionResult

    mock_build.return_value = SuggestionResult(
        summary="Added ramp.",
        proposals=[{"id": 1, "action": "add", "card_name": "Sol Ring", "status": "pending"}],
    )
    deck = repo.create_deck(session)
    sentinel_provider = object()

    result = dispatch(
        "suggest_cards",
        {"deck_id": deck.id, "intent": "cheap ramp", "conversation_id": 7},
        session,
        provider=sentinel_provider,
    )

    assert result.ok is True
    # same shape propose_deck_changes returns, so the engine streams it unchanged
    assert result.content["summary"] == "Added ramp."
    assert result.content["proposals"][0]["card_name"] == "Sol Ring"

    # the active provider was forwarded into the pipeline
    _, kwargs = mock_build.call_args
    args = mock_build.call_args.args
    assert sentinel_provider in args
    assert kwargs["conversation_id"] == 7


def test_suggest_cards_is_a_proposal_and_deck_scoped_tool():
    # Membership in these sets is what makes the engine force deck_id/
    # conversation_id and stream the resulting proposals — assert it explicitly
    # so a future refactor can't silently drop the wiring.
    from app.tools.dispatch import DECK_SCOPED_TOOLS, PROPOSAL_TOOLS
    assert "suggest_cards" in PROPOSAL_TOOLS
    assert "suggest_cards" in DECK_SCOPED_TOOLS


def test_tool_schemas_and_dispatch_registry_in_sync():
    spec_names = {t.name for t in TOOL_SPECS}
    dispatch_names = set(STATELESS_TOOLS) | set(SESSION_TOOLS) | set(PROVIDER_SESSION_TOOLS)

    missing_from_dispatch = spec_names - dispatch_names
    assert not missing_from_dispatch, (
        f"TOOL_SPECS references not in dispatch: {missing_from_dispatch}"
    )

    undocumented = dispatch_names - spec_names - DISPATCH_ONLY_TOOLS
    assert not undocumented, (
        f"Dispatch-registered tools with no ToolSpec and not in "
        f"DISPATCH_ONLY_TOOLS: {undocumented}. If the model should never call "
        f"these directly, add them to DISPATCH_ONLY_TOOLS; otherwise add a "
        f"ToolSpec."
    )
