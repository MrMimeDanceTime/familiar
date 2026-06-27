# Familiar

A conversational AI assistant for Magic: The Gathering Commander/EDH deckbuilding.
Brainstorms and iteratively develops decklists with you — including off-meta commander
ideas — grounding every suggestion in real card data from Scryfall and EDHREC.

## Structure

- `backend/` — FastAPI app, SQLite persistence, LLM tool-calling loop (Claude or DeepSeek)
- `frontend/` — React + TypeScript + Vite chat UI with a live deck side panel

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
