import json

import pytest

from app.pipeline.selection import Selection, parse_selection, select
from app.pipeline.shaping import ShapedCard


def _card(name, legal=True, oracle_id=None):
    return ShapedCard(
        name=name,
        oracle_id=oracle_id or name.lower(),
        mana_cost="{1}{B}",
        cmc=2.0,
        type_line="Instant",
        oracle_text="Do a thing.",
        color_identity=["B"],
        keywords=[],
        power=None,
        toughness=None,
        loyalty=None,
        rarity="common",
        edhrec_rank=100,
        legal_in_deck=legal,
        illegal_reasons=[] if legal else ["off identity"],
    )


POOL = [_card("Demonic Tutor"), _card("Sign in Blood"), _card("Bad Card", legal=False)]


class FakeProvider:
    def __init__(self, payload):
        self._payload = payload if isinstance(payload, str) else json.dumps(payload)
        self.calls = []

    def complete_json(self, system, user, *, model=None, thinking=True, reasoning_effort=None):
        self.calls.append({"system": system, "user": user, "model": model})
        return self._payload


# ── parse_selection ────────────────────────────────────────────────────────

def test_parse_selection_happy_path():
    payload = {
        "summary": "Two efficient black staples.",
        "picks": [
            {"name": "Demonic Tutor", "reason": "best tutor"},
            {"name": "Sign in Blood", "reason": "cheap draw"},
        ],
        "cuts": [{"name": "Some Filler", "reason": "underperforms"}],
    }
    sel = parse_selection(json.dumps(payload), POOL)
    assert isinstance(sel, Selection)
    assert [p.name for p in sel.picks] == ["Demonic Tutor", "Sign in Blood"]
    assert sel.picks[0].reason == "best tutor"
    assert sel.cuts[0].name == "Some Filler"
    assert sel.summary.startswith("Two efficient")


def test_parse_selection_drops_cards_not_in_pool():
    payload = {"picks": [{"name": "Black Lotus"}, {"name": "Sign in Blood"}]}
    sel = parse_selection(json.dumps(payload), POOL)
    assert [p.name for p in sel.picks] == ["Sign in Blood"]  # hallucination dropped


def test_parse_selection_drops_illegal_pool_cards():
    # "Bad Card" is in the pool but marked illegal — must not be pickable.
    payload = {"picks": [{"name": "Bad Card"}, {"name": "Demonic Tutor"}]}
    sel = parse_selection(json.dumps(payload), POOL)
    assert [p.name for p in sel.picks] == ["Demonic Tutor"]


def test_parse_selection_canonicalizes_name_casing():
    payload = {"picks": [{"name": "demonic TUTOR", "reason": "x"}]}
    sel = parse_selection(json.dumps(payload), POOL)
    assert sel.picks[0].name == "Demonic Tutor"  # pool spelling wins


def test_parse_selection_dedupes_picks():
    payload = {"picks": [{"name": "Sign in Blood"}, {"name": "sign in blood"}]}
    sel = parse_selection(json.dumps(payload), POOL)
    assert len(sel.picks) == 1


def test_parse_selection_accepts_bare_string_picks():
    payload = {"picks": ["Demonic Tutor"]}
    sel = parse_selection(json.dumps(payload), POOL)
    assert sel.picks[0].name == "Demonic Tutor"
    assert sel.picks[0].reason == ""


def test_parse_selection_caps_picks():
    pool = [_card(f"C{i}") for i in range(20)]
    payload = {"picks": [{"name": f"C{i}"} for i in range(20)]}
    sel = parse_selection(json.dumps(payload), pool, max_picks=5)
    assert len(sel.picks) == 5


def test_parse_selection_empty_picks_is_allowed():
    # Unlike stage 1, an empty selection is a valid answer ("nothing fits").
    sel = parse_selection(json.dumps({"picks": []}), POOL)
    assert sel.picks == []


def test_parse_selection_rejects_non_json():
    with pytest.raises(ValueError):
        parse_selection("<not json>", POOL)


def test_parse_selection_missing_fields_default():
    sel = parse_selection(json.dumps({"picks": [{"name": "Demonic Tutor"}]}), POOL)
    assert sel.summary == ""
    assert sel.cuts == []


# ── select (with fake provider) ────────────────────────────────────────────

def test_select_end_to_end_offline():
    provider = FakeProvider({"summary": "s", "picks": [{"name": "Demonic Tutor"}]})
    sel = select(provider, POOL, "I need a tutor", model="deepseek-v4-pro")
    assert [p.name for p in sel.picks] == ["Demonic Tutor"]
    # the pool got rendered into the prompt, and the illegal card is marked
    assert "Demonic Tutor" in provider.calls[0]["user"]
    assert "ILLEGAL" in provider.calls[0]["user"]
    assert provider.calls[0]["model"] == "deepseek-v4-pro"


def test_prompt_carries_the_players_own_words():
    from app.pipeline.selection import build_prompt

    _system, user = build_prompt(POOL, "ramp package", player_message="cheap  artifact ramp, no green\nplease")
    assert "Player intent: ramp package\nPlayer's own words: cheap artifact ramp, no green please\n" in user
    _system, bare = build_prompt(POOL, "ramp package")
    assert "Player's own words" not in bare
