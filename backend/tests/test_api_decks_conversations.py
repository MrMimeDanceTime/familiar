import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

from app.api import chat, conversations, decks


@pytest.fixture
def client(tmp_path, monkeypatch):
    test_engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(test_engine)
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
