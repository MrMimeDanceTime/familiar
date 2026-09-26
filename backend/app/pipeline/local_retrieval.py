"""Stage 2 without the network: candidates from the local card index.

Stage 1 asked the model to write Scryfall queries and stage 2 ran each one
against Scryfall's API, rate-limited to nine requests a second, with a
broadening retry per underfilled query. For the intents players actually
ask for ("some ramp", "cheap removal", "card draw that fits") the answer is
a tag lookup the index can do in milliseconds, with no model call and no
network at all.

Two sources, both local:

1. **Role tags.** The intent is matched to the fine roles in
   ``pipeline.roles`` (ramp, removal, card draw, board wipe, tutor, ...) and
   each role's slug rules become a tag query against ``card_tags``. This is
   the same vocabulary shaping uses to label the pool, so a candidate found
   this way is guaranteed to render with the role the intent asked for.
2. **Full text.** The intent's content words are searched in name, rules
   text, and type line, which catches phrasing that is not a role ("cares
   about rooms", "doubles tokens").

Colour identity and legality are applied here so the pool that reaches
shaping is already on-colour; shaping still does the authoritative check.
The model's query planning (stage 1) remains the fallback for an intent that
neither source can fill.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.pipeline import roles

logger = logging.getLogger(__name__)

# How an intent's words map to fine roles. Each phrase is matched as a whole
# word or phrase, case-insensitive. Kept small and obvious: a phrase here is a
# claim that the player who says it wants that role, and a wrong claim floods
# the pool with the wrong cards.
_ROLE_PHRASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (roles.RAMP, ("ramp", "mana rock", "mana rocks", "mana dork", "mana dorks", "acceleration", "accelerant")),
    (roles.FAST_MANA, ("fast mana", "ritual", "rituals")),
    (roles.FIXING, ("fixing", "color fixing", "colour fixing", "fix colors", "fix colours", "mana fixing")),
    (roles.CARD_DRAW, ("draw", "card draw", "card advantage", "cantrip", "cantrips")),
    (roles.CARD_SELECTION, ("selection", "card selection", "scry", "impulse", "looting", "rummage")),
    (roles.WHEEL, ("wheel", "wheels")),
    (roles.SPOT_REMOVAL, ("removal", "spot removal", "interaction", "answers", "kill spell", "kill spells")),
    (roles.BOARD_WIPE, ("board wipe", "board wipes", "wipe", "wipes", "sweeper", "sweepers", "wrath", "wraths")),
    (roles.COUNTERSPELL, ("counterspell", "counterspells", "counter magic", "countermagic", "counters")),
    (roles.LAND_DESTRUCTION, ("land destruction", "mld")),
    (roles.TUTOR, ("tutor", "tutors", "search my library")),
    (roles.RECURSION, ("recursion", "reanimation", "reanimate", "regrowth", "graveyard recursion")),
    (roles.GRAVEYARD_HATE, ("graveyard hate", "grave hate", "graveyard removal")),
    (roles.ARISTOCRATS, ("aristocrats", "death trigger", "death triggers", "dies trigger")),
    (roles.SACRIFICE_OUTLET, ("sacrifice outlet", "sacrifice outlets", "sac outlet", "sac outlets")),
    (roles.TOKENS, ("tokens", "token maker", "token makers", "go wide", "token generation")),
    (roles.PROTECTION, ("protection", "protect", "hexproof", "indestructible", "counterspell protection")),
    (roles.LAND, ("lands", "land base", "manabase", "mana base", "dual lands", "utility lands")),
)

_STOPWORDS = frozenset({
    "a", "an", "the", "some", "more", "few", "cards", "card", "for", "that",
    "this", "deck", "my", "with", "and", "or", "to", "of", "in", "on", "at",
    "cheap", "good", "best", "please", "need", "want", "add", "find", "suggest",
    "fits", "fit", "commander", "under", "over", "mana", "budget", "package",
})


def intent_roles(intent: str) -> set[str]:
    """The fine roles an intent asks for, by phrase match."""
    text = f" {re.sub(r'[^a-z0-9 ]+', ' ', intent.lower())} "
    found: set[str] = set()
    for role, phrases in _ROLE_PHRASES:
        for phrase in phrases:
            if f" {phrase} " in text:
                found.add(role)
                break
    # "counters" alone is ambiguous (+1/+1 counters); only keep the
    # counterspell reading when nothing about counters-the-noun is present.
    # Checked on the raw intent: the sanitised text has lost its punctuation.
    if roles.COUNTERSPELL in found and re.search(
        r"\+1/\+1|charge counters?|loyalty counters?|poison counters?", intent.lower()
    ):
        found.discard(roles.COUNTERSPELL)
    return found


def intent_terms(intent: str) -> list[str]:
    """Content words worth a full-text search, once role phrases are out."""
    words = re.sub(r"[^a-z0-9 ]+", " ", intent.lower()).split()
    return [w for w in words if len(w) > 2 and w not in _STOPWORDS]


def _within_identity(card: dict[str, Any], identity: frozenset[str]) -> bool:
    return {c for c in (card.get("color_identity") or []) if c}.issubset(identity)


def retrieve(
    intent: str,
    identity: frozenset[str],
    *,
    store: Any,
    per_role_limit: int = 80,
    text_limit: int = 40,
    themes: list[str] | None = None,
    commander_tags: set[str] | None = None,
    theme_limit: int = 25,
    tag_limit: int = 150,
) -> list[dict[str, Any]]:
    """Candidates for an intent from the local index, deduped by oracle id.

    Role hits come first, EDHREC-ordered within each role, then full-text
    hits. Anything off-identity is dropped here so the pool that reaches
    shaping is on-colour.
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    def take(cards: list[dict[str, Any]]) -> None:
        for card in cards:
            oid = card.get("oracle_id")
            if not oid or oid in seen:
                continue
            if not _within_identity(card, identity):
                continue
            seen.add(oid)
            out.append(card)

    for role in sorted(intent_roles(intent)):
        exact, prefixes, suffixes = roles.slug_rules_for(role)
        if not (exact or prefixes or suffixes):
            continue
        try:
            take(store.cards_matching_slug_rules(
                exact, prefixes, suffixes, limit=per_role_limit,
            ))
        except Exception as exc:  # noqa: BLE001 - the index is optional
            logger.warning("local retrieval: role %s failed: %s", role, exc)

    # A request naming no role ("what fits my deck") used to search only its
    # own words. The deck's gameplan themes (rules-text phrases) and the
    # commander's own mechanic tags say what "fits" means for this deck.
    if not intent_roles(intent):
        allowed = "".join(sorted(identity)) or None
        for theme in themes or []:
            try:
                take(store.search_text(theme, limit=theme_limit, identity=allowed))
            except Exception as exc:  # noqa: BLE001
                logger.warning("local retrieval: theme %r failed: %s", theme, exc)
        if commander_tags:
            try:
                take(store.cards_with_any_tag(sorted(commander_tags), limit=tag_limit))
            except Exception as exc:  # noqa: BLE001
                logger.warning("local retrieval: commander tags failed: %s", exc)

    terms = intent_terms(intent)
    if terms:
        try:
            take(store.search_text(" ".join(terms), limit=text_limit, identity="".join(sorted(identity)) or None))
        except Exception as exc:  # noqa: BLE001
            logger.warning("local retrieval: text search failed: %s", exc)

    return out
