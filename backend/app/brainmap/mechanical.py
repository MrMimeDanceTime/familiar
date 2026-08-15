"""Layer 2 — what actually works, whether or not anyone plays it.

EDHREC can only report consensus: it aggregates decks people have already
built, so it lags discovery and systematically underweights cards that are
correct but unfashionable. Anything it "barely lifts" has to be found by a
signal that is not a vote count.

That signal is the oracle-tag vocabulary. Tagger is human-curated functional
tagging covering ~36k cards at a median of 6 tags each, which is a far better
foundation than regexing oracle text. A commander's text names a mechanic, the
vocabulary names every card that feeds it, and the intersection is computable.

Measured on Korvold, Fae-Cursed King ("whenever you sacrifice a permanent, draw
a card"): 326 free/repeatable sacrifice outlets exist in Jund identity, and
**305 of them are absent from EDHREC's 225-card page** — including Carrion
Feeder, Greater Good, Altar of Dementia, Yahenni, and Krark-Clan Ironworks. All
verified to carry a genuine sacrifice ability in their oracle text.

So precision is not the problem; volume is. 326 correct answers where a pool
needs 40 is a ranking problem, which is what the layered score exists to solve.

## Why themes are matched, not inferred

A theme is detected from the commander's own oracle text plus the deck's stated
direction, never guessed from the card list. Inferring intent from what is
already in a deck makes the layer agree with whatever the deck already does,
which would defeat the purpose of having a layer that can disagree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.brainmap.layers import MECHANICAL, LayerScore, ScoringContext


@dataclass(frozen=True)
class Theme:
    """A mechanical theme: how to spot it, and what feeds it.

    ``triggers`` are regexes matched against the commander's oracle text and
    the deck's declared themes. ``payoff_slugs`` are cards that CARE about the
    mechanic; ``enabler_slugs`` are cards that DO it. A deck usually needs both,
    and the distinction is what lets the layer say something more useful than
    "these cards are related".
    """

    key: str
    label: str
    triggers: tuple[str, ...]
    enabler_slugs: tuple[str, ...] = ()
    payoff_slugs: tuple[str, ...] = ()

    def matches(self, text: str) -> bool:
        return any(re.search(t, text, re.I) for t in self.triggers)


# The starting vocabulary. Deliberately narrow and high-confidence: every slug
# here was verified to exist in the bulk export with a meaningful card count,
# because Tagger's vocabulary is a hierarchy whose ancestor tags (`removal`,
# `tutor`, `sacrifice-outlet`) resolve on the API but ship ZERO rows in the
# export. Guessing slug names produces silent empty results.
THEMES: tuple[Theme, ...] = (
    Theme(
        key="sacrifice",
        label="sacrifice",
        triggers=(r"sacrific", r"\bdies\b", r"aristocrat"),
        enabler_slugs=(
            "free-sacrifice-outlet", "repeatable-sacrifice-outlet",
            "sacrifice-outlet-creature", "sacrifice-outlet-artifact",
            "sacrifice-outlet-permanent", "sacrifice-outlet-token",
        ),
        payoff_slugs=("your-sacrifice-matters", "death-trigger", "dies-trigger"),
    ),
    Theme(
        key="treasure",
        label="treasure",
        triggers=(r"treasure", r"\bartifact token"),
        enabler_slugs=("repeatable-treasures", "synergy-treasure"),
        payoff_slugs=("your-sacrifice-matters",),
    ),
    Theme(
        key="tokens",
        label="tokens",
        triggers=(r"\btoken", r"populate", r"go.wide"),
        enabler_slugs=("token-maker", "creates-tokens", "populate"),
        payoff_slugs=("token-matters", "go-wide"),
    ),
    Theme(
        key="graveyard",
        label="graveyard",
        triggers=(r"graveyard", r"reanimat", r"mill"),
        enabler_slugs=("self-mill", "mill-self", "discard-outlet"),
        payoff_slugs=("graveyard-matters", "reanimation", "graveyard-recursion"),
    ),
    Theme(
        key="counters",
        label="+1/+1 counters",
        triggers=(r"\+1/\+1 counter", r"\bproliferate\b"),
        enabler_slugs=("counter-adder", "proliferate"),
        payoff_slugs=("counter-matters", "counters-matter"),
    ),
    Theme(
        key="lifegain",
        label="lifegain",
        # Must be a lifegain PAYOFF, not merely a card that happens to gain
        # life. "gain N life" appears on a huge number of commanders as a minor
        # rider — Rin and Seri's activated ability gains life, which made a
        # Cat/Dog tribal deck read as a lifegain deck and mislabelled every
        # on-type card as "engine piece for lifegain".
        triggers=(
            r"whenever you gain life", r"if you (?:would )?gain(?:ed)? life",
            r"\blifegain\b", r"life you gained",
        ),
        enabler_slugs=("lifegain", "lifelink"),
        payoff_slugs=("lifegain-matters", "life-matters"),
    ),
    Theme(
        key="landfall",
        label="landfall",
        triggers=(r"\blandfall\b", r"land enters", r"whenever a land"),
        enabler_slugs=("land-ramp", "extra-land-drop", "tutor-land-to-battlefield"),
        payoff_slugs=("landfall",),
    ),
    Theme(
        key="spellslinger",
        label="instants & sorceries",
        # "whenever you cast" alone is far too loose — it matches any cast
        # trigger, including tribal commanders like Rin and Seri ("whenever you
        # cast a Dog spell"). Require the spell type to actually be named.
        triggers=(
            r"instant or sorcery", r"\bprowess\b", r"\bmagecraft\b",
            r"whenever you cast (?:an? )?(?:instant|sorcery)",
        ),
        enabler_slugs=("cost-reducer-instant-sorcery", "spell-copy"),
        payoff_slugs=("instant-sorcery-matters", "prowess", "magecraft"),
    ),
)


# Tribal is not a fixed theme: the relevant slugs depend on WHICH creature type
# the commander cares about, so it is resolved per deck rather than declared
# above. Tagger exposes one slug per type (`typal-dragon`, `typal-elf`, …) plus
# `changeling` for cards that count as every type at once.
#
# This was the gap that made the layer nearly silent on a tribal deck: Rin and
# Seri is a Cat/Dog commander, and none of the fixed themes describe creature
# typing, so the layer scored 4 of 60 pool cards.
_TYPAL_PREFIX = "typal-"
_CHANGELING_SLUGS = ("changeling", "gives-changeling", "becomes-changeling")

# "Whenever you cast a Dog spell" / "Dogs you control get" / "other Cats".
_TYPE_PATTERNS = (
    r"cast (?:an?|another) (\w+) spell",
    r"\b(\w+)s? you control\b",
    r"\bother (\w+)s\b",
    r"\beach (\w+) you control\b",
)

# Words that match the patterns above but are not creature types. Without this
# the extractor happily decides the deck is "permanent" tribal.
_NOT_A_TYPE = frozenset({
    "creature", "creatures", "permanent", "permanents", "token", "tokens",
    "artifact", "artifacts", "enchantment", "enchantments", "land", "lands",
    "player", "players", "opponent", "opponents", "spell", "spells", "card",
    "cards", "other", "another", "this", "that", "you", "your", "it", "them",
})


def detect_creature_types(commander_text: str, store: Any) -> list[str]:
    """Creature types the commander cares about, verified against the tag data.

    Extracted from the commander's oracle text, then filtered to types that
    actually have a ``typal-<type>`` slug — which both removes false positives
    and guarantees the resulting query returns something.
    """
    if not commander_text:
        return []

    available = {slug for slug, _count in store.tag_slugs(_TYPAL_PREFIX, limit=100000)}
    found: list[str] = []
    for pattern in _TYPE_PATTERNS:
        for match in re.finditer(pattern, commander_text, re.I):
            word = match.group(1).lower().rstrip("s")
            if word in _NOT_A_TYPE or word in found:
                continue
            if f"{_TYPAL_PREFIX}{word}" in available:
                found.append(word)
    return found


@dataclass
class ThemeHit:
    """A theme the deck is actually built around, with its available slugs."""

    theme: Theme
    enablers: list[str] = field(default_factory=list)
    payoffs: list[str] = field(default_factory=list)

    @property
    def slugs(self) -> list[str]:
        return self.enablers + self.payoffs


def detect_themes(
    commander_text: str, declared: list[str] | None = None
) -> list[Theme]:
    """Which themes this deck is built around.

    Read from the commander's oracle text and the deck's declared direction
    only. Deliberately NOT inferred from the current card list: a layer that
    reads the deck's contents would agree with whatever the deck already does,
    and the whole point of this layer is that it can disagree.
    """
    haystack = " ".join([commander_text or "", " ".join(declared or [])])
    if not haystack.strip():
        return []
    return [theme for theme in THEMES if theme.matches(haystack)]


def resolve_slugs(themes: list[Theme], store: Any) -> list[ThemeHit]:
    """Keep only slugs that actually exist in the tag data.

    Necessary because the export ships leaf taggings only, so a plausible slug
    name can silently match nothing. Verifying up front turns a silent empty
    result into a visible one.
    """
    # One vocabulary read for all themes; this is a full scan of the slug list,
    # so doing it per theme would repeat it needlessly.
    available = {slug for slug, _count in store.tag_slugs(limit=100000)}

    hits: list[ThemeHit] = []
    for theme in themes:
        enablers = [s for s in theme.enabler_slugs if s in available]
        payoffs = [s for s in theme.payoff_slugs if s in available]
        if enablers or payoffs:
            hits.append(ThemeHit(theme=theme, enablers=enablers, payoffs=payoffs))
    return hits


class MechanicalLayer:
    """Scores cards by whether they feed the deck's mechanical themes."""

    name = MECHANICAL

    def __init__(self, store: Any) -> None:
        self._store = store
        self._slug_cache: dict[str, set[str]] | None = None

    def _tags_for(self, cards: list[dict]) -> dict[str, set[str]]:
        oracle_ids = [c.get("oracle_id") for c in cards if c.get("oracle_id")]
        return self._store.tags_for_many(oracle_ids)

    def score(
        self, cards: list[dict], context: ScoringContext
    ) -> dict[str, LayerScore]:
        """Score each card on how well it serves the deck's themes.

        A card scores for being an enabler, a payoff, or both. Both is the
        strongest signal: a card that both does the thing and cares about it is
        an engine piece rather than a component.
        """
        commander_text = context_commander_text(context, self._store)
        themes = detect_themes(commander_text, context.themes)
        if not themes:
            return {}

        hits = resolve_slugs(themes, self._store)

        # Tribal, resolved per deck from the commander's creature types.
        tribal_types = detect_creature_types(commander_text, self._store)
        tribal_slugs = {f"{_TYPAL_PREFIX}{t}" for t in tribal_types}
        if tribal_slugs:
            tribal_slugs.update(_CHANGELING_SLUGS)

        if not hits and not tribal_slugs:
            return {}

        enabler_slugs = {s for h in hits for s in h.enablers}
        payoff_slugs = {s for h in hits for s in h.payoffs}
        # Slug -> theme label, so a card's reason names the theme IT serves.
        # Joining every detected theme onto every card produces confident
        # nonsense: Korvold triggers both "sacrifice" and "+1/+1 counters", so a
        # sacrifice outlet would claim to be a counters card.
        slug_labels: dict[str, str] = {}
        for hit in hits:
            for slug in hit.slugs:
                slug_labels[slug] = hit.theme.label

        tags_by_id = self._tags_for(cards)
        out: dict[str, LayerScore] = {}

        for card in cards:
            oracle_id = card.get("oracle_id")
            name = (card.get("name") or "").strip()
            if not name:
                continue
            tags = tags_by_id.get(oracle_id, set()) if oracle_id else set()
            if not tags:
                continue

            matched_enablers = tags & enabler_slugs
            matched_payoffs = tags & payoff_slugs
            matched_tribal = tags & tribal_slugs

            if matched_tribal and not (matched_enablers or matched_payoffs):
                # On-type without also serving another theme. Still a real
                # signal on a tribal deck — being a Cat IS the synergy when the
                # commander triggers on casting Cats.
                types = ", ".join(tribal_types) or "the deck's type"
                out[name.lower()] = LayerScore(
                    layer=MECHANICAL, score=0.7,
                    reason=f"on-type for {types} tribal",
                )
                continue

            if not matched_enablers and not matched_payoffs:
                continue

            # Both roles is the engine case and scores highest; a payoff alone
            # outranks an enabler alone because payoffs are scarcer and define
            # what the deck is doing.
            if matched_enablers and matched_payoffs:
                score, role = 1.0, "engine piece"
            elif matched_payoffs:
                score, role = 0.75, "payoff"
            else:
                score, role = 0.6, "enabler"

            # On-type AND useful is strictly better than either alone on a
            # tribal deck, so it takes the engine-piece score.
            if matched_tribal:
                score = 1.0
                role = f"on-type {role}"

            matched_themes = sorted({
                slug_labels[slug]
                for slug in (matched_enablers | matched_payoffs)
                if slug in slug_labels
            })
            theme_label = ", ".join(matched_themes) or "the deck's plan"
            out[name.lower()] = LayerScore(
                layer=MECHANICAL,
                score=score,
                reason=f"{role} for {theme_label}",
            )

        return out


def context_commander_text(context: ScoringContext, store: Any) -> str:
    """The commander's oracle text, which is where a theme is declared."""
    if not context.commander:
        return ""
    card = store.by_name(context.commander)
    return (card or {}).get("oracle_text") or ""
