"""Deck plan tests.

The plan is what a deck is TRYING to be. Before it, the only view of a deck was
its card list, so every suggestion had to re-infer the plan from names and a
suggestion aimed at an already-filled role looked as good as one filling a gap.

The two behaviours that matter here: a card counts toward every role it
satisfies (not one "primary" bucket), and the rendered block leads with what the
deck is short on.
"""

import pytest

from app.deckplan import (
    DEFAULT_TARGETS,
    build_plan,
    count_roles,
    render_card_rationale,
    render_plan,
)


def _card(name, tags=None, type_line="Creature — Human", quantity=1, notes=None):
    return {
        "name": name,
        "tags": tags or [],
        "type_line": type_line,
        "quantity": quantity,
        "notes": notes,
    }


def _snapshot(cards, **kwargs):
    base = {
        "commander": "Korvold, Fae-Cursed King",
        "cards": [_card("Korvold, Fae-Cursed King")] + cards,
    }
    base.update(kwargs)
    return base


# ── Role counting ────────────────────────────────────────────────────────


def test_counts_roles_from_tags():
    cards = [
        _card("Sol Ring", tags=["mana-rock"], type_line="Artifact"),
        _card("Rhystic Study", tags=["repeatable-pure-draw"], type_line="Enchantment"),
    ]
    counts = count_roles(cards)
    assert counts["ramp"] == 1
    assert counts["draw"] == 1


def test_land_counted_from_type_line_without_tags():
    """A card with no tags still counts as a land from its type line."""
    counts = count_roles([_card("Wastes", tags=[], type_line="Basic Land")])
    assert counts["land"] == 1


def test_card_counts_toward_every_role_it_satisfies():
    """A land that also draws is genuinely both. Forcing one 'primary' role
    would undercount the deck's real ramp and draw density."""
    cards = [_card(
        "Mikokoro, Center of the Sea",
        tags=["repeatable-pure-draw"],
        type_line="Legendary Land",
    )]
    counts = count_roles(cards)
    assert counts["land"] == 1
    assert counts["draw"] == 1


def test_quantity_is_respected():
    counts = count_roles([
        _card("Forest", tags=[], type_line="Basic Land", quantity=10)
    ])
    assert counts["land"] == 10


def test_unknown_roles_are_ignored():
    counts = count_roles([_card("Weird", tags=["alliteration"])])
    assert sum(counts.values()) == 0


# ── Plan assembly ────────────────────────────────────────────────────────


def test_defaults_used_when_no_targets_set():
    plan = build_plan(_snapshot([]))
    assert plan.targets == DEFAULT_TARGETS


def test_explicit_targets_override_defaults():
    plan = build_plan(_snapshot([], role_targets={"ramp": 12}))
    assert plan.targets["ramp"] == 12
    assert plan.targets["land"] == DEFAULT_TARGETS["land"]


def test_unknown_target_role_is_ignored():
    plan = build_plan(_snapshot([], role_targets={"nonsense": 5}))
    assert "nonsense" not in plan.targets


def test_commander_excluded_from_counts():
    """The commander is a card in the list but is not part of the 99."""
    plan = build_plan(_snapshot([]))
    assert plan.total_cards == 0


def test_gap_reports_shortfall():
    cards = [_card("Sol Ring", tags=["mana-rock"], type_line="Artifact")]
    plan = build_plan(_snapshot(cards, role_targets={"ramp": 10}))
    ramp = next(g for g in plan.gaps if g.role == "ramp")
    assert ramp.current == 1
    assert ramp.gap == 9
    assert ramp.satisfied is False


def test_gap_satisfied_when_at_or_over_target():
    cards = [_card(f"Rock{i}", tags=["mana-rock"], type_line="Artifact") for i in range(11)]
    plan = build_plan(_snapshot(cards, role_targets={"ramp": 10}))
    ramp = next(g for g in plan.gaps if g.role == "ramp")
    assert ramp.satisfied is True
    assert ramp.gap == -1


def test_unmet_is_ordered_worst_first():
    """Worst gap first — that is the build order a suggestion should follow."""
    cards = [_card("Sol Ring", tags=["mana-rock"], type_line="Artifact")]
    plan = build_plan(_snapshot(
        cards, role_targets={"land": 36, "ramp": 10, "draw": 10, "removal": 8}
    ))
    assert [g.role for g in plan.unmet][0] == "land"


def test_has_plan_false_without_themes_or_notes():
    assert build_plan(_snapshot([])).has_plan() is False


def test_has_plan_true_with_themes():
    plan = build_plan(_snapshot([], themes=["treasure sacrifice"]))
    assert plan.has_plan() is True


# ── Rendering ────────────────────────────────────────────────────────────


def test_render_leads_with_what_is_missing():
    cards = [_card("Sol Ring", tags=["mana-rock"], type_line="Artifact")]
    text = render_plan(build_plan(_snapshot(cards)))
    assert "Still needs:" in text
    assert "land" in text


def test_render_reports_satisfied_plan():
    cards = (
        [_card(f"Land{i}", tags=[], type_line="Basic Land") for i in range(36)]
        + [_card(f"Rock{i}", tags=["mana-rock"], type_line="Artifact") for i in range(10)]
        + [_card(f"Draw{i}", tags=["pure-draw"], type_line="Enchantment") for i in range(10)]
        + [_card(f"Kill{i}", tags=["spot-removal"], type_line="Instant") for i in range(8)]
    )
    text = render_plan(build_plan(_snapshot(cards)))
    assert "nothing" in text.lower()


def test_render_includes_themes_and_notes():
    plan = build_plan(_snapshot(
        [], themes=["treasure", "aristocrats"], plan_notes="Keep the curve low."
    ))
    text = render_plan(plan)
    assert "treasure" in text
    assert "Keep the curve low." in text


def test_render_card_rationale_lists_only_explained_cards():
    cards = [
        _card("Sol Ring", notes="Fastest possible ramp."),
        _card("Mystery Card", notes=None),
    ]
    text = render_card_rationale(cards)
    assert "Sol Ring: Fastest possible ramp." in text
    assert "Mystery Card" not in text


def test_render_card_rationale_empty_when_nothing_explained():
    assert render_card_rationale([_card("A"), _card("B")]) == ""


def test_render_card_rationale_respects_limit():
    cards = [_card(f"C{i}", notes=f"reason {i}") for i in range(50)]
    text = render_card_rationale(cards, limit=5)
    assert len(text.splitlines()) == 6  # header + 5


# ── Integration with the selection context ───────────────────────────────


def test_selection_context_carries_the_plan():
    """The plan has to reach stage 4 or it changes nothing about suggestions."""
    from app.pipeline.service import _render_deck_context

    snapshot = _snapshot(
        [_card("Sol Ring", tags=["mana-rock"], type_line="Artifact",
               notes="Fastest possible ramp.")],
        themes=["treasure sacrifice"],
    )
    text = _render_deck_context(snapshot)
    assert "treasure sacrifice" in text
    assert "Still needs:" in text
    assert "Fastest possible ramp." in text


# ── Rationale persistence ────────────────────────────────────────────────


def test_approving_a_proposal_stores_its_reasoning_on_the_card(tmp_path):
    """Stage 4 already writes a justification per pick and it was discarded at
    approval, so the deck could never answer "why is this card here" without
    another LLM call. Approval must carry it onto the card."""
    from unittest.mock import patch

    from sqlmodel import Session, SQLModel, create_engine

    from app.db import repository as repo
    from app.db.models import DeckProposal

    engine = create_engine(f"sqlite:///{tmp_path / 'plan.db'}")
    SQLModel.metadata.create_all(engine)

    fake_card = {
        "name": "Sol Ring",
        "oracle_id": "oid-sol-ring",
        "cmc": 1.0,
        "color_identity": [],
        "type_line": "Artifact",
        "oracle_text": "{T}: Add {C}{C}.",
    }

    with Session(engine) as session:
        deck = repo.create_deck(session, name="Test")
        conversation = repo.create_conversation(session)
        proposal = DeckProposal(
            conversation_id=conversation.id,
            deck_id=deck.id,
            action="add",
            card_name="Sol Ring",
            quantity=1,
            reasoning="Fastest ramp available, and it fixes nothing so it always fits.",
        )
        session.add(proposal)
        session.commit()
        session.refresh(proposal)

        with patch("app.tools.deck_tools.get_scryfall_client") as mock_client, \
             patch("app.tools.deck_tools.get_tags_for_card", return_value=["mana-rock"]):
            mock_client.return_value.named.return_value = fake_card
            repo.apply_proposal(session, proposal.id)

        snapshot = repo.deck_snapshot(session, deck.id)

    card = next(c for c in snapshot["cards"] if c["name"] == "Sol Ring")
    assert "Fastest ramp available" in (card["notes"] or "")


def test_deck_snapshot_exposes_plan_fields(tmp_path):
    from sqlmodel import Session, SQLModel, create_engine

    from app.db import repository as repo

    engine = create_engine(f"sqlite:///{tmp_path / 'plan2.db'}")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        deck = repo.create_deck(session, name="Test")
        repo.update_deck(
            session, deck.id,
            role_targets={"ramp": 12},
            themes=["treasure"],
            plan_notes="Low curve.",
        )
        snapshot = repo.deck_snapshot(session, deck.id)

    assert snapshot["role_targets"] == {"ramp": 12}
    assert snapshot["themes"] == ["treasure"]
    assert snapshot["plan_notes"] == "Low curve."


# ── Structured denial (the learning loop's training data) ────────────────


def test_denial_reason_is_recorded(tmp_path):
    """A bare boolean is a weak signal. "wrong slot" and "too expensive" say
    different things about what to suggest next; only those generalise."""
    from sqlmodel import Session, SQLModel, create_engine

    from app.db import repository as repo
    from app.db.models import DeckProposal

    engine = create_engine(f"sqlite:///{tmp_path / 'deny.db'}")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        deck = repo.create_deck(session, name="Test")
        conversation = repo.create_conversation(session)
        proposal = DeckProposal(
            conversation_id=conversation.id, deck_id=deck.id, action="add",
            card_name="Sol Ring", quantity=1, reasoning="ramp",
        )
        session.add(proposal)
        session.commit()
        session.refresh(proposal)

        assert repo.deny_proposal(session, proposal.id, reason="Too generic") is True
        session.refresh(proposal)

    assert proposal.status == "denied"
    assert proposal.denial_reason == "Too generic"


def test_denial_without_a_reason_still_works(tmp_path):
    from sqlmodel import Session, SQLModel, create_engine

    from app.db import repository as repo
    from app.db.models import DeckProposal

    engine = create_engine(f"sqlite:///{tmp_path / 'deny2.db'}")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        deck = repo.create_deck(session, name="Test")
        conversation = repo.create_conversation(session)
        proposal = DeckProposal(
            conversation_id=conversation.id, deck_id=deck.id, action="add",
            card_name="Sol Ring", quantity=1, reasoning="ramp",
        )
        session.add(proposal)
        session.commit()
        session.refresh(proposal)

        assert repo.deny_proposal(session, proposal.id) is True
        session.refresh(proposal)

    assert proposal.status == "denied"
    assert proposal.denial_reason is None


def test_brain_map_scores_ride_onto_the_proposal():
    """The review surface can only show why a card scored well if the score
    travels with the proposal — the pool it was scored against is gone by the
    time the player reviews."""
    from app.pipeline.selection import Pick, Selection
    from app.pipeline.validate import selection_to_changes

    selection = Selection(picks=[Pick("Mayhem Devil", "pings on sacrifice")])
    changes = selection_to_changes(
        selection, None,
        {"mayhem devil": {"total": 0.78, "consensus": 0.85, "mechanical": 0.75,
                          "personal": 0.0, "explain": "payoff for sacrifice"}},
    )
    assert changes[0]["scores"]["total"] == 0.78


def test_missing_score_does_not_break_the_change():
    from app.pipeline.selection import Pick, Selection
    from app.pipeline.validate import selection_to_changes

    changes = selection_to_changes(Selection(picks=[Pick("Unknown", "why")]), None, {})
    assert "scores" not in changes[0]


# ── Targets must agree with the power formula ────────────────────────────
#
# These were two unrelated systems. The plan's targets were rules of thumb I
# invented; the power level is computed by a banded formula in deck_tools. They
# disagreed in a way that mattered: a deck hitting the old defaults exactly
# (36 land / 10 ramp / 10 draw / 8 removal) scored power 6 and could NOT reach
# 7 by any curve. A player asking for a 7 got a plan aiming at a 6.
#
# Two bands caused it: 36 lands scores +0.5 where the formula's sweet spot is
# 33-35 at +1.0, and 8 removal scores +0.5 where 10 earns +1.0.


import pytest as _pytest  # noqa: E402 - section import

from app.deckplan import DEFAULT_POWER, TARGETS_BY_POWER, targets_for_power  # noqa: E402 - section import


@_pytest.mark.parametrize("level", sorted(TARGETS_BY_POWER))
def test_targets_actually_score_their_power_level(level):
    """The contract: hitting a row's targets scores that row's level.

    This is the test that would have caught the original mismatch.
    """
    from app.tools.deck_tools import _estimate_power_level

    t = TARGETS_BY_POWER[level]
    scored, factors = _estimate_power_level(
        2.8, t["land"], t["ramp"], t["draw"], t["removal"], 100
    )
    assert scored == level, (
        f"power {level} targets {t} score {scored}, not {level}. "
        f"Factors: {factors}"
    )


def test_power_seven_targets_reach_bracket_three():
    """Bracket 3 without a Game Changer needs mv <= 2.5, ramp >= 10, and
    interaction >= 10. The old 8-removal target missed that by two cards, so a
    "Bracket 3 / power 7" request could hit neither."""
    from app.tools.deck_tools import _estimate_bracket

    t = TARGETS_BY_POWER[7]
    bracket, _ = _estimate_bracket(
        set(), 2.5, t["land"], t["ramp"], t["removal"], {}, 100
    )
    assert bracket >= 3


def test_higher_power_never_asks_for_less():
    """A monotonic ladder: a higher power level must not require fewer cards in
    any role, or the plan would tell a player to cut to level up."""
    levels = sorted(TARGETS_BY_POWER)
    for lower, higher in zip(levels, levels[1:]):
        for role in ("ramp", "draw", "removal"):
            assert TARGETS_BY_POWER[higher][role] >= TARGETS_BY_POWER[lower][role], (
                f"{role}: power {higher} asks for less than power {lower}"
            )


def test_targets_for_power_parses_free_text():
    """power_level is set from conversation, not a picker, so it arrives as
    whatever the model wrote."""
    expected = TARGETS_BY_POWER[7]
    for value in ("7", "~7", "7-8", "power 7", 7):
        assert targets_for_power(value) == expected, value


def test_targets_for_power_falls_back_without_a_level():
    assert targets_for_power(None) == TARGETS_BY_POWER[DEFAULT_POWER]
    assert targets_for_power("unset") == TARGETS_BY_POWER[DEFAULT_POWER]


def test_out_of_range_power_clamps_to_nearest():
    assert targets_for_power(11) == TARGETS_BY_POWER[max(TARGETS_BY_POWER)]
    assert targets_for_power(1) == TARGETS_BY_POWER[min(TARGETS_BY_POWER)]


def test_plan_uses_the_decks_stated_power_level():
    """The gap view must aim at the player's goal, not a fixed guess."""
    plan = build_plan(_snapshot([], power_level="7"))
    assert plan.targets == TARGETS_BY_POWER[7]
    assert plan.power_level == 7


def test_explicit_role_targets_still_override():
    """A deck that states its own numbers keeps them, power level or not."""
    plan = build_plan(_snapshot([], power_level="7", role_targets={"land": 38}))
    assert plan.targets["land"] == 38
    assert plan.targets["ramp"] == TARGETS_BY_POWER[7]["ramp"]


def test_rendered_plan_explains_where_targets_come_from():
    """The model previously treated targets as arbitrary and negotiated with
    them; it should know missing them means scoring below the stated level."""
    text = render_plan(build_plan(_snapshot([], power_level="7")))
    assert "power-level scorer" in text or "power 7" in text


# ── Singleton enforcement on the direct path ─────────────────────────────
#
# Found by building a real deck: two copies each of Ashnod's Altar and
# Phyrexian Altar reached the list. The suggestion pipeline filters owned cards
# during retrieval, but propose_deck_changes had no such check, so a card
# approved in an earlier batch could be proposed and approved again.


def _proposing_engine(tmp_path, name):
    from sqlmodel import SQLModel, create_engine

    engine = create_engine(f"sqlite:///{tmp_path / name}")
    SQLModel.metadata.create_all(engine)
    return engine


def test_cannot_propose_a_card_already_in_the_deck(tmp_path):
    from unittest.mock import patch

    from sqlmodel import Session

    from app.db import repository as repo
    from app.tools.deck_tools import propose_deck_changes

    engine = _proposing_engine(tmp_path, "dupe.db")
    with Session(engine) as session:
        deck = repo.create_deck(session, name="T", format="commander")
        convo = repo.create_conversation(session)
        with patch("app.tools.deck_tools.get_scryfall_client") as client, \
             patch("app.tools.deck_tools.get_tags_for_card", return_value=[]):
            client.return_value.named.side_effect = lambda n, **k: {
                "name": n, "oracle_id": f"o-{n}", "cmc": 2.0,
                "color_identity": [], "type_line": "Artifact", "oracle_text": "x",
            }
            first = propose_deck_changes(
                session, deck.id, "b",
                [{"action": "add", "card_name": "Ashnod's Altar", "reasoning": "x"}],
                conversation_id=convo.id,
            )
            repo.apply_proposal(session, first["proposals"][0]["id"])

            with pytest.raises(ValueError, match="already in the deck"):
                propose_deck_changes(
                    session, deck.id, "b2",
                    [{"action": "add", "card_name": "Ashnod's Altar",
                      "reasoning": "x"}],
                    conversation_id=convo.id,
                )


def test_basic_lands_can_still_stack(tmp_path):
    """The singleton rule exempts basics, and a deck runs many."""
    from unittest.mock import patch

    from sqlmodel import Session

    from app.db import repository as repo
    from app.tools.deck_tools import propose_deck_changes

    engine = _proposing_engine(tmp_path, "basics.db")
    with Session(engine) as session:
        deck = repo.create_deck(session, name="T", format="commander")
        convo = repo.create_conversation(session)
        with patch("app.tools.deck_tools.get_scryfall_client") as client, \
             patch("app.tools.deck_tools.get_tags_for_card", return_value=[]):
            client.return_value.named.side_effect = lambda n, **k: {
                "name": n, "oracle_id": f"o-{n}", "cmc": 0.0,
                "color_identity": [], "type_line": "Basic Land", "oracle_text": "",
            }
            first = propose_deck_changes(
                session, deck.id, "b",
                [{"action": "add", "card_name": "Swamp", "reasoning": "x"}],
                conversation_id=convo.id,
            )
            repo.apply_proposal(session, first["proposals"][0]["id"])

            again = propose_deck_changes(
                session, deck.id, "b2",
                [{"action": "add", "card_name": "Swamp", "reasoning": "x"}],
                conversation_id=convo.id,
            )
    assert again["proposals"]


def test_a_bad_change_sinks_the_whole_batch_not_half_of_it(tmp_path):
    """Proposals used to commit one at a time, so a banned card in position
    three left two pending rows the engine never learned about."""
    from unittest.mock import patch

    from sqlmodel import Session

    from app.db import repository as repo
    from app.tools.deck_tools import propose_deck_changes

    engine = _proposing_engine(tmp_path, "atomic.db")
    with Session(engine) as session:
        deck = repo.create_deck(session, name="T", format="commander")
        convo = repo.create_conversation(session)
        with patch("app.tools.deck_tools.get_scryfall_client") as client:
            client.return_value.named.side_effect = lambda n, **k: {"name": n}
            with pytest.raises(ValueError, match="banned"):
                propose_deck_changes(
                    session, deck.id, "b",
                    [
                        {"action": "add", "card_name": "Sol Ring", "reasoning": "x"},
                        {"action": "add", "card_name": "Arcane Signet", "reasoning": "x"},
                        {"action": "add", "card_name": "Mana Crypt", "reasoning": "x"},
                    ],
                    conversation_id=convo.id,
                )
        assert repo.list_proposals(session, convo.id) == []


def test_setting_a_commander_already_in_the_list_keeps_one_copy(tmp_path):
    """The upsert adds an explicit quantity to an existing stack, which is
    right for a merge import and wrong for a commander: a decklist that named
    its commander plus an approved set_commander produced two copies."""
    from unittest.mock import patch

    from sqlmodel import Session

    from app.db import repository as repo
    from app.tools.deck_tools import deck_set_commander

    engine = _proposing_engine(tmp_path, "cmdr.db")
    with Session(engine) as session:
        deck = repo.create_deck(session, name="T", format="commander")
        repo.add_deck_card(session, deck.id, "Korvold, Fae-Cursed King", quantity=1)
        with patch("app.tools.deck_tools.get_scryfall_client") as client, \
             patch("app.tools.deck_tools.get_tags_for_card", return_value=[]):
            client.return_value.named.return_value = {
                "name": "Korvold, Fae-Cursed King", "cmc": 5.0,
                "color_identity": ["B", "R", "G"], "oracle_id": "o-korvold",
                "type_line": "Legendary Creature — Dragon Noble", "oracle_text": "",
            }
            snapshot = deck_set_commander(session, deck.id, "Korvold, Fae-Cursed King")

    korvold = next(c for c in snapshot["cards"] if c["name"] == "Korvold, Fae-Cursed King")
    assert korvold["quantity"] == 1
    assert snapshot["total_cards"] == 1
