"""deck_set_plan: the planning beat.

The chat flow already had two of its three beats — purposeful batches and the
closing notecard. What was missing was the first: agreeing a direction and
recording it somewhere durable. Without it every suggestion started cold and
had to re-infer the strategy from a list of card names.

This is a model-facing tool, so a plausible-but-wrong argument is a normal
input rather than an exceptional one. Everything below pins validation.
"""

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.db import repository as repo
from app.tools.deck_tools import deck_get_current, deck_set_plan


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'plan.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def deck(session):
    return repo.create_deck(session, name="Test", commander="Rin and Seri, Inseparable")


def test_records_themes_and_notes(session, deck):
    snap = deck_set_plan(
        session, deck.id,
        themes=["cat and dog tribal", "token swarm"],
        plan_notes="Go wide, then Craterhoof.",
    )
    assert snap["themes"] == ["cat and dog tribal", "token swarm"]
    assert snap["plan_notes"] == "Go wide, then Craterhoof."


def test_records_role_targets(session, deck):
    snap = deck_set_plan(session, deck.id, role_targets={"land": 34, "ramp": 12})
    assert snap["role_targets"] == {"land": 34, "ramp": 12}


def test_unknown_role_is_dropped(session, deck):
    """A hallucinated role name must not become a target nothing counts toward."""
    snap = deck_set_plan(
        session, deck.id, role_targets={"land": 34, "wincons": 5, "vibes": 2}
    )
    assert snap["role_targets"] == {"land": 34}


def test_role_targets_coerced_to_non_negative_ints(session, deck):
    snap = deck_set_plan(
        session, deck.id, role_targets={"land": 36.7, "ramp": -3, "draw": True}
    )
    assert snap["role_targets"] == {"land": 36, "ramp": 0}


def test_off_meta_is_clamped(session, deck):
    assert deck_set_plan(session, deck.id, off_meta=5.0)["off_meta"] == 1.0
    assert deck_set_plan(session, deck.id, off_meta=-2.0)["off_meta"] == 0.0


def test_blank_themes_are_dropped(session, deck):
    snap = deck_set_plan(session, deck.id, themes=["tribal", "", "  ", None, 7])
    assert snap["themes"] == ["tribal"]


def test_partial_update_preserves_other_fields(session, deck):
    """The model sets a direction early and refines targets later; the second
    call must not clobber the first."""
    deck_set_plan(session, deck.id, themes=["tribal"], plan_notes="Go wide.")
    snap = deck_set_plan(session, deck.id, role_targets={"land": 34})

    assert snap["themes"] == ["tribal"]
    assert snap["plan_notes"] == "Go wide."
    assert snap["role_targets"] == {"land": 34}


def test_empty_role_targets_object_does_not_wipe_existing(session, deck):
    deck_set_plan(session, deck.id, role_targets={"land": 34})
    snap = deck_set_plan(session, deck.id, role_targets={})
    assert snap["role_targets"] == {"land": 34}


# ── The plan coming back on a deck read ──────────────────────────────────


def test_deck_get_current_exposes_the_gap_view(session, deck):
    """The model must read "still needs" rather than deriving it from targets
    and a 99-card list — arithmetic it does unreliably and would redo each turn.
    """
    deck_set_plan(session, deck.id, role_targets={"land": 36, "ramp": 10})
    plan = deck_get_current(session, deck.id)["plan"]

    assert plan["role_counts"]["land"] == {"current": 0, "target": 36}
    assert plan["still_needs"]["land"] == 36


def test_still_needs_is_ordered_worst_first(session, deck):
    plan = deck_get_current(session, deck.id)["plan"]
    assert list(plan["still_needs"])[0] == "land"


def test_plan_reports_whether_it_was_set(session, deck):
    assert deck_get_current(session, deck.id)["plan"]["is_set"] is False
    deck_set_plan(session, deck.id, themes=["tribal"])
    assert deck_get_current(session, deck.id)["plan"]["is_set"] is True


def test_plan_carries_the_overlap_caveat(session, deck):
    """Role counts overlap by design, and "removal 20/8" reads as bloat without
    saying so."""
    plan = deck_get_current(session, deck.id)["plan"]
    assert "every role it fills" in plan["counts_overlap"]


def test_off_meta_reaches_the_plan_view(session, deck):
    deck_set_plan(session, deck.id, off_meta=0.8)
    assert deck_get_current(session, deck.id)["plan"]["off_meta"] == 0.8


# ── Registration ─────────────────────────────────────────────────────────


def test_tool_is_registered_and_dispatchable():
    from app.tools.dispatch import (
        DECK_MUTATION_TOOLS,
        DECK_SCOPED_TOOLS,
        SESSION_TOOLS,
    )
    from app.tools.schemas import TOOL_SPECS

    assert any(spec.name == "deck_set_plan" for spec in TOOL_SPECS)
    assert "deck_set_plan" in SESSION_TOOLS
    # Deck-scoped so the engine injects deck_id, and a mutation so it is
    # recognised as changing deck state rather than only reading it.
    assert "deck_set_plan" in DECK_SCOPED_TOOLS
    assert "deck_set_plan" in DECK_MUTATION_TOOLS


def test_tool_requires_only_deck_id():
    """Every plan field is optional so the model can record a direction before
    it has opinions about role counts."""
    from app.tools.schemas import TOOL_SPECS

    spec = next(s for s in TOOL_SPECS if s.name == "deck_set_plan")
    assert spec.parameters["required"] == ["deck_id"]


# ── Power level drives the targets ───────────────────────────────────────


def test_records_power_level(session, deck):
    """Nothing could set this field before, so the targets always defaulted —
    a player asking for a 7 got a plan aimed at a 6."""
    snap = deck_set_plan(session, deck.id, power_level="7")
    assert snap["power_level"] == "7"


def test_power_level_changes_the_targets(session, deck):
    from app import deckplan

    deck_set_plan(session, deck.id, power_level="7")
    plan = deckplan.build_plan(deck_get_current(session, deck.id))

    assert plan.targets == deckplan.TARGETS_BY_POWER[7]
    assert plan.power_level == 7


def test_power_level_is_in_the_tool_schema():
    """The model can only set what the schema exposes."""
    from app.tools.schemas import TOOL_SPECS

    spec = next(s for s in TOOL_SPECS if s.name == "deck_set_plan")
    assert "power_level" in spec.parameters["properties"]
