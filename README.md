# Familiar

A conversational AI assistant for Magic: The Gathering Commander/EDH deckbuilding.
Brainstorms and iteratively develops decklists with you — including off-meta commander
ideas — grounding every suggestion in real card data from Scryfall, EDHREC, a local
deckbuilding knowledge base, and Scryfall's functional card tags.

It proposes changes you approve, deny, or undo (it never edits the deck on
its own), scores every suggestion against play rate, mechanical fit with the
commander, and your own history, respects a per-card budget, computes each
deck's bracket, power level, price, mana-source balance, and the infinite
combos it contains (from Commander Spellbook), deals opening
hands, imports decklists from Archidekt/Moxfield and exports them for Arena
and Archidekt, lets you add your own knowledge entries beside the seeded
deckbuilding advice, remembers your standing preferences, and backs up its
SQLite database on startup and daily.

## Structure

- `backend/` — FastAPI app, SQLite persistence, LLM tool-calling loop (DeepSeek), knowledge base (FTS5), deck-platform integrations, scheduled DB backups
- `frontend/` — React + TypeScript + Vite chat UI with a live deck side panel
- `docs/` — architecture, product philosophy, and provider-specific gotchas for anyone (or any coding assistant) working on this codebase
- `design-package/` — the UI visual-refresh design brief and approved handoff (reference material; not built or imported by the app)

## Running locally

The simplest way to run Familiar day-to-day is the bundled launcher, which
builds the frontend once and serves it together with the API on a single port:

```
.\run.ps1
```

Then open `http://localhost:8420`. First run will create a venv, install
dependencies, and prompt you to fill in `.env` if it doesn't exist yet.
Subsequent runs skip the frontend rebuild unless `frontend/src` has changed.
Pass `-Port` to use a different port, e.g. `.\run.ps1 -Port 8080`.

### Configuration

All settings live in `.env` (copied from `.env.example` on first run):

- **LLM provider** — DeepSeek, via `DEEPSEEK_API_KEY` and the `DEEPSEEK_MODEL*`
  settings. See `docs/PROVIDERS.md`.
- **Backups** — `BACKUP_MODE` controls DB snapshots: `folder` (default; point
  `BACKUP_DIR` at a synced folder like Drive/OneDrive/Dropbox), `pcloud` (set
  `PCLOUD_AUTH_TOKEN`), or `off`. One snapshot is taken at startup and then
  every `BACKUP_INTERVAL_HOURS` (default 24). `BACKUP_KEEP` caps retained
  snapshots.

### Frontend development (hot reload)

If you're actively editing the frontend, run the backend and the Vite dev
server separately instead, so changes show up without a rebuild:

```
cd backend
.venv\Scripts\activate
uvicorn app.main:app --reload --port 8420
```

```
cd frontend
npm run dev
```

The Vite dev server runs on `:5173` and proxies `/api` to the backend on `:8420`.
