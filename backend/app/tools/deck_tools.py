"""Deck-state tool functions: let the LLM read/mutate the in-progress deck
during conversation, so the UI side panel stays in sync with what's agreed
on. Card additions are validated and enriched via Scryfall before insert.
"""

from __future__ import annotations

import re
from typing import Any

from sqlmodel import Session

from app.db import repository as repo
from app.db.models import DeckProposal
from app.knowledge.tag_lookup import get_tags_for_card
from app.tools.scryfall_client import ScryfallNotFoundError, get_scryfall_client

_LINE_RE = re.compile(r"^(?:(\d+)\s*x?\s+)?(.+)$")


def _normalize_category(category: str | None) -> str | None:
    if not category:
        return None
    return category.strip().lower().title()


def _enrich(card: dict[str, Any]) -> dict[str, Any]:
    """Project a Scryfall card dict into the enriched fields import/add store."""
    return {
        "canonical_name": card["name"],
        "cmc": card["cmc"],
        "color_identity": "".join(card["color_identity"] or []),
        "type_line": card.get("type_line"),
        "oracle_text": card.get("oracle_text") or "",
        "oracle_id": card.get("oracle_id"),
    }


def _resolve_card(scryfall: Any, name: str) -> dict[str, Any]:
    """Validate a single card name against Scryfall and return enriched fields."""
    try:
        card = scryfall.named(name, fuzzy=True)
    except ScryfallNotFoundError:
        raise ValueError(f"No Scryfall card found matching '{name}'")
    return _enrich(card)


def _resolve_many(scryfall: Any, names: list[str]) -> dict[str, dict[str, Any]]:
    """Resolve many card names at once, keyed by the input name (lowercased).

    Uses Scryfall's batch ``/cards/collection`` endpoint (75 names/request) for
    the bulk of the work — one round-trip per 75 cards instead of one per card.
    ``collection`` matches names exactly, so any name it can't find falls back
    to a per-card fuzzy ``named`` lookup (handles minor misspellings/punctuation
    in hand-typed lists). Names that resolve nowhere are simply absent from the
    returned map; the caller reports them as errors.
    """
    resolved: dict[str, dict[str, Any]] = {}
    unique = list(dict.fromkeys(names))  # de-dupe, preserve order
    if not unique:
        return resolved

    result = scryfall.collection(unique)
    # Map found cards back to the requested name. collection() returns canonical
    # names; match case-insensitively against the request so "sol ring" resolves.
    found_by_lower = {c["name"].lower(): c for c in result.get("found", [])}
    not_found: list[str] = []
    for name in unique:
        card = found_by_lower.get(name.lower())
        if card is not None:
            resolved[name.lower()] = _enrich(card)
        else:
            not_found.append(name)

    # Fuzzy fallback only for the misses — keeps clean lists at zero extra calls.
    for name in not_found:
        try:
            resolved[name.lower()] = _resolve_card(scryfall, name)
        except ValueError:
            continue

    return resolved


def category_for_card(type_line: str | None, oracle_text: str,
                      oracle_id: str | None = None,
                      tags: list[str] | None = None) -> str | None:
    """Derive a card's single display category from its Scryfall tags.

    Uses the same guarded role detection that scoring uses, then picks one
    role by priority (land > ramp > draw > removal) so a multi-role card
    (e.g. ramp + draw) groups under its most defining role. Returns None when
    no functional role is detected (the card shows as Uncategorized). Note:
    the "Commander" designation is applied by the caller (deck_snapshot), not
    here — it's a deck role, not a functional tag.
    """
    roles = _detect_roles(type_line, oracle_text, oracle_id=oracle_id, tags=tags)
    for role in ("land", "ramp", "draw", "removal"):
        if role in roles:
            return role
    return None


# Backwards-compatible alias used by import/add paths that still store a
# category column (the stored value is now vestigial for display, which
# deck_snapshot computes from tags, but harmless to keep populated).
_auto_categorize = category_for_card


def _roles_from_tag_set(tags: set[str] | list[str], type_line: str | None) -> set[str]:
    """Map a set of Scryfall oracle tags to internal roles, applying the
    land/equipment guards so generic mana tags on lands and land-exiling
    equipment don't get miscounted as ramp."""
    from app.knowledge.tag_lookup import _tag_to_role

    roles: set[str] = set()
    tl = (type_line or "").lower()
    is_land = "land" in tl
    if is_land:
        roles.add("land")
    for tag in tags:
        role = _tag_to_role(tag)
        if not role:
            continue
        # Lands only count as ramp if explicitly tagged as land-ramp or a
        # real accelerant.  Generic mana tags on lands (Bojuka Bog, Castle
        # Locthwain) don't make them ramp.
        if is_land and role == "ramp":
            if not (tag.endswith("-ramp") or tag in (
                "ramp", "mana-rock", "mana-dork", "ritual", "ritual-untap",
            )):
                continue
        # Equipment that searches for lands (Strata Scythe) exiles them — not ramp.
        if role == "ramp" and "equipment" in tl and not tag.endswith("-ramp"):
            continue
        roles.add(role)
    return roles


def _detect_roles(type_line: str | None, oracle_text: str,
                  stored_category: str | None = None,
                  oracle_id: str | None = None,
                  tags: list[str] | None = None) -> set[str]:
    """Return ALL roles a card fills, from Scryfall Tagger community tags.

    Tags are the source of truth: they encode functional nuance (impulse
    draw counts as draw, mana-fix does NOT count as ramp, counterspells and
    sweepers count as removal) that oracle-text regex can't match reliably.
    Uses stored tags first, then an ``oracle_id`` lookup.  An untagged card
    (rare — usually a basic land or a brand-new card not yet community-tagged)
    gets only its land role from the type line; it is NOT role-guessed from
    oracle text, which would silently disagree with the tags.
    """
    from app.knowledge.tag_lookup import get_tag_lookup

    if tags:
        return _roles_from_tag_set(tags, type_line)

    if oracle_id:
        return _roles_from_tag_set(get_tag_lookup().get(oracle_id, set()), type_line)

    # No tags available: the only role we can assert from the type line
    # alone is land. Everything else waits for the tag backfill.
    return {"land"} if "land" in (type_line or "").lower() else set()


# ── Public tool functions ───────────────────────────────────────────────


def deck_get_current(session: Session, deck_id: int) -> dict:
    """The deck as it stands, plus what it is trying to become.

    The snapshot carries the raw plan fields; this attaches the computed
    current-vs-target view so the model reads "still needs: draw (short 4)"
    rather than deriving it from role targets and a 99-card list — arithmetic
    it does unreliably and would have to redo every turn.
    """
    from app import deckplan

    snapshot = repo.deck_snapshot(session, deck_id)
    plan = deckplan.build_plan(snapshot)
    snapshot["plan"] = {
        "themes": plan.themes,
        "notes": plan.notes,
        "off_meta": snapshot.get("off_meta"),
        "role_counts": {
            g.role: {"current": g.current, "target": g.target} for g in plan.gaps
        },
        "still_needs": {g.role: g.gap for g in plan.unmet},
        "is_set": plan.has_plan(),
        "counts_overlap": (
            "A card counts toward every role it fills, so a land that draws is "
            "in both. A role over target is not necessarily bloated, and these "
            "targets are rules of thumb rather than requirements."
        ),
    }
    return snapshot


def deck_get_stats(session: Session, deck_id: int, provider: Any | None = None) -> dict:
    """Return the deck's bracket, power level, and breakdown factors.

    ``provider`` enables the cached LLM power-level nuance (computed once per
    deck-content change); omitting it returns the deterministic base score plus
    any still-fresh cached nuance."""
    return compute_deck_stats(session, deck_id, provider)


def deck_add_card(
    session: Session,
    deck_id: int,
    card_name: str,
    qty: int = 1,
    category: str | None = None,
    notes: str | None = None,
) -> dict:
    scryfall = get_scryfall_client()
    try:
        card = scryfall.named(card_name, fuzzy=True)
    except ScryfallNotFoundError:
        raise ValueError(f"No Scryfall card found matching '{card_name}'")

    card_oracle_id = card.get("oracle_id")
    card_tags = get_tags_for_card(card_oracle_id)
    if not category:
        category = _auto_categorize(
            card.get("type_line"), card.get("oracle_text") or "",
            oracle_id=card_oracle_id, tags=card_tags,
        )

    repo.add_deck_card(
        session,
        deck_id=deck_id,
        card_name=card["name"],
        quantity=qty,
        category=_normalize_category(category),
        mana_value=card["cmc"],
        color_identity="".join(card["color_identity"] or []),
        type_line=card.get("type_line"),
        oracle_text=card.get("oracle_text"),
        oracle_id=card_oracle_id,
        tags=card_tags,
        notes=notes,
    )
    return repo.deck_snapshot(session, deck_id)


def deck_remove_card(
    session: Session, deck_id: int, card_name: str, quantity: int | None = None
) -> dict:
    repo.remove_deck_card(session, deck_id, card_name, quantity=quantity)
    return repo.deck_snapshot(session, deck_id)


def deck_set_commander(session: Session, deck_id: int, commander_name: str) -> dict:
    scryfall = get_scryfall_client()
    try:
        card = scryfall.named(commander_name, fuzzy=True)
    except ScryfallNotFoundError:
        raise ValueError(f"No Scryfall card found matching '{commander_name}'")

    repo.update_deck(session, deck_id, commander=card["name"])
    # Ensure the commander is in the card list.  Upsert so it always
    # has qty=1 and category="Commander" regardless of prior state.
    repo.add_deck_card(
        session,
        deck_id=deck_id,
        card_name=card["name"],
        quantity=1,
        category=_normalize_category("Commander"),
        mana_value=card["cmc"],
        color_identity="".join(card["color_identity"] or []),
        type_line=card.get("type_line"),
        oracle_text=card.get("oracle_text"),
        oracle_id=card.get("oracle_id"),
        tags=get_tags_for_card(card.get("oracle_id")),
    )
    return repo.deck_snapshot(session, deck_id)


def set_deck_commanders(
    session: Session,
    deck_id: int,
    commander_name: str | None,
    partner_commander_name: str | None = None,
) -> dict:
    """Designate the deck's commander(s) from cards already in the deck.

    Sets the ``commander``/``partner_commander`` fields, tags the chosen cards
    with the ``Commander`` category, and demotes any card previously tagged
    ``Commander`` that is no longer a commander back to its auto-detected role.
    Passing ``None`` for both clears the deck's commanders. No partner-legality
    checks are enforced — the second slot accepts any card.
    """
    if not repo.get_deck(session, deck_id):
        raise ValueError(f"Deck {deck_id} not found")

    chosen = [n for n in (commander_name, partner_commander_name) if n]
    chosen_lower = {n.lower() for n in chosen}

    for card in repo.list_deck_cards(session, deck_id):
        is_commander = card.card_name.lower() in chosen_lower
        currently_tagged = (card.category or "").lower() == "commander"
        if is_commander and not currently_tagged:
            card.category = _normalize_category("Commander")
            session.add(card)
        elif currently_tagged and not is_commander:
            card.category = _auto_categorize(
                card.type_line, card.oracle_text or "",
                oracle_id=card.oracle_id, tags=card.tags,
            )
            session.add(card)
    session.commit()

    repo.set_deck_commander_fields(
        session,
        deck_id,
        commander=commander_name or None,
        partner_commander=partner_commander_name or None,
    )
    return repo.deck_snapshot(session, deck_id)


def deck_update_notes(session: Session, deck_id: int, notes: str) -> dict:
    repo.update_deck(session, deck_id, notes=notes)
    return repo.deck_snapshot(session, deck_id)


# Roles the plan tracks. Anything else in role_targets is dropped rather than
# stored, so a hallucinated role name can't become a target nothing counts
# toward.
_PLAN_ROLES = ("land", "ramp", "draw", "removal")


def deck_set_plan(
    session: Session,
    deck_id: int,
    themes: list | None = None,
    role_targets: dict | None = None,
    plan_notes: str | None = None,
    off_meta: float | None = None,
) -> dict:
    """Record what the deck is TRYING to be.

    Every field is optional and only supplied ones are written, so the model can
    set a direction early and refine targets later without clobbering what it
    already recorded.

    Arguments are validated rather than trusted: unknown role names are dropped,
    counts coerced to non-negative ints, off_meta clamped to 0-1. This is a
    model-facing tool, so a plausible-but-wrong argument is a normal input, not
    an exceptional one.
    """
    clean_targets: dict[str, int] | None = None
    if isinstance(role_targets, dict):
        clean_targets = {}
        for role in _PLAN_ROLES:
            value = role_targets.get(role)
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                clean_targets[role] = max(0, int(value))
        if not clean_targets:
            clean_targets = None

    clean_themes: list[str] | None = None
    if isinstance(themes, list):
        clean_themes = [t.strip() for t in themes if isinstance(t, str) and t.strip()]

    clean_off_meta: float | None = None
    if isinstance(off_meta, (int, float)) and not isinstance(off_meta, bool):
        clean_off_meta = max(0.0, min(1.0, float(off_meta)))

    repo.update_deck(
        session, deck_id,
        themes=clean_themes,
        role_targets=clean_targets,
        plan_notes=plan_notes,
        off_meta=clean_off_meta,
    )
    return repo.deck_snapshot(session, deck_id)


def import_decklist(
    session: Session, deck_id: int, text: str, mode: str = "merge"
) -> dict:
    """Parse a decklist in text form and insert all cards into *deck_id*.

    Format accepted::

        1 Sol Ring
        1x Arcane Signet
        //Ramp
        Cultivate
        2 Kodama's Reach

    Cards without an explicit ``//Category`` header are auto-categorised
    from their Scryfall oracle text.

    ``mode`` controls how the incoming list interacts with cards already in
    the deck:

    - ``"merge"`` (default): add the incoming quantities on top of whatever's
      there (a Sol Ring already in the deck ends up at qty 2).
    - ``"replace"``: wipe the deck's existing cards first, so the imported
      list *is* the deck. Only cleared once the list parses; a fully
      unparseable list leaves the deck untouched.
    """
    if mode not in ("merge", "replace"):
        raise ValueError(f"Unknown import mode {mode!r} (expected 'merge' or 'replace').")
    scryfall = get_scryfall_client()
    imported = 0
    cleared = 0
    errors: list[str] = []

    # Phase 1: parse every line into (qty, name, category) up front, so all
    # card names can be resolved against Scryfall in one batch rather than one
    # request per card (a 100-card list was ~100 sequential HTTP round-trips).
    parsed: list[tuple[int, str, str | None]] = []
    category: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("//"):
            category = line.removeprefix("//").strip() or None
            continue

        m = _LINE_RE.match(line)
        if not m:
            errors.append(f"Could not parse line: {line!r}")
            continue

        qty = int(m.group(1)) if m.group(1) else 1
        card_name = m.group(2).strip()
        if not card_name:
            errors.append(f"Empty card name in line: {line!r}")
            continue

        parsed.append((qty, card_name, category))

    # In replace mode, wipe the existing cards now that we know the list parsed
    # into at least one card — a fully-unparseable paste shouldn't nuke the deck.
    if mode == "replace" and parsed:
        cleared = repo.clear_deck_cards(session, deck_id)

    # Phase 2: batch-resolve all names, then insert.
    resolved_map = _resolve_many(scryfall, [name for _, name, _ in parsed])

    for qty, card_name, line_category in parsed:
        resolved = resolved_map.get(card_name.lower())
        if resolved is None:
            errors.append(f"No Scryfall card found matching '{card_name}'")
            continue

        resolved_tags = get_tags_for_card(resolved.get("oracle_id"))

        # Use explicit category if set, otherwise auto-detect
        card_category = _normalize_category(line_category)
        if not card_category:
            card_category = _auto_categorize(
                resolved["type_line"], resolved["oracle_text"],
                oracle_id=resolved.get("oracle_id"), tags=resolved_tags,
            )

        repo.add_deck_card(
            session,
            deck_id=deck_id,
            card_name=resolved["canonical_name"],
            quantity=qty,
            category=card_category,
            mana_value=resolved["cmc"],
            color_identity=resolved["color_identity"],
            type_line=resolved["type_line"],
            oracle_text=resolved.get("oracle_text"),
            oracle_id=resolved.get("oracle_id"),
            tags=resolved_tags,
            notes=None,
        )
        imported += 1

    snapshot = repo.deck_snapshot(session, deck_id)
    snapshot["_import"] = {
        "imported": imported, "errors": errors, "mode": mode, "cleared": cleared,
    }
    return snapshot


# ── Stats ────────────────────────────────────────────────────────────────


def _classify_type(type_line: str | None) -> str:
    """Map a Scryfall ``type_line`` to a broad card-type bucket."""
    if not type_line:
        return "Unknown"
    t = type_line.lower()
    if "land" in t:
        return "Land"
    if "creature" in t:
        return "Creature"
    if "instant" in t:
        return "Instant"
    if "sorcery" in t:
        return "Sorcery"
    if "artifact" in t:
        return "Artifact"
    if "enchantment" in t:
        return "Enchantment"
    if "planeswalker" in t:
        return "Planeswalker"
    if "battle" in t:
        return "Battle"
    return "Other"


def _resolve_power_nuance(
    session: Session,
    deck: Any,
    deck_id: int,
    base_score: int,
    base_factors: list[str],
    provider: Any | None,
) -> tuple[int, float, str]:
    """Return ``(nuanced_score, adjustment, reason)`` for the power level.

    Cache-first: if the deck's stored ``power_nuance_key`` matches the current
    deck-content hash, reuse the cached adjustment (free — no LLM call), whether
    or not a provider is present. On a miss WITH a provider, compute the nuance,
    cache it, and apply. On a miss WITHOUT a provider (grounding, pipeline), skip
    the LLM and return the base score unadjusted rather than blocking. Only
    applied to the commander format — power level is a commander concept here.
    """
    from app.tools.power_nuance import compute_nuance, deck_content_hash

    if deck is None or deck.format != "commander":
        return base_score, 0.0, ""

    snapshot = repo.deck_snapshot(session, deck_id)
    key = deck_content_hash(snapshot)

    if deck.power_nuance_key == key and deck.power_nuance_adj is not None:
        adj, reason = deck.power_nuance_adj, deck.power_nuance_reason or ""
    elif provider is not None:
        adj, reason = compute_nuance(provider, snapshot, base_score, base_factors)
        repo.set_deck_power_nuance(session, deck_id, adj, reason, key)
    else:
        return base_score, 0.0, ""

    # Keep the half-point: base is an integer band, adj is a multiple of 0.5, so
    # the sum is a clean .0/.5. Don't round — that would (a) use banker's rounding
    # (7.5->8 but 6.5->6, making a -0.5 a no-op on even bases) and (b) discard the
    # ±0.5 granularity the nuance exists to add. The frontend renders fractions.
    nuanced = min(10.0, max(1.0, base_score + adj))
    return nuanced, adj, reason


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
    missing = [c for c in cards if not c.oracle_id or not c.tags]
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
                    if not c.tags:
                        c.tags = get_tags_for_card(oid)
                    session.add(c)
            session.commit()
        except Exception:  # noqa: BLE001 - enrichment is optional, never fatal
            session.rollback()

    total_cards = sum(c.quantity for c in cards)
    if total_cards == 0:
        return _empty_stats()

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

    power_base, power_factors = _estimate_power_level(
        avg_mv, land_count, ramp_count, draw_count, interaction_count, total_cards)
    bracket, bracket_factors = _estimate_bracket(
        card_names, avg_mv, land_count, ramp_count, interaction_count, type_counts, total_cards)

    deck = repo.get_deck(session, deck_id)
    deficiencies = _compute_deficiencies(
        deck.format if deck else "commander",
        land_count, ramp_count, draw_count, removal_count,
    )

    # Nuance is a bonus on top of the deterministic base; a provider/DB failure
    # here must never sink the stats call (see the backfill rationale above).
    try:
        power_level, power_nuance_adj, power_nuance_reason = _resolve_power_nuance(
            session, deck, deck_id, power_base, power_factors, provider,
        )
    except Exception:  # noqa: BLE001 - fall back to the deterministic base score
        session.rollback()
        power_level, power_nuance_adj, power_nuance_reason = power_base, 0.0, ""
    if power_nuance_adj:
        sign = "+" if power_nuance_adj > 0 else ""
        power_factors = [
            *power_factors,
            f"LLM nuance: {sign}{power_nuance_adj} ({power_nuance_reason})",
        ]

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
        "power_level": power_level,
        "power_level_base": power_base,
        "power_nuance_adj": power_nuance_adj,
        "power_nuance_reason": power_nuance_reason,
        "power_factors": power_factors,
        "bracket": bracket,
        "bracket_factors": bracket_factors,
        "untagged": untagged,
        "deficiencies": deficiencies,
    }


# ── Card-name sets for bracket estimation ──────────────────────────────
# Official Commander Game Changers list as of the February 9, 2026 brackets
# update (the June 29, 2026 B&R made no Commander changes). 53 cards, matching
# Scryfall's `is:gamechanger` query — the canonical machine-readable source.
# When WotC next revises the list, re-run `is:gamechanger` and reconcile.
# https://magic.wizards.com/en/news/announcements/commander-brackets-beta-update-february-9-2026
_GAME_CHANGERS: set[str] = {
    # White
    "Drannith Magistrate", "Enlightened Tutor", "Farewell", "Humility",
    "Serra's Sanctum", "Smothering Tithe", "Teferi's Protection",
    # Blue
    "Consecrated Sphinx", "Cyclonic Rift", "Fierce Guardianship", "Force of Will",
    "Gifts Ungiven", "Intuition", "Mystical Tutor", "Narset, Parter of Veils",
    "Rhystic Study", "Thassa's Oracle",
    # Black
    "Ad Nauseam", "Bolas's Citadel", "Braids, Cabal Minion", "Demonic Tutor",
    "Imperial Seal", "Necropotence", "Opposition Agent", "Orcish Bowmasters",
    "Tergrid, God of Fright", "Vampiric Tutor",
    # Red
    "Gamble", "Jeska's Will", "Underworld Breach",
    # Green
    "Biorhythm", "Crop Rotation", "Gaea's Cradle", "Natural Order",
    "Seedborn Muse", "Survival of the Fittest", "Worldly Tutor",
    # Multicolor
    "Aura Shards", "Coalition Victory", "Grand Arbiter Augustin IV",
    "Notion Thief",
    # Colorless / Artifacts
    "Chrome Mox", "Grim Monolith", "Lion's Eye Diamond", "Mana Vault",
    "Mox Diamond", "Panoptic Mirror", "The One Ring",
    # Lands
    "Ancient Tomb", "Field of the Dead", "Glacial Chasm", "Mishra's Workshop",
    "The Tabernacle at Pendrell Vale",
}

# Cards banned in Commander (EDH) as of the official banned & restricted list
# current on 2026-07-01 (the June 29, 2026 B&R made no Commander changes; the
# most recent Commander change was February 9, 2026, which unbanned Biorhythm
# and left Lutri banned only as a companion). Excludes the broad category bans
# (ante cards, Conspiracies, stickers/Attractions, offensive cards).
# https://magic.wizards.com/en/banned-restricted-list
_BANNED_COMMANDER: set[str] = {
    "Ancestral Recall", "Balance", "Black Lotus", "Chaos Orb", "Channel",
    "Dockside Extortionist", "Emrakul, the Aeons Torn",
    "Erayo, Soratami Ascendant", "Falling Star", "Fastbond", "Flash",
    "Golos, Tireless Pilgrim", "Griselbrand", "Hullbreacher",
    "Iona, Shield of Emeria", "Jeweled Lotus", "Karakas",
    "Leovold, Emissary of Trest", "Library of Alexandria", "Limited Resources",
    "Mana Crypt", "Mox Emerald", "Mox Jet", "Mox Pearl", "Mox Ruby",
    "Mox Sapphire", "Nadu, Winged Wisdom", "Paradox Engine",
    "Primeval Titan", "Prophet of Kruphix", "Recurring Nightmare",
    "Rofellos, Llanowar Emissary", "Shahrazad", "Sundering Titan",
    "Sylvan Primordial", "Time Vault", "Time Walk", "Tinker",
    "Tolarian Academy", "Trade Secrets", "Upheaval", "Yawgmoth's Bargain",
}

_TUTORS: set[str] = {
    "Demonic Tutor", "Vampiric Tutor", "Imperial Seal", "Grim Tutor",
    "Diabolic Intent", "Diabolic Tutor", "Enlightened Tutor", "Mystical Tutor",
    "Worldly Tutor", "Eladamri's Call", "Green Sun's Zenith", "Finale of Devastation",
    "Chord of Calling", "Natural Order", "Tooth and Nail", "Birthing Pod",
    "Eldritch Evolution", "Neoform", "Sylvan Tutor", "Personal Tutor",
    "Merchant Scroll", "Muddle the Mixture", "Fabricate", "Whir of Invention",
    "Buried Alive", "Entomb", "Unmarked Grave", "Gamble", "Goblin Engineer",
    "Recruiter of the Guard", "Ranger-Captain of Eos", "Stoneforge Mystic",
    "Expedition Map", "Knight of the Reliquary", "Crop Rotation", "Scapeshift",
    "Wargate", "Sisay, Weatherlight Captain",
}

_MLD_CARDS: set[str] = {
    "Armageddon", "Ravages of War", "Winter Orb", "Static Orb", "Stasis",
    "Blood Moon", "Magus of the Moon", "Back to Basics", "Ruination",
    "Catastrophe", "Global Ruin", "Sunder", "Rising Waters", "Hokori, Dust Drinker",
    "Tangle Wire", "Smokestack", "Desolation Angel", "Keldon Firebombers",
    "Impending Disaster",
}

_FAST_MANA: set[str] = {
    "Dark Ritual", "Cabal Ritual", "Culling the Weak", "Lotus Petal",
    "Elvish Spirit Guide", "Simian Spirit Guide", "Rite of Flame",
    "Seething Song", "Mana Geyser", "Jeska's Will",
}


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

    gc = _match(_GAME_CHANGERS)
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
        "bracket": 1,
        "bracket_factors": [],
        "untagged": [],
        "deficiencies": [],
    }


# ── Proposals ────────────────────────────────────────────────────────────


def propose_deck_changes(
    session: Session,
    deck_id: int,
    summary: str,
    changes: list[dict[str, Any]],
    *,
    conversation_id: int | None = None,
    message_id: int | None = None,
) -> dict:
    """Validate and store proposed deck changes.

    For ``add`` actions the card name is fuzzy-matched against Scryfall to
    get the canonical name.  The deck is *not* modified — each change is
    stored as a pending ``DeckProposal`` row for the player to approve or
    deny.
    """
    if conversation_id is None:
        raise ValueError("propose_deck_changes requires a conversation_id")
    scryfall = get_scryfall_client()
    proposals: list[dict[str, Any]] = []
    deck_cards_by_lower = {c.card_name.lower(): c.card_name for c in repo.list_deck_cards(session, deck_id)}
    deck = repo.get_deck(session, deck_id)
    is_commander = deck is not None and deck.format == "commander"
    banned_lower = {n.lower() for n in _BANNED_COMMANDER}

    for change in changes:
        action = change.get("action", "add")
        card_name = change.get("card_name", "")
        # "remove" defaults to the whole stack (None) unless a quantity is
        # given explicitly — e.g. cutting 2 of 34 Swamps.  "add" defaults to 1.
        quantity = change.get("quantity") if action == "remove" else change.get("quantity", 1)
        category = change.get("category")
        reasoning = change.get("reasoning", "")

        if action in ("add", "remove", "set_commander") and not card_name:
            raise ValueError(f"'{action}' requires a card_name.")

        canonical_name: str | None = None
        if action in ("add", "set_commander"):
            try:
                card = scryfall.named(card_name, fuzzy=True)
                canonical_name = card["name"]
            except ScryfallNotFoundError:
                raise ValueError(f"No Scryfall card found matching '{card_name}'")
            # Commander is a banned-list format — refuse to propose an
            # illegal card rather than relying on the model to self-police.
            if is_commander and canonical_name.lower() in banned_lower:
                raise ValueError(
                    f"'{canonical_name}' is banned in Commander — cannot propose it. "
                    "Suggest a legal alternative instead."
                )
        elif action == "remove":
            canonical_name = deck_cards_by_lower.get(card_name.lower())
            if canonical_name is None:
                raise ValueError(f"'{card_name}' is not in the deck — cannot propose removing it.")

        proposal = DeckProposal(
            conversation_id=conversation_id,
            deck_id=deck_id,
            message_id=message_id,
            status="pending",
            action=action,
            card_name=canonical_name or card_name or None,
            quantity=quantity,
            category=category,
            commander_name=canonical_name if action == "set_commander" else None,
            reasoning=reasoning,
        )
        session.add(proposal)
        session.commit()
        session.refresh(proposal)
        proposals.append({
            "id": proposal.id,
            "deck_id": proposal.deck_id,
            "status": proposal.status,
            "action": proposal.action,
            "card_name": proposal.card_name,
            "quantity": proposal.quantity,
            "category": proposal.category,
            "commander_name": proposal.commander_name,
            "reasoning": proposal.reasoning,
        })

    return {"ok": True, "summary": summary, "proposals": proposals}


def withdraw_pending_proposals(
    session: Session,
    deck_id: int,
    include_commander: bool = False,
    card_names: list[str] | None = None,
) -> dict:
    """Mark pending proposals for *deck_id* as denied.

    Two modes:
    - ``card_names`` given: withdraw ONLY those cards (case-insensitive match on
      the proposal's card name), leaving the rest of the batch pending. This is
      how the model trims a card or two out of a batch it just proposed without
      nuking the whole thing.
    - ``card_names`` omitted: withdraw the whole pending batch — used when the
      player changes direction entirely.

    A pending ``set_commander`` proposal is the deck's identity, not a card
    batch that gets churned during review — so it is preserved by default.
    Clearing a stale card batch before proposing the next one must never
    silently cancel an unapproved commander. Pass ``include_commander=True``
    only when the player has actually decided against the proposed commander.
    (A commander named explicitly in ``card_names`` is only withdrawn when
    ``include_commander`` is also true.)

    Returns a count of how many were withdrawn, how many commander proposals
    were preserved, and the names that matched nothing (so the model can tell
    it misnamed a card rather than silently no-op).
    """
    from sqlmodel import select

    statement = select(DeckProposal).where(
        DeckProposal.deck_id == deck_id,
        DeckProposal.status == "pending",
    )
    proposals = list(session.exec(statement))

    target_lower: set[str] | None = (
        {n.strip().lower() for n in card_names if n and n.strip()}
        if card_names is not None
        else None
    )
    matched: set[str] = set()

    withdrawn = 0
    preserved_commander = 0
    for p in proposals:
        name_lower = (p.card_name or p.commander_name or "").lower()
        if target_lower is not None and name_lower not in target_lower:
            continue  # selective withdraw: skip cards not named
        if p.action == "set_commander" and not include_commander:
            preserved_commander += 1
            continue
        p.status = "denied"
        session.add(p)
        withdrawn += 1
        matched.add(name_lower)
    session.commit()

    not_found = (
        sorted(target_lower - matched) if target_lower is not None else []
    )
    return {
        "ok": True,
        "withdrawn": withdrawn,
        "preserved_commander": preserved_commander,
        "not_found": not_found,
    }
