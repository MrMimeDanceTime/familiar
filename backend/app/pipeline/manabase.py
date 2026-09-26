"""Land requests: fill the manabase to the deck's land target.

A land request went through the same path as any role, which picks ten
distinct cards: one Plains, one Swamp, a few duals, and a mana rock. A deck
wants 33-35 lands (``deckplan.TARGETS_BY_POWER``), most of them basics, and
basics are the one card Commander lets a deck run many copies of. Measured in
end-to-end simulations: six turns of building left decks with 0-1 lands.

So a land request is filled rather than sampled:

  * the gap is the plan's land target minus the lands already in the deck;
  * nonbasic lands come from the ranked pool (Jev's order when it ranked),
    non-land cards dropped, capped by colour count: a two-colour deck wants
    about a dozen, a mono-colour deck few;
  * basics fill the rest as multiples, split across the deck's colours by the
    coloured pips in its spells.
"""

from __future__ import annotations

from typing import Any

from app import deckplan
from app.pipeline import local_retrieval, roles
from app.pipeline.selection import Pick, Selection

BASICS = {"W": "Plains", "U": "Island", "B": "Swamp", "R": "Mountain", "G": "Forest"}
_BASIC_NAMES = {n.lower() for n in BASICS.values()} | {"wastes"}
# Nonbasic lands worth their slot, by colour count. Past these a deck trades
# basics (which fetch and fix nothing but never fail) for taplands and filler.
NONBASIC_CAP = {0: 4, 1: 6, 2: 12, 3: 16, 4: 20, 5: 24}


def is_land_request(intent: str) -> bool:
    return roles.LAND in local_retrieval.intent_roles(intent)


def land_gap(snapshot: dict[str, Any]) -> int:
    plan = deckplan.build_plan(snapshot)
    gap = next((g for g in plan.gaps if g.role == "land"), None)
    return max(0, gap.target - gap.current) if gap else 0


def _pip_shares(snapshot: dict[str, Any], identity: frozenset[str]) -> dict[str, float]:
    from app.tools.deck_stats import _pips_in_cost

    totals = {c: 0.0 for c in identity}
    for card in snapshot.get("cards", []):
        if "Land" in (card.get("type_line") or ""):
            continue
        for colour, n in _pips_in_cost(card.get("mana_cost")).items():
            if colour in totals:
                totals[colour] += n * (card.get("quantity") or 1)
    if not totals:
        return {}
    if not sum(totals.values()):
        return {c: 1 / len(totals) for c in totals}
    total = sum(totals.values())
    return {c: v / total for c, v in totals.items()}


def split_basics(count: int, shares: dict[str, float]) -> dict[str, int]:
    """``count`` basics across colours by share, largest remainder rounding,
    so the parts always sum to ``count``."""
    if count <= 0 or not shares:
        return {}
    raw = {c: count * s for c, s in shares.items()}
    out = {c: int(v) for c, v in raw.items()}
    for c in sorted(raw, key=lambda c: raw[c] - out[c], reverse=True)[: count - sum(out.values())]:
        out[c] += 1
    return {c: n for c, n in out.items() if n}


def fill(
    selection: Selection, shaped: list[Any], snapshot: dict[str, Any], identity: frozenset[str],
) -> Selection:
    """Turn a land request's selection into a manabase fill: ranked nonbasic
    lands up to the colour cap, basics for the rest, nothing that is not a
    land."""
    need = land_gap(snapshot)
    if need == 0:
        return Selection(picks=[], summary="The deck is already at its land target.", raw=selection.raw)

    by_name = {c.name.lower(): c for c in shaped if c.name and c.legal_in_deck}
    ranked = [j["name"] for j in (selection.raw or {}).get("judgments") or []] or [
        p.name for p in selection.picks
    ] + [c.name for c in shaped if c.legal_in_deck]
    nonbasics: list[Pick] = []
    cap = min(need, NONBASIC_CAP.get(len(identity), 12))
    seen: set[str] = set()
    for name in ranked:
        card = by_name.get(name.lower())
        if card is None or name.lower() in seen or name.lower() in _BASIC_NAMES:
            continue
        seen.add(name.lower())
        if "Land" not in (card.type_line or ""):
            continue
        nonbasics.append(Pick(name=card.name, reason=next(
            (p.reason for p in selection.picks if p.name.lower() == name.lower()),
            "Nonbasic land ranked for this deck.",
        )))
        if len(nonbasics) >= cap:
            break

    basics: list[Pick] = []
    if not identity:
        if need - len(nonbasics) > 0:
            basics.append(Pick(name="Wastes", reason="Colourless deck: basics to reach the land target.",
                               quantity=need - len(nonbasics)))
    else:
        for colour, n in split_basics(need - len(nonbasics), _pip_shares(snapshot, identity)).items():
            basics.append(Pick(
                name=BASICS[colour], quantity=n,
                reason=f"{n} of the {need} lands the deck is short of its target, split by its coloured pips.",
            ))
    return Selection(
        picks=nonbasics + basics,
        summary=(f"Manabase: {need} lands to reach the target, {len(nonbasics)} nonbasic "
                 f"and {need - len(nonbasics)} basic."),
        raw=selection.raw,
    )
