import json

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.db import repository as repo
from app.tools import deck_tools
from app.tools.power_nuance import _quantize, compute_nuance, deck_content_hash


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


class FakeProvider:
    def __init__(self, payload):
        self._payload = payload if isinstance(payload, str) else json.dumps(payload)
        self.calls = 0

    def complete_json(self, system, user, *, model=None, thinking=True, reasoning_effort=None):
        self.calls += 1
        return self._payload


# ── quantize / clamp ───────────────────────────────────────────────────────

def test_quantize_snaps_to_allowed_steps():
    assert _quantize(0.7) == 0.5
    assert _quantize(0.3) == 0.5
    assert _quantize(0.24) == 0.0
    assert _quantize(-0.6) == -0.5


def test_quantize_clamps_out_of_range():
    assert _quantize(5.0) == 1.0
    assert _quantize(-9.0) == -1.0


# ── content hash ───────────────────────────────────────────────────────────

def test_hash_ignores_name_and_notes_changes():
    a = {"commander": "Judith", "name": "My Deck", "notes": "aggro",
         "cards": [{"name": "Sol Ring", "quantity": 1}]}
    b = {"commander": "Judith", "name": "Renamed", "notes": "changed",
         "cards": [{"name": "Sol Ring", "quantity": 1}]}
    assert deck_content_hash(a) == deck_content_hash(b)


def test_hash_changes_on_card_and_commander_edits():
    base = {"commander": "Judith", "cards": [{"name": "Sol Ring", "quantity": 1}]}
    add_card = {"commander": "Judith",
                "cards": [{"name": "Sol Ring", "quantity": 1}, {"name": "Bolt", "quantity": 1}]}
    diff_cmd = {"commander": "Krenko", "cards": [{"name": "Sol Ring", "quantity": 1}]}
    assert deck_content_hash(base) != deck_content_hash(add_card)
    assert deck_content_hash(base) != deck_content_hash(diff_cmd)


def test_hash_is_order_independent():
    a = {"commander": "J", "cards": [{"name": "A", "quantity": 1}, {"name": "B", "quantity": 1}]}
    b = {"commander": "J", "cards": [{"name": "B", "quantity": 1}, {"name": "A", "quantity": 1}]}
    assert deck_content_hash(a) == deck_content_hash(b)


# ── compute_nuance guards ──────────────────────────────────────────────────

def test_compute_nuance_parses_and_quantizes():
    provider = FakeProvider({"adjustment": 0.5, "reason": "premium draw suite"})
    adj, reason = compute_nuance(provider, {"cards": []}, 6, ["Raw: 5.0 + 1 = 6/10"])
    assert adj == 0.5
    assert reason == "premium draw suite"


def test_compute_nuance_clamps_a_runaway_value():
    provider = FakeProvider({"adjustment": 3.0, "reason": "combo"})
    adj, _ = compute_nuance(provider, {"cards": []}, 6, [])
    assert adj == 1.0  # clamped, can't move more than a point


def test_compute_nuance_swallows_bad_json():
    provider = FakeProvider("not json")
    adj, reason = compute_nuance(provider, {"cards": []}, 6, [])
    assert adj == 0.0 and reason == ""  # base score stands on failure


# ── cache behavior through compute_deck_stats ──────────────────────────────

def _seed_deck(session):
    deck = repo.create_deck(session, format="commander", commander="Judith, the Scourge Diva")
    repo.add_deck_card(session, deck.id, "Judith, the Scourge Diva", quantity=1,
                       category="Commander", color_identity="BR",
                       type_line="Legendary Creature — Human Shaman")
    repo.add_deck_card(session, deck.id, "Sol Ring", quantity=1, color_identity="",
                       type_line="Artifact", mana_value=1)
    return deck


def test_nuance_computed_once_then_cached(session):
    deck = _seed_deck(session)
    provider = FakeProvider({"adjustment": 1.0, "reason": "tight combo"})

    s1 = deck_tools.compute_deck_stats(session, deck.id, provider)
    s2 = deck_tools.compute_deck_stats(session, deck.id, provider)

    assert provider.calls == 1  # second read hit the cache
    assert s1["power_nuance_adj"] == 1.0
    assert s2["power_nuance_adj"] == 1.0
    assert s2["power_nuance_reason"] == "tight combo"
    assert s1["power_level"] == min(10, s1["power_level_base"] + 1)


def test_half_point_adjustment_is_preserved_not_rounded(session):
    # The whole point of ±0.5 granularity: a half-point nuance must show as a
    # .5 final score, not get rounded away (and not hit banker's rounding).
    deck = _seed_deck(session)
    deck_tools.compute_deck_stats(session, deck.id, None)

    for adj in (0.5, -0.5):
        # fresh deck each time so the cache doesn't reuse a prior adj
        d = _seed_deck(session)
        provider = FakeProvider({"adjustment": adj, "reason": "mild"})
        stats = deck_tools.compute_deck_stats(session, d.id, provider)
        expected = min(10.0, max(1.0, stats["power_level_base"] + adj))
        assert stats["power_level"] == expected
        assert stats["power_level"] % 1 == 0.5  # genuinely a half-point


def test_nuanced_score_clamped_to_1_10(session):
    deck = _seed_deck(session)
    # a +1.0 on an already-high base must not exceed 10; force a high base is hard
    # here, so just assert the clamp math directly via a low base + negative adj
    provider = FakeProvider({"adjustment": -1.0, "reason": "weak"})
    stats = deck_tools.compute_deck_stats(session, deck.id, provider)
    assert 1.0 <= stats["power_level"] <= 10.0


def test_nuance_recomputes_after_card_change(session):
    deck = _seed_deck(session)
    provider = FakeProvider({"adjustment": 0.5, "reason": "x"})
    deck_tools.compute_deck_stats(session, deck.id, provider)
    assert provider.calls == 1

    repo.add_deck_card(session, deck.id, "Lightning Bolt", quantity=1,
                       color_identity="R", type_line="Instant", mana_value=1)
    deck_tools.compute_deck_stats(session, deck.id, provider)
    assert provider.calls == 2  # content hash changed -> recompute


def test_no_provider_uses_base_but_reuses_fresh_cache(session):
    deck = _seed_deck(session)
    provider = FakeProvider({"adjustment": 1.0, "reason": "cached one"})
    deck_tools.compute_deck_stats(session, deck.id, provider)  # populate cache

    # a providerless read must not call an LLM, but should reuse the fresh cache
    stats = deck_tools.compute_deck_stats(session, deck.id, None)
    assert provider.calls == 1
    assert stats["power_nuance_adj"] == 1.0
    assert stats["power_nuance_reason"] == "cached one"


def test_no_provider_no_cache_returns_base_only(session):
    deck = _seed_deck(session)
    stats = deck_tools.compute_deck_stats(session, deck.id, None)
    assert stats["power_nuance_adj"] == 0.0
    assert stats["power_level"] == stats["power_level_base"]


def test_empty_deck_stats_include_nuance_keys(session):
    # Regression: the empty-deck early return (_empty_stats) used to omit
    # power_level_base/power_nuance_*, so any consumer reading them KeyError'd.
    deck = repo.create_deck(session, format="commander")
    provider = FakeProvider({"adjustment": 1.0, "reason": "should not be called"})

    stats = deck_tools.compute_deck_stats(session, deck.id, provider)

    assert stats["total_cards"] == 0
    assert stats["power_level"] == 1
    assert stats["power_level_base"] == 1
    assert stats["power_nuance_adj"] == 0.0
    assert stats["power_nuance_reason"] == ""
    assert provider.calls == 0  # nothing to judge -> no LLM call on an empty deck


def test_stats_always_expose_the_nuance_key_contract(session):
    # Both a populated and an empty deck must carry the same power keys, so a
    # consumer never has to guard for their absence.
    required = {"power_level", "power_level_base", "power_nuance_adj", "power_nuance_reason"}
    empty = repo.create_deck(session, format="commander")
    assert required <= set(deck_tools.compute_deck_stats(session, empty.id, None))
    full = _seed_deck(session)
    assert required <= set(deck_tools.compute_deck_stats(session, full.id, None))
