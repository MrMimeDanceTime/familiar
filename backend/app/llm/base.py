"""Provider-neutral types for the LLM tool-calling loop.

`history` is intentionally left as a list of provider-native dicts rather
than forced into one shared schema: Claude's tool_use/tool_result content
blocks and OpenAI-style tool_calls/role:"tool" messages don't map cleanly
onto a single shape, and each provider already wants its own native shape
back on the next call. The chat engine only ever touches AssistantTurn /
ToolCallRequest / ToolResult, and passes the opaque history list through.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema


@dataclass
class ToolCallRequest:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    call_id: str
    content: str


@dataclass
class AssistantTurn:
    text: str | None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    stop_reason: str = "end_turn"
    raw_assistant_message: Any = None


class ChatProvider(Protocol):
    def send(
        self,
        system_prompt: str,
        history: list[dict[str, Any]],
        tools: list[ToolSpec],
        *,
        thinking: bool = True,
    ) -> AssistantTurn: ...

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str | None = None,
        thinking: bool = True,
    ) -> str: ...

    def append_tool_results(
        self,
        history: list[dict[str, Any]],
        assistant_turn: AssistantTurn,
        results: list[ToolResult],
    ) -> list[dict[str, Any]]: ...

    def append_user_message(
        self,
        history: list[dict[str, Any]],
        text: str,
    ) -> list[dict[str, Any]]: ...
