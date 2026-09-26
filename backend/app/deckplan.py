"""The deck plan: what a deck is trying to be, and how far off it is.

Before this, the only "holistic view" of a deck was rebuilt from scratch on
every selection call: commander oracle text, card names grouped by category,
free-text notes. That view could say what the deck *contains* but never what it
*wants*, so every suggestion started cold and the model had to re-infer the plan
from a list of names. It also could not answer "why is this card here", because
the reason a card was added was discarded at approval.

This module owns the other half:

- **Targets** — how many cards each role should have (10 ramp, 8 interaction).
- **Direction** — the themes the deck is built around.
- **Gaps** — targets minus what is actually in the deck, which is the thing
  worth putting in front of both the model and the player.

Roles are counted from the coarse role taxonomy (`pipeline.roles`), which reads
a card's stored oracle tags. A card contributes to every role it satisfies: a
land that taps for mana and draws a card is both `land` and `draw`, and pretending
otherwise would make the counts lie.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.pipeline import roles as role_taxonomy

# Roles the plan tracks: the coarse four the role taxonomy already derives from
# oracle tags. Kept deliberately small — a target the player never sets is noise
# on the view, and every role here has to be countable from card data alone.
# ("Interaction" is what players usually say, but the taxonomy folds it into
# `removal`; adding it as a separate role would need a finer count than the
# coarse map can currently give.)
PLANNED_ROLES: tuple[str, ...] = ("land", "ramp", "draw", "removal")

# Targets per power level, derived from the scoring formula in
# ``deck_stats._estimate_power_level`` rather than invented.
#
# The two were previously unrelated, and the mismatch was not subtle: a deck
# hitting the old defaults (36/10/10/8) EXACTLY scored power 6 and could not
# reach 7 by any curve. Two bands caused it — 36 lands scores +0.5 where the
# formula's sweet spot is 33-35 at +1.0, and 8 removal scores +0.5 where 10
# earns +1.0. So a player asking for a 7 got a plan aiming at a 6, and the
# build chased targets that capped it below its own goal.
#
# Each row is the cheapest configuration reaching that level with a realistic
# land count, verified against the formula in tests.
TARGETS_BY_POWER: dict[int, dict[str, int]] = {
    4: {"land": 35, "ramp": 6, "draw": 5, "removal": 5},
    5: {"land": 34, "ramp": 8, "draw": 5, "removal": 5},
    6: {"land": 34, "ramp": 8, "draw": 8, "removal": 8},
    7: {"land": 34, "ramp": 10, "draw": 12, "removal": 10},
    8: {"land": 34, "ramp": 12, "draw": 12, "removal": 13},
    9: {"land": 33, "ramp": 14, "draw": 15, "removal": 15},
}

# Used when a deck states no power level. Sits mid-range rather than at either
# extreme, and unlike the old constant it is a row of the same table, so it
# cannot drift away from what the scorer rewards.
DEFAULT_POWER = 6
DEFAULT_TARGETS: dict[str, int] = TARGETS_BY_POWER[DEFAULT_POWER]

# What the preferences panel's budget choices mean as a per-card ceiling in
# USD. A deck's own max_card_price overrides these; "unlimited" and an unset
# preference mean no ceiling.
PRICE_CEILING_BY_BUDGET: dict[str, float] = {
    "budget": 5.0,
    "mid": 25.0,
}


def price_ceiling(
    max_card_price: float | None, budget_preference: str | None
) -> float | None:
    """The per-card price ceiling in effect: the deck's own, else the one the
    player's standing budget preference implies, else none."""
    if isinstance(max_card_price, (int, float)) and max_card_price > 0:
        return float(max_card_price)
    if budget_preference:
        return PRICE_CEILING_BY_BUDGET.get(budget_preference.strip().lower())
    return None


def targets_for_power(power_level: str | int | None) -> dict[str, int]:
    """Role targets that actually reach the requested power level.

    Accepts the free-text ``power_level`` a deck stores ("7", "~7", "7-8"),
    since it is set from conversation rather than a picker.
    """
    if power_level is None:
        return dict(DEFAULT_TARGETS)
    if isinstance(power_level, str):
        digits = "".join(c for c in power_level if c.isdigit())
        if not digits:
            return dict(DEFAULT_TARGETS)
        # "7-8" -> take the first number; the lower bound is the commitment.
        power_level = int(digits[0])
    nearest = min(TARGETS_BY_POWER, key=lambda k: abs(k - int(power_level)))
    return dict(TARGETS_BY_POWER[nearest])


@dataclass
class RoleGap:
    """One role's current count against its target."""

    role: str
    current: int
    target: int

    @property
    def gap(self) -> int:
        """Positive means short by this many; negative means over."""
        return self.target - self.current

    @property
    def satisfied(self) -> bool:
        return self.current >= self.target


@dataclass
class DeckPlan:
    """A deck's intended shape, and how the current list measures against it."""

    targets: dict[str, int] = field(default_factory=dict)
    themes: list[str] = field(default_factory=list)
    notes: str = ""
    gaps: list[RoleGap] = field(default_factory=list)
    total_cards: int = 0
    # The power level these targets were derived for, so the numbers can be
    # explained rather than presented as arbitrary.
    power_level: int = DEFAULT_POWER
    # Per-card price ceiling in USD, or None for no budget.
    max_card_price: float | None = None
    restrictions: dict[str, list[str]] = field(default_factory=dict)

    @property
    def unmet(self) -> list[RoleGap]:
        """Roles still short of target, worst first — the build order."""
        return sorted(
            (g for g in self.gaps if not g.satisfied),
            key=lambda g: g.gap,
            reverse=True,
        )

    def has_plan(self) -> bool:
        """True when the deck carries an explicit plan rather than defaults."""
        return bool(self.themes or self.notes)


def restrictions_of(snapshot: dict[str, Any]) -> dict[str, list[str]]:
    """The player's standing exclusions for this deck: card types ("Dragon",
    "Demon", "Planeswalker") and specific cards. Stored on the deck so a
    restriction said once holds for every later request; it used to live only
    in the chat message that stated it, and the next batch ignored it."""
    raw = snapshot.get("restrictions") or {}
    return {
        "exclude_types": [t for t in raw.get("exclude_types") or [] if isinstance(t, str) and t.strip()],
        "exclude_cards": [c for c in raw.get("exclude_cards") or [] if isinstance(c, str) and c.strip()],
    }


def restriction_broken(
    name: str | None, type_line: str | None, restrictions: dict[str, list[str]],
) -> str | None:
    """Why a card breaks the deck's restrictions, or None. Types match whole
    words of the type line, so "Demon" catches "Creature — Demon" but not
    "Demonic Tutor"."""
    words = {w.strip(",").lower() for w in (type_line or "").replace("—", " ").split()}
    for excluded in restrictions.get("exclude_types") or []:
        e = excluded.strip().lower()
        # The player's word, forgiving plurals: "Dragons" -> Dragon, "Elves" -> Elf.
        forms = {e, e.rstrip("s"), e[:-3] + "f" if e.endswith("ves") else e}
        if forms & words:
            return f"the deck excludes {excluded} cards"
    if name and name.lower() in {c.lower() for c in restrictions.get("exclude_cards") or []}:
        return f"the deck excludes {name}"
    return None


def count_roles(cards: list[dict[str, Any]]) -> dict[str, int]:
    """Count how many cards in a deck satisfy each planned role.

    A card counts toward every role it satisfies rather than being forced into
    one bucket — a land that also draws a card is genuinely both, and a single
    "primary role" assignment would undercount the deck's real interaction and
    ramp density.

    Reads each card's stored oracle tags (populated at add time), falling back
    to the type line so a card with no tags still counts as a land.
    """
    counts = {role: 0 for role in PLANNED_ROLES}
    for card in cards:
        tags = card.get("tags") or []
        type_line = card.get("type_line")
        card_roles = role_taxonomy.coarse_roles_for_tags(set(tags), type_line)
        quantity = card.get("quantity") or 1
        for role in card_roles:
            if role in counts:
                counts[role] += quantity
    return counts


def build_plan(snapshot: dict[str, Any]) -> DeckPlan:
    """Assemble the plan + gap view for a deck snapshot.

    Falls back to DEFAULT_TARGETS for any role the deck has not set explicitly,
    so a deck with no plan still gets a usable gap view rather than an empty one.
    """
    # Start from what the deck's stated power level actually requires, so the
    # gap view aims at the player's goal instead of a fixed guess.
    targets = targets_for_power(snapshot.get("power_level"))
    stored = snapshot.get("role_targets")
    if isinstance(stored, dict):
        for role, value in stored.items():
            if role in targets and isinstance(value, (int, float)):
                targets[role] = int(value)

    commander_names = {
        (n or "").lower()
        for n in (snapshot.get("commander"), snapshot.get("partner_commander"))
        if n
    }
    body = [
        c for c in snapshot.get("cards", [])
        if (c.get("name") or "").lower() not in commander_names
    ]

    counts = count_roles(body)
    gaps = [
        RoleGap(role=role, current=counts.get(role, 0), target=targets[role])
        for role in PLANNED_ROLES
    ]

    resolved_power = DEFAULT_POWER
    raw_power = snapshot.get("power_level")
    if raw_power is not None:
        digits = "".join(c for c in str(raw_power) if c.isdigit())
        if digits:
            resolved_power = min(
                TARGETS_BY_POWER, key=lambda k: abs(k - int(digits[0]))
            )

    themes = snapshot.get("themes")
    return DeckPlan(
        targets=targets,
        themes=[t for t in themes if t] if isinstance(themes, list) else [],
        notes=(snapshot.get("plan_notes") or "").strip(),
        gaps=gaps,
        total_cards=sum((c.get("quantity") or 1) for c in body),
        power_level=resolved_power,
        max_card_price=price_ceiling(
            snapshot.get("max_card_price"), snapshot.get("budget_preference")
        ),
        restrictions=restrictions_of(snapshot),
    )


def render_plan(plan: DeckPlan) -> str:
    """Render the plan as a compact block for an LLM prompt.

    Deliberately terse and deterministic: this goes into the stage-4 context on
    every suggestion, so it has to earn its tokens. Leads with what the deck is
    SHORT on, because that is what a suggestion should be aimed at.
    """
    lines: list[str] = []

    if plan.themes:
        lines.append(f"Deck direction: {', '.join(plan.themes)}")
    if plan.notes:
        lines.append(f"Plan notes: {plan.notes}")
    if plan.max_card_price is not None:
        lines.append(
            f"Budget: no single card over ${plan.max_card_price:.2f} "
            "(candidates above it are marked ILLEGAL in the pool)"
        )
    excluded = plan.restrictions.get("exclude_types") or []
    if excluded:
        lines.append(f"Excluded types: no {', '.join(excluded)} cards (marked ILLEGAL in the pool)")
    if plan.restrictions.get("exclude_cards"):
        lines.append(f"Excluded cards: {', '.join(plan.restrictions['exclude_cards'])}")

    lines.append(f"Deck size: {plan.total_cards} cards (excluding commander)")

    shape = ", ".join(
        f"{g.role} {g.current}/{g.target}" for g in plan.gaps
    )
    lines.append(f"Role counts: {shape}")
    lines.append(
        f"(Targets are what the power-level scorer actually rewards at power "
        f"{plan.power_level} — they are derived from the same formula "
        f"deck_get_stats uses, not rules of thumb. Missing them means the deck "
        f"scores BELOW its stated power level. Counts overlap: a card counts "
        f"toward every role it fills, so a land that draws is in both.)"
    )

    unmet = plan.unmet
    if unmet:
        needs = ", ".join(f"{g.role} (short {g.gap})" for g in unmet)
        lines.append(f"Still needs: {needs}")
    else:
        lines.append("Still needs: nothing — every role is at target.")

    return "\n".join(lines)


def render_card_rationale(cards: list[dict[str, Any]], *, limit: int = 40) -> str:
    """Render the stored per-card reasoning, for cards that have one.

    This is what makes "why is this card here" answerable without an LLM call.
    Only cards carrying a note are listed — an unexplained card says nothing
    useful and would just cost tokens.
    """
    explained = [
        (c.get("name"), (c.get("notes") or "").strip())
        for c in cards
        if (c.get("notes") or "").strip()
    ]
    if not explained:
        return ""
    lines = ["Why these cards are in the deck:"]
    for name, note in explained[:limit]:
        lines.append(f"- {name}: {note}")
    return "\n".join(lines)
