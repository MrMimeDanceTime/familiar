"""What a card does for a deck: its functional roles and its display
category, both derived from Scryfall's community tags.

Tags are the source of truth. They encode nuance (impulse draw counts as
draw, mana-fix does not count as ramp, counterspells and sweepers count as
removal) that oracle-text matching cannot reproduce reliably, and the same
detection drives scoring and display so the two can never disagree.
"""

from __future__ import annotations

from app.knowledge.tag_lookup import get_tags_for_card


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
    if tags:
        return _roles_from_tag_set(tags, type_line)

    if oracle_id:
        return _roles_from_tag_set(set(get_tags_for_card(oracle_id)), type_line)

    # No tags available: the only role we can assert from the type line
    # alone is land. Everything else waits for the tag backfill.
    return {"land"} if "land" in (type_line or "").lower() else set()




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


