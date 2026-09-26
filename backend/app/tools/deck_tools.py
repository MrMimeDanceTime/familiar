"""Deck-state tool functions: read the deck and its plan, add and remove
cards, set the commander, import a decklist. Card additions are validated
and enriched via Scryfall before insert.

Statistics live in ``deck_stats``, proposals in ``proposals``, the role and
category derivation in ``card_roles``, and the hand-maintained card lists in
``card_lists``. The proposal path reaches ``get_scryfall_client`` through
this module, so a test can patch Scryfall for every deck tool in one place.
"""

from __future__ import annotations

import re
from typing import Any

from sqlmodel import Session

from app.db import repository as repo
from app.knowledge.tag_lookup import get_tags_for_card
from app.tools.card_roles import _auto_categorize
from app.tools.deck_stats import compute_deck_stats
from app.tools.scryfall_client import ScryfallNotFoundError, get_scryfall_client

_LINE_RE = re.compile(r"^(?:(\d+)\s*x?\s+)?(.+)$")

# Basic lands are the singleton exception — a deck runs many copies.
_BASIC_LAND_NAMES = frozenset({
    "plains", "island", "swamp", "mountain", "forest", "wastes",
    "snow-covered plains", "snow-covered island", "snow-covered swamp",
    "snow-covered mountain", "snow-covered forest",
})


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


def lookup_card(scryfall: Any, name: str) -> dict[str, Any]:
    """One card by name: the local index first, Scryfall's fuzzy match after.

    Every add, commander change, and proposal used to call Scryfall per card.
    Behind its rate limit and 10s timeout that measured 55s to validate one
    10-card pipeline batch, for data the local index (a full offline copy of
    Scryfall's oracle cards) already holds. Exact names, which is everything
    the pipeline produces, resolve locally; a hand-typed or misspelled name
    still gets Scryfall's fuzzy match. Raises ScryfallNotFoundError when
    neither knows it.
    """
    try:
        from app.cards import schema as card_schema
        from app.cards import store as card_store

        if card_schema.tag_count() > 0:
            hit = card_store.by_names([name]).get(name.strip().lower())
            if hit and hit.get("name"):
                return hit
    except Exception:  # noqa: BLE001 - Scryfall below still answers
        pass
    return scryfall.named(name, fuzzy=True)


def _resolve_card(scryfall: Any, name: str) -> dict[str, Any]:
    """Validate a single card name against Scryfall and return enriched fields."""
    try:
        card = lookup_card(scryfall, name)
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


# ── Public tool functions ───────────────────────────────────────────────


def _missing_auto_includes(snapshot: dict) -> list[dict]:
    """Format staples absent from the deck. Advisory, never fatal."""
    from app import autoincludes
    from app.tools.edhrec_client import get_edhrec_client

    try:
        return autoincludes.find_missing(
            snapshot.get("commander"),
            {(c.get("name") or "") for c in snapshot.get("cards", [])},
            edhrec=get_edhrec_client(),
        )
    except Exception:  # noqa: BLE001 — a deck read must not fail over a hint
        return []


def deck_get_current(
    session: Session, deck_id: int, include_oracle_text: bool = False
) -> dict:
    """The deck as it stands, plus what it is trying to become.

    The snapshot carries the raw plan fields; this attaches the computed
    current-vs-target view so the model reads "still needs: draw (short 4)"
    rather than deriving it from role targets and a 99-card list — arithmetic
    it does unreliably and would have to redo every turn.

    Oracle text is kept for the commander(s) and dropped for the rest unless
    asked for. A full deck's rules text is the single largest thing in a turn's
    context and the model reads this tool several times per conversation; the
    tags and category answer "what does this card do for the deck" for most
    reasoning, and a card the model needs the text of is one lookup away.
    """
    from app import deckplan

    snapshot = repo.deck_snapshot(session, deck_id)
    snapshot["budget_preference"] = repo.get_or_create_preferences(session).budget
    if not include_oracle_text:
        for card in snapshot["cards"]:
            if card.get("category") != "Commander":
                card.pop("oracle_text", None)
        snapshot["oracle_text_note"] = (
            "oracle_text is included for the commander(s) only. Pass "
            "include_oracle_text=true to get it for every card, or look "
            "specific cards up with scryfall_card_collection."
        )
    plan = deckplan.build_plan(snapshot)
    snapshot["plan"] = {
        "themes": plan.themes,
        "notes": plan.notes,
        "off_meta": snapshot.get("off_meta"),
        "max_card_price": plan.max_card_price,
        "role_counts": {
            g.role: {"current": g.current, "target": g.target} for g in plan.gaps
        },
        "still_needs": {g.role: g.gap for g in plan.unmet},
        # Format staples this deck is missing. Surfaced on every deck read
        # rather than left to a batch, because a card in 90% of decks does not
        # need a themed batch to justify it and should not wait for one.
        "missing_auto_includes": _missing_auto_includes(snapshot),
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
        card = lookup_card(scryfall, card_name)
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
        card = lookup_card(scryfall, commander_name)
    except ScryfallNotFoundError:
        raise ValueError(f"No Scryfall card found matching '{commander_name}'")

    repo.update_deck(session, deck_id, commander=card["name"])
    # Ensure the commander is in the card list with exactly one copy. The upsert
    # ADDS an explicit quantity to an existing stack (that is what merge-import
    # relies on), so passing quantity=1 here doubled a commander that was already
    # in the list — an imported decklist that named its commander, followed by
    # an approved set_commander proposal, produced a 101-card deck with two
    # copies. quantity=None leaves an existing stack alone (and creates a new
    # row at 1), and the count is then pinned to 1 explicitly.
    row = repo.add_deck_card(
        session,
        deck_id=deck_id,
        card_name=card["name"],
        quantity=None,
        category=_normalize_category("Commander"),
        mana_value=card["cmc"],
        color_identity="".join(card["color_identity"] or []),
        type_line=card.get("type_line"),
        oracle_text=card.get("oracle_text"),
        oracle_id=card.get("oracle_id"),
        tags=get_tags_for_card(card.get("oracle_id")),
    )
    if row.quantity != 1:
        row.quantity = 1
        session.add(row)
        session.commit()
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
    power_level: str | None = None,
    max_card_price: float | None = None,
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

    clean_price: float | None = None
    if isinstance(max_card_price, (int, float)) and not isinstance(max_card_price, bool):
        clean_price = max(0.0, float(max_card_price))

    repo.update_deck(
        session, deck_id,
        themes=clean_themes,
        role_targets=clean_targets,
        plan_notes=plan_notes,
        off_meta=clean_off_meta,
        power_level=power_level,
        max_card_price=clean_price,
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

    # Phase 2: batch-resolve all names BEFORE touching the deck. Resolution is
    # the step that talks to Scryfall, and a Scryfall failure mid-import used to
    # land after the replace-mode wipe below had already committed — leaving the
    # player with an empty deck and a 500. Nothing is deleted until every name
    # that can resolve has.
    resolved_map = _resolve_many(scryfall, [name for _, name, _ in parsed])

    # In replace mode, wipe the existing cards now that we know the list parsed
    # into at least one card — a fully-unparseable paste shouldn't nuke the deck.
    if mode == "replace" and parsed:
        cleared = repo.clear_deck_cards(session, deck_id)

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


