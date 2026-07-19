import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.api import chat, conversations, decks
from app.db import repository as repo


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
        "power_nuance_reason", "power_factors",
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
