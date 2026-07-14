from app.pipeline import roles
from app.pipeline.shaping import (
    DeckContext,
    _strip,
    legal_in_deck,
    render_pool,
    shape,
)

RAKDOS = DeckContext(identity=frozenset({"B", "R"}), card_names_lower=frozenset())


def _raw(name, **over):
    base = {
        "name": name,
        "oracle_id": name.lower().replace(" ", "-"),
        "mana_cost": "{1}{R}",
        "cmc": 2.0,
        "type_line": "Instant",
        "oracle_text": "Destroy target creature.",
        "color_identity": ["R"],
        "legal_commander": True,
        "keywords": [],
        "power": None,
        "toughness": None,
        "loyalty": None,
        "rarity": "common",
        "edhrec_rank": 1000,
        # a field NOT in the strip set, to prove strip drops it:
        "set_name": "Some Set",
    }
    base.update(over)
    return base


# ── strip ────────────────────────────────────────────────────────────────

def test_strip_keeps_only_selection_fields():
    stripped = _strip(_raw("Lightning Bolt"))
    assert "set_name" not in stripped
    assert stripped["name"] == "Lightning Bolt"
    assert set(stripped) == {
        "name", "oracle_id", "mana_cost", "cmc", "type_line", "oracle_text",
        "color_identity", "legal_commander", "keywords", "power", "toughness",
        "loyalty", "rarity", "edhrec_rank",
    }


# ── legal_in_deck ──────────────────────────────────────────────────────────

def test_legal_card_within_identity():
    ok, reasons = legal_in_deck(_raw("Lightning Bolt"), RAKDOS)
    assert ok is True
    assert reasons == []


def test_illegal_off_identity():
    ok, reasons = legal_in_deck(_raw("Counterspell", color_identity=["U"]), RAKDOS)
    assert ok is False
    assert any("U" in r and "identity" in r for r in reasons)


def test_multicolor_partially_off_identity():
    # W is off-identity for Rakdos; only the offending colors are reported.
    ok, reasons = legal_in_deck(
        _raw("Some Card", color_identity=["R", "W"]), RAKDOS
    )
    assert ok is False
    # only the offending color (W) is named, not the in-identity R
    assert reasons == ["color identity W outside commander identity"]


def test_colorless_card_always_within_identity():
    ok, _ = legal_in_deck(_raw("Sol Ring", color_identity=[]), RAKDOS)
    assert ok is True


def test_colorless_commander_rejects_colored_card():
    colorless = DeckContext(identity=frozenset(), card_names_lower=frozenset())
    ok, reasons = legal_in_deck(_raw("Lightning Bolt", color_identity=["R"]), colorless)
    assert ok is False
    assert any("identity" in r for r in reasons)


def test_not_commander_legal():
    ok, reasons = legal_in_deck(_raw("Chaos Orb", legal_commander=False), RAKDOS)
    assert ok is False
    assert any("commander-legal" in r for r in reasons)


def test_banned_card_flagged():
    ok, reasons = legal_in_deck(_raw("Mana Crypt", color_identity=[]), RAKDOS)
    assert ok is False
    assert any("banned" in r for r in reasons)


def test_already_in_deck_flagged():
    ctx = DeckContext(identity=frozenset({"B", "R"}),
                      card_names_lower=frozenset({"lightning bolt"}))
    ok, reasons = legal_in_deck(_raw("Lightning Bolt"), ctx)
    assert ok is False
    assert any("already in deck" in r for r in reasons)


def test_multiple_reasons_all_reported():
    # Off-identity AND already in deck: both surface.
    ctx = DeckContext(identity=frozenset({"B"}),
                      card_names_lower=frozenset({"lightning bolt"}))
    ok, reasons = legal_in_deck(_raw("Lightning Bolt", color_identity=["R"]), ctx)
    assert ok is False
    assert len(reasons) == 2


# ── shape: precompute + dedupe + cap + ordering ────────────────────────────

def test_shape_precomputes_roles_from_tags():
    cards = [_raw("Demonic Tutor", oracle_id="dt", color_identity=["B"])]
    tags = {"dt": {"tutor"}}
    shaped = shape(cards, RAKDOS, tags)
    assert shaped[0].fine_roles == {roles.TUTOR}
    # tutor has no coarse rollup; the card gets no coarse role from that tag
    assert shaped[0].coarse_roles == set()
    assert shaped[0].legal_in_deck is True


def test_shape_dedupes_by_oracle_id_keeping_first():
    dup = _raw("Ramp Rock", oracle_id="rr", edhrec_rank=5)
    dup2 = _raw("Ramp Rock", oracle_id="rr", edhrec_rank=999)
    shaped = shape([dup, dup2], RAKDOS, {"rr": {"mana-rock"}})
    assert len(shaped) == 1
    assert shaped[0].edhrec_rank == 5  # first occurrence kept


def test_shape_caps_after_sorting():
    cards = [_raw(f"C{i}", oracle_id=f"c{i}", edhrec_rank=i) for i in range(100)]
    shaped = shape(cards, RAKDOS, {}, cap=10)
    assert len(shaped) == 10
    # capped pool is the best-ranked ten, in rank order
    assert [c.edhrec_rank for c in shaped] == list(range(10))


def test_shape_sorts_legal_before_illegal():
    legal = _raw("Legal Card", oracle_id="l", edhrec_rank=9000)
    illegal = _raw("Off Color", oracle_id="o", color_identity=["U"], edhrec_rank=1)
    shaped = shape([illegal, legal], RAKDOS, {})
    # despite the illegal card being far better-ranked, legal comes first
    assert shaped[0].name == "Legal Card"
    assert shaped[1].legal_in_deck is False


def test_shape_missing_rank_sorts_last_among_legal():
    ranked = _raw("Ranked", oracle_id="r", edhrec_rank=500)
    unranked = _raw("Unranked", oracle_id="u", edhrec_rank=None)
    shaped = shape([unranked, ranked], RAKDOS, {})
    assert shaped[0].name == "Ranked"


# ── render ─────────────────────────────────────────────────────────────────

def test_render_includes_roles_and_marks_illegal():
    cards = [
        _raw("Demonic Tutor", oracle_id="dt", color_identity=["B"]),
        _raw("Counterspell", oracle_id="cs", color_identity=["U"]),
    ]
    tags = {"dt": {"tutor"}, "cs": {"counterspell-hard"}}
    block = render_pool(shape(cards, RAKDOS, tags))
    assert "Demonic Tutor" in block
    assert "roles: tutor" in block
    assert "ILLEGAL" in block
    assert "identity" in block  # the reason is shown


def test_render_is_deterministic():
    cards = [_raw("A", oracle_id="a"), _raw("B", oracle_id="b")]
    a = render_pool(shape(cards, RAKDOS, {}))
    b = render_pool(shape(cards, RAKDOS, {}))
    assert a == b
