"""The deterministic half of the stats is memoised per deck content."""

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.db import repository as repo
from app.tools import deck_stats


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'memo.db'}")
    SQLModel.metadata.create_all(engine)
    deck_stats.clear_stats_memo()
    with Session(engine) as s:
        yield s
    deck_stats.clear_stats_memo()


def _deck(session):
    deck = repo.create_deck(session, name="Memo")
    for i in range(5):
        repo.add_deck_card(
            session, deck_id=deck.id, card_name=f"Forest {i}", quantity=1, mana_value=0,
            type_line="Basic Land — Forest", oracle_text="", oracle_id=f"f{i}", tags=[],
        )
    return deck


def test_second_call_reuses_the_base(session, monkeypatch):
    deck = _deck(session)
    calls = {"n": 0}
    real = deck_stats._deck_combos

    def counting(names):
        calls["n"] += 1
        return real(names)

    monkeypatch.setattr(deck_stats, "_deck_combos", counting)
    first = deck_stats.compute_deck_stats(session, deck.id)
    second = deck_stats.compute_deck_stats(session, deck.id)
    assert calls["n"] == 1
    assert first == second


def test_a_card_change_invalidates(session, monkeypatch):
    deck = _deck(session)
    calls = {"n": 0}
    monkeypatch.setattr(deck_stats, "_deck_combos", lambda names: calls.__setitem__("n", calls["n"] + 1) or [])
    deck_stats.compute_deck_stats(session, deck.id)
    repo.add_deck_card(
        session, deck_id=deck.id, card_name="Llanowar Elves", quantity=1, mana_value=1,
        type_line="Creature — Elf", oracle_text="", oracle_id="le", tags=["ramp"],
    )
    stats = deck_stats.compute_deck_stats(session, deck.id)
    assert calls["n"] == 2
    assert stats["ramp_count"] == 1 and stats["total_cards"] == 6


def test_callers_cannot_poison_the_memo(session):
    deck = _deck(session)
    first = deck_stats.compute_deck_stats(session, deck.id)
    first["power_factors"].append("tampered")
    first["deficiencies"].clear()
    second = deck_stats.compute_deck_stats(session, deck.id)
    assert "tampered" not in second["power_factors"]
    assert second["deficiencies"]


def test_memo_expires(session, monkeypatch):
    deck = _deck(session)
    deck_stats.compute_deck_stats(session, deck.id)
    monkeypatch.setattr(deck_stats, "STATS_MEMO_SECONDS", 0.0)
    calls = {"n": 0}
    monkeypatch.setattr(deck_stats, "_deck_combos", lambda names: calls.__setitem__("n", calls["n"] + 1) or [])
    deck_stats.compute_deck_stats(session, deck.id)
    assert calls["n"] == 1
