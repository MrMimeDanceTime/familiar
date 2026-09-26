"""Tool results as text the model can scan, instead of a JSON dump.

A deck read for a 99-card list serialised as JSON ran to ten thousand
tokens, most of them punctuation and repeated keys, and the model read it
most turns. Rendering each result as a compact block cuts the size three to
four times and puts the fact the model needs (a card's roles, a proposal's
id, a score's factors) on one line where it can find it.

Every renderer takes the payload the tool returned and is defensive about
its shape: a missing key renders as nothing, never as an exception. Anything
without a renderer falls back to JSON, so a new tool works before it is
pretty.
"""

from __future__ import annotations

import json
from typing import Any, Callable


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def _mv(value: Any) -> str:
    if isinstance(value, (int, float)):
        return str(int(value)) if float(value).is_integer() else f"{value:.1f}"
    return ""


def _identity(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return "".join(str(c) for c in value) or "C"
    return str(value) if value else "C"


def _one_line(text: Any, limit: int | None = None) -> str:
    flat = " ".join(str(text or "").split())
    if limit and len(flat) > limit:
        return flat[: limit - 1].rstrip() + "…"
    return flat


def _card_line(card: dict[str, Any], *, text_limit: int | None = None) -> str:
    """One card, one line: name, cost, type, identity, tags; text below."""
    head = [card.get("name") or "?"]
    cost = card.get("mana_cost")
    if cost:
        head.append(str(cost))
    mv = _mv(card.get("cmc") if "cmc" in card else card.get("mana_value"))
    if mv:
        head.append(f"MV {mv}")
    if card.get("type_line"):
        stats = ""
        if card.get("power") is not None and card.get("toughness") is not None:
            stats = f" {card['power']}/{card['toughness']}"
        elif card.get("loyalty") is not None:
            stats = f" loyalty {card['loyalty']}"
        head.append(f"{card['type_line']}{stats}")
    if "color_identity" in card:
        head.append(f"identity {_identity(card.get('color_identity'))}")
    if card.get("legal_commander") is False:
        head.append("NOT commander-legal")
    if card.get("price_usd") is not None:
        head.append(f"${card['price_usd']:.2f}")
    tags = card.get("tags")
    if tags:
        head.append("tags: " + ", ".join(str(t) for t in tags))
    if card.get("unverified"):
        head.append("[unverified: could not be resolved]")
    if card.get("needs_lookup"):
        head.append("[text not fetched: look it up before relying on it]")
    lines = [" · ".join(head)]
    text = card.get("oracle_text")
    if text:
        lines.append("  text: " + _one_line(text, text_limit))
    rulings = card.get("rulings")
    if rulings:
        for r in rulings:
            if isinstance(r, dict):
                stamp = f"({r.get('published_at')}) " if r.get("published_at") else ""
                lines.append(f"  ruling: {stamp}{_one_line(r.get('comment'))}")
            else:
                lines.append(f"  ruling: {_one_line(r)}")
    return "\n".join(lines)


def _render_cards(cards: list[Any], *, text_limit: int | None = None) -> str:
    return "\n".join(_card_line(c, text_limit=text_limit) for c in cards if isinstance(c, dict))


# ── deck ───────────────────────────────────────────────────────────────────

_LAND_LIKE = {"Land", "Basic Land"}


def render_deck(snapshot: dict[str, Any]) -> str:
    lines: list[str] = []
    total = snapshot.get("total_cards")
    head = f"Deck \"{snapshot.get('name')}\" ({snapshot.get('format', 'commander')}) · {total} cards"
    lines.append(head + ".")
    commander = snapshot.get("commander")
    if commander:
        partner = snapshot.get("partner_commander")
        lines.append(f"Commander: {commander}" + (f" / {partner}" if partner else "") + ".")
    else:
        lines.append("Commander: not set.")
    if snapshot.get("notes"):
        lines.append(f"Notes: {_one_line(snapshot['notes'])}")

    plan = snapshot.get("plan")
    if isinstance(plan, dict):
        bits: list[str] = []
        if plan.get("themes"):
            bits.append("themes: " + ", ".join(str(t) for t in plan["themes"]))
        if plan.get("notes"):
            bits.append(f"plan notes: {_one_line(plan['notes'])}")
        if plan.get("off_meta") is not None:
            bits.append(f"off_meta {plan['off_meta']}")
        if plan.get("max_card_price") is not None:
            bits.append(f"budget ${plan['max_card_price']:.2f}/card")
        state = "set" if plan.get("is_set") else "not set"
        lines.append(f"Plan ({state})" + (": " + " · ".join(bits) if bits else "") + ".")
        counts = plan.get("role_counts") or {}
        if counts:
            lines.append("Role counts: " + ", ".join(
                f"{role} {v.get('current')}/{v.get('target')}" for role, v in counts.items()
                if isinstance(v, dict)
            ) + ".")
        needs = plan.get("still_needs") or {}
        lines.append(
            "Still needs: " + (", ".join(f"{r} (short {g})" for r, g in needs.items()) or "nothing") + "."
        )
        staples = plan.get("missing_auto_includes") or []
        if staples:
            lines.append("Missing format staples: " + ", ".join(
                _s(s.get("name")) for s in staples if isinstance(s, dict)
            ) + ".")
        if plan.get("counts_overlap"):
            lines.append(f"({plan['counts_overlap']})")
    elif snapshot.get("themes") or snapshot.get("plan_notes"):
        bits = []
        if snapshot.get("themes"):
            bits.append("themes: " + ", ".join(str(t) for t in snapshot["themes"]))
        if snapshot.get("plan_notes"):
            bits.append(f"plan notes: {_one_line(snapshot['plan_notes'])}")
        lines.append("Plan: " + " · ".join(bits) + ".")

    cards = [c for c in snapshot.get("cards", []) if isinstance(c, dict)]
    by_category: dict[str, list[dict[str, Any]]] = {}
    for c in cards:
        by_category.setdefault(c.get("category") or "Other", []).append(c)
    order = sorted(by_category, key=lambda k: (k != "Commander", k in _LAND_LIKE, k))
    lines.append("")
    lines.append(f"Cards ({total}):")
    for category in order:
        group = by_category[category]
        count = sum(int(c.get("quantity") or 1) for c in group)
        lines.append(f"{category} ({count}):")
        if category in _LAND_LIKE:
            lines.append("  " + ", ".join(
                f"{c.get('name')} x{c.get('quantity')}" if (c.get("quantity") or 1) > 1 else _s(c.get("name"))
                for c in group
            ))
            continue
        for c in group:
            bits = [_s(c.get("name"))]
            if (c.get("quantity") or 1) > 1:
                bits[0] += f" x{c['quantity']}"
            mv = _mv(c.get("mana_value"))
            if mv:
                bits.append(f"MV {mv}")
            if c.get("type_line"):
                bits.append(str(c["type_line"]))
            if c.get("tags"):
                bits.append("tags: " + ", ".join(str(t) for t in c["tags"]))
            if c.get("notes"):
                bits.append(f"why: {_one_line(c['notes'], 160)}")
            line = "- " + " · ".join(bits)
            if c.get("oracle_text"):
                line += "\n  text: " + _one_line(c["oracle_text"])
            lines.append(line)

    pending = snapshot.get("pending_proposals")
    if isinstance(pending, dict):
        items = pending.get("proposals") or []
        if items:
            lines.append("")
            lines.append(f"Awaiting the player's decision ({len(items)}):")
            for p in items:
                if p.get("action") == "set_commander":
                    lines.append(f"- #{p.get('id')} set commander {p.get('commander_name')}")
                else:
                    qty = f" x{p['quantity']}" if p.get("quantity") and p["quantity"] > 1 else ""
                    lines.append(f"- #{p.get('id')} {p.get('action')} {p.get('card_name')}{qty}")
        else:
            lines.append("")
            lines.append("No proposals awaiting the player's decision.")
    if snapshot.get("oracle_text_note"):
        lines.append(f"({snapshot['oracle_text_note']})")
    return "\n".join(lines)


def render_stats(stats: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(
        f"Total {stats.get('total_cards')} cards · average MV {stats.get('avg_mv')} · "
        f"lands {stats.get('land_count')} ({stats.get('land_pct')}%) · ramp {stats.get('ramp_count')} · "
        f"draw {stats.get('draw_count')} · removal {stats.get('removal_count')}."
    )
    factors = stats.get("bracket_factors") or []
    lines.append(f"Bracket {stats.get('bracket')}: " + "; ".join(str(f) for f in factors))
    power = f"Power {stats.get('power_level')}"
    if stats.get("power_nuance_adj"):
        power += f" (base {stats.get('power_level_base')}, nuance {stats['power_nuance_adj']:+})"
    if stats.get("power_nuance_pending"):
        power += " (nuance pending: the deck is still settling)"
    lines.append(power + ": " + "; ".join(str(f) for f in stats.get("power_factors") or []))
    curve = stats.get("mana_curve") or []
    if curve:
        lines.append("Curve: " + " · ".join(f"{b.get('mv')}: {b.get('count')}" for b in curve))
    colours = stats.get("color_distribution") or []
    if colours:
        lines.append("Colours: " + ", ".join(
            f"{c.get('color')} {c.get('count')} ({c.get('pct')}%)" for c in colours
        ))
    types = stats.get("type_breakdown") or []
    if types:
        lines.append("Types: " + ", ".join(
            f"{t.get('type')} {t.get('count')} ({t.get('pct')}%)" for t in types
        ))
    deficiencies = stats.get("deficiencies") or []
    if deficiencies:
        lines.append("Against targets: " + "; ".join(
            f"{d.get('category')} {d.get('count')} {d.get('status')} (want {d.get('target_low')}-{d.get('target_high')})"
            for d in deficiencies
        ))
    sources = stats.get("mana_sources") or []
    if sources:
        lines.append("Mana sources: " + "; ".join(
            f"{m.get('color')} {m.get('sources')} sources {m.get('source_pct')}% vs {m.get('pip_pct')}% of pips {m.get('status')}"
            for m in sources
        ))
    if stats.get("total_price_usd") is not None:
        lines.append(
            f"Price: ${stats['total_price_usd']:.2f} across {stats.get('priced_cards')} priced cards."
        )
    combos = stats.get("combos") or []
    if combos:
        lines.append("Combos in the deck:")
        for c in combos:
            produces = ", ".join(str(p) for p in c.get("produces") or [])
            line = "- " + " + ".join(str(n) for n in c.get("cards") or [])
            if produces:
                line += f" → {produces}"
            if c.get("description"):
                line += f" ({_one_line(c['description'], 200)})"
            lines.append(line)
    untagged = stats.get("untagged") or []
    if untagged:
        lines.append(
            f"Untagged ({len(untagged)}, so role counts are floors): " + ", ".join(untagged[:20])
            + (", …" if len(untagged) > 20 else "")
        )
    return "\n".join(lines)


# ── cards and recommendations ──────────────────────────────────────────────

def render_search(content: Any) -> str:
    if isinstance(content, dict):
        cards = content.get("cards") or []
        lines = []
        if content.get("note"):
            lines.append(str(content["note"]))
        lines.append(f"{len(cards)} card(s):")
        lines.append(_render_cards(cards))
        return "\n".join(lines)
    if isinstance(content, list):
        return f"{len(content)} card(s):\n" + _render_cards(content)
    return json.dumps(content)


def render_card(content: Any) -> str:
    return _card_line(content) if isinstance(content, dict) else json.dumps(content)


def render_collection(content: Any) -> str:
    if not isinstance(content, dict):
        return json.dumps(content)
    lines = [_render_cards(content.get("found") or [])]
    missing = content.get("not_found") or []
    if missing:
        lines.append("Not found: " + ", ".join(str(n) for n in missing))
    return "\n".join(line for line in lines if line)


def render_edhrec_recs(content: Any) -> str:
    if not isinstance(content, dict):
        return json.dumps(content)
    lines = [f"EDHREC recommendations for {content.get('commander')}:"]
    for key, category in (content.get("categories") or {}).items():
        if not isinstance(category, dict):
            continue
        cards = category.get("cards") or []
        lines.append("")
        lines.append(f"{category.get('header') or key} ({len(cards)}):")
        for c in cards:
            if not isinstance(c, dict):
                continue
            bits = [_s(c.get("name"))]
            if c.get("synergy") is not None:
                bits.append(f"synergy {float(c['synergy']):+.0%}")
            if c.get("inclusion") is not None:
                bits.append(f"in {float(c['inclusion']):.0%} of decks")
            if c.get("mana_cost"):
                bits.append(str(c["mana_cost"]))
            if c.get("type_line"):
                bits.append(str(c["type_line"]))
            if c.get("tags"):
                bits.append("tags: " + ", ".join(str(t) for t in c["tags"][:6]))
            if c.get("unverified"):
                bits.append("[unverified]")
            if c.get("needs_lookup"):
                bits.append("[text not fetched]")
            line = "- " + " · ".join(bits)
            if c.get("oracle_text"):
                line += "\n  text: " + _one_line(c["oracle_text"])
            lines.append(line)
    return "\n".join(lines)


def render_edhrec_synergy(content: Any) -> str:
    if content is None:
        return "Not on the commander's EDHREC page."
    if isinstance(content, dict):
        bits = [_s(content.get("name"))]
        if content.get("synergy") is not None:
            bits.append(f"synergy {float(content['synergy']):+.0%}")
        if content.get("inclusion") is not None:
            bits.append(f"in {float(content['inclusion']):.0%} of decks")
        if content.get("category"):
            bits.append(f"category {content['category']}")
        return " · ".join(bits)
    return json.dumps(content)


def render_knowledge(content: Any) -> str:
    if not isinstance(content, list):
        return json.dumps(content)
    if not content:
        return "No knowledge entries matched."
    lines = []
    for hit in content:
        if not isinstance(hit, dict):
            continue
        meta = ", ".join(
            str(hit[k]) for k in ("category", "format", "source") if hit.get(k)
        )
        lines.append(f"## {hit.get('title')}" + (f" ({meta})" if meta else ""))
        lines.append(str(hit.get("body") or ""))
        lines.append("")
    return "\n".join(lines).rstrip()


# ── proposals ──────────────────────────────────────────────────────────────

def _scores(scores: Any) -> str:
    if not isinstance(scores, dict):
        return ""
    bits = []
    if scores.get("total") is not None:
        bits.append(f"fit {scores['total']:.2f}")
    for layer in ("consensus", "mechanical", "personal"):
        if scores.get(layer) is not None:
            bits.append(f"{layer} {scores[layer]:.2f}")
    if scores.get("explain"):
        bits.append(str(scores["explain"]))
    return ", ".join(bits)


def render_proposals(content: Any) -> str:
    if not isinstance(content, dict):
        return json.dumps(content)
    proposals = content.get("proposals") or []
    lines = []
    if content.get("summary"):
        lines.append(str(content["summary"]))
    if not proposals:
        lines.append("No proposals were created.")
        return "\n".join(lines)
    lines.append(f"{len(proposals)} proposal(s) now awaiting the player's decision:")
    for p in proposals:
        if not isinstance(p, dict):
            continue
        if p.get("action") == "set_commander":
            what = f"set commander {p.get('commander_name')}"
        else:
            qty = f" x{p['quantity']}" if p.get("quantity") and p["quantity"] > 1 else ""
            what = f"{p.get('action')} {p.get('card_name')}{qty}"
        bits = [f"#{p.get('id')} {what}"]
        if p.get("category"):
            bits.append(str(p["category"]))
        if p.get("price_usd") is not None:
            bits.append(f"${p['price_usd']:.2f}")
        scored = _scores(p.get("scores"))
        if scored:
            bits.append(scored)
        line = "- " + " · ".join(bits)
        if p.get("reasoning"):
            line += f" — {_one_line(p['reasoning'])}"
        lines.append(line)
        # The card's real text, because the very next thing the model does is
        # describe this batch to the player. Without it that description came
        # from memory, which is where the wrong rules text came from.
        if p.get("type_line") or p.get("oracle_text"):
            head = " · ".join(
                str(p[k]) for k in ("mana_cost", "type_line") if p.get(k)
            )
            text = _one_line(p.get("oracle_text"))
            lines.append(f"  {head}" + (f" — {text}" if text else ""))
    alternatives = content.get("alternatives") or []
    if alternatives:
        lines.append("Also considered, not proposed (real text, for comparison):")
        for a in alternatives:
            if not isinstance(a, dict) or not a.get("name"):
                continue
            head = " · ".join(str(a[k]) for k in ("mana_cost", "type_line") if a.get(k))
            text = _one_line(a.get("oracle_text"))
            lines.append(f"- {a['name']}" + (f" · {head}" if head else "") + (f" — {text}" if text else ""))
    return "\n".join(lines)


def render_withdraw(content: Any) -> str:
    if not isinstance(content, dict):
        return json.dumps(content)
    lines = []
    withdrawn = content.get("withdrawn") or []
    lines.append(
        "Withdrawn: " + (", ".join(str(n) for n in withdrawn) if withdrawn else "nothing") + "."
    )
    if content.get("preserved_commander"):
        lines.append(f"The commander proposal ({content['preserved_commander']}) was kept.")
    if content.get("not_found"):
        lines.append("Not pending, so not withdrawn: " + ", ".join(str(n) for n in content["not_found"]) + ".")
    return "\n".join(lines)


_RENDERERS: dict[str, Callable[[Any], str]] = {
    "deck_get_current": render_deck,
    "deck_set_plan": render_deck,
    "deck_update_notes": render_deck,
    "deck_get_stats": render_stats,
    "search_card_index": render_search,
    "scryfall_search": render_search,
    "scryfall_card_by_name": render_card,
    "scryfall_card_collection": render_collection,
    "edhrec_commander_recs": render_edhrec_recs,
    "edhrec_card_synergy": render_edhrec_synergy,
    "search_deckbuilding_knowledge": render_knowledge,
    "propose_deck_changes": render_proposals,
    "suggest_cards": render_proposals,
    "withdraw_pending_proposals": render_withdraw,
}


# How deep to walk a tool result looking for card objects. Every payload
# shape in the toolkit nests cards at most three levels down (recs ->
# categories -> cards -> card), so this is generous.
_GROUNDING_MAX_DEPTH = 6


def grounded_card_names(content: Any) -> set[str]:
    """Lowercased names of every card whose real text is in this result.

    Walks the payload rather than switching on the tool, so a tool added
    later grounds its cards without being registered anywhere. A card counts
    as grounded when it carries an ``oracle_text`` key at all: a vanilla
    creature's empty text is still the truth about that card.
    """
    found: set[str] = set()

    def walk(node: Any, depth: int) -> None:
        if depth > _GROUNDING_MAX_DEPTH:
            return
        if isinstance(node, dict):
            name = node.get("name")
            if isinstance(name, str) and name.strip() and "oracle_text" in node:
                found.add(name.strip().lower())
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value, depth + 1)
        elif isinstance(node, list):
            for item in node:
                walk(item, depth + 1)

    walk(content, 0)
    return found


def render_result(tool_name: str, content: Any) -> str:
    """The text a tool result becomes in the model's context."""
    renderer = _RENDERERS.get(tool_name)
    if renderer is None:
        return json.dumps(content)
    try:
        return renderer(content)
    except Exception:  # noqa: BLE001 - a rendering bug must not lose the result
        return json.dumps(content)
