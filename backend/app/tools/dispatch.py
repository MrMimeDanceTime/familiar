"""Tool name -> callable registry with a safe-invoke wrapper.

Tool exceptions never propagate to the chat engine's loop; they're turned
into an error string the model can see and adapt to. Deck-mutation tools
are flagged so the chat engine knows to emit a deck_updated SSE event.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from sqlmodel import Session

from app.knowledge.store import search_knowledge
from app.knowledge.tag_lookup import get_tags_for_card
from app.tools import deck_tools
from app.tools.edhrec_client import EdhrecError, get_edhrec_client
from app.tools.scryfall_client import ScryfallError, get_scryfall_client

DECK_MUTATION_TOOLS = {
    "deck_add_card",
    "deck_remove_card",
    "deck_set_commander",
    "deck_update_notes",
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


def _scryfall_card_by_name(name: str, fuzzy: bool = True) -> dict:
    result = get_scryfall_client().named(name, fuzzy=fuzzy)
    result["tags"] = get_tags_for_card(result.get("oracle_id"))
    return result


def _scryfall_card_collection(names: list[str]) -> dict:
    result = get_scryfall_client().collection(names)
    for c in result.get("found", []):
        c["tags"] = get_tags_for_card(c.get("oracle_id"))
    return result


def _edhrec_commander_recs(commander_name: str) -> dict:
    return get_edhrec_client().commander_recs(commander_name)


def _edhrec_card_synergy(card_name: str, commander_name: str) -> dict | None:
    return get_edhrec_client().card_synergy(card_name, commander_name)


# Tools that don't need DB access (no `session` arg injected).
STATELESS_TOOLS: dict[str, Callable[..., Any]] = {
    "scryfall_search": _scryfall_search,
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
    "propose_deck_changes": deck_tools.propose_deck_changes,
    "withdraw_pending_proposals": deck_tools.withdraw_pending_proposals,
}


def _suggest_cards(
    session: Session,
    provider: Any,
    deck_id: int,
    intent: str,
    conversation_id: int | None = None,
    message_id: int | None = None,
) -> dict:
    """Run the retrieval pipeline and return its proposal batch.

    The engine forces deck_id/conversation_id (DECK_SCOPED_TOOLS / PROPOSAL_TOOLS),
    so the model's guesses for them are overridden before we get here. Returns the
    same {ok, summary, proposals} shape as propose_deck_changes so the engine's
    proposal streaming needs no special case."""
    from app.pipeline.service import build_suggestions

    result = build_suggestions(
        session, deck_id, intent, provider,
        conversation_id=conversation_id, message_id=message_id,
    )
    return {"ok": True, "summary": result.summary, "proposals": result.proposals}


# Tools that additionally need the LLM provider injected after `session`.
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
            result = PROVIDER_SESSION_TOOLS[name](session, provider, **arguments)
        elif name in SESSION_TOOLS:
            result = SESSION_TOOLS[name](session, **arguments)
        else:
            return DispatchResult(ok=False, content=f"Unknown tool: {name}")
        return DispatchResult(ok=True, content=result)
    except (ScryfallError, EdhrecError, ValueError) as exc:
        return DispatchResult(ok=False, content=str(exc))
    except Exception as exc:  # noqa: BLE001 - never let a tool crash the chat loop
        return DispatchResult(ok=False, content=f"Tool '{name}' failed: {exc}")
