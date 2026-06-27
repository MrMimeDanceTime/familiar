"""The agentic chat loop: load history, call the provider, dispatch any
tool calls, persist everything, and yield SSE-ready events as it goes.

Per the v1 streaming decision, only the final text-bearing turn streams
token-by-token (simulated here by yielding the whole text as one token
event, since neither provider SDK is wired for token-level streaming
yet — intermediate tool-calling turns are non-streamed request/response
with a tool_call event for the UI's loading indicator).
"""

from __future__ import annotations

from typing import Any, Iterator

from sqlmodel import Session

from app.chat.prompt import SYSTEM_PROMPT
from app.chat.streaming import deck_updated_event, done_event, error_event, token_event, tool_call_event
from app.db import repository as repo
from app.llm.base import ChatProvider, ToolResult
from app.tools.dispatch import DECK_MUTATION_TOOLS, dispatch
from app.tools.schemas import TOOL_SPECS

MAX_TOOL_ITERATIONS = 8


def _load_history(session: Session, conversation_id: int) -> list[dict[str, Any]]:
    messages = repo.list_messages(session, conversation_id)
    history: list[dict[str, Any]] = []
    for msg in messages:
        history.extend(msg.provider_native or [])
    return history


def run_chat_turn(
    session: Session,
    provider: ChatProvider,
    conversation_id: int,
    user_text: str,
    deck_id: int | None,
) -> Iterator[str]:
    """Run one user turn through the agentic loop, yielding formatted SSE strings."""
    history = _load_history(session, conversation_id)
    history = provider.append_user_message(history, user_text)

    sequence = repo.next_sequence(session, conversation_id)
    repo.add_message(
        session,
        conversation_id,
        role="user",
        sequence=sequence,
        text_content=user_text,
        provider_native=[{"role": "user", "content": user_text}],
    )
    sequence += 1

    try:
        for _ in range(MAX_TOOL_ITERATIONS):
            turn = provider.send(SYSTEM_PROMPT, history, TOOL_SPECS)

            if not turn.tool_calls:
                if turn.text:
                    yield token_event(turn.text)
                message = repo.add_message(
                    session,
                    conversation_id,
                    role="assistant",
                    sequence=sequence,
                    text_content=turn.text,
                    provider_native=[turn.raw_assistant_message],
                )
                repo.touch_conversation(session, conversation_id)
                yield done_event(message.id, conversation_id)
                return

            results: list[ToolResult] = []
            tool_calls_log = []
            tool_results_log = []
            for call in turn.tool_calls:
                yield tool_call_event(call.name, call.arguments)

                args = dict(call.arguments)
                if call.name in DECK_MUTATION_TOOLS or call.name == "deck_get_current":
                    args.setdefault("deck_id", deck_id)

                result = dispatch(call.name, args, session)
                content = str(result.content) if not result.ok else _serialize(result.content)
                results.append(ToolResult(call_id=call.id, content=content))

                tool_calls_log.append({"id": call.id, "name": call.name, "arguments": call.arguments})
                tool_results_log.append({"call_id": call.id, "content": content})

                if result.ok and call.name in DECK_MUTATION_TOOLS and deck_id is not None:
                    yield deck_updated_event(result.content)

            new_history = provider.append_tool_results(history, turn, results)
            # provider.append_tool_results appends exactly two new entries to
            # history: the assistant's tool-call message, then the tool-result
            # message. Persist them in that same native shape so replay can
            # feed this conversation back into the same provider exactly.
            appended = new_history[len(history) :]
            assistant_native, tool_result_native = appended[0], appended[1]

            repo.add_message(
                session,
                conversation_id,
                role="assistant",
                sequence=sequence,
                tool_calls=tool_calls_log,
                provider_native=[assistant_native],
            )
            sequence += 1
            repo.add_message(
                session,
                conversation_id,
                role="tool",
                sequence=sequence,
                tool_results=tool_results_log,
                provider_native=[tool_result_native],
            )
            sequence += 1

            history = new_history

        yield error_event("Reached max tool-call iterations without a final response.")
    except Exception as exc:  # noqa: BLE001 - surface to client instead of crashing the stream
        yield error_event(str(exc))


def _serialize(value: Any) -> str:
    import json

    return json.dumps(value)
