from __future__ import annotations

import json
from typing import Any, Iterator

from openai import OpenAI

from app.llm.base import AssistantTurn, ToolCallRequest, ToolResult, ToolSpec

DEEPSEEK_BASE_URL = "https://api.deepseek.com"


def _usage_dict(usage: Any) -> dict[str, int]:
    """Flatten the SDK's usage object. DeepSeek reports reasoning tokens under
    completion_tokens_details; absent fields count as zero."""
    if usage is None:
        return {}
    details = getattr(usage, "completion_tokens_details", None)
    return {
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "reasoning_tokens": int(getattr(details, "reasoning_tokens", 0) or 0),
    }


def _parse_arguments(raw: str) -> dict[str, Any]:
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {"_parse_error": True, "_raw": raw}


def _require_reasoning_content(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Give every assistant message a `reasoning_content` before a thinking call.

    DeepSeek rejects a thinking-mode request whose history contains an assistant
    message without it:

        400 - The `reasoning_content` in the thinking mode must be passed back
              to the API.

    That happens whenever thinking modes are MIXED within one conversation — the
    chat loop dispatches tools with thinking off, so its assistant messages carry
    none, and any later thinking call replaying that history fails. The turn is
    lost along with any proposals it had already built.

    Backfills an empty string rather than dropping the messages: the history has
    to stay byte-identical for replay, and an empty reasoning trace is truthful
    for a message that genuinely did not reason.
    """
    patched: list[dict[str, Any]] = []
    for message in messages:
        if (
            message.get("role") == "assistant"
            and "reasoning_content" not in message
        ):
            message = {**message, "reasoning_content": ""}
        patched.append(message)
    return patched


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
        # Tokens spent through this instance. The turn runner builds one
        # provider per turn, so these totals are the turn's cost, including the
        # pipeline and nuance calls made from inside tools.
        self.usage: dict[str, int] = {
            "llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "reasoning_tokens": 0,
        }

    def _record_usage(self, usage: Any) -> None:
        self.usage["llm_calls"] += 1
        for key, value in _usage_dict(usage).items():
            self.usage[key] += value

    def _request_kwargs(
        self,
        system_prompt: str,
        history: list[dict[str, Any]],
        tools: list[ToolSpec],
        thinking: bool,
    ) -> dict[str, Any]:
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

        if thinking:
            messages = _require_reasoning_content(messages)

        return {
            "model": self._model,
            "messages": messages,
            **max_kwargs,
            **tool_kwargs,
            # V4 decouples reasoning from the model ID: deepseek-v4-flash/pro
            # default to non-thinking. Thinking is slow, so the engine turns it
            # OFF for intermediate tool-dispatch iterations (mechanical "which
            # tool next" decisions) and back ON for the final synthesis turn.
            "extra_body": {"thinking": {"type": "enabled" if thinking else "disabled"}},
        }

    def send_stream(
        self,
        system_prompt: str,
        history: list[dict[str, Any]],
        tools: list[ToolSpec],
        *,
        thinking: bool = False,
    ) -> Iterator[str | AssistantTurn]:
        """Like send(), but yields text as it arrives and the AssistantTurn last.

        The final assistant reply used to land as one block after the whole
        generation finished — around 25 seconds of silence on a 1000-token
        answer. Tool-call deltas are accumulated by index and assembled into
        the same raw message shape send() persists, so replay is unchanged.
        """
        kwargs = self._request_kwargs(system_prompt, history, tools, thinking)
        try:
            stream = self._client.chat.completions.create(
                **kwargs, stream=True, stream_options={"include_usage": True},
            )
        except Exception as exc:
            raise RuntimeError(
                f"DeepSeek API call failed (provider may have returned "
                f"malformed JSON or timed out). The engine will retry on "
                f"the next user message. Detail: {exc}"
            ) from exc

        text_parts: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        usage: Any = None
        try:
            for chunk in stream:
                if getattr(chunk, "usage", None) is not None:
                    usage = chunk.usage
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                choice = choices[0]
                delta = getattr(choice, "delta", None)
                if getattr(choice, "finish_reason", None):
                    finish_reason = choice.finish_reason
                if delta is None:
                    continue
                if getattr(delta, "content", None):
                    text_parts.append(delta.content)
                    yield delta.content
                for tc in getattr(delta, "tool_calls", None) or []:
                    index = getattr(tc, "index", 0) or 0
                    entry = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                    if getattr(tc, "id", None):
                        entry["id"] = tc.id
                    function = getattr(tc, "function", None)
                    if function is not None:
                        if getattr(function, "name", None):
                            entry["name"] += function.name
                        if getattr(function, "arguments", None):
                            entry["arguments"] += function.arguments
        except Exception as exc:
            raise RuntimeError(
                f"DeepSeek stream failed mid-reply. Detail: {exc}"
            ) from exc
        self._record_usage(usage)

        text = "".join(text_parts)
        ordered = [calls[i] for i in sorted(calls)]
        tool_calls = [
            ToolCallRequest(
                id=c["id"], name=c["name"], arguments=_parse_arguments(c["arguments"]),
            )
            for c in ordered
        ]
        raw: dict[str, Any] = {"role": "assistant"}
        if text:
            raw["content"] = text
        if ordered:
            raw["tool_calls"] = [
                {
                    "id": c["id"],
                    "type": "function",
                    "function": {"name": c["name"], "arguments": c["arguments"]},
                }
                for c in ordered
            ]
        yield AssistantTurn(
            text=text or None,
            tool_calls=tool_calls,
            stop_reason=finish_reason or ("tool_calls" if tool_calls else "stop"),
            raw_assistant_message=raw,
        )

    def send(
        self,
        system_prompt: str,
        history: list[dict[str, Any]],
        tools: list[ToolSpec],
        *,
        thinking: bool = True,
    ) -> AssistantTurn:
        kwargs = self._request_kwargs(system_prompt, history, tools, thinking)
        try:
            response = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise RuntimeError(
                f"DeepSeek API call failed (provider may have returned "
                f"malformed JSON or timed out). The engine will retry on "
                f"the next user message. Detail: {exc}"
            ) from exc

        self._record_usage(getattr(response, "usage", None))
        message = response.choices[0].message
        tool_calls: list[ToolCallRequest] = []
        for tc in message.tool_calls or []:
            args = _parse_arguments(tc.function.arguments)
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
        reasoning_effort: str | None = None,
    ) -> str:
        """Single-shot JSON completion for the retrieval pipeline's stage-1/4.

        Returns the raw JSON string (the caller parses/validates). No tools, no
        history threading — deliberately separate from send() so the tool-calling
        hot path stays untouched. ``model`` overrides the provider default per
        call (the Pro/Flash seam); ``thinking`` toggles reasoning (on by default,
        matching send()).
        """
        # Always send the flag explicitly, both ways. Omitting it when thinking
        # is False does NOT disable reasoning — DeepSeek falls back to the
        # model's own default, which for the v4 family is thinking ENABLED. That
        # made `thinking=False` a no-op on this path: measured 15.5s for a
        # stage-1 call with the flag omitted against 1.5s with it explicitly
        # disabled, for identical output. send() has always done this correctly.
        extra_body: dict[str, Any] = {
            "thinking": {"type": "enabled" if thinking else "disabled"}
        }

        # Cap how long it reasons. Latency here is dominated by OUTPUT volume,
        # not prompt size: measured over repeated samples, a thinking selection
        # call emits a median 9,476 completion tokens against 489 without, and
        # at ~90 tok/s that IS the whole 93s. Shrinking the prompt does nothing
        # (halving it measured slightly slower).
        #
        # `reasoning_effort` is honoured; `budget_tokens` is silently ignored.
        # On the same pool: default 93.2s median, medium 73.7s, low 40.8s — and
        # low returned the same theme-aware picks including both changelings
        # that trigger the commander twice. Off by default so behaviour is
        # unchanged unless configured.
        if thinking and reasoning_effort:
            extra_body["reasoning_effort"] = reasoning_effort

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

        self._record_usage(getattr(response, "usage", None))
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
