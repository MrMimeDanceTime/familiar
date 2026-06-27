from __future__ import annotations

from typing import Any

import anthropic

from app.llm.base import AssistantTurn, ToolCallRequest, ToolResult, ToolSpec


class AnthropicProvider:
    def __init__(self, api_key: str, model: str) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def send(
        self,
        system_prompt: str,
        history: list[dict[str, Any]],
        tools: list[ToolSpec],
    ) -> AssistantTurn:
        anthropic_tools = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters,
            }
            for t in tools
        ]

        response = self._client.messages.create(
            model=self._model,
            max_tokens=4096,
            system=system_prompt,
            messages=history,
            tools=anthropic_tools,
        )

        text_parts = [b.text for b in response.content if b.type == "text"]
        tool_calls = [
            ToolCallRequest(id=b.id, name=b.name, arguments=b.input)
            for b in response.content
            if b.type == "tool_use"
        ]

        return AssistantTurn(
            text="\n".join(text_parts) if text_parts else None,
            tool_calls=tool_calls,
            stop_reason=response.stop_reason or "end_turn",
            raw_assistant_message={"role": "assistant", "content": response.content},
        )

    def append_tool_results(
        self,
        history: list[dict[str, Any]],
        assistant_turn: AssistantTurn,
        results: list[ToolResult],
    ) -> list[dict[str, Any]]:
        new_history = [*history, assistant_turn.raw_assistant_message]
        new_history.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": r.call_id,
                        "content": r.content,
                    }
                    for r in results
                ],
            }
        )
        return new_history

    def append_user_message(
        self,
        history: list[dict[str, Any]],
        text: str,
    ) -> list[dict[str, Any]]:
        return [*history, {"role": "user", "content": text}]
