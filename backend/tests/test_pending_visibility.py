"""The model must be able to SEE what is actually pending.

Symptom: the assistant talks about "the N cards I proposed to round the deck
out" when there is no such batch on screen. It is not hallucinating a number —
it is reading its own transcript.

Approve/deny happens over REST (`/api/decks/proposals/{id}/apply`), a path the
chat loop never observes. The proposal row flips to approved/denied in the DB,
but nothing writes that back into the conversation history, so the model's
`propose_deck_changes` tool result still lists the original batch as pending
forever. Twelve tools existed and not one of them reported pending state, so
the model had no way to check.

These tests pin that a deck read reports the CURRENT pending set.
"""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import repository as repo
from app.db.session import get_engine
from app.tools import deck_tools
from app.tools import proposals


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FAMILIAR_DB_PATH", str(tmp_path / "pending.db"))
    from app.config import settings

    monkeypatch.setattr(settings, "familiar_db_path", str(tmp_path / "pending.db"))
    # Proposals resolve names through Scryfall; these tests are about pending
    # state, not name resolution, and must not need the network.
    fake_scryfall = MagicMock()
    fake_scryfall.named.side_effect = lambda name, **k: {
        "name": name, "cmc": 1.0, "color_identity": [], "oracle_id": f"o-{name}",
        "type_line": "Artifact", "oracle_text": "",
    }
    monkeypatch.setattr(deck_tools, "get_scryfall_client", lambda: fake_scryfall)
    get_engine.cache_clear()
    from app.main import app

    with TestClient(app) as c:
        yield c
    get_engine.cache_clear()


def _deck_and_conversation(client):
    deck = client.post("/api/decks", json={"name": "Pending Test"}).json()
    conv = client.post(f"/api/decks/{deck['id']}/start-conversation").json()
    return deck["id"], conv.get("conversation_id") or conv.get("id")


def _propose(deck_id, conversation_id, *cards):
    with Session(get_engine()) as s:
        result = proposals.propose_deck_changes(
            s,
            deck_id=deck_id,
            conversation_id=conversation_id,
            changes=[
                {"action": "add", "card_name": c, "reasoning": "test"} for c in cards
            ],
            summary=f"{len(cards)} cards",
        )
    return [p["id"] for p in result["proposals"]]


def _snapshot(deck_id):
    with Session(get_engine()) as s:
        return repo.deck_snapshot(s, deck_id)


def test_deck_read_reports_pending_proposals(client):
    """THE regression: the model could not see its own outstanding batch."""
    deck_id, conv_id = _deck_and_conversation(client)
    _propose(deck_id, conv_id, "Sol Ring", "Cultivate")

    snap = _snapshot(deck_id)
    assert "pending_proposals" in snap, "deck read exposes no pending state"
    assert snap["pending_proposals"]["count"] == 2
    names = {p["card_name"] for p in snap["pending_proposals"]["proposals"]}
    assert names == {"Sol Ring", "Cultivate"}


def test_approving_in_the_ui_updates_what_the_model_sees(client):
    """Approve/deny goes over REST, which the chat loop never observes.

    Without this the model keeps citing the original batch size long after the
    player has worked through it.
    """
    deck_id, conv_id = _deck_and_conversation(client)
    ids = _propose(deck_id, conv_id, "Sol Ring", "Cultivate")

    client.post(f"/api/decks/proposals/{ids[0]}/apply")

    snap = _snapshot(deck_id)
    assert snap["pending_proposals"]["count"] == 1
    remaining = {p["card_name"] for p in snap["pending_proposals"]["proposals"]}
    assert remaining == {"Cultivate"}


def test_denying_removes_it_from_the_pending_set(client):
    deck_id, conv_id = _deck_and_conversation(client)
    ids = _propose(deck_id, conv_id, "Sol Ring", "Cultivate")

    client.post(f"/api/decks/proposals/{ids[1]}/deny")

    snap = _snapshot(deck_id)
    assert snap["pending_proposals"]["count"] == 1
    assert snap["pending_proposals"]["proposals"][0]["card_name"] == "Sol Ring"


def test_fully_resolved_batch_reports_zero(client):
    """The state the player is actually in when the bug bites: nothing pending,
    but the transcript still shows the original batch."""
    deck_id, conv_id = _deck_and_conversation(client)
    ids = _propose(deck_id, conv_id, "Sol Ring", "Cultivate")

    client.post(f"/api/decks/proposals/{ids[0]}/apply")
    client.post(f"/api/decks/proposals/{ids[1]}/deny")

    snap = _snapshot(deck_id)
    assert snap["pending_proposals"]["count"] == 0
    assert snap["pending_proposals"]["proposals"] == []


def test_no_proposals_reports_zero_not_missing(client):
    """A deck with no history must still carry the key.

    An absent key and an empty one read the same to a model that is guessing,
    which is the ambiguity this whole change exists to remove.
    """
    deck_id, _ = _deck_and_conversation(client)
    snap = _snapshot(deck_id)
    assert snap["pending_proposals"]["count"] == 0


def test_pending_set_is_scoped_to_this_deck(client):
    """A second deck's pending batch must not leak into this one's read."""
    deck_a, conv_a = _deck_and_conversation(client)
    deck_b, conv_b = _deck_and_conversation(client)

    _propose(deck_a, conv_a, "Sol Ring")
    _propose(deck_b, conv_b, "Cultivate", "Rampant Growth")

    assert _snapshot(deck_a)["pending_proposals"]["count"] == 1
    assert _snapshot(deck_b)["pending_proposals"]["count"] == 2


def test_total_cards_still_excludes_pending(client):
    """Pending cards are proposals, not deck contents.

    Folding them into total_cards would make the model think the deck is bigger
    than it is — the opposite error.
    """
    deck_id, conv_id = _deck_and_conversation(client)
    before = _snapshot(deck_id)["total_cards"]
    _propose(deck_id, conv_id, "Sol Ring", "Cultivate")

    snap = _snapshot(deck_id)
    assert snap["total_cards"] == before
    assert snap["pending_proposals"]["count"] == 2
