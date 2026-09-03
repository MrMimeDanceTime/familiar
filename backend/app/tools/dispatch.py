"""Tool name -> callable registry with a safe-invoke wrapper.

Tool exceptions never propagate to the chat engine's loop; they're turned
into an error string the model can see and adapt to. Deck-mutation tools
are flagged so the chat engine knows to emit a deck_updated SSE event.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from sqlmodel import Session

from app.knowledge.store import search_knowledge
from app.knowledge.tag_lookup import get_tags_for_card
from app.tools import deck_tools
from app.tools.edhrec_client import EdhrecError, get_edhrec_client
from app.tools.scryfall_client import ScryfallError, get_scryfall_client

logger = logging.getLogger("app.tools.dispatch")

DECK_MUTATION_TOOLS = {
    "deck_add_card",
    "deck_remove_card",
    "deck_set_commander",
    "deck_update_notes",
    "deck_set_plan",
}

PROPOSAL_TOOLS = {
    "propose_deck_changes",
    # suggest_cards runs the retrieval pipeline and, like propose_deck_changes,
    # produces pending proposals the engine streams and anchors to the turn.
    "suggest_cards",
}

# Tools scoped to the active conversation's deck. The chat engine forces
# `deck_id` on these to the conversation's deck rather than trusting the
# deck_id the model puts in the call — deck_id is a required tool param, so
# the model always guesses one, and its guess must not decide which deck the
# tool touches.
DECK_SCOPED_TOOLS = {
    "deck_get_current",
    "deck_get_stats",
    "deck_add_card",
    "deck_remove_card",
    "deck_set_commander",
    "deck_update_notes",
    "deck_set_plan",
    "propose_deck_changes",
    "withdraw_pending_proposals",
    "suggest_cards",
}


@dataclass
class DispatchResult:
    ok: bool
    content: Any  # JSON-serializable payload on success, error message string on failure


def _scryfall_search(query: str, limit: int = 10) -> list[dict]:
    return get_scryfall_client().search(query, limit=limit)


def _attach_rulings(cards: list[dict]) -> None:
    """Add each card's rulings from the local index, in place.

    Rulings are the grounding for "does X work with Y", which the model
    otherwise answers from memory. Absent when the index has none.
    """
    try:
        from app.cards import store as card_store

        by_id = card_store.rulings_for_many(
            [c.get("oracle_id") for c in cards if c.get("oracle_id")]
        )
    except Exception:  # noqa: BLE001 - the index is optional
        by_id = {}
    for c in cards:
        rulings = by_id.get(c.get("oracle_id"))
        if rulings:
            c["rulings"] = rulings


def _scryfall_card_by_name(name: str, fuzzy: bool = True) -> dict:
    result = get_scryfall_client().named(name, fuzzy=fuzzy)
    result["tags"] = get_tags_for_card(result.get("oracle_id"))
    _attach_rulings([result])
    return result


def _scryfall_card_collection(names: list[str]) -> dict:
    result = get_scryfall_client().collection(names)
    for c in result.get("found", []):
        c["tags"] = get_tags_for_card(c.get("oracle_id"))
    _attach_rulings(result.get("found", []))
    return result


def _edhrec_commander_recs(commander_name: str) -> dict:
    """EDHREC's recommendations, enriched with each card's real rules text.

    EDHREC returns names plus synergy numbers and nothing else. Handing the
    model a bare list of names is an invitation to fill in what those cards do
    from training data, which is where the misremembered rules text came from —
    the model would then theorise a line of play around a card that doesn't
    work that way. The retrieval pipeline (suggest_cards) already ships oracle
    text with its candidates; this closes the same gap on the EDHREC path.

    One batched Scryfall call covers the whole payload. Enrichment is
    best-effort: if Scryfall is unreachable the recommendations still return,
    because losing them entirely is worse than losing the rules text.
    """
    recs = get_edhrec_client().commander_recs(commander_name)
    return _enrich_recs_with_card_text(recs)


# Enrichment resolves ~300 names, which the Scryfall client chunks at 75 per
# request and self-throttles to ~9 req/s — roughly 10s wall time. EDHREC's own
# response is disk-cached for 24h, but this runs after that cache, so without a
# memo every repeat call in a session pays the full cost again. Keyed by the
# card names actually present, so a stale EDHREC cache refresh invalidates it.
_ENRICHED_RECS: dict[tuple[str, ...], dict[str, Any]] = {}
_ENRICHED_RECS_MAX = 16


# Cards per category to resolve. EDHREC returns ~290 across 13 categories, but
# the bulk ones (creatures 50, lands 50, instants 35) are browse lists — the
# model reasons from the curated heads (high synergy, top cards, game changers).
# Resolving everything costs ~11s of Scryfall time (4 chunks, self-throttled)
# on a tool that runs mid-conversation. Enriching the heads keeps the useful
# grounding at roughly a third of the latency; the tail is explicitly marked so
# the model knows to look a card up rather than assume it knows the text.
_ENRICH_PER_CATEGORY = 12


def _enrich_recs_with_card_text(recs: dict) -> dict:
    """Attach oracle text, type line, and cost to the recommended cards.

    Enriches the head of each category (see _ENRICH_PER_CATEGORY); everything
    beyond that is flagged ``needs_lookup`` so an unenriched name is never
    mistaken for a card whose text the model already has.
    """
    categories = recs.get("categories") or {}
    names: list[str] = []
    for category in categories.values():
        cards = category.get("cards") or []
        for card in cards[:_ENRICH_PER_CATEGORY]:
            name = card.get("name")
            if name:
                names.append(name)
        for card in cards[_ENRICH_PER_CATEGORY:]:
            card["needs_lookup"] = True
    if not names:
        return recs

    unique = list(dict.fromkeys(names))

    memo_key = tuple(unique)
    cached = _ENRICHED_RECS.get(memo_key)
    if cached is not None:
        return cached

    try:
        found = get_scryfall_client().collection(unique)
    except Exception:  # noqa: BLE001 - grounding is best-effort, recs are not
        logger.warning("EDHREC enrichment failed; returning names only", exc_info=True)
        return recs

    by_name = {c["name"].lower(): c for c in found.get("found", [])}
    for category in categories.values():
        for card in (category.get("cards") or [])[:_ENRICH_PER_CATEGORY]:
            match = by_name.get((card.get("name") or "").lower())
            if match is None:
                # Never leave a card bare and unmarked: an unannotated name is
                # exactly the state that invites the model to invent its text.
                card["unverified"] = True
                continue
            card["oracle_text"] = match.get("oracle_text") or ""
            card["type_line"] = match.get("type_line")
            card["mana_cost"] = match.get("mana_cost")
            card["cmc"] = match.get("cmc")
            card["color_identity"] = match.get("color_identity")
            card["tags"] = get_tags_for_card(match.get("oracle_id"))

    if len(_ENRICHED_RECS) >= _ENRICHED_RECS_MAX:
        _ENRICHED_RECS.pop(next(iter(_ENRICHED_RECS)))
    _ENRICHED_RECS[memo_key] = recs
    return recs


def _edhrec_card_synergy(card_name: str, commander_name: str) -> dict | None:
    return get_edhrec_client().card_synergy(card_name, commander_name)


# Fields the model needs to reason about a search hit. The index row carries
# more (image, rank, rarity), which is noise in a tool result.
_SEARCH_FIELDS = (
    "name", "mana_cost", "cmc", "type_line", "oracle_text", "color_identity",
    "legal_commander", "keywords", "power", "toughness",
)


def _search_card_index(
    query: str, limit: int = 10, color_identity: str | None = None
) -> dict:
    """Full-text search over the local card index.

    Scryfall's API is the right tool for its query language; this is for the
    quick "what cards say X" lookups the model makes several times a turn,
    which the index answers instantly, offline, and without a rate limit.
    """
    from app.cards import schema as card_schema
    from app.cards import store as card_store

    if card_schema.card_count() == 0:
        return {
            "cards": [],
            "note": "The local card index has not been built yet; use scryfall_search.",
        }
    limit = max(1, min(int(limit or 10), 50))
    hits = card_store.search_text(query, limit=limit, identity=color_identity)
    oracle_ids = [c["oracle_id"] for c in hits if c.get("oracle_id")]
    tags = card_store.tags_for_many(oracle_ids)
    cards = []
    for hit in hits:
        card = {k: hit.get(k) for k in _SEARCH_FIELDS}
        card["oracle_id"] = hit.get("oracle_id")
        card["tags"] = sorted(tags.get(hit.get("oracle_id"), set()))
        cards.append(card)
    # Rulings on the top few only: a wide search is browsing, not a ruling
    # question, and fifty cards' rulings would swamp the result.
    _attach_rulings(cards[:5])
    for card in cards:
        card.pop("oracle_id", None)
    return {"cards": cards, "count": len(cards)}


# Tools that don't need DB access (no `session` arg injected).
STATELESS_TOOLS: dict[str, Callable[..., Any]] = {
    "scryfall_search": _scryfall_search,
    "search_card_index": _search_card_index,
    "scryfall_card_by_name": _scryfall_card_by_name,
    "scryfall_card_collection": _scryfall_card_collection,
    "edhrec_commander_recs": _edhrec_commander_recs,
    "edhrec_card_synergy": _edhrec_card_synergy,
    "search_deckbuilding_knowledge": search_knowledge,
}

# Tools that operate on deck state and need a `session` injected as the first arg.
SESSION_TOOLS: dict[str, Callable[..., Any]] = {
    "deck_get_current": deck_tools.deck_get_current,
    "deck_add_card": deck_tools.deck_add_card,
    "deck_remove_card": deck_tools.deck_remove_card,
    "deck_set_commander": deck_tools.deck_set_commander,
    "deck_update_notes": deck_tools.deck_update_notes,
    "deck_set_plan": deck_tools.deck_set_plan,
    "propose_deck_changes": deck_tools.propose_deck_changes,
    "withdraw_pending_proposals": deck_tools.withdraw_pending_proposals,
}


# The prompt asks for batches of three to six; a default of five leaves the
# model trimming one rather than four.
_DEFAULT_SUGGEST_COUNT = 5


def _suggest_cards(
    session: Session,
    provider: Any,
    deck_id: int,
    intent: str,
    count: int | None = None,
    conversation_id: int | None = None,
    message_id: int | None = None,
) -> dict:
    """Run the retrieval pipeline and return its proposal batch.

    The engine forces deck_id/conversation_id (DECK_SCOPED_TOOLS / PROPOSAL_TOOLS),
    so the model's guesses for them are overridden before we get here. Returns the
    same {ok, summary, proposals} shape as propose_deck_changes so the engine's
    proposal streaming needs no special case."""
    from app.pipeline.service import build_suggestions

    try:
        picks = int(count) if count is not None else _DEFAULT_SUGGEST_COUNT
    except (TypeError, ValueError):
        picks = _DEFAULT_SUGGEST_COUNT
    picks = max(1, min(picks, 10))
    result = build_suggestions(
        session, deck_id, intent, provider,
        conversation_id=conversation_id, message_id=message_id,
        max_picks=picks,
    )
    return {"ok": True, "summary": result.summary, "proposals": result.proposals}


# Tools that additionally need the LLM provider injected alongside `session`.
# The provider is passed BY KEYWORD (see dispatch below), so a tool here only
# has to declare a `provider` parameter — its position in the signature is its
# own business. Injecting positionally broke deck_get_stats, whose provider
# comes after deck_id: the provider bound to deck_id and the real deck_id then
# collided as a duplicate keyword.
PROVIDER_SESSION_TOOLS: dict[str, Callable[..., Any]] = {
    "suggest_cards": _suggest_cards,
    "deck_get_stats": deck_tools.deck_get_stats,
}


def dispatch(
    name: str,
    arguments: dict[str, Any],
    session: Session,
    provider: Any | None = None,
) -> DispatchResult:
    if arguments.get("_parse_error"):
        return DispatchResult(
            ok=False,
            content=f"Failed to parse arguments for '{name}'. Raw: {arguments.get('_raw', '')[:200]}",
        )
    try:
        if name in STATELESS_TOOLS:
            result = STATELESS_TOOLS[name](**arguments)
        elif name in PROVIDER_SESSION_TOOLS:
            if provider is None:
                return DispatchResult(
                    ok=False, content=f"Tool '{name}' requires an LLM provider but none was supplied."
                )
            result = PROVIDER_SESSION_TOOLS[name](session, provider=provider, **arguments)
        elif name in SESSION_TOOLS:
            result = SESSION_TOOLS[name](session, **arguments)
        else:
            return DispatchResult(ok=False, content=f"Unknown tool: {name}")
        return DispatchResult(ok=True, content=result)
    except (ScryfallError, EdhrecError, ValueError) as exc:
        return DispatchResult(ok=False, content=str(exc))
    except Exception as exc:  # noqa: BLE001 - never let a tool crash the chat loop
        return DispatchResult(ok=False, content=f"Tool '{name}' failed: {exc}")
