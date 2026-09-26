import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.api import grading
from app.db import repository as repo
from app.knowledge import models as _knowledge_models  # noqa: F401
from app.pipeline.selection import Pick, Selection
from app.pipeline.service import SuggestionResult


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def client(engine, monkeypatch):
    import app.cards.store as card_store
    import app.llm.factory as factory
    import app.pipeline.service as service

    monkeypatch.setattr(grading, "get_engine", lambda: engine)
    monkeypatch.setattr(factory, "get_provider", lambda: object())
    monkeypatch.setattr(grading, "_edhrec_page", lambda commander: {"sol ring"})
    monkeypatch.setattr(card_store, "by_names", lambda names: {
        n.lower(): {"mana_cost": "{1}", "type_line": "Artifact", "oracle_text": "T: Add C."} for n in names
    })
    calls = []

    def fake_build(session, deck_id, intent, provider, select_backend=None, **kwargs):
        calls.append(select_backend)
        return SuggestionResult(
            summary="", selection=Selection(picks=[Pick("Sol Ring", "fast"), Pick("Odd Rock", "odd")]),
            debug={"select_backend": select_backend},
        )

    monkeypatch.setattr(service, "build_suggestions", fake_build)
    app = FastAPI()
    app.include_router(grading.router)
    with TestClient(app) as c:
        c.calls = calls
        yield c


def _deck(engine):
    with Session(engine) as s:
        return repo.create_deck(s, name="D", commander="Cmd").id


def test_batch_is_blind_until_every_pick_is_graded(client, engine):
    deck_id = _deck(engine)
    batch = client.post("/api/grading/batches", json={"deck_id": deck_id, "intent": "more ramp"}).json()
    assert batch["backend"] is None
    assert [p["name"] for p in batch["picks"]] == ["Sol Ring", "Odd Rock"]
    assert [p["off_page"] for p in batch["picks"]] == [False, True]
    assert client.calls[0] in ("jev", "llm")

    url = f"/api/grading/batches/{batch['id']}/grades"
    half = client.put(url, json={"card_name": "Sol Ring", "grade": "good"}).json()
    assert half["backend"] is None and not half["complete"]
    done = client.put(url, json={"card_name": "Odd Rock", "grade": "bad", "note": "off-plan"}).json()
    assert done["complete"] and done["backend"] == client.calls[0]


def test_regrading_replaces_the_grade(client, engine):
    deck_id = _deck(engine)
    batch = client.post("/api/grading/batches",
                        json={"deck_id": deck_id, "intent": "x", "backend": "jev"}).json()
    url = f"/api/grading/batches/{batch['id']}/grades"
    client.put(url, json={"card_name": "Sol Ring", "grade": "bad"})
    after = client.put(url, json={"card_name": "Sol Ring", "grade": "good"}).json()
    assert after["picks"][0]["grade"] == "good"


def test_grading_a_card_outside_the_batch_is_refused(client, engine):
    batch = client.post("/api/grading/batches",
                        json={"deck_id": _deck(engine), "intent": "x", "backend": "jev"}).json()
    resp = client.put(f"/api/grading/batches/{batch['id']}/grades",
                      json={"card_name": "Nope", "grade": "good"})
    assert resp.status_code == 400


def test_summary_counts_only_complete_batches(client, engine):
    deck_id = _deck(engine)
    done = client.post("/api/grading/batches", json={"deck_id": deck_id, "intent": "x", "backend": "jev"}).json()
    client.post("/api/grading/batches", json={"deck_id": deck_id, "intent": "x", "backend": "llm"})
    url = f"/api/grading/batches/{done['id']}/grades"
    client.put(url, json={"card_name": "Sol Ring", "grade": "good"})
    client.put(url, json={"card_name": "Odd Rock", "grade": "bad"})
    summary = client.get("/api/grading/summary").json()["by_backend"]
    assert set(summary) == {"jev"}
    assert summary["jev"]["bad_rate"] == 0.5 and summary["jev"]["picks"] == 2


def test_delete_removes_the_batch_and_its_grades(client, engine):
    batch = client.post("/api/grading/batches",
                        json={"deck_id": _deck(engine), "intent": "x", "backend": "jev"}).json()
    client.put(f"/api/grading/batches/{batch['id']}/grades", json={"card_name": "Sol Ring", "grade": "good"})
    assert client.delete(f"/api/grading/batches/{batch['id']}").json() == {"ok": True}
    assert client.get("/api/grading/batches").json() == []
