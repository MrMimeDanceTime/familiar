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

# Rules-of-thumb defaults for a 100-card Commander deck, used only when a deck
# has no explicit targets. These are starting points to be argued with, not
# prescriptions — which is why they are overridable per deck.
DEFAULT_TARGETS: dict[str, int] = {
    "land": 36,
    "ramp": 10,
    "draw": 10,
    "removal": 8,
}


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
    targets = dict(DEFAULT_TARGETS)
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

    themes = snapshot.get("themes")
    return DeckPlan(
        targets=targets,
        themes=[t for t in themes if t] if isinstance(themes, list) else [],
        notes=(snapshot.get("plan_notes") or "").strip(),
        gaps=gaps,
        total_cards=sum((c.get("quantity") or 1) for c in body),
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

    lines.append(f"Deck size: {plan.total_cards} cards (excluding commander)")

    shape = ", ".join(
        f"{g.role} {g.current}/{g.target}" for g in plan.gaps
    )
    lines.append(f"Role counts: {shape}")

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
