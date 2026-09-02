from __future__ import annotations

import json
import re
from typing import Any

import anthropic

from app.llm.base import AssistantTurn, ToolCallRequest, ToolResult, ToolSpec

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def _extract_json_object(text: str) -> str:
    """Pull the JSON object out of a reply that may have decoration around it.

    Assistant prefill (seeding the reply with ``{``) was how this used to force
    JSON, and it returns a 400 on every current Claude model. Without it the
    model can wrap the object in a code fence or a lead-in sentence, so this
    strips fences and, failing a clean parse, takes the outermost ``{...}``
    span. The caller still parses and validates; this only removes wrapping.
    """
    stripped = _FENCE_RE.sub("", text).strip()
    try:
        json.loads(stripped)
        return stripped
    except json.JSONDecodeError:
        pass
    start, end = stripped.find("{"), stripped.rfind("}")
    if start != -1 and end > start:
        return stripped[start : end + 1]
    return stripped


def _plain_blocks(content: Any) -> list[dict[str, Any]]:
    """Turn the SDK's typed content blocks into plain dicts.

    The raw assistant message is persisted as JSON so a conversation can be
    replayed into the same provider byte-identical. The SDK returns pydantic
    objects, and ``json.dumps`` cannot serialise those — every tool-calling turn
    on this provider failed at persist time. Dumped with ``exclude_none`` the
    dicts are exactly the input shape the API accepts back.
    """
    blocks: list[dict[str, Any]] = []
    for block in content:
        if isinstance(block, dict):
            blocks.append(block)
        elif hasattr(block, "model_dump"):
            blocks.append(block.model_dump(exclude_none=True))
        else:
            blocks.append(dict(vars(block)))
    return blocks


class AnthropicProvider:
    def __init__(
        self, api_key: str, model: str, timeout: float | None = None,
        max_tokens: int | None = None,
    ) -> None:
        # Cap the per-request timeout so a stalled call fails fast instead of
        # hanging the chat loop (see DeepSeekProvider for the rationale).
        client_kwargs: dict[str, Any] = {"api_key": api_key}
        if timeout is not None:
            client_kwargs["timeout"] = timeout
        self._client = anthropic.Anthropic(**client_kwargs)
        self._model = model
        # Bound send() output for parity with DeepSeek (concise, snappy final
        # turns). Anthropic requires a max_tokens; fall back to 4096 when unset.
        self._max_tokens = max_tokens or 4096
        # The last non-empty tool list this provider was sent. The engine forces
        # a text-only reply by passing no tools, but the API rejects a request
        # whose history carries tool_use/tool_result blocks and defines no
        # tools — so a toolless send re-declares the tools it last saw and
        # forbids their use with tool_choice instead.
        self._last_tools: list[dict[str, Any]] = []

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

        tool_kwargs: dict[str, Any] = {}
        if anthropic_tools:
            self._last_tools = anthropic_tools
            tool_kwargs["tools"] = anthropic_tools
        elif self._last_tools and _history_has_tool_blocks(history):
            tool_kwargs["tools"] = self._last_tools
            tool_kwargs["tool_choice"] = {"type": "none"}

        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system_prompt,
            messages=history,
            **tool_kwargs,
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
            raw_assistant_message={
                "role": "assistant",
                "content": _plain_blocks(response.content),
            },
        )

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str | None = None,
        thinking: bool = True,
        reasoning_effort: str | None = None,
    ) -> str:
        """Single-shot JSON completion for the retrieval pipeline's stage-1/4.

        Anthropic has no response_format=json_object, and assistant prefill is
        no longer accepted, so the format is asked for in the system prompt and
        any wrapping is stripped from the reply (see ``_extract_json_object``).
        ``thinking`` and ``reasoning_effort`` are accepted for a provider-neutral
        signature but ignored. ``model`` overrides the provider default per call.
        """
        response = self._client.messages.create(
            model=model or self._model,
            max_tokens=4096,
            system=(
                f"{system_prompt}\n\nReply with a single JSON object and nothing "
                "else: no prose before or after it, no code fence."
            ),
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        return _extract_json_object(text)

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


def _history_has_tool_blocks(history: list[dict[str, Any]]) -> bool:
    for message in history:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            block_type = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            if block_type in ("tool_use", "tool_result"):
                return True
    return False
