"""SSE event vocabulary for the chat stream.

Event types: token (incremental text), tool_call (loading indicator while
a tool runs), deck_proposal (a pending proposal batch for the player to
approve/deny), deck_updated (fresh deck snapshot after a deck_* mutation),
done (final, with persisted message id), error.
"""

from __future__ import annotations

import json
from typing import Any


def format_sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def token_event(text: str) -> str:
    return format_sse("token", {"text": text})


def tool_call_event(name: str, arguments: dict[str, Any]) -> str:
    return format_sse("tool_call", {"name": name, "arguments": arguments})


def deck_proposal_event(data: dict[str, Any]) -> str:
    return format_sse("deck_proposal", data)


def deck_updated_event(deck_snapshot: dict[str, Any]) -> str:
    return format_sse("deck_updated", deck_snapshot)


def done_event(message_id: int, conversation_id: int) -> str:
    return format_sse("done", {"message_id": message_id, "conversation_id": conversation_id})


def error_event(message: str) -> str:
    return format_sse("error", {"message": message})
