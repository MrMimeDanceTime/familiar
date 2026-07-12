from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import ToolResult, ToolSpec
from app.llm.deepseek_provider import DeepSeekProvider

TOOL = ToolSpec(
    name="get_weather",
    description="Get current weather",
    parameters={"type": "object", "properties": {"city": {"type": "string"}}},
)


def _block(type_, **kwargs):
    return SimpleNamespace(type=type_, **kwargs)


@patch("app.llm.anthropic_provider.anthropic.Anthropic")
def test_anthropic_send_parses_tool_use(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.return_value = SimpleNamespace(
        content=[
            _block("text", text="Let me check."),
            _block("tool_use", id="call_1", name="get_weather", input={"city": "Tokyo"}),
        ],
        stop_reason="tool_use",
    )

    provider = AnthropicProvider(api_key="fake", model="claude-sonnet-4-6")
    turn = provider.send("system prompt", [{"role": "user", "content": "hi"}], [TOOL])

    assert turn.text == "Let me check."
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].name == "get_weather"
    assert turn.tool_calls[0].arguments == {"city": "Tokyo"}
    assert turn.stop_reason == "tool_use"

    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert call_kwargs["tools"][0]["name"] == "get_weather"
    assert call_kwargs["tools"][0]["input_schema"] == TOOL.parameters


@patch("app.llm.anthropic_provider.anthropic.Anthropic")
def test_anthropic_append_tool_results_shape(mock_anthropic_cls):
    provider = AnthropicProvider(api_key="fake", model="claude-sonnet-4-6")
    from app.llm.base import AssistantTurn, ToolCallRequest

    turn = AssistantTurn(
        text=None,
        tool_calls=[ToolCallRequest(id="call_1", name="get_weather", arguments={})],
        stop_reason="tool_use",
        raw_assistant_message={"role": "assistant", "content": [{"type": "tool_use"}]},
    )
    history = provider.append_tool_results(
        [{"role": "user", "content": "hi"}],
        turn,
        [ToolResult(call_id="call_1", content='{"temp": 22}')],
    )

    assert history[-2] == turn.raw_assistant_message
    assert history[-1]["role"] == "user"
    assert history[-1]["content"][0]["type"] == "tool_result"
    assert history[-1]["content"][0]["tool_use_id"] == "call_1"


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
