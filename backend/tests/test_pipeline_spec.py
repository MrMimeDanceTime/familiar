import json

import pytest

from app.pipeline.spec import (
    QuerySpec,
    enforce_query,
    generate_query_spec,
    identity_token,
    parse_spec,
)

RAKDOS = frozenset({"B", "R"})
COLORLESS = frozenset()


class FakeProvider:
    """Returns a canned JSON string, records the prompts it was given."""

    def __init__(self, payload):
        self._payload = payload if isinstance(payload, str) else json.dumps(payload)
        self.calls = []

    def complete_json(self, system, user, *, model=None, thinking=True, reasoning_effort=None):
        self.calls.append({"system": system, "user": user, "model": model})
        return self._payload


# ── identity_token ─────────────────────────────────────────────────────────

def test_identity_token_uses_nickname_when_available():
    assert identity_token(RAKDOS) == "rakdos"


def test_identity_token_colorless_is_c():
    assert identity_token(COLORLESS) == "c"


def test_identity_token_wubrg_order_for_odd_combos():
    # {W, B} has a nickname (orzhov); a comboless set would fall back to letters.
    assert identity_token(frozenset({"W", "B"})) == "orzhov"
    assert identity_token(frozenset({"W", "U", "B", "R", "G"})) == "wubrg"


# ── enforce_query ──────────────────────────────────────────────────────────

def test_enforce_appends_missing_identity_and_format():
    out = enforce_query("otag:ramp mv<=3", RAKDOS)
    assert "id<=rakdos" in out
    assert "f:commander" in out


def test_enforce_leaves_present_filters_alone():
    q = "id<=rakdos otag:removal f:commander"
    assert enforce_query(q, RAKDOS) == q


def test_enforce_recognizes_bare_letter_identity_and_colon_form():
    # id:br and format:commander are valid alternate spellings; don't double-add.
    q = "id<=br o:draw format:commander"
    out = enforce_query(q, RAKDOS)
    assert out.count("id<") + out.count("id:") == 1
    assert out.lower().count("commander") == 1


# ── parse_spec ─────────────────────────────────────────────────────────────

def test_parse_spec_happy_path():
    payload = {"intent_summary": "cheap removal", "queries": ["otag:removal mv<=2"]}
    spec = parse_spec(json.dumps(payload), RAKDOS)
    assert isinstance(spec, QuerySpec)
    assert spec.intent_summary == "cheap removal"
    assert spec.queries == ["otag:removal mv<=2 id<=rakdos f:commander"]


def test_parse_spec_coerces_single_string_to_list():
    spec = parse_spec(json.dumps({"queries": "otag:ramp"}), RAKDOS)
    assert len(spec.queries) == 1


def test_parse_spec_drops_blank_and_duplicate_queries():
    payload = {"queries": ["otag:ramp", "", "otag:ramp", "   "]}
    spec = parse_spec(json.dumps(payload), RAKDOS)
    # blank dropped; the two "otag:ramp" collapse after enforcement
    assert len(spec.queries) == 1


def test_parse_spec_caps_query_count():
    payload = {"queries": [f"o:x{i}" for i in range(20)]}
    spec = parse_spec(json.dumps(payload), RAKDOS, max_queries=3)
    assert len(spec.queries) == 3


def test_parse_spec_rejects_non_json():
    with pytest.raises(ValueError):
        parse_spec("not json at all", RAKDOS)


def test_parse_spec_rejects_zero_queries():
    with pytest.raises(ValueError):
        parse_spec(json.dumps({"queries": []}), RAKDOS)


def test_parse_spec_missing_summary_defaults_empty():
    spec = parse_spec(json.dumps({"queries": ["o:draw"]}), RAKDOS)
    assert spec.intent_summary == ""


# ── generate_query_spec (with fake provider) ───────────────────────────────

def test_generate_query_spec_end_to_end_offline():
    provider = FakeProvider({"intent_summary": "ramp", "queries": ["otag:ramp mv<=3"]})
    spec = generate_query_spec(provider, "I want cheap ramp", RAKDOS, model="deepseek-v4-flash")
    assert spec.queries == ["otag:ramp mv<=3 id<=rakdos f:commander"]
    # the identity token made it into the prompt, and the model override passed through
    assert "id<=rakdos" in provider.calls[0]["system"]
    assert provider.calls[0]["model"] == "deepseek-v4-flash"


def test_parse_spec_repairs_an_unescaped_backslash_in_a_query():
    r"""Seen live: the model left a regex backslash unescaped and the whole
    suggestion failed on 'Invalid \escape'."""
    raw = r'{"queries": ["o:/deals \d+ damage/"], "intent_summary": "burn"}'
    spec = parse_spec(raw, frozenset("R"))
    assert r"\d+" in spec.queries[0]


def test_parse_spec_still_rejects_json_it_cannot_repair():
    import pytest

    with pytest.raises(ValueError):
        parse_spec('{"queries": [', frozenset("R"))
