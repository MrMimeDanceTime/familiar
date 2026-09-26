"""Stage 3 — context shaping. The signal-to-noise core of the pipeline.

Takes the raw candidate pool from stage 2 and turns it into a compact, tagged,
legality-annotated block the selection model reasons over. All pure given the
raw cards and a ``DeckContext`` — no DB, no network — so it is exhaustively
unit-testable, which matters because this is where most of the pipeline's value
(and most of the ways to silently corrupt a suggestion) live.

The stages, in order:

1. strip     — project each raw card to only the fields selection needs.
2. precompute — derive ``legal_in_deck`` and fine/coarse roles per card.
3. dedupe    — collapse duplicate oracle_ids (a card can match many queries).
4. cap       — keep the top N by EDHREC rank, so the model never sees hundreds.
5. render    — emit a stable, compact text block of card entries.

Illegal cards are annotated, not dropped, here — stage 5 (validate) is the hard
gate. But ``shape`` caps *after* sorting legal cards ahead of illegal ones, so a
capped pool is legal-first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.pipeline import roles

# Single source of truth for the banned list — do not duplicate it here.
from app.tools.card_lists import _BANNED_COMMANDER

_BANNED_LOWER = {n.lower() for n in _BANNED_COMMANDER}

# Fields the selection model actually reasons over. Everything else in a raw
# Scryfall card is noise that costs tokens and can distract the model.
_STRIP_FIELDS = (
    "name", "oracle_id", "mana_cost", "cmc", "type_line", "oracle_text",
    "color_identity", "legal_commander", "keywords", "power", "toughness",
    "loyalty", "rarity", "edhrec_rank",
    # The EDHREC signal. Stage 2 has always attached this and this tuple always
    # dropped it, so the selection model could see a card's rules text but never
    # that 75% of decks with this commander run it. That is the single most
    # informative thing available about a candidate.
    "edhrec",
    # The brain map's per-layer scores and explanation.
    "brainmap",
    # USD price from the index, for the budget ceiling and the pool render.
    "price_usd",
)


@dataclass(frozen=True)
class DeckContext:
    """Everything shaping needs to know about the target deck. Built once by the
    service layer from a deck snapshot; passed by value so shaping stays pure.

    ``identity`` is the commander's colour identity as a set of single-letter
    symbols (e.g. ``{"B", "R"}``); a colourless commander is the empty set.
    ``card_names_lower`` is every card already in the deck, lowercased, for the
    singleton check (Commander is singleton outside basic lands).
    """

    identity: frozenset[str]
    card_names_lower: frozenset[str]
    # Per-card USD ceiling, or None for no budget. A priced candidate above it
    # is marked illegal for this deck; an unpriced one passes, since "unknown"
    # is not "too expensive".
    max_card_price: float | None = None
    # lower(candidate name) -> the combo partners already in the deck, for
    # candidates that would complete a combo. Rendered so the selection model
    # has a concrete reason rather than an impression of synergy.
    combo_partners: dict[str, list[str]] = field(default_factory=dict)
    # The deck's standing exclusions (deckplan.restrictions_of).
    restrictions: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def from_snapshot(
        cls,
        snapshot: dict[str, Any],
        identity: frozenset[str],
        max_card_price: float | None = None,
    ) -> "DeckContext":
        names = {
            (c.get("name") or "").lower()
            for c in snapshot.get("cards", [])
            if c.get("name")
        }
        from app import deckplan

        return cls(
            identity=identity, card_names_lower=frozenset(names),
            max_card_price=max_card_price,
            restrictions=deckplan.restrictions_of(snapshot),
        )


@dataclass
class ShapedCard:
    """A stripped, precomputed candidate ready for rendering/selection."""

    name: str
    oracle_id: str | None
    mana_cost: str | None
    cmc: float | None
    type_line: str | None
    oracle_text: str | None
    color_identity: list[str]
    keywords: list[str]
    power: str | None
    toughness: str | None
    loyalty: str | None
    rarity: str | None
    edhrec_rank: int | None
    edhrec: dict[str, Any] | None = None
    brainmap: dict[str, Any] | None = None
    price_usd: float | None = None
    completes_combo_with: list[str] = field(default_factory=list)
    fine_roles: set[str] = field(default_factory=set)
    coarse_roles: set[str] = field(default_factory=set)
    legal_in_deck: bool = False
    illegal_reasons: list[str] = field(default_factory=list)


def _strip(raw: dict[str, Any]) -> dict[str, Any]:
    return {k: raw.get(k) for k in _STRIP_FIELDS}


def _restriction_broken(card: dict[str, Any], ctx: DeckContext) -> str | None:
    from app import deckplan

    return deckplan.restriction_broken(card.get("name"), card.get("type_line"), ctx.restrictions)


def legal_in_deck(card: dict[str, Any], ctx: DeckContext) -> tuple[bool, list[str]]:
    """Return ``(is_legal, reasons)`` for a candidate against the deck.

    A card is legal to *suggest* when: it's Commander-legal, its colour identity
    is a subset of the commander's, it isn't banned, and it isn't already in the
    deck. ``reasons`` lists every failure (not just the first) so debug output
    and repair prompts can see the full picture.
    """
    reasons: list[str] = []

    if card.get("legal_commander") is False:
        reasons.append("not commander-legal")

    card_identity = {c for c in (card.get("color_identity") or []) if c}
    if not card_identity.issubset(ctx.identity):
        off = "".join(sorted(card_identity - ctx.identity))
        reasons.append(f"color identity {off} outside commander identity")

    name = (card.get("name") or "")
    if name.lower() in _BANNED_LOWER:
        reasons.append("banned in commander")

    if name.lower() in ctx.card_names_lower:
        reasons.append("already in deck")

    broken = _restriction_broken(card, ctx)
    if broken:
        reasons.append(broken)

    price = card.get("price_usd")
    if (
        ctx.max_card_price is not None
        and isinstance(price, (int, float))
        and price > ctx.max_card_price
    ):
        reasons.append(f"over budget (${price:.2f} > ${ctx.max_card_price:.2f})")

    return (not reasons, reasons)


def _precompute(raw: dict[str, Any], ctx: DeckContext, tags: set[str]) -> ShapedCard:
    stripped = _strip(raw)
    type_line = stripped.get("type_line")
    fine = roles.fine_roles_for_tags(tags, type_line)
    coarse = roles.coarse_roles_for_tags(tags, type_line)
    ok, why = legal_in_deck(stripped, ctx)
    return ShapedCard(
        name=stripped.get("name") or "",
        oracle_id=stripped.get("oracle_id"),
        mana_cost=stripped.get("mana_cost"),
        cmc=stripped.get("cmc"),
        type_line=type_line,
        oracle_text=stripped.get("oracle_text"),
        color_identity=list(stripped.get("color_identity") or []),
        keywords=list(stripped.get("keywords") or []),
        power=stripped.get("power"),
        toughness=stripped.get("toughness"),
        loyalty=stripped.get("loyalty"),
        rarity=stripped.get("rarity"),
        edhrec_rank=stripped.get("edhrec_rank"),
        edhrec=stripped.get("edhrec"),
        brainmap=stripped.get("brainmap"),
        price_usd=stripped.get("price_usd") if isinstance(stripped.get("price_usd"), (int, float)) else None,
        completes_combo_with=list(ctx.combo_partners.get((stripped.get("name") or "").lower(), [])),
        fine_roles=fine,
        coarse_roles=coarse,
        legal_in_deck=ok,
        illegal_reasons=why,
    )


# EDHREC rank is "lower is more played"; a missing rank sorts last.
_RANK_LAST = float("inf")


def _rank_key(card: ShapedCard) -> tuple[bool, int, float]:
    """Sort key: legal first, then scored-by-the-map, then best within each tier.

    Three tiers rather than one blended number, because a brain-map fit score
    (0-1, higher is better) and an EDHREC rank (1-30000, lower is better) are
    not comparable and mixing them into one float only works by accident.

    A map score wins over EDHREC rank because the map ranks a card against THIS
    deck while `edhrec_rank` is global popularity that knows nothing about it.
    That ordering matters more than it looks: ``shape`` CAPS the pool, so
    sorting on rank here would discard exactly the cards the map ranked highest.
    """
    illegal = not card.legal_in_deck
    if card.brainmap:
        total = card.brainmap.get("total")
        if isinstance(total, (int, float)):
            return (illegal, 0, -float(total))
    rank = card.edhrec_rank if card.edhrec_rank is not None else _RANK_LAST
    return (illegal, 1, rank)


def shape(
    raw_cards: list[dict[str, Any]],
    ctx: DeckContext,
    tags_by_oracle_id: dict[str, set[str]],
    *,
    cap: int = 60,
) -> list[ShapedCard]:
    """Strip → precompute → dedupe → sort (legal-first, EDHREC-ordered) → cap.

    ``tags_by_oracle_id`` supplies each card's oracle tag slugs (the caller reads
    them from the tag cache once); a card missing from the map is tagged from its
    type line alone. Dedupe is by oracle_id, keeping the first occurrence — since
    stage 2 already merged in EDHREC-rank order, the first is the best-ranked.
    """
    seen: set[str] = set()
    shaped: list[ShapedCard] = []
    for raw in raw_cards:
        oid = raw.get("oracle_id")
        if oid is not None:
            if oid in seen:
                continue
            seen.add(oid)
        tags = tags_by_oracle_id.get(oid, set()) if oid else set()
        shaped.append(_precompute(raw, ctx, tags))

    shaped.sort(key=_rank_key)
    return shaped[:cap]


def _fmt_roles(card: ShapedCard) -> str:
    if card.fine_roles:
        return ", ".join(sorted(card.fine_roles))
    return "—"


def _fmt_brainmap(card: ShapedCard) -> str:
    """Render the brain map's verdict for one card.

    Shows the layer breakdown rather than only the total, because the SHAPE of
    the score is the useful part: "mechanical 0.75, consensus 0.31" is an
    underplayed card that genuinely works here, and it should read differently
    from a staple that everyone runs.
    """
    data = card.brainmap
    if not data:
        return ""
    bits = [f"fit {data.get('total', 0):.2f}"]
    explain = data.get("explain")
    if explain:
        bits.append(str(explain))
    return " | ".join(bits)


def _fmt_edhrec(card: ShapedCard) -> str:
    """Render the EDHREC signal for one card.

    Both numbers, deliberately, because they mean different things and the
    difference is the point: a card at 75% play with +0.02 synergy is a staple
    every deck in these colours runs, while 20% play with +0.18 synergy is
    specific to THIS commander. Showing only popularity is what makes every deck
    converge on the same list.
    """
    data = card.edhrec
    if not data:
        return ""
    parts: list[str] = []
    rate = data.get("inclusion_rate")
    if isinstance(rate, (int, float)):
        parts.append(f"{rate * 100:.0f}% of decks")
    synergy = data.get("synergy")
    if isinstance(synergy, (int, float)):
        parts.append(f"synergy {synergy:+.2f}")
    signal = data.get("signal")
    if signal and signal not in ("by-type",):
        parts.append(str(signal))
    return ", ".join(parts)


def render_pool(cards: list[ShapedCard]) -> str:
    """Render shaped cards as a compact, stable text block for the stage-4 prompt.

    One entry per card. Deterministic field order and spacing so golden diffs are
    meaningful. Illegal cards carry an explicit ``ILLEGAL`` marker with reasons —
    the model is told not to pick them, and stage 5 enforces it regardless.
    """
    lines: list[str] = []
    for card in cards:
        header = f"{card.name} — {card.mana_cost or ''}".rstrip(" —")
        parts = [header]
        if card.type_line:
            parts.append(card.type_line)
        stats = _fmt_pt(card)
        if stats:
            parts.append(stats)
        if card.price_usd is not None:
            parts.append(f"${card.price_usd:.2f}")
        parts.append(f"roles: {_fmt_roles(card)}")
        edhrec = _fmt_edhrec(card)
        if edhrec:
            parts.append(f"EDHREC: {edhrec}")
        fit = _fmt_brainmap(card)
        if fit:
            parts.append(fit)
        if card.completes_combo_with:
            parts.append(f"COMPLETES A COMBO with {' + '.join(card.completes_combo_with)}")
        if not card.legal_in_deck:
            parts.append(f"ILLEGAL ({'; '.join(card.illegal_reasons)})")
        lines.append(" | ".join(parts))
        if card.oracle_text:
            lines.append(f"    {card.oracle_text.strip()}")
    return "\n".join(lines)


def _fmt_pt(card: ShapedCard) -> str:
    if card.power is not None or card.toughness is not None:
        return f"{card.power or '?'}/{card.toughness or '?'}"
    if card.loyalty is not None:
        return f"loyalty {card.loyalty}"
    return ""
