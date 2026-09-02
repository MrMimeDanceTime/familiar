import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from sqlmodel import Session

from app.api import chat, conversations, decks, preferences
from app.backup import run_startup_backup, start_periodic_backup
from app.cards import importer as card_importer
from app.cards import schema as card_schema
from app.config import settings
from app.db import repository as repo
from app.db.session import get_engine, init_db
from app.knowledge.models import _ensure_fts
from app.knowledge.seed import seed_knowledge_base


# Configure app logging once, at import time, so module loggers (pipeline
# timings, EDHREC/Scryfall warnings) actually reach the console. Without this
# the root logger defaults to WARNING with no handler and INFO logs vanish —
# which is why intermittent pipeline stalls left nothing to inspect. Level comes
# from LOG_LEVEL in .env (default INFO).
logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger("app.main")


def _reconcile_turns() -> None:
    """Clean up the turn log after a restart.

    Nothing survives a process restart mid-turn, so any turn still marked
    `running` is orphaned: no thread is driving it, and a reconnecting client
    would tail it forever. Mark those failed, then drop the replay buffer for
    turns old enough that nobody is coming back for them.

    Fully guarded — this is housekeeping, and it must never stop the app from
    serving.
    """
    try:
        with Session(get_engine()) as session:
            orphaned = repo.fail_orphaned_turns(session)
            purged = repo.purge_old_turn_events(session)
        if orphaned:
            logger.info("Marked %d orphaned turn(s) as failed after restart", orphaned)
        if purged:
            logger.info("Purged %d stale turn event(s)", purged)
    except Exception:  # noqa: BLE001
        logger.exception("Turn reconciliation failed; continuing startup")


def _refresh_card_index() -> None:
    """Refresh the local Scryfall card index in the background.

    A cold import is ~15s (38k cards + 230k taggings), which would block the
    app from serving on every stale start, so it runs off the startup path in a
    daemon thread. A stale index is still a usable index and the pipeline falls
    back to the Scryfall API when a card is missing, so nothing here is on the
    critical path. Fully guarded: an import failure leaves the existing index
    in place and the app serves either way.
    """
    card_schema.ensure_schema()
    if not settings.card_index_refresh_on_startup:
        return

    def _run() -> None:
        try:
            card_importer.refresh_if_stale()
        except Exception:  # noqa: BLE001
            logger.exception("Card index refresh failed; continuing with existing index")

    threading.Thread(target=_run, name="card-index-refresh", daemon=True).start()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _ensure_fts()
    seed_knowledge_base()
    _refresh_card_index()
    # Off-machine backup to pCloud (no-ops unless configured). Runs at startup
    # so it captures the prior session even after a hard crash; fully guarded
    # so a backup failure never blocks the app from serving.
    run_startup_backup()
    periodic = start_periodic_backup()
    _reconcile_turns()
    yield
    if periodic is not None:
        periodic[1].set()


app = FastAPI(title="Familiar", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router)
app.include_router(decks.router)
app.include_router(conversations.router)
app.include_router(preferences.router)

FRONTEND_DIST = settings.frontend_dist
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
