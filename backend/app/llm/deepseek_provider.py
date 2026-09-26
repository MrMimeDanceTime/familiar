from __future__ import annotations

import json
import logging
import time
from typing import Any, Iterator

from openai import OpenAI

from app.llm.base import AssistantTurn, ToolCallRequest, ToolResult, ToolSpec

DEEPSEEK_BASE_URL = "https://api.deepseek.com"

logger = logging.getLogger("app.llm.deepseek")
# One record per streamed send: its setup, token counts, and when the first
# byte, reasoning token, text token, and tool call arrived.
timing_log = logging.getLogger("app.llm.timing")


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
        # DeepSeek's context cache: tokens of the prompt it did not have to
        # process again. A prompt whose prefix changes every send gets none.
        "cache_hit_tokens": int(getattr(usage, "prompt_cache_hit_tokens", 0) or 0),
    }


def _parse_arguments(raw: str) -> dict[str, Any]:
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {"_parse_error": True, "_raw": raw}


EMPTY_REPLY_CONTENT = "[empty reply]"


def _repair_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give every assistant message content or tool calls.

    DeepSeek rejects a request whose history holds an assistant message with
    neither ("Invalid assistant message: content or tool_calls must be set"),
    and one such message, once persisted, fails every later call in the
    conversation. The engine no longer persists them, but a transcript that
    already carries one has to keep working.
    """
    repaired: list[dict[str, Any]] = []
    for message in messages:
        if (
            message.get("role") == "assistant"
            and not message.get("content")
            and not message.get("tool_calls")
        ):
            message = {**message, "content": EMPTY_REPLY_CONTENT}
        repaired.append(message)
    return repaired


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
        max_tokens: int | None = None, reasoning_effort: str | None = None,
        thinking_max_tokens: int | None = None,
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
        # Thinking sends need room for the reasoning as well as the reply;
        # see Settings.chat_thinking_max_tokens. Falls back to max_tokens.
        self._thinking_max_tokens = thinking_max_tokens or max_tokens
        # Applied to send()/send_stream() calls that think. complete_json takes
        # its own per-call value because the pipeline stages differ.
        self._reasoning_effort = reasoning_effort or None
        # Tokens spent through this instance. The turn runner builds one
        # provider per turn, so these totals are the turn's cost, including the
        # pipeline and nuance calls made from inside tools.
        self.usage: dict[str, int] = {
            "llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "reasoning_tokens": 0, "cache_hit_tokens": 0,
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
        model: str | None = None,
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

        cap = self._thinking_max_tokens if thinking else self._max_tokens
        max_kwargs: dict[str, Any] = {"max_tokens": cap} if cap else {}

        messages = _repair_history(messages)

        if thinking:
            messages = _require_reasoning_content(messages)

        extra_body: dict[str, Any] = {"thinking": {"type": "enabled" if thinking else "disabled"}}
        if thinking and self._reasoning_effort:
            extra_body["reasoning_effort"] = self._reasoning_effort

        return {
            "model": model or self._model,
            "messages": messages,
            **max_kwargs,
            **tool_kwargs,
            # V4 decouples reasoning from the model ID: deepseek-v4-flash/pro
            # default to non-thinking, so the flag is always sent explicitly.
            "extra_body": extra_body,
        }

    def send_stream(
        self,
        system_prompt: str,
        history: list[dict[str, Any]],
        tools: list[ToolSpec],
        *,
        thinking: bool = False,
        model: str | None = None,
    ) -> Iterator[str | AssistantTurn]:
        """Like send(), but yields text as it arrives and the AssistantTurn last.

        The final assistant reply used to land as one block after the whole
        generation finished — around 25 seconds of silence on a 1000-token
        answer. Tool-call deltas are accumulated by index and assembled into
        the same raw message shape send() persists, so replay is unchanged.
        """
        kwargs = self._request_kwargs(system_prompt, history, tools, thinking, model)
        started = time.perf_counter()
        marks: dict[str, float] = {}
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
        # Thinking-mode streams carry the reasoning as its own delta field.
        # It is never shown, but it is kept on the raw message: DeepSeek
        # wants the reasoning of a tool-calling assistant message passed back
        # with the tool results, and the streaming path used to drop it and
        # rely on the empty-string backfill.
        reasoning_parts: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        usage: Any = None
        try:
            for chunk in stream:
                marks.setdefault("first_byte", time.perf_counter() - started)
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
                if getattr(delta, "reasoning_content", None):
                    marks.setdefault("first_reasoning", time.perf_counter() - started)
                    reasoning_parts.append(delta.reasoning_content)
                if getattr(delta, "content", None):
                    marks.setdefault("first_text", time.perf_counter() - started)
                    text_parts.append(delta.content)
                    yield delta.content
                for tc in getattr(delta, "tool_calls", None) or []:
                    marks.setdefault("first_tool_call", time.perf_counter() - started)
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
        timing_log.info("send", extra={"send_timing": {
            "model": kwargs.get("model"), "thinking": thinking,
            "effort": (kwargs.get("extra_body") or {}).get("reasoning_effort"),
            "max_tokens": kwargs.get("max_tokens"),
            "system_chars": len(system_prompt), "history_messages": len(history),
            "tools": len(tools), "finish": finish_reason,
            "seconds": round(time.perf_counter() - started, 2),
            **{k: round(v, 2) for k, v in marks.items()},
            **_usage_dict(usage),
        }})

        text = "".join(text_parts)
        ordered = [calls[i] for i in sorted(calls)]
        if not text and not ordered:
            logger.warning(
                "DeepSeek stream ended with no text and no tool calls (finish_reason=%s, "
                "thinking=%s, reasoning chars=%d)", finish_reason, thinking,
                sum(len(r) for r in reasoning_parts),
            )
        tool_calls = [
            ToolCallRequest(
                id=c["id"], name=c["name"], arguments=_parse_arguments(c["arguments"]),
            )
            for c in ordered
        ]
        raw: dict[str, Any] = {"role": "assistant"}
        if reasoning_parts:
            raw["reasoning_content"] = "".join(reasoning_parts)
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
        model: str | None = None,
    ) -> AssistantTurn:
        kwargs = self._request_kwargs(system_prompt, history, tools, thinking, model)
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
            started = time.perf_counter()
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
        timing_log.info("json", extra={"send_timing": {
            "model": model or self._model, "thinking": thinking, "kind": "json",
            "effort": extra_body.get("reasoning_effort"), "system_chars": len(system_prompt),
            "seconds": round(time.perf_counter() - started, 2),
            **_usage_dict(getattr(response, "usage", None)),
        }})
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
