"""Layer 2 — what actually works with this commander, derived not declared.

EDHREC can only report consensus: it aggregates decks already built, so it lags
discovery and underweights cards that are correct but unfashionable. Anything it
barely lifts has to be found by a signal that is not a vote count.

## Why this was rebuilt

The first version matched the commander's oracle text against a hand-written
list of eight themes (sacrifice, treasure, tokens, graveyard, counters,
lifegain, landfall, spellslinger) plus a tribal special case bolted on after a
Cat/Dog commander scored 4 of 60 pool cards. That is a closed vocabulary
somebody must keep extending, and every commander outside it silently scored
zero. It also duplicated, badly, a structure already present in the data.

## How it works now

Tagger already encodes what a card cares about, and commanders self-declare it:

    Korvold, Fae-Cursed King   -> your-sacrifice-matters
    Rin and Seri, Inseparable  -> typal-cat, typal-dog
    Prosper, Tome-Bound        -> synergy-exile-cast
    Marina Vendrell            -> synergy-room
    Torbran, Thane of Red Fell -> damage-increaser

``synergy-room`` is the case that settles it: no hand-written list would have
included rooms, and none will keep pace with new mechanics.

So: read the commander's own tags, expand each into the tags that co-occur with
it (``cards.cooccurrence``), and score a card by the strength of the strongest
relationship it shares with that expanded set. Nothing is authored.

## Two guards, both measured

**Colour artifacts.** Torbran carries ``synergy-red``, which is true and
useless. It is not too broad — 171 cards, narrower than ``synergy-artifact`` at
658 — it is the wrong KIND of tag. Detected by its neighbours: 80% of its top
partners are the other colour tags, where every genuine mechanic measured 0%.
Dropping it leaves ``damage-increaser``, whose top partner is ``synergy-burn``
at 292.9x, which finds Solphim, Ojer Axonil, and Chandra's Incinerator.

**Declared themes add, they do not replace.** A player building tokens under a
commander whose text never says "token" can say so via ``deck_set_plan``, and
those themes are resolved to tags the same way. The commander's own tags remain
the default so the layer works with no plan set.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.brainmap.layers import MECHANICAL, LayerScore, ScoringContext

logger = logging.getLogger(__name__)

# Lift at or above this is a strong relationship; below it, weak but real. Used
# to grade a match rather than to include or exclude it — a card matching only a
# weak partner still scores, just far below one matching a strong partner.
_STRONG_LIFT = 25.0
_MODERATE_LIFT = 8.0

# Scores per match quality. A card carrying the commander's OWN tag is doing
# exactly what the commander cares about and is the strongest possible signal.
_SCORE_EXACT = 1.0
# A declared theme resolves from free text and is much broader than a
# commander's own tag, so it must not reach exact-match strength. Sits below a
# strong co-occurrence partner: "the commander demonstrably relates to this"
# beats "the player used a word that matched this tag".
_SCORE_THEMED = 0.7
_SCORE_STRONG = 0.8
_SCORE_MODERATE = 0.6
_SCORE_WEAK = 0.4

# How many related tags to pull per commander tag. Wide enough to cover a real
# theme, tight enough that the tail of weak partners does not swamp the pool.
_RELATED_PER_TAG = 12

# Minimum lift for a tag to enter the relationship set at all.
_MIN_LIFT = 5.0

# A tag on this share of the legal pool describes the format, not the deck.
#
# Measured: `activated-ability` covers 26.5% of commander-legal cards and
# `triggered-ability` 21%, while genuine relationships sit far lower —
# `typal-cat` 0.1%, `your-sacrifice-matters` 0.3%, `drain-life` 1.1%. Because
# Rin and Seri carries `activated-ability`, Sol Ring and Command Tower matched
# it at exact strength and scored mechanical 1.00, so RAISING mechanical weight
# surfaced MORE staples. That inverted the off-meta control: at 1.0 the top ten
# had a median EDHREC rank of 34, against 3,021 at 0.0.
_GENERIC_TAG_SHARE = 0.05

# A generic tag is not dropped outright — a card can legitimately relate through
# one — but its match is discounted toward the weakest tier so it cannot
# outscore a specific relationship.
_GENERIC_PENALTY = 0.45

# Tags that describe a card's FLAVOUR, NAME, or PRINTING rather than what it
# does. Tagger records plenty of these and they are useless here — Korvold
# carries `alliteration`, and expanding that would relate him to every card with
# a catchy name.
#
# Excluded by family rather than by listing every slug: `cycle-*` alone is 1,594
# of 4,383 slugs (set draft signposts). Measured, these rules remove 1,655 slugs
# and catch ZERO real mechanics from a hand-checked sample.
_FLAVOUR_PREFIXES = ("cycle-", "type-errata-")
_FLAVOUR_SUFFIXES = ("-name", "-flavor")
_FLAVOUR_EXACT = frozenset({
    "alliteration", "unique-type-line", "multi-character-card",
    "multiple-species-types", "punny-name", "single-english-word-name",
    "rhyming-name", "namesake-spell", "unnoted-tracked-information",
    "flavor-text", "french-vanilla", "vanilla",
})


def is_flavour_tag(slug: str) -> bool:
    """Whether a tag describes flavour/naming/printing rather than function."""
    return (
        slug.startswith(_FLAVOUR_PREFIXES)
        or slug.endswith(_FLAVOUR_SUFFIXES)
        or slug in _FLAVOUR_EXACT
    )


@dataclass
class Relationship:
    """The tags this deck cares about, and how strongly."""

    # Tags the commander itself carries — the deck's stated identity.
    own: set[str] = field(default_factory=set)
    # Related tag -> lift. Higher means more reliably part of the same package.
    related: dict[str, float] = field(default_factory=dict)
    # Tags resolved from the deck's DECLARED themes. Kept separate from `own`
    # because they are matched from free text and are far broader: the theme
    # "enchantment ramp" resolves to `land-ramp`, which every ramp spell in the
    # format carries. Scoring those at exact-match strength made Rampant Growth
    # (EDHREC rank 26) and Font of Fertility (rank 6,410) both score 1.00 and
    # tie, which is how a rank-6,410 card outranked a staple.
    themed: set[str] = field(default_factory=set)

    # Tags too broad to identify a deck; matches through them are discounted.
    generic: set[str] = field(default_factory=set)

    def _grade(self, score: float, tag: str) -> tuple[float, str]:
        if tag in self.generic:
            return score * _GENERIC_PENALTY, tag
        return score, tag

    def strength(self, tags: set[str]) -> tuple[float, str] | None:
        """Best match between a card's tags and this deck's relationships.

        Prefers a SPECIFIC match over a broad one rather than taking the first
        hit: a card sharing both `typal-cat` and `activated-ability` relates
        through the former, and grading it on the latter would rank every card
        with an activated ability alongside real tribal payoffs.
        """
        specific_own = sorted(tags & self.own - self.generic)
        if specific_own:
            return _SCORE_EXACT, specific_own[0]

        specific_theme = sorted(tags & self.themed - self.generic)
        if specific_theme:
            return _SCORE_THEMED, specific_theme[0]

        best: tuple[float, str] | None = None
        for tag in sorted(tags & self.own):
            best = max(best or (0.0, ""), self._grade(_SCORE_EXACT, tag))
        for tag in sorted(tags & self.themed):
            best = max(best or (0.0, ""), self._grade(_SCORE_THEMED, tag))
        for tag in tags:
            lift = self.related.get(tag)
            if lift is None:
                continue
            if lift >= _STRONG_LIFT:
                score = _SCORE_STRONG
            elif lift >= _MODERATE_LIFT:
                score = _SCORE_MODERATE
            else:
                score = _SCORE_WEAK
            graded = self._grade(score, tag)
            if best is None or graded[0] > best[0]:
                best = graded
        return best if best and best[0] > 0 else None

    def is_empty(self) -> bool:
        return not self.own and not self.related and not self.themed


def _theme_tags(themes: list[str], store: Any) -> set[str]:
    """Resolve declared theme phrases to real tag slugs.

    A theme is free text ("cat and dog tribal", "treasure sacrifice"), so it is
    matched against the tag vocabulary by substring on the theme's words. Only
    tags that actually exist are returned, which is the same discipline the rest
    of the pipeline uses: a plausible slug that ships zero rows is worse than no
    slug, because it silently matches nothing.
    """
    if not themes:
        return set()

    found: set[str] = set()
    for theme in themes:
        for word in theme.lower().replace("/", " ").split():
            if len(word) < 4:
                continue
            for slug, _count in store.tag_slugs(word, limit=6):
                found.add(slug)
    return found


def build_relationship(
    commander_tags: set[str], store: Any, themes: list[str] | None = None
) -> Relationship:
    """Derive what this deck cares about from the commander's tags plus themes.

    Colour-artifact tags are dropped before expansion: they would pull in the
    whole colour rather than the deck's actual plan.
    """
    usable = {t for t in commander_tags if not is_flavour_tag(t)}
    own = {t for t in usable if not store.is_colour_artifact(t)}
    dropped = usable - own
    if dropped:
        logger.info("mechanical: ignoring colour-artifact tag(s) %s", sorted(dropped))

    themed = {
        t for t in _theme_tags(themes or [], store)
        if not is_flavour_tag(t) and t not in own
    }
    if not own and not themed:
        return Relationship()

    related = store.related_tags(
        sorted(own | themed), min_lift=_MIN_LIFT, limit=_RELATED_PER_TAG
    )
    # Own and themed tags are tracked separately, so drop them from the related
    # map to keep the three tiers distinct.
    related = {
        k: v for k, v in related.items() if k not in own and k not in themed
    }
    breadth = store.tag_breadth(sorted(own | themed | set(related)))
    generic = {
        slug for slug, share in breadth.items() if share >= _GENERIC_TAG_SHARE
    }
    if generic:
        logger.info("mechanical: discounting %d generic tag(s)", len(generic))
    return Relationship(
        own=own, related=related, themed=themed, generic=generic
    )


class MechanicalLayer:
    """Scores cards by how strongly they relate to the commander's own tags."""

    name = MECHANICAL

    def __init__(self, store: Any) -> None:
        self._store = store

    def score(
        self, cards: list[dict], context: ScoringContext
    ) -> dict[str, LayerScore]:
        commander_tags = self._commander_tags(context)
        relationship = build_relationship(
            commander_tags, self._store, context.themes
        )
        if relationship.is_empty():
            return {}

        oracle_ids = [c.get("oracle_id") for c in cards if c.get("oracle_id")]
        tags_by_id = self._store.tags_for_many(oracle_ids)

        out: dict[str, LayerScore] = {}
        for card in cards:
            name = (card.get("name") or "").strip()
            oracle_id = card.get("oracle_id")
            if not name or not oracle_id:
                continue
            tags = tags_by_id.get(oracle_id, set())
            if not tags:
                continue

            match = relationship.strength(tags)
            if match is None:
                continue
            score, via = match
            out[name.lower()] = LayerScore(
                layer=MECHANICAL,
                score=score,
                reason=f"works with the commander via {via}",
            )
        return out

    def _commander_tags(self, context: ScoringContext) -> set[str]:
        if not context.commander:
            return set()
        card = self._store.by_name(context.commander)
        if not card or not card.get("oracle_id"):
            return set()
        return self._store.tags_for(card["oracle_id"])
