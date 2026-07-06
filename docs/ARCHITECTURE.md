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
- Data sources: Scryfall REST API (self-throttled httpx client), EDHREC's
  unofficial JSON endpoint (disk-cached, defensively parsed since it's
  undocumented), and Scryfall's Oracle Tags bulk export (disk-cached ~24h)
  for functional card roles (ramp/draw/removal/land).
- Local knowledge base of deckbuilding best practices, full-text searchable
  via SQLite FTS5 (BM25 ranked), seeded on startup and queried as a tool.
- Deck-platform integrations (Archidekt/Moxfield) behind a provider interface
  for importing external decklists.
- Best-effort DB backup on startup (synced-folder or pCloud), fully guarded.

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
  main.py            FastAPI app; startup lifespan (init_db, FTS setup, seed KB, backup); mounts built frontend/dist/ as static files if present
  config.py          pydantic-settings, reads .env from repo root
  backup.py          best-effort startup DB snapshot -> synced folder or pCloud (guarded, never blocks startup)
  llm/
    base.py           ChatProvider Protocol + neutral types (AssistantTurn, ToolCallRequest, ToolResult, ToolSpec)
    anthropic_provider.py / deepseek_provider.py
    factory.py        get_provider(name)
  tools/
    scryfall_client.py / edhrec_client.py   external data clients
    deck_tools.py      deck_* tool implementations (import, stats/bracket/power, proposals)
    schemas.py         JSON-Schema ToolSpec definitions (provider-neutral; tune wording here to fix model misuse)
    dispatch.py         name -> callable registry, catches exceptions into error ToolResults
  knowledge/
    models.py          KnowledgeEntry model + FTS5 virtual table & sync triggers (_ensure_fts)
    seed.py            seeds the knowledge base on startup (idempotent)
    store.py           search_knowledge(): FTS5 MATCH + BM25 ranking, optional format/category filter
    tag_lookup.py      Scryfall Oracle Tags bulk cache -> functional roles (ramp/draw/removal/land)
  integrations/
    base.py            DeckProvider ABC + registry + NormalizedDeck/Card types; supports_fetch/supports_push capability flags
    archidekt.py / moxfield.py   fetch-capable providers (register themselves on import)
    service.py         fetch_into_deck(): normalize -> reuse import_decklist pipeline -> designate commanders
  db/
    models.py / session.py / repository.py   (Conversation, Message, Deck, DeckCard, DeckProposal, UserPreferences)
  chat/
    engine.py           THE agentic loop — see PROVIDER_SHAPES.md
    prompt.py            system prompt (behavior contract, see PRODUCT.md)
    streaming.py          SSE event formatting
  api/
    chat.py / conversations.py / decks.py / preferences.py

frontend/src/
  App.tsx, api/client.ts, api/sse.ts, api/cardImage.ts
  components/  ChatView, MessageBubble, ProposalCard, ToolActivityIndicator,
               DeckPanel, DeckDetail, DeckCardRow, deckViz, ImportPanel,
               PreferencesPanel, ConversationSidebar, CardPreview,
               CardPinContext, PinnedTray, MobileHeader, MobileNav,
               ErrorBoundary, icons
  hooks/       useChatStream (SSE lifecycle), useDeck, useTheme
  styles/      global.css, fonts.css (self-hosted woff2 under public/fonts/)
  types/api.ts
```

## Data model

- **Conversation**: `id, title, deck_id (FK, nullable), created_at, updated_at`
- **Message**: `id, conversation_id (FK), role, text_content, provider_native (JSON), tool_calls (JSON), tool_results (JSON), sequence, created_at`
- **Deck**: `id, name, commander, partner_commander, notes, power_level, format, created_at, updated_at`
- **DeckCard**: `id, deck_id (FK), card_name, quantity, category, mana_value, color_identity, type_line, oracle_text, oracle_id, tags (JSON), notes, added_at` — `oracle_id`/`tags` feed the functional-role stats (see `tag_lookup.py`)
- **DeckProposal**: `id, conversation_id (FK), deck_id (FK), message_id, status (pending|approved|denied), action (add|remove|set_commander), card_name, quantity, category, commander_name, reasoning, created_at` — the model never edits a deck directly; it proposes changes the player approves/denies (see `propose_deck_changes` tool and `apply_proposal`/`deny_proposal`)
- **UserPreferences**: single row (`id=1`): `preferred_bracket, preferred_power, budget, rule0_notes, build_preferences` — surfaced in the system prompt so advice respects the player's standing preferences
- **KnowledgeEntry** (+ `knowledge_fts` FTS5 mirror): `id, title, body, category, format` — seeded on startup, searched by the `search_deckbuilding_knowledge` tool

`Message.provider_native` stores the exact provider-native message dict(s)
for a turn so a conversation can be replayed byte-identical back into
whichever provider produced it. No FK cascade is configured anywhere —
deleting a parent row (conversation, deck) requires explicitly deleting its
children first in the repository function (see `delete_conversation`,
`delete_deck` in `app/db/repository.py` for the pattern).

No Alembic/migrations — single-user local SQLite. Apply a schema change with
a targeted, non-destructive edit to the existing DB where possible: an
additive change (new nullable column) is a one-off idempotent
`ALTER TABLE ... ADD COLUMN`, guarded by a `PRAGMA table_info` check, run
once against `backend/familiar.db` — no migration framework, no data loss.
Wipe the DB (delete `backend/familiar.db`, let `init_db` recreate it) only
when the change can't be done in place — a column type change, a table
restructure, or intentionally dropping data.

## SSE event vocabulary

`token`, `tool_call`, `deck_updated`, `done`, `error` — formatted in
`app/chat/streaming.py`, consumed by `frontend/src/hooks/useChatStream.ts`.
Deck proposals aren't a separate event type: the model calls
`propose_deck_changes`, and the pending proposals ride along in the
`deck_updated` snapshot; the player approves/denies them through the
`/api/decks/proposals/{id}/apply|deny` REST endpoints.

## Subsystems

### Tools
The model can't touch the deck directly. Beyond the Scryfall/EDHREC lookups it
gets: `search_deckbuilding_knowledge` (local KB), `deck_get_current`,
`deck_get_stats` (computed bracket 1-5, power 1-10, and the factor breakdown),
`propose_deck_changes` / `withdraw_pending_proposals` (the approval workflow),
and `deck_update_notes`. Specs live in `app/tools/schemas.py` — tune the
description wording there to correct model misuse rather than adding code.

### Knowledge base (`app/knowledge/`)
Deckbuilding best-practice entries in a `knowledge_entries` table mirrored into
an FTS5 index. `_ensure_fts()` creates the virtual table + sync triggers once;
`seed_knowledge_base()` populates it idempotently on startup; `search_knowledge`
runs a BM25-ranked `MATCH` with optional format/category narrowing. Separately,
`tag_lookup.py` caches Scryfall's Oracle Tags bulk file (~24h) and maps a card's
`oracle_id` to functional roles (ramp/draw/removal/land) that drive deck stats.

### Integrations (`app/integrations/`)
`DeckProvider` ABC with a registry and explicit `supports_fetch`/`supports_push`
capability flags. Providers register on import (Archidekt, Moxfield — both
fetch-only today). Fetching reuses the existing import pipeline:
`service.fetch_into_deck` normalizes the remote deck, renders it to the
decklist text `import_decklist` already accepts, imports it, then designates
commanders. Push is scaffolded (returns 501) — no write-capable provider yet.

### Backup (`app/backup.py`)
Best-effort DB snapshot on startup via SQLite's online backup API (consistent
even mid-write). `BACKUP_MODE`: `folder` (default — write into a synced folder
like Drive/OneDrive/Dropbox; no API token), `pcloud` (upload via the pCloud
API), or `off`. Keeps the newest `BACKUP_KEEP` timestamped snapshots. Every
step is guarded so a backup failure can never block startup. Configured in
`.env` (see `.env.example`).

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
