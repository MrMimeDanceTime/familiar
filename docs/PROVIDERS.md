# LLM provider

Familiar talks to one LLM backend, DeepSeek, through the `ChatProvider`
Protocol in `backend/app/llm/base.py`. `backend/app/llm/factory.py` builds it
from the `DEEPSEEK_*` settings. `LLM_PROVIDER` exists so a second backend can
be added behind the same seam; today `deepseek` is the only accepted value.

## Models and thinking

`DEEPSEEK_MODEL=deepseek-v4-pro` runs the chat loop: V4 pricing makes the
Flash/Pro gap negligible, so the conversational path gets the better model.
`DEEPSEEK_MODEL_FAST=deepseek-v4-flash` (`get_fast_model()`) is the Flash seam.
The retrieval pipeline routes both its LLM stages to Flash by default, query
planning with thinking off and deck-aware selection with thinking on at a
capped reasoning effort, because Python already owns retrieval and legality
there, so the stages are bounded and Flash keeps them fast (see
[PIPELINE.md](PIPELINE.md)). Pass a `model` override to `send`/`complete_json`
to opt a call back onto Pro.

The old `deepseek-reasoner` ID stopped working after 2026-07-24. In V4,
reasoning is decoupled from the model ID: the v4 IDs default to non-thinking,
so `DeepSeekProvider.send` and `complete_json` take a `thinking` flag and send
`extra_body={"thinking": {"type": "enabled"|"disabled"}}` explicitly, both
ways. Omitting the flag does not disable thinking. The chat loop runs with
thinking off (it is mostly tool dispatch); the pipeline and the power-nuance
call turn it on.

LLM calls have a request timeout (`LLM_TIMEOUT_SECONDS`, default 90s) with
`max_retries=1`, so a stalled call fails visibly instead of hanging on the
SDK's 600s default.

## History shape

`Message.provider_native` stores DeepSeek's exact wire messages so a
conversation replays byte-identical. The OpenAI-style shape emits one `tool`
message per call, and the engine must never assume how many entries a turn
appended; see [PROVIDER_SHAPES.md](PROVIDER_SHAPES.md) for the bug that rule
comes from.

## The Anthropic provider was removed

An `AnthropicProvider` existed until September 2026 and was removed because
nothing exercised it: it had been unable to complete a tool-calling turn for
some time (it persisted SDK objects that could not be serialised, and relied
on assistant prefill that current Claude models reject) and no deployment
was configured to use it. The last commit carrying it is `dc112b3`. Adding a
backend again means implementing `ChatProvider` and verifying its real
multi-tool-call shape against a live turn before trusting it.
