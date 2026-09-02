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
  DeepSeek. **deepseek-v4-pro (thinking mode) is the default/primary provider** in this
  deployment, with deepseek-v4-flash available as a per-call fast seam — see
  [PROVIDERS.md](PROVIDERS.md).
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

The suite never reaches the network. `tests/conftest.py` turns off the startup
card-index refresh and pins the oracle-tag lookup to an empty in-memory map;
tests that need Scryfall or EDHREC patch the client. A test that only passes
with a cached bulk file sitting in `backend/` is a bug in the test.

Frontend tests run with vitest from `frontend/`:

```
npm test            # once
npm run test:watch  # while iterating
npm run test:coverage
```

Coverage is deliberately focused on `src/api/` and `src/hooks/` — the streaming
and reconnect path, where every shipped bug in the chat transport has lived.
Components are not covered; a live smoke test in-browser is still the check for
UI behavior changes. Also run `npm run build`, which type-checks.

The tests that matter most simulate a **dropped connection**: `killStream()` in
`src/test/sseFixture.ts` destroys the response body mid-read the way a
backgrounded mobile tab does, which the browser reports as a generic
`TypeError` rather than an abort. That distinction is not reachable by
type-checking or by clicking around, and it is where the bugs were.

## Code layout

```
backend/app/
  main.py            FastAPI app; startup lifespan (init_db, FTS setup, seed KB, backup); mounts built frontend/dist/ as static files if present
  config.py          pydantic-settings, reads .env from repo root
  backup.py          best-effort startup DB snapshot -> synced folder or pCloud (guarded, never blocks startup)
  deckplan.py        the deck plan: role targets derived from the stated power level, themes, gaps (what the deck still needs)
  autoincludes.py    format staples the deck is missing (high play rate, no commander-specific synergy), surfaced on every deck read
  cards/
    schema.py / importer.py   local Scryfall card index (oracle cards + oracle tags + FTS5), refreshed in a background thread at startup
    store.py                  read side: name/oracle_id lookups, tag lookups, text search, tag co-occurrence queries
    cooccurrence.py           derives which oracle tags relate (lift) and which describe colour rather than function
  brainmap/
    map.py             score_pool(): runs every layer over a candidate pool and blends the result
    layers.py          the three-layer model, weights, the off_meta blend, and per-card confidence
    consensus.py       layer 1: EDHREC play rate + synergy
    mechanical.py      layer 2: commander tags expanded through co-occurrence, with colour-artifact and generic-tag guards
    personal.py        layer 3: the player's own approve/deny history, weighted by denial reason
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
  pipeline/            deterministic retrieval pipeline — Python owns retrieval; the LLM only emits query specs (stage 1) and selects from a curated pool (stage 4)
    roles.py           fine functional-role taxonomy layered over the coarse 4 (delegates to tag_lookup, total fallthrough)
    spec.py            stage 1: intent + identity -> Scryfall query specs (LLM JSON) + enforcement/repair
    candidates.py      stage 2: run queries via search_pipeline, merge/dedupe, EDHREC-annotate, cap
    shaping.py         stage 3: strip -> precompute legality/roles -> dedupe -> cap (legal-first) -> render
    selection.py       stage 4: curated pool -> picks/cuts (LLM JSON), hallucination-guarded to legal pool cards
    validate.py        stage 5: picks -> pending proposals by reusing propose_deck_changes (the shared gate)
    service.py         build_suggestions(): wires stages 1-5, returns SuggestionResult (used by the suggest_cards tool)
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
    turn_runner.py        runs a turn to completion on a worker thread, writing every event to the durable turn log
    turn_bus.py           how a reader learns new turn events exist (polling today; the log is the source of truth)
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
- **Deck**: `id, name, commander, partner_commander, notes, power_level, format, created_at, updated_at` plus the cached power nuance (`power_nuance_adj, power_nuance_reason, power_nuance_key`) and the plan (`role_targets` (JSON), `themes` (JSON), `plan_notes, off_meta`) — see `app/deckplan.py`
- **DeckCard**: `id, deck_id (FK), card_name, quantity, category, mana_value, color_identity, type_line, oracle_text, oracle_id, tags (JSON), notes, added_at` — `oracle_id`/`tags` feed the functional-role stats (see `tag_lookup.py`)
- **DeckProposal**: `id, conversation_id (FK), deck_id (FK), message_id, status (pending|approved|denied), action (add|remove|set_commander), card_name, quantity, category, commander_name, reasoning, scores (JSON), denial_reason, created_at` — the model never edits a deck directly; it proposes changes the player approves/denies (see `propose_deck_changes` tool and `apply_proposal`/`deny_proposal`). `scores` is the brain map's verdict at proposal time; `denial_reason` feeds the personal scoring layer. A batch is written atomically: a change that fails validation writes nothing.
- **Turn**: `id (uuid hex), conversation_id (FK), owner_id, status (running|done|error), error, created_at, updated_at` — one execution of the chat loop, independent of any HTTP connection. Only one turn may be running per conversation (`POST /api/chat` returns 409 otherwise).
- **TurnEvent**: `id, turn_id (FK), seq (per-turn, unique with turn_id), event, data (JSON), created_at` — the replay buffer clients tail from a cursor; purged for terminal turns older than a day.
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

## Durable turns

`POST /api/chat` starts a turn and returns its id immediately; the turn then
runs on a worker thread (`app/chat/turn_runner.py`) and appends every event to
`turn_event`. `GET /api/chat/turns/{id}/events?after=N` replays from a cursor
and follows live, so reconnect, refresh, and cold load are one code path. A
single stream is capped at 15 minutes; the client treats a clean close with
no `done`/`error` event as a cap, checks the turn's status, and resumes.

## SSE event vocabulary

`token`, `tool_call`, `deck_proposal`, `deck_updated`, `done`, `error` —
formatted in `app/chat/streaming.py`, consumed by
`frontend/src/hooks/useChatStream.ts`. A proposal tool
(`propose_deck_changes` or `suggest_cards`) creates the pending proposals
mid-turn, but the engine does NOT stream them immediately. Once any batch is
created, the model is restricted to `withdraw_pending_proposals` (it can trim
cards but not add another batch), and the `deck_proposal` event is emitted once
at turn end carrying only the proposals still pending after trims, anchored to
the turn's final assistant message. This deferred, settled reveal keeps trimmed
cards from flashing into the UI and back out, and stops the
propose→withdraw→propose churn. The player approves/denies through the
`/api/decks/proposals/{id}/apply|deny` REST endpoints.

## Subsystems

### Tools
The model can't touch the deck directly. Beyond the Scryfall/EDHREC lookups it
gets: `search_deckbuilding_knowledge` (local KB), `deck_get_current`,
`deck_get_stats` (computed bracket 1-5, power 1-10, and the factor breakdown),
`propose_deck_changes` / `withdraw_pending_proposals` (the approval workflow),
`suggest_cards` (runs the `pipeline/` retrieval flow for open-ended "what should
I add" requests — see below), and `deck_update_notes`. Specs live in
`app/tools/schemas.py` — tune the description wording there to correct model
misuse rather than adding code.

### Retrieval pipeline (`app/pipeline/`)
See [PIPELINE.md](PIPELINE.md) for the full walkthrough (stages, model/thinking
policy, deck-aware selection, timing/timeout diagnostics).

Inverts control of card suggestion: instead of the model driving retrieval by
electing to call Scryfall/EDHREC turn-by-turn (which misfired often — the model
would answer from training data), deterministic Python owns the flow and the
model is bounded to two JSON calls — emit query specs (stage 1) and pick from a
pre-filtered, legality-checked, EDHREC-ranked pool (stage 4). `build_suggestions`
returns the same proposal shape as `propose_deck_changes`, so it plugs into the
existing approval workflow with no new plumbing. It's currently triggered by the
model electing to call the `suggest_cards` tool (entry-gated, but everything
after entry is deterministic); a future proactive trigger could grow out of the
`_deck_grounding` hook in `engine.py`.

### Knowledge base (`app/knowledge/`)
Deckbuilding best-practice entries in a `knowledge_entries` table mirrored into
an FTS5 index. `_ensure_fts()` creates the virtual table + sync triggers once;
`seed_knowledge_base()` populates it idempotently on startup; `search_knowledge`
runs a BM25-ranked `MATCH` with optional format/category narrowing. Separately,
`tag_lookup.py` caches Scryfall's Oracle Tags bulk file (~24h) and maps a card's
`oracle_id` to functional roles (ramp/draw/removal/land) that drive deck stats.

### Brain map (`app/brainmap/`) and card index (`app/cards/`)
The retrieval pipeline ranks its candidate pool through three independent
scoring layers (consensus, mechanical, personal) before capping it; the
per-layer scores ride on each proposal so the review UI can say why a card was
suggested. The mechanical layer reads the local card index, a SQLite copy of
Scryfall's oracle cards and oracle tags with derived tag co-occurrence, so it
works from what a card *does* rather than a hand-written theme list. Any change
here must be measured with `tools/coverage_report.py` (see `CLAUDE.md`).

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
