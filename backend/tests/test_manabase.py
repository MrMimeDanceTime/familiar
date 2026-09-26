from app.pipeline import manabase
from app.pipeline.selection import Pick, Selection
from tests.test_pipeline_jev import _card


def _land(name):
    card = _card(name)
    card.type_line = "Land"
    return card


def _snapshot(cards):
    return {"commander": "Cmd", "cards": [{"name": "Cmd", "type_line": "Legendary Creature"}, *cards]}


def test_split_basics_sums_to_the_count_and_follows_pips():
    split = manabase.split_basics(17, {"W": 0.5, "B": 0.3, "R": 0.2})
    assert sum(split.values()) == 17
    assert split["W"] > split["B"] > split["R"]


def test_fill_reaches_the_land_target_with_ranked_nonbasics_and_basics():
    shaped = [_land("Godless Shrine"), _card("Arcane Signet"), _land("Isolated Chapel"), _land("Plains")]
    selection = Selection(picks=[Pick("Godless Shrine", "shock")], raw={"judgments": [
        {"name": "Arcane Signet"}, {"name": "Godless Shrine"}, {"name": "Plains"}, {"name": "Isolated Chapel"},
    ]})
    spells = [{"name": f"Spell {i}", "type_line": "Instant", "mana_cost": "{W}{B}"} for i in range(4)]
    result = manabase.fill(selection, shaped, _snapshot(spells), frozenset("WB"))
    names = [p.name for p in result.picks]
    assert "Arcane Signet" not in names  # not a land
    assert names[:2] == ["Godless Shrine", "Isolated Chapel"]  # ranked order, basics excluded here
    target = manabase.land_gap(_snapshot(spells))
    assert sum(p.quantity for p in result.picks) == target
    basics = {p.name: p.quantity for p in result.picks if p.name in ("Plains", "Swamp")}
    assert set(basics) == {"Plains", "Swamp"}


def test_nonbasics_are_capped_by_colour_count():
    shaped = [_land(f"Dual {i}") for i in range(30)]
    selection = Selection(picks=[], raw={"judgments": [{"name": c.name} for c in shaped]})
    result = manabase.fill(selection, shaped, _snapshot([]), frozenset("B"))
    nonbasic = [p for p in result.picks if p.name.startswith("Dual")]
    assert len(nonbasic) == manabase.NONBASIC_CAP[1]


def test_a_deck_at_its_land_target_gets_no_lands():
    lands = [{"name": "Swamp", "type_line": "Basic Land — Swamp", "quantity": 40}]
    result = manabase.fill(Selection(picks=[]), [], _snapshot(lands), frozenset("B"))
    assert result.picks == []


def test_land_requests_are_recognised():
    assert manabase.is_land_request("fill out the manabase")
    assert manabase.is_land_request("add lands")
    assert not manabase.is_land_request("more card draw")
