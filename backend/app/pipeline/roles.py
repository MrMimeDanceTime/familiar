"""Fine-grained functional role taxonomy for the retrieval pipeline.

The rest of the app tags cards with four *coarse* roles (ramp / draw / removal
/ land) via ``deck_tools._detect_roles`` and ``tag_lookup._tag_to_role``. Those
feed ``deck_get_stats`` and drive the bracket/power-level math. This module does
NOT replace them — it layers a richer taxonomy on top for stage-4 selection,
where the reasoning model benefits from knowing a card is specifically a
*tutor* or *board-wipe* rather than just "removal".

Two guarantees keep the two systems from drifting:

1. Every fine role rolls up (``FINE_TO_COARSE``) to exactly one coarse role or
   to ``None``. The rollup is the "fallthrough": a card tagged only with fine
   roles still contributes to the coarse stats through its rollup.

2. The coarse roles themselves are NOT re-derived here. ``coarse_roles_for_tags``
   delegates to the existing ``_tag_to_role`` so this module can add distinctions
   without ever disagreeing with the stats layer about the four it already knows.
"""

from __future__ import annotations

from app.knowledge.tag_lookup import _tag_to_role

# ── Fine role vocabulary ─────────────────────────────────────────────────
#
# A closed set. Each entry maps a fine role to the Scryfall oracle-tag slugs
# that imply it. Slugs are matched by exact value, by prefix (``"foo-"``), or
# by suffix (``"-foo"``); see ``_SLUG_RULES`` below. The taxonomy is a superset
# of the coarse four: several fine roles roll up to the same coarse role, and a
# few (tutor, counterspell as an interaction subtype, aristocrats, recursion,
# fixing) carry information the coarse map deliberately discards or lumps.
#
# Kept intentionally small and high-value per the "high-value subset +
# fallthrough" decision — this is not an attempt to mirror all ~1000 Tagger
# slugs, only the ones that change how a card should be selected.

RAMP = "ramp"
FAST_MANA = "fast-mana"
FIXING = "fixing"
CARD_DRAW = "card-draw"
CARD_SELECTION = "card-selection"
WHEEL = "wheel"
SPOT_REMOVAL = "spot-removal"
BOARD_WIPE = "board-wipe"
COUNTERSPELL = "counterspell"
LAND_DESTRUCTION = "land-destruction"
TUTOR = "tutor"
RECURSION = "recursion"
GRAVEYARD_HATE = "graveyard-hate"
ARISTOCRATS = "aristocrats"
SACRIFICE_OUTLET = "sacrifice-outlet"
TOKENS = "tokens"
PROTECTION = "protection"
LAND = "land"

# All fine roles, for validation/enumeration.
FINE_ROLES: frozenset[str] = frozenset({
    RAMP, FAST_MANA, FIXING,
    CARD_DRAW, CARD_SELECTION, WHEEL,
    SPOT_REMOVAL, BOARD_WIPE, COUNTERSPELL, LAND_DESTRUCTION,
    TUTOR, RECURSION, GRAVEYARD_HATE,
    ARISTOCRATS, SACRIFICE_OUTLET, TOKENS, PROTECTION,
    LAND,
})

# ── Fine → coarse rollup (the fallthrough) ───────────────────────────────
#
# Each fine role maps to one coarse role or None. None means "a real functional
# distinction the coarse stats don't track" (tutor, aristocrats, tokens…) — the
# card may still get a coarse role independently from its other tags, but this
# fine role alone doesn't grant one.

FINE_TO_COARSE: dict[str, str | None] = {
    RAMP: "ramp",
    FAST_MANA: "ramp",
    FIXING: None,            # color fixing is not acceleration — matches _tag_to_role
    CARD_DRAW: "draw",
    CARD_SELECTION: "draw",
    WHEEL: "draw",
    SPOT_REMOVAL: "removal",
    BOARD_WIPE: "removal",
    COUNTERSPELL: "removal",
    LAND_DESTRUCTION: "removal",
    TUTOR: None,
    RECURSION: None,
    GRAVEYARD_HATE: None,
    ARISTOCRATS: None,
    SACRIFICE_OUTLET: None,
    TOKENS: None,
    PROTECTION: None,
    LAND: "land",
}

# ── Slug → fine role rules ───────────────────────────────────────────────
#
# (exact, prefixes, suffixes) per fine role. A slug matches a fine role if it
# equals one of ``exact``, starts with one of ``prefixes``, or ends with one of
# ``suffixes``. Rules are deliberately conservative: an unmatched slug simply
# contributes no fine role (and still contributes its coarse role via the
# delegate), rather than being force-fit.

_SLUG_RULES: dict[str, tuple[frozenset[str], tuple[str, ...], tuple[str, ...]]] = {
    FAST_MANA: (
        frozenset({"ritual", "ritual-untap", "fast-mana", "mana-storage"}),
        (), (),
    ),
    FIXING: (
        frozenset({"mana-fix", "mana-fixing", "mana-filter", "fixing"}),
        (), ("-fixing",),
    ),
    RAMP: (
        frozenset({"ramp", "mana-rock", "mana-dork", "mana-egg",
                   "mana-producer", "utility-mana-rock", "mana-increaser"}),
        ("tutor-land",), ("-ramp",),
    ),
    WHEEL: (
        frozenset({"wheel", "miniwheel"}),
        ("wheel-",), (),
    ),
    CARD_DRAW: (
        frozenset({"draw", "card-draw", "pure-draw", "burst-draw",
                   "repeatable-draw", "draw-engine", "force-draw",
                   "draw-to-seven"}),
        (), ("-draw",),
    ),
    CARD_SELECTION: (
        frozenset({"impulsive-draw", "long-term-impulsive-draw",
                   "repeatable-impulsive-draw", "scry", "surveil",
                   "card-selection", "rummage", "loot"}),
        (), (),
    ),
    BOARD_WIPE: (
        frozenset({"board-wipe", "multi-removal", "mass-fight"}),
        ("sweeper",), ("-sweeper",),
    ),
    COUNTERSPELL: (
        frozenset(),
        ("counterspell",), (),
    ),
    LAND_DESTRUCTION: (
        frozenset({"land-destruction"}),
        (), (),
    ),
    SPOT_REMOVAL: (
        frozenset({"removal", "spot-removal", "repeatable-removal",
                   "bounce", "exile-on-resolution", "exile-with-tax",
                   "one-sided-fight", "old-fight", "opponent-sacrifices"}),
        ("removal-",), (),
    ),
    TUTOR: (
        frozenset({"tutor", "transmute"}),
        ("tutor-",), ("-tutor",),
    ),
    # The slugs below are verified against the bulk export, not guessed. Tagger's
    # vocabulary is a hierarchy and the export ships ONLY leaf taggings, so the
    # obvious concept names (`recursion`, `protection`, `sacrifice-outlet`)
    # resolve on Scryfall's API but match zero rows locally. Audited 2026-08-15:
    # the previous guessed names left `protection` and `graveyard-hate` matching
    # nothing at all, and `sacrifice-outlet`/`aristocrats` matching one slug
    # each. See tests/test_pipeline_roles.py, which fails if a rule stops
    # matching real data.
    RECURSION: (
        frozenset({"regrowth"}),
        ("recursion-", "reanimate-", "reanimation-"),
        ("-recursion", "-reanimation"),
    ),
    GRAVEYARD_HATE: (
        frozenset({"hate-graveyard", "graveyard-hate", "grave-hate"}),
        ("hate-graveyard-",), (),
    ),
    ARISTOCRATS: (
        frozenset({"aristocrats", "death-matters", "dies-trigger",
                   "payoff-sacrifice", "your-sacrifice-matters"}),
        ("death-trigger",), ("-death-trigger",),
    ),
    SACRIFICE_OUTLET: (
        frozenset({"sacrifice-outlet", "free-sacrifice-outlet",
                   "repeatable-sacrifice-outlet"}),
        ("sacrifice-outlet-",), ("-sacrifice-outlet",),
    ),
    TOKENS: (
        frozenset({"tokens", "token", "token-maker", "go-wide", "populate"}),
        ("token-", "repeatable-creature-tokens", "repeatable-artifact-tokens"),
        ("-token", "-tokens"),
    ),
    PROTECTION: (
        frozenset({"protection", "phase-out", "flicker-protection",
                   "protect-your-stuff"}),
        ("protects-", "gives-protection", "gives-hexproof",
         "gives-indestructible", "gains-hexproof", "gains-indestructible",
         "gains-protection"),
        (),
    ),
    LAND: (
        frozenset({"land"}),
        (), (),
    ),
}


def slug_rules_for(fine_role: str) -> tuple[frozenset[str], tuple[str, ...], tuple[str, ...]]:
    """The (exact, prefixes, suffixes) slug rules behind a fine role, so a
    retriever can ask the index for the cards that would be labelled with it."""
    return _SLUG_RULES.get(fine_role, (frozenset(), (), ()))


def _slug_matches(slug: str, exact: frozenset[str],
                  prefixes: tuple[str, ...], suffixes: tuple[str, ...]) -> bool:
    if slug in exact:
        return True
    if any(slug.startswith(p) for p in prefixes):
        return True
    if any(slug.endswith(s) for s in suffixes):
        return True
    return False


# Generic fine-role bucket for each coarse role. When a slug is recognized by
# the coarse map but matches no specific fine rule (a niche mana/draw/removal
# variant), it falls back to its coarse role's generic bucket. This makes the
# fine taxonomy total over everything the coarse map knows — the fallthrough
# can never lose a coarse role, even as the daily-refreshed tag cache adds new
# slugs we haven't hand-classified.
_COARSE_TO_GENERIC_FINE: dict[str, str] = {
    "ramp": RAMP,
    "draw": CARD_DRAW,
    "removal": SPOT_REMOVAL,
    "land": LAND,
}


def fine_roles_for_tags(tags: set[str] | list[str], type_line: str | None = None) -> set[str]:
    """Return the set of fine roles implied by a card's oracle tag slugs.

    ``type_line`` grants the land role when the card is a land even if it
    carries no ``land`` slug (mirrors ``_roles_from_tag_set``); it does NOT
    override any other rule.
    """
    result: set[str] = set()
    for slug in tags:
        matched: set[str] = set()
        for fine, (exact, prefixes, suffixes) in _SLUG_RULES.items():
            if _slug_matches(slug, exact, prefixes, suffixes):
                matched.add(fine)
        result |= matched
        # Backstop: if the slug's coarse role isn't already covered by the fine
        # roles it matched, add that coarse role's generic bucket. This handles
        # both unclassified niche slugs and slugs a fine rule matched into the
        # "wrong" coarse role (e.g. tutor-to-battlefield matches TUTOR but is
        # ramp per _tag_to_role — the fine TUTOR rolls up to nothing, so without
        # this backstop the ramp coarse role would be lost).
        coarse = _tag_to_role(slug)
        if coarse and coarse not in coarse_for_fine(matched):
            result.add(_COARSE_TO_GENERIC_FINE[coarse])
    if type_line and "land" in type_line.lower():
        result.add(LAND)
    return result


def coarse_roles_for_tags(tags: set[str] | list[str], type_line: str | None = None) -> set[str]:
    """Return the coarse roles (ramp/draw/removal/land) for a card's tags.

    Delegates to the existing ``_tag_to_role`` so the pipeline can never
    disagree with the stats layer about the coarse four; adds only the
    type-line land role that ``_tag_to_role`` doesn't cover.
    """
    result: set[str] = set()
    for slug in tags:
        role = _tag_to_role(slug)
        if role:
            result.add(role)
    if type_line and "land" in type_line.lower():
        result.add("land")
    return result


def coarse_for_fine(fine_roles: set[str] | list[str]) -> set[str]:
    """Roll a set of fine roles up to their coarse roles (the fallthrough).

    None-mapped fine roles contribute nothing. Use this when you have fine
    roles but need coarse ones for the stats layer.
    """
    result: set[str] = set()
    for fine in fine_roles:
        coarse = FINE_TO_COARSE.get(fine)
        if coarse:
            result.add(coarse)
    return result
