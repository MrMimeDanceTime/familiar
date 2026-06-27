# Familiar

A conversational AI assistant for Magic: The Gathering Commander/EDH deckbuilding.
Brainstorms and iteratively develops decklists with you — including off-meta commander
ideas — grounding every suggestion in real card data from Scryfall and EDHREC.

## Structure

- `backend/` — FastAPI app, SQLite persistence, LLM tool-calling loop (Claude or DeepSeek)
- `frontend/` — React + TypeScript + Vite chat UI with a live deck side panel

## Running locally

### Backend

```
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -e .
cp ../.env.example ../.env   # fill in your API key(s)
uvicorn app.main:app --reload --port 8000
```

### Frontend

```
cd frontend
npm install
npm run dev
```

Frontend dev server runs on `:5173` and proxies `/api` to the backend on `:8000`.
