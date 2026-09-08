"""Exact card text for every card in play, injected without the model asking.

The model's memory of what a card does is wrong in a specific and expensive
way: the name is real, the text is subtly wrong, and a line of play gets
built on it. The prompt asked it to look cards up first; it complied when it
remembered to, which was not often, because a card it "knows" does not feel
like a card it needs to check.

So the app stops asking. Every card name that appears in the conversation —
in the player's message, in a pending proposal, in the model's own draft —
is resolved against the local card index and its real text goes into the
prompt before the model writes. The index is a complete offline copy of
Scryfall's oracle data, so this costs a millisecond and no network.

The same resolution answers the other half of the problem: a name that
resolves to nothing is a card the model invented, and the block says so by
name, which is how an invention gets caught in the same pass.

Without an index there is no ground truth here, so the whole mechanism turns
itself off (`is_available`) rather than reporting every real card as
invented.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable

logger = logging.getLogger("app.chat.card_facts")

# The prompt requires [[Card Name]] around every card name the model writes,
# which is what makes extraction exact rather than a guess at which
# capitalised phrases are cards.
CARD_NAME_RE = re.compile(r"\[\[\s*([^\]\[\n]{2,120}?)\s*\]\]")

# Basic lands carry no text worth grounding and appear in almost every reply;
# grounding them would fill the block with "Add {G}".
_BASICS = frozenset({
    "plains", "island", "swamp", "mountain", "forest", "wastes",
    "snow-covered plains", "snow-covered island", "snow-covered swamp",
    "snow-covered mountain", "snow-covered forest",
})

# A guard on the block's size: a long conversation accumulates names, and the
# facts for the cards under discussion are what matter, not every card ever
# mentioned. Newest first, so the cards in play survive the cut.
MAX_FACTS = 40


def is_available() -> bool:
    """Whether the local index can answer name lookups at all.

    Without it every name would resolve to nothing and the block would call
    real cards invented, so the feature disables itself instead.
    """
    try:
        from app.cards import schema as card_schema

        return card_schema.card_count() > 0
    except Exception:  # noqa: BLE001 - no index is the same as no answer
        return False


def names_in_text(text: str | None) -> list[str]:
    """Card names the writer marked with [[brackets]], in order, deduped."""
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for raw in CARD_NAME_RE.findall(text):
        name = " ".join(raw.split())
        key = name.lower()
        if not name or key in seen or key in _BASICS:
            continue
        seen.add(key)
        out.append(name)
    return out


def resolve(names: Iterable[str]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Look names up in the local index.

    Returns ``(found, unknown)``: found is ``lower(name) -> card row``, unknown
    is the names the index has never heard of, in input order. A name the
    index cannot match is either a typo or a card that does not exist, and
    both are worth telling the model about.
    """
    wanted = [n for n in dict.fromkeys(n.strip() for n in names if n and n.strip())]
    if not wanted:
        return {}, []
    try:
        from app.cards import store as card_store

        found = card_store.by_names(wanted)
    except Exception as exc:  # noqa: BLE001 - grounding must never sink a turn
        logger.warning("card facts: index lookup failed: %s", exc)
        return {}, []
    unknown = [n for n in wanted if n.lower() not in found and n.lower() not in _BASICS]
    return found, unknown


def _fact_line(card: dict[str, Any]) -> str:
    bits = [str(card.get("name"))]
    if card.get("mana_cost"):
        bits.append(str(card["mana_cost"]))
    if card.get("type_line"):
        line = str(card["type_line"])
        if card.get("power") is not None and card.get("toughness") is not None:
            line += f" {card['power']}/{card['toughness']}"
        elif card.get("loyalty") is not None:
            line += f" loyalty {card['loyalty']}"
        bits.append(line)
    if not card.get("legal_commander", True):
        bits.append("NOT commander-legal")
    head = " · ".join(bits)
    text = " ".join(str(card.get("oracle_text") or "").split())
    return f"- {head} — {text}" if text else f"- {head} — (no rules text)"


def render_block(found: dict[str, dict[str, Any]], unknown: Iterable[str]) -> str:
    """The <card_facts> block, or empty when there is nothing to say."""
    cards = list(found.values())[-MAX_FACTS:]
    unknown = [n for n in unknown]
    if not cards and not unknown:
        return ""
    lines = [
        "<card_facts>",
        "Every card named so far this turn, with its real text from the local "
        "card index. This is what these cards do. Your memory of them is not, "
        "and it is wrong often enough that using it costs the player a bad "
        "line of play. Quote from here, and look up anything not listed "
        "before you write about it.",
    ]
    lines.extend(_fact_line(c) for c in sorted(cards, key=lambda c: str(c.get("name", ""))))
    for name in unknown:
        lines.append(
            f"NO SUCH CARD: \"{name}\" is not in the card index. Either you "
            "misremembered the name or the card does not exist — do not "
            "describe it, and say so if the player named it."
        )
    lines.append("</card_facts>")
    return "\n".join(lines)
