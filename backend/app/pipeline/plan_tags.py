"""The deck's plan as Tagger slugs, and retrieval through them.

Theme requests used to search rules text with the plan's theme phrases, which
only finds cards that print the words. The cards a theme deck runs often do
not: Parallel Lives for a token deck, Phantasmal Image for a copy deck. On the
player's decks a third of the held-out theme cards were off the commander's
EDHREC page and none of them reached the pool (tools/reach_report.py).
Scryfall's community tags already say what each card does, so the plan names
tags and retrieval reads them.

The drafter picks from the real vocabulary: a short list built from the
commander's tags, the tags that co-occur with them, and the tags common
among the deck's cards, and any other slug it names must exist. Tags that
cover more than ``GENERIC_SHARE`` of the format describe it rather than a
deck and are dropped.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)

GENERIC_SHARE = 0.05
MAX_TAGS = 8
CANDIDATES = 60

_SYSTEM_PROMPT = """\
You choose the Scryfall oracle tags that describe what a Magic: The Gathering
Commander deck's theme cards DO, so a search over those tags finds cards that
fit this deck's plan, including ones few decks play.

Output ONLY a JSON object: {"tags": ["<slug>", ...]}

Rules:
- 4 to 8 slugs. Prefer slugs from the candidate list; any other must be a real
  Scryfall oracle tag slug.
- Pick the mechanics the plan is built on and the cards that amplify them
  (a token deck wants token makers AND token doublers and anthems).
- No generic roles (ramp, card draw, removal, counterspells, tutors, lands):
  those are searched separately.\
"""


def candidates(snapshot: dict[str, Any], commander_tags: set[str], store: Any) -> list[tuple[str, int]]:
    """``(slug, percent of the format carrying it)`` the drafter chooses from, most relevant first."""
    from app.brainmap.mechanical import is_flavour_tag
    from app.pipeline import roles

    commanders = {(n or "").lower() for n in (snapshot.get("commander"), snapshot.get("partner_commander"))}
    deck_counts: Counter[str] = Counter()
    for c in snapshot.get("cards", []):
        if (c.get("name") or "").lower() not in commanders and "Land" not in (c.get("type_line") or ""):
            deck_counts.update(set(c.get("tags") or []))
    related = store.related_tags(sorted(commander_tags), min_lift=5.0, limit=12) if commander_tags else {}
    order = [*sorted(commander_tags), *related, *(t for t, n in deck_counts.most_common(150) if n >= 2)]
    seen: list[str] = []
    for slug in order:
        if slug not in seen and not is_flavour_tag(slug) and not roles.fine_roles_for_tags([slug], None):
            seen.append(slug)
    breadth = store.tag_breadth(seen)
    out = [s for s in seen if 0 < breadth.get(s, 0) < GENERIC_SHARE and not store.is_colour_artifact(s)]
    return [(s, round(breadth[s] * 100, 2)) for s in out[:CANDIDATES]]


def build_prompt(snapshot: dict[str, Any], cands: list[tuple[str, int]]) -> str:
    commander = snapshot.get("commander") or ""
    by_name = {(c.get("name") or "").lower(): c for c in snapshot.get("cards", [])}
    text = " ".join(str((by_name.get(commander.lower()) or {}).get("oracle_text") or "").split())
    lines = [f"Commander: {commander}" + (f" | {text}" if text else "")]
    if snapshot.get("plan_notes"):
        lines.append(f"Plan: {' '.join(str(snapshot['plan_notes']).split())}")
    if snapshot.get("themes"):
        lines.append(f"Themes: {', '.join(snapshot['themes'])}")
    names = [c.get("name") for c in snapshot.get("cards", []) if c.get("name") and c.get("name") != commander]
    lines.append(f"Current cards: {', '.join(names)}")
    lines.append("")
    lines.append("Candidate tags (slug: % of the format carrying it):")
    lines.extend(f"- {s}: {n}%" for s, n in cands)
    return "\n".join(lines)


def draft(provider: Any, snapshot: dict[str, Any], commander_tags: set[str], store: Any,
          *, model: str | None = None) -> list[str]:
    """4-8 real, specific tag slugs for the deck's plan."""
    cands = candidates(snapshot, commander_tags, store)
    raw = provider.complete_json(_SYSTEM_PROMPT, build_prompt(snapshot, cands), model=model, thinking=False)
    data = json.loads(raw)
    picked = [t.strip().lower() for t in (data.get("tags") if isinstance(data, dict) else None) or []
              if isinstance(t, str) and t.strip()]
    breadth = store.tag_breadth(picked)
    out = []
    for slug in picked:
        if slug in out:
            continue
        if not 0 < breadth.get(slug, 0) < GENERIC_SHARE:
            logger.info("plan tags: dropping %r (unknown or generic)", slug)
            continue
        out.append(slug)
    return out[:MAX_TAGS]


def retrieve(tags: list[str], identity: frozenset[str], store: Any, *, limit: int = 80) -> list[dict[str, Any]]:
    """On-colour cards carrying the plan's tags, an equal share per tag.

    Summing tag weights let two broad tags that travel together
    (synergy-instant, synergy-sorcery) fill the whole list and crowd out a
    narrow one (copy-spell, where Twinning Staff sits). Each tag gets its
    share, taken in turn; within a tag, cards carrying more of the plan come
    first, then popularity. Colour is filtered before the limit, not after.
    """
    if not tags:
        return []
    per_tag: dict[str, list[dict[str, Any]]] = {}
    carried: Counter[str] = Counter()
    for tag in tags:
        cards = [c for c in store.cards_with_any_tag([tag], limit=100_000)
                 if c.get("oracle_id") and {x for x in c.get("color_identity") or [] if x} <= identity]
        per_tag[tag] = cards
        carried.update(c["oracle_id"] for c in cards)
    _last = 10**9
    for tag, cards in per_tag.items():
        cards.sort(key=lambda c: (-carried[c["oracle_id"]], c.get("edhrec_rank") or _last))
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    cursors = {t: 0 for t in tags}
    while len(out) < limit and any(cursors[t] < len(per_tag[t]) for t in tags):
        for tag in tags:
            cards = per_tag[tag]
            while cursors[tag] < len(cards) and cards[cursors[tag]]["oracle_id"] in seen:
                cursors[tag] += 1
            if cursors[tag] < len(cards) and len(out) < limit:
                card = cards[cursors[tag]]
                seen.add(card["oracle_id"])
                out.append(card)
                cursors[tag] += 1
    return out
