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
        *,
        thinking: bool = True,  # accepted for a provider-neutral signature; Anthropic
        # reasoning is a separate mechanism the chat loop doesn't toggle here.
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

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str | None = None,
        thinking: bool = True,
    ) -> str:
        """Single-shot JSON completion for the retrieval pipeline's stage-1/4.

        Anthropic has no response_format=json_object, so we force JSON the
        idiomatic way: prefill the assistant turn with ``{`` and re-prepend it to
        the reply. ``thinking`` is accepted for a provider-neutral signature but
        ignored — Anthropic reasoning is a separate mechanism the chat loop
        doesn't use here. ``model`` overrides the provider default per call.
        """
        response = self._client.messages.create(
            model=model or self._model,
            max_tokens=4096,
            system=system_prompt,
            messages=[
                {"role": "user", "content": user_prompt},
                {"role": "assistant", "content": "{"},
            ],
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        return "{" + text

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
