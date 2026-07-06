from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from app.api import chat, conversations, decks, preferences
from app.backup import run_startup_backup
from app.db.session import init_db
from app.knowledge.models import _ensure_fts
from app.knowledge.seed import seed_knowledge_base


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _ensure_fts()
    seed_knowledge_base()
    # Off-machine backup to pCloud (no-ops unless configured). Runs at startup
    # so it captures the prior session even after a hard crash; fully guarded
    # so a backup failure never blocks the app from serving.
    run_startup_backup()
    yield


app = FastAPI(title="Familiar", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router)
app.include_router(decks.router)
app.include_router(conversations.router)
app.include_router(preferences.router)

FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    # index.html must never be cached: its asset references are content-hashed,
    # so a stale index.html points the browser at an old JS bundle after a
    # rebuild. Hashed assets under /assets are safe to cache normally.
    class NoCacheIndexStatic(StaticFiles):
        async def get_response(self, path: str, scope) -> Response:
            response = await super().get_response(path, scope)
            if path in ("", ".", "index.html"):
                response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            return response

    app.mount("/", NoCacheIndexStatic(directory=FRONTEND_DIST, html=True), name="frontend")
