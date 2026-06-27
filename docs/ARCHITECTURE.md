# Architecture

Personal, local-only AI assistant for Magic: The Gathering Commander/EDH
deckbuilding. Single user, runs locally, no auth, no deployment target.
Optimize for correctness and conversational feel over scalability.

See [PRODUCT.md](PRODUCT.md) for the product philosophy behind the chat
behavior, and [PROVIDER_SHAPES.md](PROVIDER_SHAPES.md) for the single most
important implementation detail in this codebase before touching the chat
engine.

## Stack

- Backend: FastAPI + SQLModel (SQLite) + a hand-rolled agentic tool-calling
  loop (no LangChain/LangGraph). Python ≥3.10.
- Frontend: React + TypeScript + Vite, plain CSS (no component library).
- LLM: provider-neutral abstraction supporting Anthropic (Claude) and
  DeepSeek. **DeepSeek-reasoner is the default/primary provider** in this
  deployment — see [PROVIDERS.md](PROVIDERS.md).
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
`.env.example` and exits so you can fill in API keys.

For frontend hot-reload while actively editing UI, run two processes
instead (see root `README.md` "Frontend development" section): backend via
`uvicorn app.main:app --reload --port 8420` from `backend/`, frontend via
`npm run dev` from `frontend/` (Vite on `:5173`, proxies `/api` to `:8420`).

## Tests

```
cd backend && .venv\Scripts\python.exe -m pytest -q
```

Always run the full suite after engine or provider changes — `app/chat/engine.py`
and the provider shape contract (see [PROVIDER_SHAPES.md](PROVIDER_SHAPES.md))
are the highest-risk area in this codebase.

Frontend has no test suite yet; verify with `npx tsc --noEmit` and
`npm run build` from `frontend/`, and a live smoke test in-browser for any
UI behavior change (not just type-checking).

## Code layout

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
    engine.py           THE agentic loop — see PROVIDER_SHAPES.md
    prompt.py            system prompt (behavior contract, see PRODUCT.md)
    streaming.py          SSE event formatting
  api/
    chat.py / conversations.py / decks.py

frontend/src/
  App.tsx, api/client.ts, api/sse.ts
  components/  ChatView, MessageBubble, ToolActivityIndicator, DeckPanel, DeckCardRow, ConversationSidebar
  hooks/       useChatStream (SSE lifecycle), useDeck
  types/api.ts
```

## Data model

- **Conversation**: `id, title, deck_id (FK, nullable), created_at, updated_at`
- **Message**: `id, conversation_id (FK), role, text_content, provider_native (JSON), tool_calls (JSON), tool_results (JSON), sequence, created_at`
- **Deck**: `id, name, commander, partner_commander, notes, power_level, created_at, updated_at`
- **DeckCard**: `id, deck_id (FK), card_name, quantity, category, mana_value, color_identity, notes, added_at`

`Message.provider_native` stores the exact provider-native message dict(s)
for a turn so a conversation can be replayed byte-identical back into
whichever provider produced it. No FK cascade is configured anywhere —
deleting a parent row (conversation, deck) requires explicitly deleting its
children first in the repository function (see `delete_conversation`,
`delete_deck` in `app/db/repository.py` for the pattern).

No Alembic/migrations — single-user local SQLite; schema changes during
development are expected to mean a manual DB wipe (delete `backend/familiar.db`).

## SSE event vocabulary

`token`, `tool_call`, `deck_updated`, `done`, `error` — formatted in
`app/chat/streaming.py`, consumed by `frontend/src/hooks/useChatStream.ts`.

## Known limitations

- Conversations created before the provider-shape persistence bug was fixed
  (see [PROVIDER_SHAPES.md](PROVIDER_SHAPES.md)) may have permanently
  corrupted history (missing tool-result entries) and will 400 if
  continued. No migration/repair tooling exists or is planned — only
  affects a handful of early-development conversations, not new ones.
- Windows dev note: stale/orphaned TCP listen sockets on a previously-used
  port can survive process kills with no backing live process. If
  `run.ps1` fails to bind, prefer changing the port (`-Port` param) over
  fighting OS-level socket state.
