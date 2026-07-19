from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from app.llm.base import AssistantTurn, ToolCallRequest, ToolResult, ToolSpec

DEEPSEEK_BASE_URL = "https://api.deepseek.com"


class DeepSeekProvider:
    def __init__(
        self, api_key: str, model: str, timeout: float | None = None,
        max_tokens: int | None = None,
    ) -> None:
        # Cap the per-request timeout: the SDK default (600s) reads as a total
        # UI freeze when a call stalls. With a timeout the SDK raises instead,
        # and dispatch() turns that into a visible tool error. max_retries=1
        # (down from the SDK default of 2) so a persistent stall doesn't multiply
        # the timeout into minutes of waiting before it surfaces.
        self._client = OpenAI(
            api_key=api_key, base_url=DEEPSEEK_BASE_URL,
            timeout=timeout, max_retries=1,
        )
        self._model = model
        # Bound send() output. Unbounded generation is the measured dominant
        # cause of slow turns (DeepSeek ~40 tok/s, so a 2000-token answer ~50s).
        # None means no cap. Only send() applies it; complete_json (pipeline)
        # sets its own limits.
        self._max_tokens = max_tokens

    def send(
        self,
        system_prompt: str,
        history: list[dict[str, Any]],
        tools: list[ToolSpec],
        *,
        thinking: bool = True,
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

        # Omit `tools` entirely when empty — some OpenAI-compatible backends
        # reject an empty tools array. The engine passes no tools to force a
        # text-only wrap-up turn (e.g. after a proposal batch), and deck naming
        # calls send() toolless too.
        tool_kwargs: dict[str, Any] = {"tools": openai_tools} if openai_tools else {}

        max_kwargs: dict[str, Any] = (
            {"max_tokens": self._max_tokens} if self._max_tokens else {}
        )

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                **max_kwargs,
                **tool_kwargs,
                # V4 decouples reasoning from the model ID: deepseek-v4-flash/pro
                # default to non-thinking. Thinking is slow, so the engine turns it
                # OFF for intermediate tool-dispatch iterations (mechanical "which
                # tool next" decisions) and back ON for the final synthesis turn.
                extra_body={"thinking": {"type": "enabled" if thinking else "disabled"}},
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

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str | None = None,
        thinking: bool = True,
    ) -> str:
        """Single-shot JSON completion for the retrieval pipeline's stage-1/4.

        Returns the raw JSON string (the caller parses/validates). No tools, no
        history threading — deliberately separate from send() so the tool-calling
        hot path stays untouched. ``model`` overrides the provider default per
        call (the Pro/Flash seam); ``thinking`` toggles reasoning (on by default,
        matching send()).
        """
        extra_body: dict[str, Any] = {}
        if thinking:
            extra_body["thinking"] = {"type": "enabled"}

        try:
            response = self._client.chat.completions.create(
                model=model or self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                extra_body=extra_body,
            )
        except Exception as exc:
            raise RuntimeError(
                f"DeepSeek JSON completion failed (provider may have returned "
                f"malformed JSON or timed out). Detail: {exc}"
            ) from exc

        return response.choices[0].message.content or ""

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
