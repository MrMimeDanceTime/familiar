# Critical invariant: provider-native history shapes differ

This is the single most important implementation detail in this codebase.
Read this before touching `app/chat/engine.py`, `app/llm/*_provider.py`, or
`tests/test_chat_engine.py`'s `FakeProvider`.

## The problem

`Message.provider_native` stores the exact provider-native message dict(s)
for a turn, so a conversation can be replayed byte-identical back into
whichever provider produced it. **Anthropic and DeepSeek/OpenAI-style
providers do not produce the same shape for a tool-calling turn:**

- **Anthropic** bundles all tool results from one turn into a single
  `user`-role message with multiple `tool_result` content blocks. A turn
  with any number of tool calls always produces exactly 2 new history
  entries: 1 assistant message + 1 bundled user message.
- **DeepSeek (OpenAI-style)** emits one separate `tool`-role message per
  call. A turn with N tool calls produces N+1 new history entries: 1
  assistant message + N tool messages.

## The bug this caused

`run_chat_turn` in `app/chat/engine.py` once hardcoded:

```python
assistant_native, tool_result_native = appended[0], appended[1]
```

assuming `provider.append_tool_results` always returns exactly 2 new
entries. This holds for Anthropic but is false for DeepSeek whenever a turn
makes more than one tool call — `appended[1]` silently discarded every
result past the first when persisting to the database. The corrupted
history then caused a 400 on the *next* turn: `"An assistant message with
'tool_calls' must be followed by tool messages responding to each
'tool_call_id'. (insufficient tool messages following tool_calls message)"`.

Existing tests never caught it because the test fixture
(`FakeProvider.append_tool_results` in `tests/test_chat_engine.py`) also
bundled results into exactly 2 entries — mirroring Anthropic's shape
instead of DeepSeek's, which is the shape actually used in this deployment
(see [PROVIDERS.md](PROVIDERS.md)).

## The fix

```python
assistant_native, tool_result_natives = appended[0], appended[1:]
...
repo.add_message(..., provider_native=[assistant_native])
...
repo.add_message(..., provider_native=tool_result_natives)
```

`appended[1:]` — never assume there's exactly one tool-result entry.

## Guardrails

- `tests/test_chat_engine.py::test_multiple_tool_calls_in_one_turn_all_persisted`
  is a regression test for exactly this bug. Keep it passing.
- `FakeProvider.append_tool_results` deliberately mirrors DeepSeek's
  per-call shape (one tool message per result), not Anthropic's bundled
  shape, *because* the bundled shape happens to mask this bug class. Don't
  "simplify" it back to bundling without re-checking this invariant.
- Any future change to `append_tool_results`, `run_chat_turn`, or the test
  fixture must preserve genericity over the number of tool-result entries
  per turn. If you add a third provider, verify its real shape against a
  live multi-tool-call turn before trusting it — don't assume it matches
  either existing provider.
