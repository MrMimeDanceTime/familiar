"""What the model is told before it reads anything: the deck as it stands,
what the player decided since the last reply, and what the player's record
says about their taste.

Each block exists because the alternative was a tool call or a guess. The
deck-state block replaces the deck read most turns opened with, and with it
the prompt lines that nagged the model to "read total_cards this turn". The
since-last-turn note closes the gap where approve, deny, and undo happened
over REST and the model learned of them only if the player clicked Done
reviewing. The player-history block turns the personal scoring layer's
private knowledge into something the model can say out loud.

All three are best-effort: a failure produces an empty block, never a
failed turn.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlmodel import Session, select

from app import deckplan
from app.db import repository as repo
from app.db.models import DENIAL_SUPERSEDED, DENIAL_WITHDRAWN, DeckProposal

logger = logging.getLogger("app.chat.context")

# Deficiency category from deck stats -> (knowledge category, query) for the
# guidance entry pulled in alongside an off-target count.
_DEFICIENCY_KNOWLEDGE = {
    "lands": ("land-base", "land count formula commander"),
    "ramp": ("ramp", "ramp package sizing commander"),
    "draw": ("card-draw", "card draw density commander"),
    "removal": ("removal", "removal suite composition commander"),
}

# Reasons the app writes rather than the player. Not decisions to report.
_APP_REASONS = frozenset({DENIAL_WITHDRAWN, DENIAL_SUPERSEDED})

# Below this many decisions the player-history block would be reading tea
# leaves; the personal layer stays silent at the same threshold.
MIN_DECISIONS_FOR_HISTORY = 5

_FORMAT_SIZE = {"commander": 100, "brawl": 60, "oathbreaker": 60}


@dataclass
class DeckState:
    """The deck-state block plus the two facts the prompt phases on."""

    block: str = ""
    plan_is_set: bool | None = None
    total_cards: int | None = None
    # Every card this block names (staples it says are missing, combo pieces,
    # pending proposals, the commander). The engine grounds them: a card the
    # app puts in front of the model is one the model will write about, and
    # it should not have to look up a name we handed it.
    card_names: list[str] = field(default_factory=list)


def _identity_letters(snapshot: dict[str, Any]) -> str:
    colours: set[str] = set()
    commander_names = {
        (n or "").lower() for n in (snapshot.get("commander"), snapshot.get("partner_commander"))
    }
    for card in snapshot.get("cards", []):
        if (card.get("name") or "").lower() in commander_names:
            for ch in card.get("color_identity") or "":
                if ch in "WUBRG":
                    colours.add(ch)
    return "".join(c for c in "WUBRG" if c in colours)


def _names(items: list[dict[str, Any]], key: str, limit: int) -> str:
    names = [str(i.get(key)) for i in items if i.get(key)]
    shown = ", ".join(names[:limit])
    if len(names) > limit:
        shown += f", +{len(names) - limit} more"
    return shown


def deck_state(session: Session, deck_id: int | None) -> DeckState:
    """The <deck_state> block for a turn, read fresh from the database."""
    if deck_id is None:
        return DeckState()
    try:
        return _deck_state(session, deck_id)
    except Exception:  # noqa: BLE001 - the prompt must build regardless
        logger.exception("deck_state block failed; the model gets no header")
        return DeckState()


def _deck_state(session: Session, deck_id: int) -> DeckState:
    from app.tools.deck_stats import compute_deck_stats

    snapshot = repo.deck_snapshot(session, deck_id)
    plan = deckplan.build_plan(snapshot)
    total = int(snapshot.get("total_cards") or 0)
    named: list[str] = []
    fmt = snapshot.get("format") or "commander"
    size = _FORMAT_SIZE.get(fmt)
    lines: list[str] = []

    count = f"{total} of {size} cards" if size else f"{total} cards"
    lines.append(f"Deck \"{snapshot.get('name')}\" · {fmt} · {count}.")

    commander = snapshot.get("commander")
    partner = snapshot.get("partner_commander")
    pending_commander = repo.pending_commander_for_deck(session, deck_id)
    named.extend(n for n in (commander, partner, pending_commander) if n)
    if commander:
        who = f"{commander} / {partner}" if partner else commander
        identity = _identity_letters(snapshot)
        lines.append(f"Commander: {who}" + (f" (identity {identity})." if identity else "."))
    elif pending_commander:
        lines.append(
            f"Commander: not set. Your proposal of {pending_commander} is awaiting "
            "the player's decision."
        )
    else:
        lines.append("Commander: not set.")

    plan_bits: list[str] = []
    if plan.themes:
        plan_bits.append("themes: " + ", ".join(plan.themes))
    if snapshot.get("power_level"):
        plan_bits.append(f"power {snapshot['power_level']}")
    ceiling = deckplan.price_ceiling(
        snapshot.get("max_card_price"), repo.get_or_create_preferences(session).budget
    )
    if ceiling is not None:
        plan_bits.append(f"budget ceiling ${ceiling:.0f}/card")
    if plan.restrictions.get("exclude_types"):
        plan_bits.append("excludes: " + ", ".join(plan.restrictions["exclude_types"]))
    if plan.restrictions.get("exclude_cards"):
        plan_bits.append("never: " + ", ".join(plan.restrictions["exclude_cards"]))
    if plan.notes:
        plan_bits.append(f"notes: {plan.notes}")
    unmet = plan.unmet
    needs = ", ".join(f"{g.role} (short {g.gap})" for g in unmet) if unmet else "nothing"
    head = "Plan: set" if plan.has_plan() else "Plan: not set"
    lines.append(" · ".join([head, *plan_bits]) + f" · still needs: {needs}.")

    stats: dict[str, Any] = {}
    if total > 0:
        stats = compute_deck_stats(session, deck_id)
        untagged = stats.get("untagged") or []
        roles = (
            f"Roles: land {stats.get('land_count', 0)} · ramp {stats.get('ramp_count', 0)} · "
            f"draw {stats.get('draw_count', 0)} · removal {stats.get('removal_count', 0)}"
        )
        if untagged:
            roles += f" ({len(untagged)} untagged cards not counted, so these are floors)"
        lines.append(roles + ".")
        off = [d for d in stats.get("deficiencies", []) if d.get("status") != "OK"]
        score = (
            f"Bracket {stats.get('bracket')} · power {stats.get('power_level')} · "
            f"average MV {stats.get('avg_mv')}"
        )
        if stats.get("total_price_usd") is not None:
            score += f" · ${stats['total_price_usd']:.0f} total"
        if off:
            score += " · off target: " + ", ".join(
                f"{d['category']} {d['count']} {d['status']} (want {d['target_low']}-{d['target_high']})"
                for d in off
            )
        lines.append(score + ".")
        low = [m for m in stats.get("mana_sources", []) if m.get("status") == "LOW"]
        if low:
            lines.append(
                "Mana sources short: " + ", ".join(
                    f"{m['color']} ({m['sources']} sources for {m['pips']} pips)" for m in low
                ) + "."
            )
        combos = stats.get("combos") or []
        if combos:
            lines.append(
                "Combos in the deck: " + "; ".join(" + ".join(c["cards"]) for c in combos[:4]) + "."
            )
            named.extend(n for c in combos[:4] for n in c["cards"])

    staples = deckplan_missing_staples(snapshot)
    if staples:
        lines.append("Missing format staples: " + ", ".join(staples[:6]) + ".")
        named.extend(staples[:6])

    pending = snapshot.get("pending_proposals") or {}
    cards_pending = [p for p in pending.get("proposals", []) if p.get("action") != "set_commander"]
    if cards_pending:
        lines.append(
            f"Awaiting the player's decision: {len(cards_pending)} proposal(s) "
            f"({_names(cards_pending, 'card_name', 8)})."
        )
        named.extend(str(p["card_name"]) for p in cards_pending if p.get("card_name"))
    else:
        lines.append("No card proposals are awaiting the player's decision.")

    guidance = _deficiency_guidance(stats)
    block = (
        "<deck_state>\n"
        "Read from the database as this turn began. Quote these numbers; do not "
        "recount the list. deck_get_current has the cards, deck_get_stats the "
        "factors behind the scores.\n"
        + "\n".join(lines)
        + (f"\n{guidance}" if guidance else "")
        + "\n</deck_state>"
    )
    return DeckState(
        block=block, plan_is_set=plan.has_plan(), total_cards=total,
        card_names=list(dict.fromkeys(named)),
    )


def deckplan_missing_staples(snapshot: dict[str, Any]) -> list[str]:
    try:
        from app.tools.deck_tools import _missing_auto_includes

        return [s["name"] for s in _missing_auto_includes(snapshot) if s.get("name")]
    except Exception:  # noqa: BLE001
        return []


def _deficiency_guidance(stats: dict[str, Any]) -> str:
    """Knowledge-base entries for the roles that are off target.

    Retrieval is otherwise model-elective, so a deck short on draw used to get
    advice from training data instead of the curated entry.
    """
    off = [d for d in stats.get("deficiencies", []) if d.get("status") != "OK"]
    if not off:
        return ""
    try:
        from app.knowledge.store import search_knowledge
    except Exception:  # noqa: BLE001
        return ""
    lines: list[str] = []
    seen: set[str] = set()
    for d in off:
        mapping = _DEFICIENCY_KNOWLEDGE.get(d.get("category"))
        if not mapping or mapping[0] in seen:
            continue
        seen.add(mapping[0])
        try:
            hits = search_knowledge(mapping[1], top_k=1, format="commander", category=mapping[0])
        except Exception:  # noqa: BLE001
            continue
        if hits:
            lines.append(f"- {hits[0]['title']}: {hits[0]['body']}")
    if not lines:
        return ""
    return "Guidance for the off-target roles, from the knowledge base:\n" + "\n".join(lines)


# ── since last turn ────────────────────────────────────────────────────────


def _label(p: DeckProposal) -> str:
    if p.action == "set_commander":
        return f"commander {p.commander_name}"
    if p.action == "remove":
        return f"cut {p.card_name}"
    return p.card_name or "a proposal"


def since_last_turn(session: Session, deck_id: int | None) -> str:
    """Decisions the player made on proposals since the model last saw them.

    Marks every proposal it reports (and every fresh pending one) so the next
    turn reports only what changed after this one. A proposal reverted from
    approved to pending is reported as an undo.
    """
    if deck_id is None:
        return ""
    try:
        rows = list(session.exec(
            select(DeckProposal)
            .where(DeckProposal.deck_id == deck_id)
            .order_by(DeckProposal.created_at.asc())
        ))
    except Exception:  # noqa: BLE001
        logger.exception("since_last_turn: could not read proposals")
        return ""

    approved: list[str] = []
    denied: list[str] = []
    undone: list[str] = []
    changed = False
    for p in rows:
        if p.reported_status == p.status:
            continue
        if p.status == "approved":
            approved.append(_label(p))
        elif p.status == "denied":
            if p.denial_reason not in _APP_REASONS:
                reason = p.denial_reason or "no reason given"
                denied.append(f"{_label(p)} ({reason})")
        elif p.status == "pending" and p.reported_status == "approved":
            undone.append(_label(p))
        p.reported_status = p.status
        session.add(p)
        changed = True
    if changed:
        session.commit()

    parts: list[str] = []
    if approved:
        parts.append("approved " + ", ".join(approved))
    if denied:
        parts.append("denied " + ", ".join(denied))
    if undone:
        parts.append("undid the approval of " + ", ".join(undone) + " (back to pending)")
    if not parts:
        return ""
    return (
        "<since_last_turn>\n"
        "The player's decisions on your proposals since your last reply: "
        + "; ".join(parts)
        + ". The deck_state block already reflects them.\n</since_last_turn>"
    )


# ── player history ─────────────────────────────────────────────────────────


def player_history(session: Session) -> str:
    """What the player's approve/deny record says about their taste."""
    try:
        return _player_history(session)
    except Exception:  # noqa: BLE001
        logger.exception("player_history block failed")
        return ""


def _player_history(session: Session) -> str:
    rows = session.exec(text(
        "SELECT lower(card_name), card_name, status, denial_reason "
        "FROM deck_proposals WHERE action = 'add' AND status IN ('approved', 'denied') "
        "AND card_name IS NOT NULL"
    )).fetchall()
    approved = 0
    denied = 0
    reasons: dict[str, int] = {}
    denied_names: dict[str, tuple[str, int]] = {}
    for lower, name, status, reason in rows:
        if status == "approved":
            approved += 1
            continue
        if reason in _APP_REASONS:
            continue
        denied += 1
        if reason:
            key = reason.strip().lower()
            reasons[key] = reasons.get(key, 0) + 1
        shown, count = denied_names.get(lower, (name, 0))
        denied_names[lower] = (shown, count + 1)
    if approved + denied < MIN_DECISIONS_FOR_HISTORY:
        return ""

    lines = [
        f"Across all decks the player has approved {approved} of your card proposals "
        f"and denied {denied}."
    ]
    if reasons:
        top = sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))[:4]
        lines.append(
            "Denial reasons they reach for: "
            + ", ".join(f"\"{r}\" ({n})" for r, n in top) + "."
        )
    repeats = sorted(
        (name for name, count in denied_names.values() if count >= 2), key=str.lower
    )
    if repeats:
        lines.append("Cards they have passed on more than once: " + ", ".join(repeats[:8]) + ".")
    curve = _curve_preference(session)
    if curve:
        lines.append(curve)
    lines.append(
        "This is their record, not a rule: use it to aim, and say so when you "
        "choose against it."
    )
    return "<player_history>\n" + "\n".join(lines) + "\n</player_history>"


def _curve_preference(session: Session) -> str:
    try:
        from app.brainmap.personal import PersonalLayer

        curve = PersonalLayer(session.get_bind()).curve_preference()
    except Exception:  # noqa: BLE001
        return ""
    if not curve:
        return ""
    taken, passed = curve
    if abs(passed - taken) < 0.5:
        return ""
    lean = "cheaper" if taken < passed else "bigger"
    return (
        f"They take {lean} cards than they pass on (average mana value "
        f"{taken:.1f} taken vs {passed:.1f} passed)."
    )
