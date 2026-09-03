import pytest

from app.knowledge.tag_lookup import _CACHE_PATH, _iter_cached_records, _tag_to_role
from app.pipeline import roles


def test_every_fine_role_has_a_coarse_rollup():
    # The taxonomy is closed: FINE_ROLES and FINE_TO_COARSE keys must match
    # exactly, so no fine role can be produced without a defined fallthrough.
    assert set(roles.FINE_ROLES) == set(roles.FINE_TO_COARSE)


def test_coarse_rollups_are_only_the_known_four_or_none():
    allowed = {"ramp", "draw", "removal", "land", None}
    assert set(roles.FINE_TO_COARSE.values()) <= allowed


def test_slug_rules_only_reference_known_fine_roles():
    assert set(roles._SLUG_RULES) <= set(roles.FINE_ROLES)


def test_fine_roles_distinguish_within_a_coarse_role():
    # "removal" the coarse role splits into several fine roles the stats layer
    # can't see — that split is the whole point of the fine taxonomy.
    assert roles.fine_roles_for_tags({"board-wipe"}) == {roles.BOARD_WIPE}
    assert roles.fine_roles_for_tags({"counterspell-hard"}) == {roles.COUNTERSPELL}
    assert roles.fine_roles_for_tags({"spot-removal"}) == {roles.SPOT_REMOVAL}
    # ...yet all three roll up to the same coarse role.
    assert roles.coarse_for_fine({roles.BOARD_WIPE}) == {"removal"}
    assert roles.coarse_for_fine({roles.COUNTERSPELL}) == {"removal"}
    assert roles.coarse_for_fine({roles.SPOT_REMOVAL}) == {"removal"}


def test_tutor_is_a_fine_role_with_no_coarse_rollup():
    # Tutors are information the coarse map deliberately discards.
    assert roles.fine_roles_for_tags({"tutor"}) == {roles.TUTOR}
    assert roles.coarse_for_fine({roles.TUTOR}) == set()


def test_ramp_fine_roles_roll_up_to_ramp():
    assert roles.coarse_for_fine({roles.RAMP}) == {"ramp"}
    assert roles.coarse_for_fine({roles.FAST_MANA}) == {"ramp"}
    # Fixing is NOT ramp — must agree with _tag_to_role's exclusion.
    assert roles.coarse_for_fine({roles.FIXING}) == set()


def test_land_role_from_type_line_without_land_slug():
    assert roles.LAND in roles.fine_roles_for_tags(set(), "Land — Forest")
    assert "land" in roles.coarse_roles_for_tags(set(), "Basic Land — Island")


def test_coarse_roles_delegates_to_tag_to_role_and_cannot_drift():
    # Property: for any single slug, coarse_roles_for_tags must return exactly
    # what _tag_to_role says (plus the type-line land role, tested separately).
    sample_slugs = [
        "ramp", "mana-rock", "mana-dork", "ritual",
        "draw", "card-draw", "impulsive-draw", "wheel",
        "removal", "board-wipe", "counterspell-hard", "bounce",
        "mana-fix", "tutor", "aristocrats", "not-a-real-tag",
    ]
    for slug in sample_slugs:
        expected = {_tag_to_role(slug)} - {None}
        assert roles.coarse_roles_for_tags({slug}) == expected, slug


def test_fallthrough_matches_direct_coarse_for_taxonomy_slugs():
    # For slugs the fine taxonomy recognizes AND the coarse map recognizes, the
    # fine→coarse rollup must land on the same coarse role. This is the core
    # "fallthrough preserves stats" guarantee.
    for slug in ("ramp", "mana-rock", "draw", "card-draw", "wheel",
                 "removal", "board-wipe", "bounce", "land"):
        direct = roles.coarse_roles_for_tags({slug})
        via_fine = roles.coarse_for_fine(roles.fine_roles_for_tags({slug}))
        assert direct <= via_fine, slug


def test_unknown_slug_yields_no_roles():
    assert roles.fine_roles_for_tags({"totally-made-up-slug"}) == set()
    assert roles.coarse_roles_for_tags({"totally-made-up-slug"}) == set()


def test_multi_tag_card_accumulates_fine_roles():
    fine = roles.fine_roles_for_tags({"ramp", "tutor", "draw"})
    assert fine == {roles.RAMP, roles.TUTOR, roles.CARD_DRAW}
    assert roles.coarse_for_fine(fine) == {"ramp", "draw"}


def test_niche_coarse_slug_falls_back_to_generic_fine_bucket():
    # A slug the coarse map knows but the fine rules don't name explicitly
    # still yields the coarse role's generic fine bucket, so the fallthrough
    # never loses the coarse role.
    fine = roles.fine_roles_for_tags({"powerstone-mana"})
    assert fine == {roles.RAMP}
    assert roles.coarse_for_fine(fine) == {"ramp"}


def test_fallthrough_is_total_over_real_cached_tags():
    # The load-bearing guarantee: for EVERY slug in the live tag cache that the
    # coarse map recognizes, the fine fallthrough reproduces that coarse role.
    # Guards against cache drift silently dropping cards out of the stats.
    if not _CACHE_PATH.exists():
        pytest.skip("oracle tag cache not present")
    # Read through the module's own reader so the test tracks the cache format
    # instead of re-implementing it (it was a plain JSON array until Scryfall
    # moved to gzipped JSONL in August 2026).
    slugs = {e.get("slug", "") for e in _iter_cached_records() if e.get("slug")}
    misses = []
    for slug in slugs:
        coarse = _tag_to_role(slug)
        if coarse is None:
            continue
        via_fine = roles.coarse_for_fine(roles.fine_roles_for_tags({slug}))
        if coarse not in via_fine:
            misses.append((slug, coarse))
    assert not misses, f"fallthrough dropped {len(misses)} coarse roles: {misses[:20]}"


# ── Vocabulary drift guard ───────────────────────────────────────────────
#
# The fine-role rules were originally written against GUESSED slug names, and
# most of them matched nothing. Tagger's vocabulary is a hierarchy and the bulk
# export ships only LEAF taggings, so the obvious concept names (`recursion`,
# `protection`, `sacrifice-outlet`) resolve on Scryfall's API while matching
# zero rows locally. Audited 2026-08-15: `protection` and `graveyard-hate`
# matched nothing at all; `sacrifice-outlet` and `aristocrats` matched one slug
# each out of 4,383.
#
# These tests pin representative REAL slugs so a rule that stops matching is a
# test failure rather than a silently emptier pool.


from app.pipeline.roles import fine_roles_for_tags  # noqa: E402 - section import

# (slug, expected fine role). Every slug here was verified present in the bulk
# export with a meaningful card count.
_REAL_SLUGS = [
    ("reanimate-creature", "recursion"),        # 534 cards
    ("recursion-from-exile", "recursion"),      # 20
    ("protects-creature", "protection"),        # 731
    ("gives-indestructible", "protection"),     # 285
    ("gives-hexproof", "protection"),           # 165
    ("death-trigger", "aristocrats"),           # 591
    ("your-sacrifice-matters", "aristocrats"),  # 118
    ("sacrifice-outlet-creature", "sacrifice-outlet"),   # 894
    ("repeatable-sacrifice-outlet", "sacrifice-outlet"), # 580
    ("free-sacrifice-outlet", "sacrifice-outlet"),       # 183
    ("hate-graveyard", "graveyard-hate"),       # 300
    ("repeatable-creature-tokens", "tokens"),   # 1471
    ("mana-rock", "ramp"),
    ("spot-removal", "spot-removal"),
]


@pytest.mark.parametrize("slug,expected", _REAL_SLUGS)
def test_real_slug_maps_to_its_role(slug, expected):
    assert expected in fine_roles_for_tags({slug}), (
        f"{slug!r} no longer maps to {expected!r} — the rule may have been "
        f"written against a guessed slug name rather than the real vocabulary"
    )


def test_roles_are_reachable_from_leaf_slugs():
    """Ancestor slugs like `sacrifice-outlet` resolve on Scryfall's API but ship
    ZERO rows in the bulk export. Mapping them is harmless; DEPENDING on them is
    the bug, so every role must also be reachable from a real leaf slug."""
    assert "sacrifice-outlet" in fine_roles_for_tags({"sacrifice-outlet-artifact"})
    assert "recursion" in fine_roles_for_tags({"reanimate-self"})
    assert "protection" in fine_roles_for_tags({"protects-all"})
