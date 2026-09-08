"""The chat engine's event vocabulary.

Event types: token (incremental text), tool_call (loading indicator while
a tool runs), deck_proposal (a pending proposal batch for the player to
approve/deny), deck_updated (fresh deck snapshot after a deck_* mutation),
done (final, with persisted message id), error.

The engine yields ``ChatEvent`` tuples; the turn runner writes them to the
event log and the HTTP layer formats them as SSE with ``format_sse``. The
engine used to format SSE itself and the runner parsed it back, which was a
round trip through text for no reason.
"""

from __future__ import annotations

import json
from typing import Any, NamedTuple


class ChatEvent(NamedTuple):
    event: str
    data: dict[str, Any]


def format_sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def token_event(text: str) -> ChatEvent:
    return ChatEvent("token", {"text": text})


def tool_call_event(name: str, arguments: dict[str, Any]) -> ChatEvent:
    return ChatEvent("tool_call", {"name": name, "arguments": arguments})


def message_reset_event() -> ChatEvent:
    """Discard the assistant text streamed so far for this turn.

    Emitted when a draft reply is withdrawn before the player can act on it —
    today, when it described a card whose text the model never read. What
    follows on the stream replaces it.
    """
    return ChatEvent("message_reset", {})


def deck_proposal_event(data: dict[str, Any]) -> ChatEvent:
    return ChatEvent("deck_proposal", data)


def deck_updated_event(deck_snapshot: dict[str, Any]) -> ChatEvent:
    return ChatEvent("deck_updated", deck_snapshot)


def done_event(message_id: int, conversation_id: int) -> ChatEvent:
    return ChatEvent("done", {"message_id": message_id, "conversation_id": conversation_id})


def error_event(message: str) -> ChatEvent:
    return ChatEvent("error", {"message": message})
