# Familiar

Personal, local-only AI assistant for Magic: The Gathering Commander/EDH
deckbuilding. A conversational brainstorming partner grounded in real
Scryfall/EDHREC data — explicitly NOT a "generate a meta decklist" tool. If
the user proposes an off-meta commander idea, the assistant should help
develop that idea, not steer it toward the most popular EDHREC list.

Single user, runs locally, no auth, no deployment target. Optimize for
correctness and a good conversational feel over scalability.

## Stack

- Backend: FastAPI + SQLModel (SQLite) + a hand-rolled agentic tool-calling
  loop (no LangChain/LangGraph). Python ≥3.10.
- Frontend: React + TypeScript + Vite, plain CSS (no component library).
- LLM: provider-neutral abstraction supporting Anthropic (Claude) and
  DeepSeek. **DeepSeek-reasoner is the default/primary provider** — the user
  pays for DeepSeek directly and uses a Claude Code subscription (not the
  Anthropic API) for coding, so don't assume Anthropic API access works or
  is configured. Don't add features that only work with one provider
  without checking both code paths.
- Data sources: Scryfall REST API (self-throttled httpx client) and EDHREC's
  unofficial JSON endpoint (disk-cached, defensively parsed since it's
  undocumented).

## Running locally

```
.\run.ps1
```

Builds the frontend (if `frontend/src` changed since the last build) and
starts the backend, which serves both the API and the built frontend on one
port (default `:8420`). First run bootstraps a venv and `.env` from
`.env.example` and exits so the user can fill in API keys.

For frontend hot-reload while actively editing UI, run two processes
instead (see README.md "Frontend development" section): backend via
`uvicorn app.main:app --reload --port 8420` from `backend/`, frontend via
`npm run dev` from `frontend/` (Vite on `:5173`, proxies `/api` to `:8420`).

## Tests

```
cd backend && .venv\Scripts\python.exe -m pytest -q
```

69 tests as of the last feature add. Always run the full suite after engine
or provider changes — `app/chat/engine.py` and the provider shape contract
are the highest-risk area in this codebase (see below).

Frontend has no test suite yet; verify with `npx tsc --noEmit` and
`npm run build` from `frontend/`, and a live smoke test in-browser for any
UI behavior change (not just type-checking).

## Architecture

```
backend/app/
  main.py            FastAPI app; mounts built frontend/dist/ as static files if present
  config.py          pydantic-settings, reads .env from repo root
  llm/
    base.py           ChatProvider Protocol + neutral types (AssistantTurn, ToolCallRequest, ToolResult, ToolSpec)
    anthropic_provider.py / deepseek_provider.py
    factory.py        get_provider(name)
  tools/
    scryfall_client.py / edhrec_client.py   external data clients
    deck_tools.py      deck_* tool implementations
    schemas.py         JSON-Schema ToolSpec definitions (provider-neutral; tune wording here to fix model misuse)
    dispatch.py         name -> callable registry, catches exceptions into error ToolResults
  db/
    models.py / session.py / repository.py
  chat/
    engine.py           THE agentic loop — see "Critical invariant" below
    prompt.py            system prompt (behavior contract)
    streaming.py          SSE event formatting
  api/
    chat.py / conversations.py / decks.py

frontend/src/
  App.tsx, api/client.ts, api/sse.ts
  components/  ChatView, MessageBubble, ToolActivityIndicator, DeckPanel, DeckCardRow, ConversationSidebar
  hooks/       useChatStream (SSE lifecycle), useDeck
  types/api.ts
```

## Critical invariant: provider-native history shapes differ

`Message.provider_native` stores the exact provider-native message dict(s)
for a turn, so a conversation can be replayed byte-identical back into
whichever provider produced it. **Anthropic and DeepSeek/OpenAI-style
providers do not produce the same shape for a tool-calling turn**:

- Anthropic bundles all tool results from one turn into a single `user`
  message with multiple `tool_result` content blocks → exactly 2 new
  history entries per turn (1 assistant + 1 bundled user message),
  regardless of how many tools were called.
- DeepSeek (OpenAI-style) emits one separate `tool`-role message per call →
  N+1 new entries for N tool calls in one turn.

`run_chat_turn` in `engine.py` must persist `provider.append_tool_results`'s
output generically (`appended[0]` is the assistant entry, `appended[1:]` is
*all* tool-result entries — never assume there's exactly one). A prior bug
hardcoded `appended[1]` as a single tool-result entry, which silently
dropped every result past the first whenever a turn made >1 tool call,
corrupting persisted history and causing a 400 on the next turn ("insufficient
tool messages following tool_calls message"). The regression test
`test_multiple_tool_calls_in_one_turn_all_persisted` in
`tests/test_chat_engine.py` guards this — keep it passing, and if you touch
`append_tool_results` or `run_chat_turn`, re-verify both providers' real
shapes, not just one.

The test suite's `FakeProvider.append_tool_results` mirrors DeepSeek's
per-call shape (not Anthropic's bundled shape) specifically because the
bundled shape happens to mask this class of bug — don't "simplify" it back
to bundling without re-checking.

## Other behavioral notes

- `MAX_TOOL_ITERATIONS = 8` in `engine.py` is a hard safety valve, not a
  target — if the model is burning all 8 iterations without replying, that's
  a prompt/tool-description problem (see `chat/prompt.py`'s "RESEARCH
  PACING" section and `tools/schemas.py`'s `scryfall_search` description),
  not something to fix by raising the limit.
- Scryfall query syntax has no other source of truth for the model besides
  the tool's JSON-Schema `description` field — if the model misuses query
  syntax (e.g. `commander legal` instead of `legal:commander`, or adds
  `game:paper` unprompted), fix it by tightening the description in
  `tools/schemas.py`, not by post-processing queries in code.
- Old conversations created before a `provider_native` persistence bug was
  fixed may have permanently corrupted history (missing tool-result
  entries) and will 400 if continued. This is a known, accepted gap — no
  migration/repair tooling exists or is planned; only matters for
  conversations from early development, not new ones.
- Windows dev note: stale/orphaned TCP listen sockets on a previously-used
  port can survive process kills with no backing live process. If `run.ps1`
  fails to bind, prefer changing the port (`-Port` param) over fighting the
  OS-level socket state.

## Conventions

- No comments explaining *what* code does; only for non-obvious *why*
  (see the `provider_native` slicing comment in `engine.py` as the model).
- Don't add migration tooling (Alembic etc.) — single-user local SQLite,
  schema changes during development are expected to mean a manual DB wipe.
- Don't add features that only work for one LLM provider without checking
  the other provider's code path too.
- `.env` is gitignored and must never be committed; `.env.example` is the
  tracked template — keep them in sync when adding new settings.
