"""Tool name -> callable registry with a safe-invoke wrapper.

Tool exceptions never propagate to the chat engine's loop; they're turned
into an error string the model can see and adapt to. Deck-mutation tools
are flagged so the chat engine knows to emit a deck_updated SSE event.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from sqlmodel import Session

from app.tools import deck_tools
from app.tools.edhrec_client import EdhrecError, get_edhrec_client
from app.tools.scryfall_client import ScryfallError, get_scryfall_client

DECK_MUTATION_TOOLS = {
    "deck_add_card",
    "deck_remove_card",
    "deck_set_commander",
    "deck_update_notes",
}


@dataclass
class DispatchResult:
    ok: bool
    content: Any  # JSON-serializable payload on success, error message string on failure


def _scryfall_search(query: str, limit: int = 10) -> list[dict]:
    return get_scryfall_client().search(query, limit=limit)


def _scryfall_card_by_name(name: str, fuzzy: bool = True) -> dict:
    return get_scryfall_client().named(name, fuzzy=fuzzy)


def _scryfall_card_collection(names: list[str]) -> dict:
    return get_scryfall_client().collection(names)


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
}

# Tools that operate on deck state and need a `session` injected as the first arg.
SESSION_TOOLS: dict[str, Callable[..., Any]] = {
    "deck_get_current": deck_tools.deck_get_current,
    "deck_add_card": deck_tools.deck_add_card,
    "deck_remove_card": deck_tools.deck_remove_card,
    "deck_set_commander": deck_tools.deck_set_commander,
    "deck_update_notes": deck_tools.deck_update_notes,
}


def dispatch(name: str, arguments: dict[str, Any], session: Session) -> DispatchResult:
    try:
        if name in STATELESS_TOOLS:
            result = STATELESS_TOOLS[name](**arguments)
        elif name in SESSION_TOOLS:
            result = SESSION_TOOLS[name](session, **arguments)
        else:
            return DispatchResult(ok=False, content=f"Unknown tool: {name}")
        return DispatchResult(ok=True, content=result)
    except (ScryfallError, EdhrecError, ValueError) as exc:
        return DispatchResult(ok=False, content=str(exc))
    except Exception as exc:  # noqa: BLE001 - never let a tool crash the chat loop
        return DispatchResult(ok=False, content=f"Tool '{name}' failed: {exc}")
