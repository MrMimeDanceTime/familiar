from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from app.llm.base import AssistantTurn, ToolCallRequest, ToolResult, ToolSpec

DEEPSEEK_BASE_URL = "https://api.deepseek.com"


class DeepSeekProvider:
    def __init__(self, api_key: str, model: str) -> None:
        self._client = OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)
        self._model = model

    def send(
        self,
        system_prompt: str,
        history: list[dict[str, Any]],
        tools: list[ToolSpec],
    ) -> AssistantTurn:
        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]

        messages = [{"role": "system", "content": system_prompt}, *history]

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                tools=openai_tools,
            )
        except Exception as exc:
            raise RuntimeError(
                f"DeepSeek API call failed (provider may have returned "
                f"malformed JSON or timed out). The engine will retry on "
                f"the next user message. Detail: {exc}"
            ) from exc

        message = response.choices[0].message
        tool_calls: list[ToolCallRequest] = []
        for tc in message.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {"_parse_error": True, "_raw": tc.function.arguments}
            tool_calls.append(
                ToolCallRequest(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                )
            )

        return AssistantTurn(
            text=message.content,
            tool_calls=tool_calls,
            stop_reason=response.choices[0].finish_reason or "stop",
            raw_assistant_message=message.model_dump(exclude_none=True),
        )

    def append_tool_results(
        self,
        history: list[dict[str, Any]],
        assistant_turn: AssistantTurn,
        results: list[ToolResult],
    ) -> list[dict[str, Any]]:
        new_history = [*history, assistant_turn.raw_assistant_message]
        for r in results:
            new_history.append(
                {
                    "role": "tool",
                    "tool_call_id": r.call_id,
                    "content": r.content,
                }
            )
        return new_history

    def append_user_message(
        self,
        history: list[dict[str, Any]],
        text: str,
    ) -> list[dict[str, Any]]:
        return [*history, {"role": "user", "content": text}]
