import json
from unittest.mock import patch

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.db import repository as repo
from app.pipeline.service import (
    _commander_identity,
    _render_deck_context,
    build_suggestions,
)


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _rakdos_deck(session):
    deck = repo.create_deck(session, name="Rakdos Test", format="commander",
                            commander="Judith, the Scourge Diva")
    # commander card carries the identity the pipeline reads
    repo.add_deck_card(session, deck.id, "Judith, the Scourge Diva", quantity=1,
                       category="Commander", color_identity="BR",
                       type_line="Legendary Creature — Human Shaman")
    # one existing card, so "already in deck" is exercised
    repo.add_deck_card(session, deck.id, "Terminate", quantity=1,
                       color_identity="BR", type_line="Instant")
    return deck


class TwoStageProvider:
    """Returns stage-1 JSON when the prompt is the query-planner, stage-4 JSON
    when it's the selector — distinguished by a marker in the system prompt."""

    def __init__(self, stage1, stage4):
        self._stage1 = json.dumps(stage1)
        self._stage4 = json.dumps(stage4)
        self.calls = []

    def complete_json(self, system, user, *, model=None, thinking=True, reasoning_effort=None):
        self.calls.append({"system": system, "user": user, "model": model})
        if "query-planning stage" in system:
            return self._stage1
        return self._stage4


class FakeScryfall:
    def __init__(self, cards):
        self._cards = cards
        self.queries = []

    def search_pipeline(self, query, limit=50):
        self.queries.append(query)
        return [dict(c) for c in self._cards]


class FakeEdhrec:
    def commander_recs(self, name):
        return {"commander": name, "categories": {}}


def _pool_card(name, oid, ci=("B", "R"), rank=100):
    return {
        "name": name, "oracle_id": oid, "mana_cost": "{1}{B}", "cmc": 2.0,
        "type_line": "Instant", "oracle_text": "Destroy target creature.",
        "color_identity": list(ci), "legal_commander": True, "keywords": [],
        "power": None, "toughness": None, "loyalty": None, "rarity": "common",
        "edhrec_rank": rank,
    }


class _FakeNamedScryfall:
    def __init__(self, identity):
        self._identity = list(identity)
        self.named_calls = []

    def named(self, name, fuzzy=True):
        self.named_calls.append(name)
        return {"name": name, "color_identity": self._identity}


def test_commander_identity_reads_from_decklist():
    snap = {
        "commander": "Judith, the Scourge Diva",
        "cards": [{"name": "Judith, the Scourge Diva", "color_identity": "BR"}],
    }
    assert _commander_identity(snap) == frozenset({"B", "R"})


def test_commander_identity_falls_back_to_scryfall_when_decklist_empty():
    # Import path didn't populate color_identity (or commander not in cards):
    # empty from the decklist -> Scryfall lookup rescues it instead of returning
    # an empty (colourless) identity that would drop every colored suggestion.
    snap = {
        "commander": "Judith, the Scourge Diva",
        "cards": [{"name": "Judith, the Scourge Diva", "color_identity": ""}],
    }
    fake = _FakeNamedScryfall(["B", "R"])
    assert _commander_identity(snap, fake) == frozenset({"B", "R"})
    assert fake.named_calls == ["Judith, the Scourge Diva"]


def test_commander_identity_no_commander_stays_empty_without_scryfall_call():
    fake = _FakeNamedScryfall(["B", "R"])
    snap = {"commander": None, "cards": []}
    assert _commander_identity(snap, fake) == frozenset()
    assert fake.named_calls == []  # no commander -> no lookup


def test_commander_identity_unions_partners_from_decklist():
    snap = {
        "commander": "Commander A",
        "partner_commander": "Commander B",
        "cards": [
            {"name": "Commander A", "color_identity": "W"},
            {"name": "Commander B", "color_identity": "U"},
        ],
    }
    assert _commander_identity(snap) == frozenset({"W", "U"})


@patch("app.tools.deck_tools.get_scryfall_client")
def test_build_suggestions_end_to_end_creates_proposals(mock_sf, session):
    # propose_deck_changes (inside stage 5) canonicalizes via this mock
    mock_sf.return_value.named.return_value = {"name": "Bedevil"}
    deck = _rakdos_deck(session)
    convo = repo.create_conversation(session)

    sf = FakeScryfall([
        _pool_card("Bedevil", "bedevil", rank=50),
        _pool_card("Terminate", "terminate", rank=10),  # already in deck -> illegal
        _pool_card("Swords to Plowshares", "swords", ci=("W",), rank=5),  # off-identity
    ])
    provider = TwoStageProvider(
        stage1={"intent_summary": "removal", "queries": ["otag:removal"]},
        stage4={"summary": "Added premium removal.",
                "picks": [{"name": "Bedevil", "reason": "flexible removal"}]},
    )

    result = build_suggestions(
        session, deck.id, "I need removal", provider,
        conversation_id=convo.id, scryfall=sf, edhrec=FakeEdhrec(),
    )

    # stage 5 produced a pending proposal for the legal pick
    assert len(result.proposals) == 1
    assert result.proposals[0]["card_name"] == "Bedevil"
    assert result.proposals[0]["status"] == "pending"
    assert result.summary == "Added premium removal."

    # the identity token derived from the BR commander reached the stage-1 prompt
    assert "id<=rakdos" in provider.calls[0]["system"]
    # every query got legality-enforced before hitting Scryfall
    assert all("f:commander" in q for q in sf.queries)

    # debug reflects the shaping: 3 in pool, only Bedevil legal
    assert result.debug["pool_size"] == 3
    assert result.debug["legal_shaped"] == 1


def test_render_deck_context_includes_commander_and_current_cards():
    snapshot = {
        "commander": "Judith, the Scourge Diva",
        "partner_commander": None,
        "notes": "Aristocrats — sacrifice for value.",
        "cards": [
            {"name": "Judith, the Scourge Diva", "category": "Commander",
             "oracle_text": "Other creatures you control get +1/+0. Whenever a "
                            "nontoken creature you control dies, Judith deals 1 damage."},
            {"name": "Blood Artist", "category": "Drain", "oracle_text": "..."},
            {"name": "Carrion Feeder", "category": "Sacrifice", "oracle_text": "..."},
        ],
    }
    ctx = _render_deck_context(snapshot)
    # Commander is named with its oracle text (the fit/combo signal).
    assert "Judith, the Scourge Diva" in ctx
    assert "nontoken creature you control dies" in ctx
    # Current non-commander cards are listed by category (names only).
    assert "Blood Artist" in ctx and "Carrion Feeder" in ctx
    # Strategy notes carry through.
    assert "Aristocrats" in ctx
    # The commander is not double-listed under a body category.
    assert ctx.count("Judith, the Scourge Diva") == 1


def test_render_deck_context_no_commander():
    ctx = _render_deck_context({"commander": None, "cards": []})
    assert "not set yet" in ctx


@patch("app.tools.deck_tools.get_scryfall_client")
def test_selection_prompt_receives_deck_context(mock_sf, session):
    """The stage-4 (selection) call must carry the commander so the model can
    judge fit against this specific deck, not just match the intent."""
    mock_sf.return_value.named.return_value = {"name": "Bedevil"}
    deck = _rakdos_deck(session)
    sf = FakeScryfall([_pool_card("Bedevil", "bedevil", rank=50)])
    provider = TwoStageProvider(
        stage1={"queries": ["otag:removal"]},
        stage4={"summary": "s", "picks": [{"name": "Bedevil", "reason": "x"}]},
    )
    build_suggestions(session, deck.id, "removal", provider,
                      scryfall=sf, edhrec=FakeEdhrec())

    # calls[0] is stage 1 (query planner), calls[1] is stage 4 (selector).
    selection_user_prompt = provider.calls[1]["user"]
    assert "Judith, the Scourge Diva" in selection_user_prompt
    assert "Current deck" in selection_user_prompt


def test_build_suggestions_preview_mode_no_conversation(session):
    deck = _rakdos_deck(session)
    sf = FakeScryfall([_pool_card("Bedevil", "bedevil")])
    provider = TwoStageProvider(
        stage1={"queries": ["otag:removal"]},
        stage4={"summary": "s", "picks": [{"name": "Bedevil", "reason": "x"}]},
    )

    # no conversation_id -> no proposals created, raw selection returned
    result = build_suggestions(
        session, deck.id, "removal", provider, scryfall=sf, edhrec=FakeEdhrec(),
    )
    assert result.proposals == []
    assert result.selection is not None
    assert [p.name for p in result.selection.picks] == ["Bedevil"]


def test_off_identity_pick_cannot_be_selected(session):
    # The model tries to pick an off-identity card; the shaping marks it illegal
    # and the selection stage drops it — defense in depth before stage 5.
    deck = _rakdos_deck(session)
    sf = FakeScryfall([_pool_card("Swords to Plowshares", "swords", ci=("W",))])
    provider = TwoStageProvider(
        stage1={"queries": ["otag:removal"]},
        stage4={"summary": "s", "picks": [{"name": "Swords to Plowshares", "reason": "x"}]},
    )
    result = build_suggestions(
        session, deck.id, "removal", provider, scryfall=sf, edhrec=FakeEdhrec(),
    )
    assert result.selection.picks == []
