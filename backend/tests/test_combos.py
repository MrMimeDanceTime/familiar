"""The local combo table: Spellbook import, the two queries, and the bracket floor."""

import httpx
import pytest
import respx

from app.cards import combos, schema
from app.pipeline.shaping import DeckContext, render_pool, shape
from app.tools.deck_tools import _apply_combo_floor

SOURCE = "https://json.commanderspellbook.com/variants.json"


def _variant(vid, names, *, legal="legal", popularity=10, produces=("Infinite mana",), **extra):
    raw = {
        "id": vid,
        "uses": [{"card": {"name": n}} for n in names],
        "produces": [{"feature": {"name": p}} for p in produces],
        "legalities": {"commander": legal},
        "identity": "WU",
        "description": f"Combo {vid}",
        "popularity": popularity,
        "bracketTag": "R",
    }
    raw.update(extra)
    return raw


VARIANTS = [
    _variant("a", ["Thassa's Oracle", "Demonic Consultation"], popularity=900),
    _variant("b", ["Kiki-Jiki, Mirror Breaker", "Zealous Conscripts"], popularity=500),
    _variant("c", ["Isochron Scepter", "Dramatic Reversal", "Sol Ring"], popularity=300),
    _variant("d", ["Thassa's Oracle", "Tainted Pact"], popularity=800, legal="banned"),
    _variant("e", ["Lonely Card"]),
    _variant("f", ["A", "B", "C", "D", "E"]),
    {"uses": [{"card": {"name": "No Id"}}, {"card": {"name": "Either"}}]},
]


@pytest.fixture
def combo_db(tmp_path, monkeypatch):
    from app.config import settings
    from app.db import session as db_session

    monkeypatch.setattr(settings, "familiar_db_path", str(tmp_path / "combos.db"))
    monkeypatch.setattr(settings, "combo_source_url", SOURCE)
    db_session.get_engine.cache_clear()
    schema.ensure_schema()
    combos.ensure_schema()
    yield
    db_session.get_engine.cache_clear()


@pytest.fixture
def imported(combo_db):
    with respx.mock:
        respx.get(SOURCE).mock(return_value=httpx.Response(200, json={"variants": VARIANTS}))
        with httpx.Client() as client:
            written = combos.import_combos(client)
    return written


# ── parsing ────────────────────────────────────────────────────────────────

def test_parse_variant_keeps_usable_row():
    row = combos.parse_variant(VARIANTS[0])
    assert row["id"] == "a"
    assert row["card_count"] == 2
    assert '"Thassa\'s Oracle"' in row["card_names"]
    assert row["popularity"] == 900
    assert row["bracket_tag"] == "R"


@pytest.mark.parametrize("raw", [VARIANTS[3], VARIANTS[4], VARIANTS[5], VARIANTS[6]])
def test_parse_variant_drops_unusable(raw):
    assert combos.parse_variant(raw) is None


def test_parse_variant_tolerates_missing_optional_fields():
    row = combos.parse_variant({"id": 7, "uses": [{"card": {"name": "X"}}, {"card": {"name": "Y"}}]})
    assert row["id"] == "7"
    assert row["produces"] == "[]"
    assert row["identity"] is None
    assert row["popularity"] is None


def test_parse_variant_dedupes_repeated_names():
    row = combos.parse_variant(_variant("z", ["X", "X", "Y"]))
    assert row["card_count"] == 2


def test_iter_variants_accepts_bare_list_and_results_key():
    assert len(list(combos._iter_variants([{"id": 1}, "junk"]))) == 1
    assert len(list(combos._iter_variants({"results": [{"id": 1}]}))) == 1


# ── import ─────────────────────────────────────────────────────────────────

def test_import_writes_usable_rows_only(imported):
    assert imported == 3
    assert not combos.is_stale()


def test_is_stale_on_empty_table(combo_db):
    assert combos.is_stale()


def test_import_replaces_previous_rows(imported):
    with respx.mock:
        respx.get(SOURCE).mock(return_value=httpx.Response(200, json=[VARIANTS[1]]))
        with httpx.Client() as client:
            assert combos.import_combos(client) == 1
    assert combos.combos_in_deck(["Thassa's Oracle", "Demonic Consultation"]) == []
    assert len(combos.combos_in_deck(["Kiki-Jiki, Mirror Breaker", "Zealous Conscripts"])) == 1


def test_refresh_failure_keeps_existing_rows(imported):
    with respx.mock:
        respx.get(SOURCE).mock(side_effect=httpx.ConnectError("offline"))
        with httpx.Client() as client:
            assert combos.refresh_if_stale(force=True, client=client) is False
    assert len(combos.combos_in_deck(["Thassa's Oracle", "Demonic Consultation"])) == 1


def test_refresh_skips_when_fresh(imported):
    with respx.mock:
        route = respx.get(SOURCE).mock(return_value=httpx.Response(200, json=[]))
        with httpx.Client() as client:
            assert combos.refresh_if_stale(client=client) is False
    assert not route.called


def test_refresh_disabled_by_blank_url(combo_db, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "combo_source_url", "")
    assert combos.refresh_if_stale(force=True) is False


# ── queries ────────────────────────────────────────────────────────────────

def test_combos_in_deck_requires_every_card(imported):
    found = combos.combos_in_deck(["thassa's oracle", "Demonic Consultation", "Isochron Scepter", "Dramatic Reversal"])
    assert [c["id"] for c in found] == ["a"]
    assert found[0]["cards"] == ["Thassa's Oracle", "Demonic Consultation"]
    assert found[0]["produces"] == ["Infinite mana"]


def test_combos_in_deck_orders_by_popularity(imported):
    found = combos.combos_in_deck([
        "Kiki-Jiki, Mirror Breaker", "Zealous Conscripts", "Isochron Scepter",
        "Dramatic Reversal", "Sol Ring",
    ])
    assert [c["id"] for c in found] == ["b", "c"]


def test_combos_in_deck_empty_inputs(imported):
    assert combos.combos_in_deck([]) == []
    assert combos.combos_in_deck(["Nothing Here"]) == []


def test_combos_one_short_names_completing_candidates(imported):
    deck = ["Thassa's Oracle", "Isochron Scepter", "Sol Ring", "Llanowar Elves"]
    one_short = combos.combos_one_short(
        deck, ["Demonic Consultation", "Dramatic Reversal", "Zealous Conscripts", "Sol Ring"]
    )
    assert set(one_short) == {"demonic consultation", "dramatic reversal"}
    assert one_short["dramatic reversal"][0]["id"] == "c"


def test_combos_one_short_ignores_cards_already_in_deck(imported):
    assert combos.combos_one_short(["Thassa's Oracle"], ["Thassa's Oracle"]) == {}
    assert combos.combos_one_short([], ["Demonic Consultation"]) == {}


def test_queries_survive_missing_table(tmp_path, monkeypatch):
    from app.config import settings
    from app.db import session as db_session

    monkeypatch.setattr(settings, "familiar_db_path", str(tmp_path / "bare.db"))
    db_session.get_engine.cache_clear()
    try:
        assert combos.combos_in_deck(["X"]) == []
        assert combos.combos_one_short(["X"], ["Y"]) == {}
    finally:
        db_session.get_engine.cache_clear()


# ── bracket floor ──────────────────────────────────────────────────────────

def _combo(*cards):
    return {"cards": list(cards), "card_count": len(cards), "produces": [], "description": ""}


def test_combo_floor_lifts_low_bracket_to_three():
    bracket, factors = _apply_combo_floor(1, ["Bracket 1: no game changers"], [_combo("A", "B")])
    assert bracket == 3
    assert factors[0].startswith("Bracket 3: two-card combo present (A + B)")
    assert any(f.startswith("2-card combos (1): A + B") for f in factors)


def test_combo_floor_leaves_higher_brackets_alone():
    bracket, factors = _apply_combo_floor(4, ["Bracket 4: 5 game changers"], [_combo("A", "B")])
    assert bracket == 4
    assert factors[0] == "Bracket 4: 5 game changers"
    assert len(factors) == 2


def test_combo_floor_ignores_three_card_combos():
    assert _apply_combo_floor(2, ["x"], [_combo("A", "B", "C")]) == (2, ["x"])


# ── pipeline rendering ─────────────────────────────────────────────────────

def test_render_marks_combo_completing_candidates():
    ctx = DeckContext(
        identity=frozenset({"U", "B"}), card_names_lower=frozenset({"thassa's oracle"}),
        combo_partners={"demonic consultation": ["Thassa's Oracle"]},
    )
    cards = [
        {"name": "Demonic Consultation", "oracle_id": "dc", "color_identity": ["B"],
         "type_line": "Instant", "oracle_text": "", "cmc": 1},
        {"name": "Counterspell", "oracle_id": "cs", "color_identity": ["U"],
         "type_line": "Instant", "oracle_text": "", "cmc": 2},
    ]
    block = render_pool(shape(cards, ctx, {}))
    assert "COMPLETES A COMBO with Thassa's Oracle" in block
    assert "COMPLETES A COMBO" not in [ln for ln in block.splitlines() if "Counterspell" in ln][0]


def test_service_annotates_context_with_partners(monkeypatch):
    from app.pipeline import service

    monkeypatch.setattr(
        "app.cards.combos.combos_one_short",
        lambda deck, cands: {"dramatic reversal": [
            {"cards": ["Isochron Scepter", "Dramatic Reversal", "Sol Ring"]},
            {"cards": ["Dramatic Reversal", "Isochron Scepter", "Basalt Monolith"]},
        ]},
    )
    ctx = DeckContext(identity=frozenset(), card_names_lower=frozenset())
    snapshot = {"cards": [{"name": "Isochron Scepter"}, {"name": "Sol Ring"}]}
    out = service._with_combo_partners(ctx, snapshot, [{"name": "Dramatic Reversal"}])
    assert out.combo_partners == {"dramatic reversal": ["Basalt Monolith", "Isochron Scepter", "Sol Ring"]}


def test_service_annotation_failure_keeps_context(monkeypatch):
    from app.pipeline import service

    def boom(*_a, **_k):
        raise RuntimeError("no table")

    monkeypatch.setattr("app.cards.combos.combos_one_short", boom)
    ctx = DeckContext(identity=frozenset(), card_names_lower=frozenset())
    assert service._with_combo_partners(ctx, {"cards": []}, [{"name": "X"}]) is ctx
