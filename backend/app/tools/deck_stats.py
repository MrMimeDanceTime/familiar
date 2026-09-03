"""Deck statistics: curve, colours, roles, bracket, power, price, mana
sources, combos, and how the deck measures against its targets.

``compute_deck_stats`` is the one entry point. Everything deterministic is
memoised per deck content (see ``_deterministic_stats``), because the turn
header, the stats panel, and the model's own stats call all ask for the
same numbers within seconds of each other; the LLM power nuance is resolved
on every call on top of the memoised base.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from typing import Any

from sqlmodel import Session

from app.db import repository as repo
from app.tools.card_lists import (
    _FAST_MANA,
    _MLD_CARDS,
    _TUTORS,
    game_changer_names,
)
from app.tools.card_roles import _classify_type, _detect_roles


def _deck_is_settling(deck: Any) -> bool:
    """True while the deck changed more recently than the settle window."""
    from datetime import datetime, timezone

    from app.config import settings

    window = settings.power_nuance_settle_seconds
    if window <= 0:
        return False
    updated = deck.updated_at
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - updated).total_seconds() < window


def _resolve_power_nuance(
    session: Session,
    deck: Any,
    deck_id: int,
    base_score: int,
    base_factors: list[str],
    provider: Any | None,
) -> tuple[float, float, str, bool]:
    """Return ``(nuanced_score, adjustment, reason, pending)`` for the power level.

    Cache-first: if the deck's stored ``power_nuance_key`` matches the current
    deck-content hash, reuse the cached adjustment (free — no LLM call), whether
    or not a provider is present. On a miss WITH a provider, compute the nuance
    on the fast model, cache it, and apply — unless the deck changed within the
    settle window, in which case the base score is returned with ``pending``
    set so the caller can come back once the deck stops moving. On a miss
    WITHOUT a provider (grounding, pipeline), skip the LLM and return the base
    score unadjusted rather than blocking. Only applied to the commander format
    — power level is a commander concept here.
    """
    from app.tools.power_nuance import compute_nuance, deck_content_hash

    if deck is None or deck.format != "commander":
        return base_score, 0.0, "", False

    snapshot = repo.deck_snapshot(session, deck_id)
    key = deck_content_hash(snapshot)

    if deck.power_nuance_key == key and deck.power_nuance_adj is not None:
        adj, reason = deck.power_nuance_adj, deck.power_nuance_reason or ""
    elif provider is not None:
        if _deck_is_settling(deck):
            return base_score, 0.0, "", True
        # A bounded ±1 classification against a closed rubric does not need the
        # Pro model's depth; Flash answers it in a fraction of the time.
        from app.llm.factory import get_fast_model

        adj, reason = compute_nuance(
            provider, snapshot, base_score, base_factors, model=get_fast_model(),
        )
        repo.set_deck_power_nuance(session, deck_id, adj, reason, key)
    else:
        return base_score, 0.0, "", False

    # Keep the half-point: base is an integer band, adj is a multiple of 0.5, so
    # the sum is a clean .0/.5. Don't round — that would (a) use banker's rounding
    # (7.5->8 but 6.5->6, making a -0.5 a no-op on even bases) and (b) discard the
    # ±0.5 granularity the nuance exists to add. The frontend renders fractions.
    nuanced = min(10.0, max(1.0, base_score + adj))
    return nuanced, adj, reason, False


def compute_deck_stats(session: Session, deck_id: int, provider: Any | None = None) -> dict:
    """Compute stats for a deck from its stored cards.

    If any card is missing ``type_line``, backfills via Scryfall's
    ``/cards/collection`` endpoint (up to 75 names per request).

    When ``provider`` is given, the 1-10 power level is refined by a bounded LLM
    nuance adjustment (see ``power_nuance``): computed once per deck-content
    change and cached on the Deck row, so routine callers that pass no provider
    (grounding, pipeline internals) still reuse a fresh cached adjustment for
    free but never trigger an LLM call.
    """
    cards = repo.list_deck_cards(session, deck_id)

    # Backfill any card missing oracle_id or tags (from before those columns
    # existed). Best-effort: a Scryfall hiccup here must NOT sink the whole
    # stats call — the deterministic count/curve/bracket the model relies on to
    # know the deck size still comes from stored data. A failed backfill just
    # leaves some cards untagged (already surfaced via the "untagged" list and
    # the floor caveat), which is strictly better than the tool erroring out and
    # the model falling back to counting the list by hand.
    # `tags is None` means never looked up; `[]` means looked up and the card
    # has no community tags. Treating the two alike made every stats call for
    # a deck with one untagged card refetch it from Scryfall, forever.
    missing = [c for c in cards if not c.oracle_id or c.tags is None]
    if missing:
        from app.knowledge.tag_lookup import get_tags_for_card
        from app.tools.scryfall_client import get_scryfall_client
        try:
            scryfall = get_scryfall_client()
            result = scryfall.collection([c.card_name for c in missing])
            found_map = {c["name"]: c for c in result["found"]}
            for c in missing:
                if c.card_name in found_map:
                    fc = found_map[c.card_name]
                    oid = c.oracle_id or fc.get("oracle_id")
                    if not c.type_line:
                        c.type_line = fc.get("type_line")
                    if not c.oracle_text:
                        c.oracle_text = fc.get("oracle_text", "")
                    if not c.oracle_id:
                        c.oracle_id = oid
                    if c.tags is None and oid:
                        c.tags = get_tags_for_card(oid)
                    session.add(c)
            session.commit()
        except Exception:  # noqa: BLE001 - enrichment is optional, never fatal
            session.rollback()

    total_cards = sum(c.quantity for c in cards)
    if total_cards == 0:
        return _empty_stats()

    deck = repo.get_deck(session, deck_id)
    key = _content_key(cards, deck)
    base = _memoised_base(key)
    if base is None:
        base = _deterministic_stats(session, cards, deck_id, total_cards)
        _remember_base(key, base)
    base = copy.deepcopy(base)

    power_base = base["power_level_base"]
    power_factors = list(base["power_factors"])

    # Nuance is a bonus on top of the deterministic base; a provider/DB failure
    # here must never sink the stats call.
    try:
        power_level, power_nuance_adj, power_nuance_reason, nuance_pending = (
            _resolve_power_nuance(
                session, deck, deck_id, power_base, power_factors, provider,
            )
        )
    except Exception:  # noqa: BLE001 - fall back to the deterministic base score
        session.rollback()
        power_level, power_nuance_adj, power_nuance_reason = power_base, 0.0, ""
        nuance_pending = False
    if power_nuance_adj:
        sign = "+" if power_nuance_adj > 0 else ""
        power_factors = [
            *power_factors,
            f"LLM nuance: {sign}{power_nuance_adj} ({power_nuance_reason})",
        ]

    base.update({
        "power_level": power_level,
        "power_nuance_adj": power_nuance_adj,
        "power_nuance_reason": power_nuance_reason,
        "power_nuance_pending": nuance_pending,
        "power_factors": power_factors,
    })
    return base


# Deterministic stats per deck content. The turn header, the stats panel, and
# the model's own stats call ask for the same numbers within seconds of each
# other, and the role detection, combo query, and index lookups behind them
# are the expensive part. Short-lived, because the inputs that are not the
# deck (the card index landing, the combo import, the Game Changers flag)
# change underneath it.
STATS_MEMO_SECONDS = 60.0
_STATS_MEMO_MAX = 32
_stats_memo: dict[str, tuple[float, dict]] = {}


def _content_key(cards: list[Any], deck: Any) -> str:
    """Hash of everything the deterministic stats read: the cards, and the
    deck fields that shape them (commanders for mana sources, format for the
    targets)."""
    rows = sorted(
        (
            c.card_name, int(c.quantity or 0), c.oracle_id or "", c.type_line or "",
            c.mana_value if c.mana_value is not None else -1, c.color_identity or "",
            tuple(c.tags or []), c.category or "",
        )
        for c in cards
    )
    payload = json.dumps({
        "deck": getattr(deck, "id", None),
        "format": getattr(deck, "format", None),
        "commanders": [getattr(deck, "commander", None), getattr(deck, "partner_commander", None)],
        "cards": rows,
    }, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _memoised_base(key: str) -> dict | None:
    hit = _stats_memo.get(key)
    if hit is None:
        return None
    stamped, base = hit
    if time.monotonic() - stamped > STATS_MEMO_SECONDS:
        _stats_memo.pop(key, None)
        return None
    return base


def _remember_base(key: str, base: dict) -> None:
    if len(_stats_memo) >= _STATS_MEMO_MAX:
        _stats_memo.pop(next(iter(_stats_memo)))
    _stats_memo[key] = (time.monotonic(), copy.deepcopy(base))


def clear_stats_memo() -> None:
    _stats_memo.clear()


def _deterministic_stats(
    session: Session, cards: list[Any], deck_id: int, total_cards: int
) -> dict:
    """Everything in the stats payload that follows from the cards alone."""
    curve_buckets: dict[str, int] = {"0-1": 0, "2": 0, "3": 0, "4": 0, "5": 0, "6+": 0}
    nonland_mvs: list[float] = []
    color_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    land_count = 0
    # Nonland cards with no functional tags: role detection can't see them,
    # so ramp/draw/removal counts may undercount. Surfaced in the result so
    # the model can caveat its numbers instead of trusting them blindly.
    untagged: list[str] = []

    for c in cards:
        bucket = _classify_type(c.type_line)
        type_counts[bucket] = type_counts.get(bucket, 0) + c.quantity
        if bucket == "Land":
            land_count += c.quantity
        else:
            if c.mana_value is not None:
                mvs = [c.mana_value] * c.quantity
                nonland_mvs.extend(mvs)
                for _ in range(c.quantity):
                    if c.mana_value <= 1:
                        curve_buckets["0-1"] += 1
                    elif c.mana_value <= 2:
                        curve_buckets["2"] += 1
                    elif c.mana_value <= 3:
                        curve_buckets["3"] += 1
                    elif c.mana_value <= 4:
                        curve_buckets["4"] += 1
                    elif c.mana_value <= 5:
                        curve_buckets["5"] += 1
                    else:
                        curve_buckets["6+"] += 1

        if c.color_identity:
            for ch in c.color_identity:
                color_counts[ch] = color_counts.get(ch, 0) + c.quantity

        # Roles come from Scryfall community tags (multi-match — a card can
        # be both ramp and draw). A nonland card with no tags can't be
        # role-detected; record it so the caller knows the counts may be low.
        roles = _detect_roles(c.type_line, c.oracle_text or "", c.category, c.oracle_id, c.tags)
        for role in roles:
            category_counts[role] = category_counts.get(role, 0) + c.quantity
        if bucket != "Land" and not c.tags:
            untagged.append(c.card_name)

    mana_curve = [{"mv": k, "count": v} for k, v in curve_buckets.items()]
    avg_mv = round(sum(nonland_mvs) / len(nonland_mvs), 1) if nonland_mvs else 0

    # Color percentages are based on cards that actually have a colour
    # identity, so lands (colourless) don't dilute the numbers.
    colored_total = sum(
        c.quantity for c in cards if c.color_identity and c.color_identity.strip()
    ) or 1
    color_distribution = [
        {"color": color, "count": count, "pct": round(count / colored_total * 100)}
        for color, count in sorted(color_counts.items(), key=lambda x: -x[1])
    ]

    type_order = ["Creature", "Instant", "Sorcery", "Artifact", "Enchantment", "Planeswalker", "Battle", "Land", "Other", "Unknown"]
    type_breakdown = [
        {"type": t, "count": type_counts[t], "pct": round(type_counts[t] / total_cards * 100)}
        for t in type_order if t in type_counts
    ]

    ramp_count = category_counts.get("ramp", 0)
    draw_count = category_counts.get("draw", 0)
    removal_count = category_counts.get("removal", 0)
    # The "removal" role already includes counterspells, sweepers, and bounce
    # (see _tag_to_role), so interaction == removal here. Kept as a separate
    # name because the scoring functions read "interaction" conceptually.
    interaction_count = removal_count

    land_pct = round(land_count / total_cards * 100)
    card_names = {c.card_name for c in cards}

    combos = _deck_combos(card_names)
    power_base, power_factors = _estimate_power_level(
        avg_mv, land_count, ramp_count, draw_count, interaction_count, total_cards)
    bracket, bracket_factors = _estimate_bracket(
        card_names, avg_mv, land_count, ramp_count, interaction_count, type_counts, total_cards)
    bracket, bracket_factors = _apply_combo_floor(bracket, bracket_factors, combos)

    deck = repo.get_deck(session, deck_id)
    deficiencies = _compute_deficiencies(
        deck.format if deck else "commander",
        land_count, ramp_count, draw_count, removal_count,
    )

    # Price and mana sources both come from the card index, in one lookup.
    # Neither is critical: without the index both come back empty.
    index_cards = _index_cards_for(cards)
    total_price, priced = _deck_price(cards, index_cards)
    commander_names = {
        n.lower()
        for n in ((deck.commander if deck else None), (deck.partner_commander if deck else None))
        if n
    }
    mana_sources = _mana_sources(cards, index_cards, commander_names=commander_names)

    return {
        "mana_curve": mana_curve,
        "avg_mv": avg_mv,
        "color_distribution": color_distribution,
        "type_breakdown": type_breakdown,
        "land_count": land_count,
        "land_pct": land_pct,
        "ramp_count": ramp_count,
        "draw_count": draw_count,
        "removal_count": removal_count,
        "total_cards": total_cards,
        "power_level": power_base,
        "power_level_base": power_base,
        "power_nuance_adj": 0.0,
        "power_nuance_reason": "",
        "power_nuance_pending": False,
        "power_factors": power_factors,
        "bracket": bracket,
        "bracket_factors": bracket_factors,
        "untagged": untagged,
        "deficiencies": deficiencies,
        "total_price_usd": total_price,
        "priced_cards": priced,
        "mana_sources": mana_sources,
        "combos": combos,
    }


def _deck_combos(card_names: set[str]) -> list[dict]:
    """Combos the deck already contains, trimmed for the stats payload."""
    try:
        from app.cards import combos as combo_db

        found = combo_db.combos_in_deck(card_names, limit=20)
    except Exception:  # noqa: BLE001 - the combo table is optional
        return []
    return [
        {
            "cards": c["cards"],
            "produces": c["produces"][:4],
            "description": (c["description"] or "")[:400],
            "card_count": c["card_count"],
        }
        for c in found
    ]


def _apply_combo_floor(bracket: int, factors: list[str], combos: list[dict]) -> tuple[int, list[str]]:
    """Brackets 1 and 2 exclude two-card infinite combos. A deck that has one
    is at least a bracket 3, whatever its card names said."""
    two_card = [c for c in combos if c["card_count"] == 2]
    if not two_card:
        return bracket, factors
    listing = "; ".join(" + ".join(c["cards"]) for c in two_card[:4])
    factors = [*factors, f"2-card combos ({len(two_card)}): {listing}"]
    if bracket < 3:
        factors[0] = f"Bracket 3: two-card combo present ({two_card[0]['cards'][0]} + {two_card[0]['cards'][1]})"
        return 3, factors
    return bracket, factors


def _index_cards_for(cards: list[Any]) -> dict[str, dict]:
    """lower(name) -> index row for the deck's cards; empty without an index."""
    try:
        from app.cards import store as card_store

        return card_store.by_names([c.card_name for c in cards])
    except Exception:  # noqa: BLE001 - the index is optional
        return {}


def _deck_price(cards: list[Any], index_cards: dict[str, dict]) -> tuple[float | None, int]:
    """Sum of the deck's card prices at the index's representative printing,
    and how many cards had a price. None when nothing is priced."""
    total = 0.0
    priced = 0
    for c in cards:
        row = index_cards.get(c.card_name.lower())
        price = row.get("price_usd") if row else None
        if isinstance(price, (int, float)):
            total += price * c.quantity
            priced += c.quantity
    return (round(total, 2) if priced else None), priced


_PIP_COLORS = ("W", "U", "B", "R", "G")


def _pips_in_cost(mana_cost: str | None) -> dict[str, float]:
    """Coloured pips per colour in a mana cost. A hybrid symbol counts a half
    for each of its colours; Phyrexian and generic symbols count for the colour
    they name or nothing."""
    counts: dict[str, float] = {c: 0.0 for c in _PIP_COLORS}
    if not mana_cost:
        return counts
    for symbol in re.findall(r"\{([^}]+)\}", mana_cost):
        colours = [part for part in symbol.upper().split("/") if part in _PIP_COLORS]
        if not colours:
            continue
        share = 1.0 / len(colours)
        for colour in colours:
            counts[colour] += share
    return counts


def _mana_sources(
    cards: list[Any], index_cards: dict[str, dict], *, commander_names: set[str]
) -> list[dict]:
    """Per colour: how many cards produce it against how many pips ask for it.

    The classic mana-base check. A colour whose share of sources falls well
    short of its share of pips is the one that will miss its drops. Sources
    count every producer (lands, rocks, dorks) weighted by quantity; pips
    count the coloured symbols in nonland, non-commander cards. Empty when the
    index has nothing to say.
    """
    if not index_cards:
        return []
    sources: dict[str, float] = {c: 0.0 for c in _PIP_COLORS}
    pips: dict[str, float] = {c: 0.0 for c in _PIP_COLORS}
    for c in cards:
        row = index_cards.get(c.card_name.lower())
        if not row:
            continue
        for colour in row.get("produced_mana") or []:
            if colour in sources:
                sources[colour] += c.quantity
        if "land" in (row.get("type_line") or "").lower():
            continue
        if c.card_name.lower() in commander_names:
            continue
        for colour, n in _pips_in_cost(row.get("mana_cost")).items():
            pips[colour] += n * c.quantity

    total_sources = sum(sources.values()) or 1.0
    total_pips = sum(pips.values()) or 1.0
    out: list[dict] = []
    for colour in _PIP_COLORS:
        if sources[colour] == 0 and pips[colour] == 0:
            continue
        source_pct = round(sources[colour] / total_sources * 100)
        pip_pct = round(pips[colour] / total_pips * 100)
        # Ten points short is a colour that will come up missing in a real
        # game; the threshold is deliberately coarse, since produced_mana
        # counts a triome and a basic alike.
        status = "LOW" if pips[colour] > 0 and source_pct + 10 < pip_pct else "OK"
        out.append({
            "color": colour,
            "sources": int(sources[colour]),
            "pips": round(pips[colour], 1),
            "source_pct": source_pct,
            "pip_pct": pip_pct,
            "status": status,
        })
    return out




def _estimate_power_level(avg_mv: float, land_count: int, ramp_count: int,
                           draw_count: int, interaction_count: int,
                           total_cards: int) -> tuple[int, list[str]]:
    """Estimate traditional 1-10 power level from deckbuilding fundamentals.

    Returns ``(score, factors)`` where *factors* is a human-readable
    list explaining each dimension's contribution.
    """
    factors: list[str] = []

    # Land count
    if land_count >= 36:
        land_score = 0.5
        factors.append(f"Lands: {land_count} (casual — safe but slow) +0.5")
    elif land_count >= 33:
        land_score = 1.0
        factors.append(f"Lands: {land_count} (focused sweet spot) +1.0")
    elif land_count >= 28:
        land_score = 2.0
        factors.append(f"Lands: {land_count} (cEDH range) +2.0")
    else:
        land_score = 1.5
        factors.append(f"Lands: {land_count} (risky low) +1.5")

    # Ramp
    if ramp_count >= 14:
        ramp_score = 2.0
        factors.append(f"Ramp: {ramp_count} cards (cEDH density) +2.0")
    elif ramp_count >= 10:
        ramp_score = 1.5
        factors.append(f"Ramp: {ramp_count} cards (optimized) +1.5")
    elif ramp_count >= 8:
        ramp_score = 1.0
        factors.append(f"Ramp: {ramp_count} cards (focused) +1.0")
    elif ramp_count >= 6:
        ramp_score = 0.5
        factors.append(f"Ramp: {ramp_count} cards (light) +0.5")
    else:
        ramp_score = 0.0
        factors.append(f"Ramp: {ramp_count} cards (very light) +0")

    # Draw
    if draw_count >= 15:
        draw_score = 2.0
        factors.append(f"Draw: {draw_count} cards (cEDH density) +2.0")
    elif draw_count >= 12:
        draw_score = 1.5
        factors.append(f"Draw: {draw_count} cards (optimized) +1.5")
    elif draw_count >= 8:
        draw_score = 1.0
        factors.append(f"Draw: {draw_count} cards (focused) +1.0")
    elif draw_count >= 5:
        draw_score = 0.5
        factors.append(f"Draw: {draw_count} cards (light) +0.5")
    else:
        draw_score = 0.0
        factors.append(f"Draw: {draw_count} cards (very light) +0")

    # Interaction
    if interaction_count >= 15:
        int_score = 2.0
        factors.append(f"Interaction: {interaction_count} cards (cEDH density) +2.0")
    elif interaction_count >= 13:
        int_score = 1.5
        factors.append(f"Interaction: {interaction_count} cards (optimized) +1.5")
    elif interaction_count >= 10:
        int_score = 1.0
        factors.append(f"Interaction: {interaction_count} cards (focused) +1.0")
    elif interaction_count >= 5:
        int_score = 0.5
        factors.append(f"Interaction: {interaction_count} cards (light) +0.5")
    else:
        int_score = 0.0
        factors.append(f"Interaction: {interaction_count} cards (very light) +0")

    # Curve
    if avg_mv <= 0:
        curve_score = 0.0
    elif avg_mv <= 2.0:
        curve_score = 2.0
        factors.append(f"Avg MV: {avg_mv} (very fast) +2.0")
    elif avg_mv <= 2.5:
        curve_score = 1.5
        factors.append(f"Avg MV: {avg_mv} (fast) +1.5")
    elif avg_mv <= 3.0:
        curve_score = 1.0
        factors.append(f"Avg MV: {avg_mv} (moderate) +1.0")
    elif avg_mv <= 3.5:
        curve_score = 0.5
        factors.append(f"Avg MV: {avg_mv} (slow) +0.5")
    else:
        curve_score = 0.0
        factors.append(f"Avg MV: {avg_mv} (very slow) +0")

    raw = land_score + ramp_score + draw_score + int_score + curve_score
    level = min(10, max(1, round(raw + 1)))
    factors.insert(0, f"Raw: {raw:.1f} + 1 = {level}/10")
    return level, factors


def _estimate_bracket(card_names: set[str], avg_mv: float, land_count: int,
                       ramp_count: int, interaction_count: int,
                       type_counts: dict[str, int],
                       total_cards: int) -> tuple[int, list[str]]:
    """Estimate Commander bracket (1–5) using the official WotC criteria.

    Returns ``(bracket, factors)`` where *factors* explains the result.
    """
    # Normalise to lowercase for matching — card names from Scryfall are
    # canonical but the sets might have slight variations.
    names_lower = {n.lower(): n for n in card_names}

    def _match(needles: set[str]) -> set[str]:
        found: set[str] = set()
        for needle in needles:
            original = names_lower.get(needle.lower())
            if original:
                found.add(original)
        return found

    gc = _match(game_changer_names())
    tutors = _match(_TUTORS)
    mld = _match(_MLD_CARDS)
    fast = _match(_FAST_MANA)

    factors: list[str] = []
    if gc:
        factors.append(f"Game Changers ({len(gc)}): {', '.join(sorted(gc))}")
    else:
        factors.append("Game Changers: none")
    if tutors:
        factors.append(f"Tutors ({len(tutors)}): {', '.join(sorted(tutors))}")
    if mld:
        factors.append(f"MLD/Stax: {', '.join(sorted(mld))}")
    if fast:
        factors.append(f"Fast mana: {', '.join(sorted(fast))}")

    gc_count = len(gc)
    # Tutors are still surfaced as a factor (useful context for the player and
    # the model) but no longer drive the bracket — see the October 2025 note in
    # the Bracket 3 block below.
    has_mld = bool(mld)
    fast_mana_count = len(fast)

    # Bracket 5
    if gc_count >= 6 or (gc_count >= 4 and avg_mv <= 1.8 and fast_mana_count >= 4):
        factors.insert(0, f"Bracket 5: cEDH profile ({gc_count} GCs, MV {avg_mv})")
        return 5, factors

    # Bracket 4
    if has_mld or gc_count >= 4:
        reason = "MLD present" if has_mld else f"{gc_count} Game Changers"
        factors.insert(0, f"Bracket 4: {reason}")
        return 4, factors
    if gc_count >= 2 and avg_mv <= 2.2 and fast_mana_count >= 2:
        factors.insert(0, f"Bracket 4: fast combo profile ({gc_count} GCs, MV {avg_mv}, {fast_mana_count} fast mana)")
        return 4, factors

    # Bracket 3 — up to 3 Game Changers is the defining allowance.
    if gc_count >= 1:
        factors.insert(0, f"Bracket 3: {gc_count} Game Changer(s) present (Bracket 3 allows up to 3)")
        return 3, factors
    # NB: tutor COUNT is deliberately not a bracket driver. Tutor limits were
    # removed from every bracket in the October 2025 update — only tutors that
    # are themselves on the Game Changers list (Demonic Tutor, Vampiric Tutor,
    # Imperial Seal, Crop Rotation) move a deck, and they do it through
    # gc_count above. Counting plain tutors (Diabolic Tutor, Expedition Map,
    # Fabricate) dragged thematic decks up two brackets and made the tool quote
    # a retired rule back at the player.
    if avg_mv <= 2.5 and ramp_count >= 10 and interaction_count >= 10:
        factors.insert(
            0,
            f"Bracket 3: tuned fundamentals (MV {avg_mv}, {ramp_count} ramp, {interaction_count} interaction)",
        )
        return 3, factors

    # Bracket 2
    if land_count >= 35 and ramp_count >= 8 and avg_mv <= 3.5:
        factors.insert(0, "Bracket 2: solid precon fundamentals")
        return 2, factors

    # Bracket 1
    factors.insert(0, "Bracket 1: exhibition — no Game Changers")
    return 1, factors


# Commander deckbuilding targets (mirrors the knowledge-base entries for
# land/ramp/draw/removal). Each is (low, high) — below low is LOW, above high
# is HIGH, within is OK. Only applied to the commander format; other formats
# have very different targets, so we skip the check rather than mislead.
_COMMANDER_TARGETS: dict[str, tuple[int, int]] = {
    "lands": (33, 40),
    "ramp": (10, 12),
    "draw": (10, 12),
    "removal": (10, 15),
}


def _compute_deficiencies(
    format_key: str, land_count: int, ramp_count: int,
    draw_count: int, removal_count: int,
) -> list[dict]:
    """Compare key deckbuilding counts against commander targets.

    Returns a list of ``{category, count, target_low, target_high, status}``
    where status is "LOW"/"OK"/"HIGH", so the model can give consistent,
    grounded fix advice without re-deriving the targets each turn. Empty for
    non-commander formats (their targets differ enough to mislead here).
    """
    if format_key != "commander":
        return []
    counts = {
        "lands": land_count, "ramp": ramp_count,
        "draw": draw_count, "removal": removal_count,
    }
    out: list[dict] = []
    for cat, (low, high) in _COMMANDER_TARGETS.items():
        n = counts[cat]
        status = "LOW" if n < low else "HIGH" if n > high else "OK"
        out.append({
            "category": cat, "count": n,
            "target_low": low, "target_high": high, "status": status,
        })
    return out


def _empty_stats() -> dict:
    return {
        "mana_curve": [],
        "avg_mv": 0,
        "color_distribution": [],
        "type_breakdown": [],
        "land_count": 0,
        "land_pct": 0,
        "ramp_count": 0,
        "draw_count": 0,
        "removal_count": 0,
        "total_cards": 0,
        "power_level": 1,
        "power_level_base": 1,
        "power_nuance_adj": 0.0,
        "power_nuance_reason": "",
        "power_factors": [],
        "power_nuance_pending": False,
        "bracket": 1,
        "bracket_factors": [],
        "untagged": [],
        "deficiencies": [],
        "total_price_usd": None,
        "priced_cards": 0,
        "mana_sources": [],
        "combos": [],
    }


# ── Proposals ────────────────────────────────────────────────────────────


