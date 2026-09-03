from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.llm.base import ToolResult, ToolSpec
from app.llm.deepseek_provider import DeepSeekProvider

TOOL = ToolSpec(
    name="get_weather",
    description="Get current weather",
    parameters={"type": "object", "properties": {"city": {"type": "string"}}},
)


def _block(type_, **kwargs):
    return SimpleNamespace(type=type_, **kwargs)


@patch("app.llm.deepseek_provider.OpenAI")
def test_deepseek_send_parses_tool_calls(mock_openai_cls):
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client

    fake_tool_call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="get_weather", arguments='{"city": "Tokyo"}'),
    )
    fake_message = SimpleNamespace(
        content=None,
        tool_calls=[fake_tool_call],
        model_dump=lambda exclude_none=True: {
            "role": "assistant",
            "tool_calls": [{"id": "call_1"}],
        },
    )
    mock_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=fake_message, finish_reason="tool_calls")]
    )

    provider = DeepSeekProvider(api_key="fake", model="deepseek-v4-flash")
    turn = provider.send("system prompt", [{"role": "user", "content": "hi"}], [TOOL])

    assert turn.tool_calls[0].name == "get_weather"
    assert turn.tool_calls[0].arguments == {"city": "Tokyo"}
    assert turn.stop_reason == "tool_calls"

    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["tools"][0]["function"]["name"] == "get_weather"
    assert call_kwargs["messages"][0] == {"role": "system", "content": "system prompt"}
    # V4 IDs default to non-thinking; the provider must opt in explicitly or it
    # silently regresses from the always-thinking deepseek-reasoner it replaced.
    assert call_kwargs["extra_body"] == {"thinking": {"type": "enabled"}}


@patch("app.llm.deepseek_provider.OpenAI")
def test_deepseek_send_can_disable_thinking(mock_openai_cls):
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    fake_message = SimpleNamespace(
        content="ok", tool_calls=None,
        model_dump=lambda exclude_none=True: {"role": "assistant", "content": "ok"},
    )
    mock_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=fake_message, finish_reason="stop")]
    )
    provider = DeepSeekProvider(api_key="fake", model="deepseek-v4-pro")
    provider.send("sys", [{"role": "user", "content": "hi"}], [TOOL], thinking=False)

    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["extra_body"] == {"thinking": {"type": "disabled"}}


@patch("app.llm.deepseek_provider.OpenAI")
def test_deepseek_append_tool_results_shape(mock_openai_cls):
    provider = DeepSeekProvider(api_key="fake", model="deepseek-v4-flash")
    from app.llm.base import AssistantTurn, ToolCallRequest

    turn = AssistantTurn(
        text=None,
        tool_calls=[ToolCallRequest(id="call_1", name="get_weather", arguments={})],
        stop_reason="tool_calls",
        raw_assistant_message={"role": "assistant", "tool_calls": [{"id": "call_1"}]},
    )
    history = provider.append_tool_results(
        [{"role": "user", "content": "hi"}],
        turn,
        [ToolResult(call_id="call_1", content='{"temp": 22}')],
    )

    assert history[-2] == turn.raw_assistant_message
    assert history[-1] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": '{"temp": 22}',
    }


@patch("app.llm.deepseek_provider.OpenAI")
def test_deepseek_complete_json_forces_json_and_honors_model_override(mock_openai_cls):
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    mock_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"queries": []}'))]
    )

    provider = DeepSeekProvider(api_key="fake", model="deepseek-v4-pro")
    out = provider.complete_json("sys", "user", model="deepseek-v4-flash")

    assert out == '{"queries": []}'
    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    # per-call override wins over the provider default
    assert call_kwargs["model"] == "deepseek-v4-flash"
    assert call_kwargs["response_format"] == {"type": "json_object"}
    assert call_kwargs["extra_body"] == {"thinking": {"type": "enabled"}}
    assert call_kwargs["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "user"},
    ]


@patch("app.llm.deepseek_provider.OpenAI")
def test_deepseek_complete_json_defaults_to_provider_model_and_can_disable_thinking(
    mock_openai_cls,
):
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    mock_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))]
    )

    provider = DeepSeekProvider(api_key="fake", model="deepseek-v4-pro")
    provider.complete_json("sys", "user", thinking=False)

    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "deepseek-v4-pro"  # no override -> default
    # The flag must be sent EXPLICITLY disabled, not omitted. Omitting it lets
    # DeepSeek fall back to the model's own default, which for the v4 family is
    # thinking ENABLED — so `thinking=False` silently did nothing. Measured
    # 15.5s with the flag omitted against 1.5s with it explicitly disabled, on
    # identical stage-1 input. This assertion previously pinned `== {}`, which
    # is why the bug survived: it tested the behaviour rather than the intent.
    assert call_kwargs["extra_body"] == {"thinking": {"type": "disabled"}}


@patch("app.llm.deepseek_provider.OpenAI")
def test_deepseek_complete_json_enables_thinking_explicitly(mock_openai_cls):
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    mock_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))]
    )

    provider = DeepSeekProvider(api_key="fake", model="deepseek-v4-pro")
    provider.complete_json("sys", "user", thinking=True)

    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["extra_body"] == {"thinking": {"type": "enabled"}}


@patch("app.llm.deepseek_provider.OpenAI")
def test_deepseek_send_parses_plain_text_no_tools(mock_openai_cls):
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    fake_message = SimpleNamespace(
        content="It's sunny.",
        tool_calls=None,
        model_dump=lambda exclude_none=True: {"role": "assistant", "content": "It's sunny."},
    )
    mock_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=fake_message, finish_reason="stop")]
    )

    provider = DeepSeekProvider(api_key="fake", model="deepseek-v4-flash")
    turn = provider.send("system prompt", [{"role": "user", "content": "hi"}], [TOOL])

    assert turn.text == "It's sunny."
    assert turn.tool_calls == []


@patch("app.llm.deepseek_provider.OpenAI")
def test_deepseek_complete_json_caps_reasoning_effort(mock_openai_cls):
    """Stage-4 latency is dominated by OUTPUT volume, not prompt size: a
    thinking call emits a median 9,476 completion tokens against 489 without,
    and at ~90 tok/s that is the whole wait. `reasoning_effort` is the only
    lever that moves it (`budget_tokens` is silently ignored by the API)."""
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    mock_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))]
    )

    provider = DeepSeekProvider(api_key="fake", model="deepseek-v4-pro")
    provider.complete_json("sys", "user", thinking=True, reasoning_effort="low")

    extra = mock_client.chat.completions.create.call_args.kwargs["extra_body"]
    assert extra["thinking"] == {"type": "enabled"}
    assert extra["reasoning_effort"] == "low"


@patch("app.llm.deepseek_provider.OpenAI")
def test_reasoning_effort_is_not_sent_when_thinking_is_off(mock_openai_cls):
    """Capping reasoning is meaningless when there is no reasoning, and sending
    both flags together is a contradiction the API should never see."""
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    mock_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))]
    )

    provider = DeepSeekProvider(api_key="fake", model="deepseek-v4-pro")
    provider.complete_json("sys", "user", thinking=False, reasoning_effort="low")

    extra = mock_client.chat.completions.create.call_args.kwargs["extra_body"]
    assert extra == {"thinking": {"type": "disabled"}}


# ── Mixed thinking modes in one conversation ─────────────────────────────


def test_thinking_call_backfills_missing_reasoning_content():
    """DeepSeek rejects a thinking-mode request whose history has an assistant
    message without `reasoning_content`.

    Reproduced against the live API: the chat loop dispatches tools with
    thinking OFF, so its assistant messages carry none, and the max-iteration
    wrap-up then called with thinking ON and 400'd — losing the whole turn
    along with any proposals it had already built.
    """
    from app.llm.deepseek_provider import _require_reasoning_content

    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "sure", "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "result"},
    ]

    patched = _require_reasoning_content(history)

    assert patched[1]["reasoning_content"] == ""
    # Non-assistant messages are untouched.
    assert patched[0] == history[0]
    assert patched[2] == history[2]


def test_existing_reasoning_content_is_preserved():
    """A real reasoning trace must survive; only absent ones are backfilled."""
    from app.llm.deepseek_provider import _require_reasoning_content

    history = [
        {"role": "assistant", "content": "x", "reasoning_content": "I thought hard"},
    ]

    assert _require_reasoning_content(history)[0]["reasoning_content"] == "I thought hard"


def test_backfill_does_not_mutate_the_original_history():
    """History is persisted for byte-identical replay, so patching a copy for
    one API call must not rewrite what is stored."""
    from app.llm.deepseek_provider import _require_reasoning_content

    original = [{"role": "assistant", "content": "x"}]
    _require_reasoning_content(original)

    assert "reasoning_content" not in original[0]


@patch("app.llm.deepseek_provider.OpenAI")
def test_send_backfills_only_when_thinking(mock_openai_cls):
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    fake_message = SimpleNamespace(
        content="ok", tool_calls=None,
        model_dump=lambda exclude_none=True: {"role": "assistant", "content": "ok"},
    )
    mock_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=fake_message, finish_reason="stop")]
    )
    provider = DeepSeekProvider(api_key="fake", model="deepseek-v4-pro")
    history = [{"role": "assistant", "content": "earlier"}]

    provider.send("sys", list(history), [], thinking=False)
    sent = mock_client.chat.completions.create.call_args.kwargs["messages"]
    assert "reasoning_content" not in sent[-1]

    provider.send("sys", list(history), [], thinking=True)
    sent = mock_client.chat.completions.create.call_args.kwargs["messages"]
    assert sent[-1]["reasoning_content"] == ""


def _chunk(content=None, tool_calls=None, finish_reason=None, usage=None, reasoning=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls, reasoning_content=reasoning)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    has_choice = content is not None or tool_calls or finish_reason or reasoning is not None
    return SimpleNamespace(choices=[choice] if has_choice else [], usage=usage)


@patch("app.llm.deepseek_provider.OpenAI")
def test_deepseek_send_stream_yields_text_then_the_turn(mock_openai_cls):
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    usage = SimpleNamespace(
        prompt_tokens=120, completion_tokens=8,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=0),
    )
    mock_client.chat.completions.create.return_value = iter([
        _chunk(content="Hel"),
        _chunk(content="lo."),
        _chunk(finish_reason="stop"),
        _chunk(usage=usage),
    ])

    provider = DeepSeekProvider(api_key="fake", model="deepseek-v4-pro")
    items = list(provider.send_stream("sys", [{"role": "user", "content": "hi"}], [TOOL]))

    assert items[:2] == ["Hel", "lo."]
    turn = items[-1]
    assert turn.text == "Hello."
    assert turn.tool_calls == []
    assert turn.raw_assistant_message == {"role": "assistant", "content": "Hello."}
    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["stream"] is True
    assert provider.usage == {
        "llm_calls": 1, "prompt_tokens": 120, "completion_tokens": 8, "reasoning_tokens": 0,
    }


@patch("app.llm.deepseek_provider.OpenAI")
def test_deepseek_send_stream_assembles_tool_calls_from_deltas(mock_openai_cls):
    """Argument JSON arrives in fragments across chunks and must be joined by
    index before parsing; the raw message must match what send() persists."""
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client

    def tc(index, id=None, name=None, arguments=None):
        return SimpleNamespace(
            index=index, id=id, function=SimpleNamespace(name=name, arguments=arguments),
        )

    mock_client.chat.completions.create.return_value = iter([
        _chunk(tool_calls=[tc(0, id="call_1", name="get_weather", arguments='{"ci')]),
        _chunk(tool_calls=[tc(0, arguments='ty": "Oslo"}')]),
        _chunk(tool_calls=[tc(1, id="call_2", name="get_weather", arguments='{"city": "Rome"}')]),
        _chunk(finish_reason="tool_calls"),
    ])

    provider = DeepSeekProvider(api_key="fake", model="deepseek-v4-pro")
    items = list(provider.send_stream("sys", [{"role": "user", "content": "hi"}], [TOOL]))

    assert all(not isinstance(i, str) for i in items)
    turn = items[-1]
    assert [c.id for c in turn.tool_calls] == ["call_1", "call_2"]
    assert turn.tool_calls[0].arguments == {"city": "Oslo"}
    assert turn.stop_reason == "tool_calls"
    assert turn.raw_assistant_message["tool_calls"][0] == {
        "id": "call_1", "type": "function",
        "function": {"name": "get_weather", "arguments": '{"city": "Oslo"}'},
    }
    assert "content" not in turn.raw_assistant_message


@patch("app.llm.deepseek_provider.OpenAI")
def test_deepseek_usage_accumulates_across_send_and_complete_json(mock_openai_cls):
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, completion_tokens_details=None)
    mock_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content="{}", tool_calls=None,
                                    model_dump=lambda exclude_none=True: {"role": "assistant", "content": "{}"}),
            finish_reason="stop",
        )],
        usage=usage,
    )
    provider = DeepSeekProvider(api_key="fake", model="deepseek-v4-pro")
    provider.send("sys", [{"role": "user", "content": "hi"}], [], thinking=False)
    provider.complete_json("sys", "user")

    assert provider.usage == {
        "llm_calls": 2, "prompt_tokens": 20, "completion_tokens": 10, "reasoning_tokens": 0,
    }


@patch("app.llm.deepseek_provider.OpenAI")
def test_chat_reasoning_effort_rides_on_thinking_sends_only(mock_openai_cls):
    provider = DeepSeekProvider(api_key="k", model="deepseek-v4-pro", reasoning_effort="low")
    client = mock_openai_cls.return_value
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(
                content="ok", tool_calls=None,
                model_dump=lambda exclude_none=True: {"role": "assistant", "content": "ok"},
            ),
            finish_reason="stop",
        )]
    )
    provider.send("sys", [{"role": "user", "content": "hi"}], [], thinking=True)
    assert client.chat.completions.create.call_args.kwargs["extra_body"]["reasoning_effort"] == "low"
    provider.send("sys", [{"role": "user", "content": "hi"}], [], thinking=False)
    assert "reasoning_effort" not in client.chat.completions.create.call_args.kwargs["extra_body"]


@patch("app.llm.deepseek_provider.OpenAI")
def test_deepseek_stream_recorded_thinking_tool_call_shape(mock_openai_cls):
    """The chunk sequence a thinking-mode tool call actually produces: a
    keep-alive chunk with no choices, reasoning deltas, a burst of text, two
    tool calls whose arguments interleave across chunks, then the finish and
    a usage-only chunk. Reasoning is kept on the raw message and never
    yielded as text."""
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client

    def tc(index, id=None, name=None, arguments=None):
        return SimpleNamespace(
            index=index, id=id, function=SimpleNamespace(name=name, arguments=arguments),
        )

    usage = SimpleNamespace(
        prompt_tokens=900, completion_tokens=140,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=90),
    )
    mock_client.chat.completions.create.return_value = iter([
        SimpleNamespace(choices=[], usage=None),
        _chunk(reasoning="The deck is short on "),
        _chunk(reasoning="ramp; check the plan first."),
        _chunk(content="Let me "),
        _chunk(content="look."),
        _chunk(tool_calls=[tc(0, id="call_a", name="deck_get", arguments="")]),
        _chunk(tool_calls=[tc(0, name="_current", arguments='{"deck_')]),
        _chunk(tool_calls=[tc(1, id="call_b", name="search_card_index", arguments='{"query": "ma')]),
        _chunk(tool_calls=[tc(0, arguments='id": 1}')]),
        _chunk(tool_calls=[tc(1, arguments='na dork"}')]),
        _chunk(finish_reason="tool_calls"),
        _chunk(usage=usage),
    ])

    provider = DeepSeekProvider(api_key="fake", model="deepseek-v4-pro")
    items = list(provider.send_stream(
        "sys", [{"role": "user", "content": "hi"}], [TOOL], thinking=True,
    ))

    assert [i for i in items if isinstance(i, str)] == ["Let me ", "look."]
    turn = items[-1]
    assert turn.text == "Let me look."
    assert [(c.id, c.name, c.arguments) for c in turn.tool_calls] == [
        ("call_a", "deck_get_current", {"deck_id": 1}),
        ("call_b", "search_card_index", {"query": "mana dork"}),
    ]
    assert turn.raw_assistant_message["reasoning_content"] == (
        "The deck is short on ramp; check the plan first."
    )
    assert turn.raw_assistant_message["content"] == "Let me look."
    assert turn.stop_reason == "tool_calls"
    assert provider.usage["reasoning_tokens"] == 90


@patch("app.llm.deepseek_provider.OpenAI")
def test_bare_assistant_messages_are_repaired_before_sending(mock_openai_cls):
    """One assistant message with neither content nor tool_calls fails every
    later call in a conversation; the provider repairs it at send time."""
    provider = DeepSeekProvider(api_key="k", model="deepseek-v4-pro")
    client = mock_openai_cls.return_value
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(
                content="ok", tool_calls=None,
                model_dump=lambda exclude_none=True: {"role": "assistant", "content": "ok"},
            ),
            finish_reason="stop",
        )]
    )
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant"},
        {"role": "user", "content": "again"},
    ]
    provider.send("sys", history, [], thinking=False)
    sent = client.chat.completions.create.call_args.kwargs["messages"]
    assert sent[2] == {"role": "assistant", "content": "[empty reply]"}
    assert history[1] == {"role": "assistant"}  # the record is untouched


@patch("app.llm.deepseek_provider.OpenAI")
def test_thinking_sends_get_their_own_output_budget(mock_openai_cls):
    provider = DeepSeekProvider(
        api_key="k", model="deepseek-v4-pro", max_tokens=1000, thinking_max_tokens=8000,
    )
    client = mock_openai_cls.return_value
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(
                content="ok", tool_calls=None,
                model_dump=lambda exclude_none=True: {"role": "assistant", "content": "ok"},
            ),
            finish_reason="stop",
        )]
    )
    provider.send("sys", [{"role": "user", "content": "hi"}], [], thinking=True)
    assert client.chat.completions.create.call_args.kwargs["max_tokens"] == 8000
    provider.send("sys", [{"role": "user", "content": "hi"}], [], thinking=False)
    assert client.chat.completions.create.call_args.kwargs["max_tokens"] == 1000
