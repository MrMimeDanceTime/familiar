# LLM providers

Familiar supports two LLM providers behind the `ChatProvider` Protocol in
`backend/app/llm/base.py`: Anthropic (Claude) and DeepSeek. Selected via the
`LLM_PROVIDER` env var (`anthropic` | `deepseek`), read by
`backend/app/llm/factory.py`.

## DeepSeek is the default and the actively-used provider

`LLM_PROVIDER=deepseek` and `DEEPSEEK_MODEL=deepseek-reasoner` are the
defaults in both `.env` and `.env.example`, and `Settings.llm_provider`
defaults to `"deepseek"` in `backend/app/config.py`.

This deployment's owner pays for DeepSeek API usage directly and does not
have a paid Anthropic API key configured (they use Claude via a separate
Claude Code subscription for coding work, which is unrelated to this app's
runtime LLM calls). **Don't assume an Anthropic API key is configured or
working** — when verifying chat-engine behavior, test against DeepSeek
first, since that's the path actually exercised in this deployment.

## Provider-neutral abstraction is still a real goal

The `AnthropicProvider` code path exists and should stay functional — the
abstraction in `llm/base.py` (`AssistantTurn`, `ToolCallRequest`,
`ToolResult`, `ToolSpec`) is a genuine design goal, not vestigial. Don't add
features that only work with one provider's request/response shape without
checking the other provider's code path too.

The two providers' tool-calling message shapes genuinely differ at the
wire level — see [PROVIDER_SHAPES.md](PROVIDER_SHAPES.md) for the specific
invariant this creates and a real bug it caused.

Switching providers mid-conversation is an explicit out-of-scope edge case;
`provider_native` history is provider-specific and isn't translated between
providers.
