import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from app.api import chat, conversations, decks
from app.db import repository as repo
# Registers the knowledge table with SQLModel's metadata before create_all.
from app.knowledge import models as _knowledge_models  # noqa: F401


@pytest.fixture
def test_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def client(test_engine, monkeypatch):
    monkeypatch.setattr(chat, "get_engine", lambda: test_engine)
    monkeypatch.setattr(decks, "get_engine", lambda: test_engine)
    monkeypatch.setattr(conversations, "get_engine", lambda: test_engine)

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(chat.router)
    app.include_router(decks.router)
    app.include_router(conversations.router)

    with TestClient(app) as c:
        yield c


def test_create_and_list_decks(client):
    resp = client.post("/api/decks", json={"name": "Test Deck", "commander": "Korvold"})
    assert resp.status_code == 200
    deck = resp.json()
    assert deck["name"] == "Test Deck"

    resp = client.get("/api/decks")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_get_deck_404(client):
    resp = client.get("/api/decks/999")
    assert resp.status_code == 404


def test_stats_endpoint_never_calls_llm(client, monkeypatch):
    # /stats must stay fast: it computes deterministic stats only and must not
    # reach for the LLM provider (that's what /stats/nuance is for). If it did,
    # a cold nuance cache would hang the whole panel.
    def _boom():
        raise AssertionError("/stats must not construct the LLM provider")

    monkeypatch.setattr(decks, "get_llm_provider", _boom)
    deck = client.post("/api/decks", json={"name": "S", "commander": "Korvold"}).json()

    resp = client.get(f"/api/decks/{deck['id']}/stats")
    assert resp.status_code == 200
    assert "total_cards" in resp.json()


def test_stats_endpoints_404_for_missing_deck(client):
    assert client.get("/api/decks/999/stats").status_code == 404
    assert client.get("/api/decks/999/stats/nuance").status_code == 404


def test_nuance_endpoint_returns_power_fields(client, monkeypatch):
    # With no provider available the endpoint still returns the (base) power
    # fields rather than erroring, so the frontend can merge them safely.
    monkeypatch.setattr(decks, "get_llm_provider", lambda: (_ for _ in ()).throw(RuntimeError("no provider")))
    deck = client.post("/api/decks", json={"name": "N", "commander": "Korvold"}).json()

    resp = client.get(f"/api/decks/{deck['id']}/stats/nuance")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {
        "power_level", "power_level_base", "power_nuance_adj",
        "power_nuance_reason", "power_nuance_pending", "settle_seconds",
        "power_factors",
    }


def test_patch_deck(client):
    deck = client.post("/api/decks", json={"name": "Old"}).json()
    resp = client.patch(f"/api/decks/{deck['id']}", json={"name": "New", "power_level": "casual"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "New"
    assert resp.json()["power_level"] == "casual"


def test_delete_deck(client):
    deck = client.post("/api/decks", json={"name": "ToDelete"}).json()
    resp = client.delete(f"/api/decks/{deck['id']}")
    assert resp.status_code == 200
    assert client.get(f"/api/decks/{deck['id']}").status_code == 404


def test_get_deck_cards_empty(client):
    deck = client.post("/api/decks", json={"name": "Test"}).json()
    resp = client.get(f"/api/decks/{deck['id']}/cards")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_conversations_empty(client):
    resp = client.get("/api/conversations")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_conversation_404(client):
    resp = client.get("/api/conversations/999")
    assert resp.status_code == 404


def test_delete_conversation(client, test_engine):
    with Session(test_engine) as session:
        convo = repo.create_conversation(session, title="ToDelete")
        repo.add_message(session, convo.id, role="user", sequence=0, text_content="hi")
        convo_id = convo.id

    resp = client.delete(f"/api/conversations/{convo_id}")
    assert resp.status_code == 200
    assert client.get(f"/api/conversations/{convo_id}").status_code == 404
    assert client.get("/api/conversations").json() == []


def test_delete_conversation_missing_is_a_noop(client):
    resp = client.delete("/api/conversations/999")
    assert resp.status_code == 200


def test_delete_deck_unlinks_its_conversations(client, test_engine):
    deck = client.post("/api/decks", json={"name": "Doomed"}).json()
    convo = client.post(f"/api/decks/{deck['id']}/start-conversation").json()

    assert client.delete(f"/api/decks/{deck['id']}").status_code == 200

    detail = client.get(f"/api/conversations/{convo['id']}").json()
    assert detail["conversation"]["deck_id"] is None


def test_second_turn_on_a_running_conversation_is_rejected(client, test_engine, monkeypatch):
    monkeypatch.setattr(chat, "submit_turn", lambda *a, **k: None)
    first = client.post("/api/chat", json={"conversation_id": "new", "message": "hi"})
    assert first.status_code == 200
    convo_id = first.json()["conversation_id"]

    second = client.post("/api/chat", json={"conversation_id": convo_id, "message": "again"})
    assert second.status_code == 409


def test_deck_list_is_a_summary_not_a_snapshot(client, test_engine):
    deck = client.post("/api/decks", json={"name": "Listed", "commander": "Korvold"}).json()
    with Session(test_engine) as session:
        repo.add_deck_card(session, deck["id"], "Sol Ring", quantity=1, oracle_text="{T}: Add {C}{C}.")
        repo.add_deck_card(session, deck["id"], "Swamp", quantity=30)

    rows = client.get("/api/decks").json()
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "Listed"
    assert row["commander"] == "Korvold"
    assert row["total_cards"] == 31
    assert "cards" not in row
    # The full snapshot is still one request away.
    assert len(client.get(f"/api/decks/{deck['id']}").json()["cards"]) == 2


def test_revert_undoes_an_approved_add(client, test_engine):
    from unittest.mock import patch

    deck = client.post("/api/decks", json={"name": "U"}).json()
    with Session(test_engine) as session:
        convo = repo.create_conversation(session)
        with patch("app.tools.deck_tools.get_scryfall_client") as scry, \
             patch("app.tools.deck_tools.get_tags_for_card", return_value=[]):
            scry.return_value.named.side_effect = lambda n, **k: {
                "name": n, "cmc": 1.0, "color_identity": [], "oracle_id": f"o-{n}",
                "type_line": "Artifact", "oracle_text": "",
            }
            from app.tools.deck_tools import propose_deck_changes
            pid = propose_deck_changes(
                session, deck["id"], "b",
                [{"action": "add", "card_name": "Sol Ring", "reasoning": "x"}],
                conversation_id=convo.id,
            )["proposals"][0]["id"]
            repo.apply_proposal(session, pid)
            assert repo.get_deck_card(session, deck["id"], "Sol Ring") is not None

            resp = client.post(f"/api/decks/proposals/{pid}/revert")

    assert resp.status_code == 200
    assert [c["name"] for c in resp.json()["cards"]] == []
    with Session(test_engine) as session:
        assert repo.get_proposal(session, pid).status == "pending"


def test_revert_refuses_a_pending_or_commander_proposal(client, test_engine):
    deck = client.post("/api/decks", json={"name": "U"}).json()
    with Session(test_engine) as session:
        convo = repo.create_conversation(session)
        from app.db.models import DeckProposal
        pending = DeckProposal(conversation_id=convo.id, deck_id=deck["id"], action="add", card_name="X")
        cmd = DeckProposal(conversation_id=convo.id, deck_id=deck["id"], action="set_commander",
                           card_name="Y", commander_name="Y", status="approved")
        session.add_all([pending, cmd])
        session.commit()
        ids = (pending.id, cmd.id)
    for pid in ids:
        assert client.post(f"/api/decks/proposals/{pid}/revert").status_code == 400


def test_knowledge_entries_crud_and_seed_protection(client, test_engine, monkeypatch):
    from app.api import knowledge
    from app.knowledge.models import SOURCE_SEED, KnowledgeEntry

    monkeypatch.setattr(knowledge, "get_engine", lambda: test_engine)
    from app.knowledge.models import _ensure_fts
    monkeypatch.setattr("app.knowledge.models.get_engine", lambda: test_engine)
    _ensure_fts()
    client.app.include_router(knowledge.router)
    with Session(test_engine) as session:
        session.add(KnowledgeEntry(title="Seeded", body="x", category="ramp", source=SOURCE_SEED))
        session.commit()
        seeded_id = session.exec(select(KnowledgeEntry)).first().id

    created = client.post("/api/knowledge", json={
        "title": "Our table", "body": "No infinite combos before turn 8.", "category": "playgroup",
    })
    assert created.status_code == 200
    entry = created.json()
    assert entry["source"] == "user"

    assert client.post("/api/knowledge", json={"title": "t", "body": "b", "category": "nonsense"}).status_code == 400

    mine = client.get("/api/knowledge?source=user").json()
    assert [e["id"] for e in mine] == [entry["id"]]

    updated = client.put(f"/api/knowledge/{entry['id']}", json={
        "title": "Our table", "body": "No infinite combos, full stop.", "category": "playgroup",
    })
    assert updated.json()["body"] == "No infinite combos, full stop."

    assert client.put(f"/api/knowledge/{seeded_id}", json={"title": "x", "body": "y", "category": "ramp"}).status_code == 403
    assert client.delete(f"/api/knowledge/{seeded_id}").status_code == 403
    assert client.delete(f"/api/knowledge/{entry['id']}").status_code == 200
    assert client.get("/api/knowledge?source=user").json() == []


def test_cancel_endpoint_flags_a_running_turn(client, test_engine, monkeypatch):
    from app.db.models import TURN_DONE

    monkeypatch.setattr(chat, "submit_turn", lambda *a, **k: None)
    started = client.post("/api/chat", json={"conversation_id": "new", "message": "hi"}).json()
    turn_id = started["turn_id"]

    resp = client.post(f"/api/chat/turns/{turn_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["cancel_requested"] is True
    with Session(test_engine) as session:
        assert repo.turn_cancel_requested(session, turn_id) is True
        repo.finish_turn(session, turn_id, TURN_DONE)

    again = client.post(f"/api/chat/turns/{turn_id}/cancel")
    assert again.status_code == 200
    assert again.json()["cancel_requested"] is False
    assert client.post("/api/chat/turns/nope/cancel").status_code == 404
