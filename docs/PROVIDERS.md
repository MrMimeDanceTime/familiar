# LLM providers

Familiar supports two LLM providers behind the `ChatProvider` Protocol in
`backend/app/llm/base.py`: Anthropic (Claude) and DeepSeek. Selected via the
`LLM_PROVIDER` env var (`anthropic` | `deepseek`), read by
`backend/app/llm/factory.py`.

## DeepSeek is the default and the actively-used provider

`LLM_PROVIDER=deepseek` and `DEEPSEEK_MODEL=deepseek-v4-pro` are the
defaults in both `.env` and `.env.example`, and `Settings.llm_provider`
defaults to `"deepseek"` in `backend/app/config.py`. The chat loop runs on
`DEEPSEEK_MODEL` (Pro): V4 pricing makes the Flash/Pro gap negligible, so we
pay for the better model on the conversational path.
`DEEPSEEK_MODEL_FAST=deepseek-v4-flash` (`get_fast_model()`) is the Flash
seam. The retrieval pipeline routes both its LLM stages to Flash by default —
query planning with thinking off, deck-aware selection with thinking on —
because Python already owns retrieval/legality there, so the stages are
bounded and Flash keeps them fast (see [PIPELINE.md](PIPELINE.md)). Pass a
`model` override to `send`/`complete_json` to opt a call back onto Pro.

The old `deepseek-reasoner` ID stopped working after 2026-07-24. In V4,
reasoning is decoupled from the model ID — the v4 IDs default to
non-thinking, so `DeepSeekProvider.send` and `complete_json` take a
`thinking` flag and pass `extra_body={"thinking": {"type": "enabled"}}` when
it's set, preserving the always-on reasoning that `deepseek-reasoner` used to
give. The chat loop and pipeline pick the flag per call.

LLM API calls have a request timeout (`LLM_TIMEOUT_SECONDS`, default 90s,
applied to both providers via the factory) with `max_retries=1`, so a stalled
provider call fails visibly instead of hanging on the SDK's 600s default.

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
