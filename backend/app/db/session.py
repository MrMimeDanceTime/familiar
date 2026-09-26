import logging
from functools import lru_cache

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

logger = logging.getLogger("app.db.session")

# Additive, non-destructive schema patches for existing DBs. SQLModel's
# create_all only creates missing tables/columns on a fresh DB; it never adds a
# column to an existing table. Each entry is (table, column, SQL type); applied
# idempotently, guarded by a PRAGMA table_info check, so the user's decks and
# conversations survive. See CLAUDE.md — no Alembic, single-user local SQLite.
_ADDITIVE_COLUMNS: list[tuple[str, str, str]] = [
    ("deck", "power_nuance_adj", "REAL"),
    ("deck", "power_nuance_reason", "TEXT"),
    ("deck", "power_nuance_key", "TEXT"),
    # The deck plan (role targets, themes, plan notes). JSON columns are TEXT
    # in SQLite; SQLModel's JSON type reads/writes them transparently.
    ("deck", "role_targets", "JSON"),
    ("deck", "themes", "JSON"),
    ("deck", "restrictions", "JSON"),
    ("deck", "plan_notes", "TEXT"),
    ("deck", "off_meta", "REAL"),
    ("deck", "max_card_price", "REAL"),
    ("deck_proposals", "price_usd", "REAL"),
    # Brain-map verdict and structured denial reason on each proposal.
    ("deck_proposals", "scores", "JSON"),
    ("deck_proposals", "denial_reason", "TEXT"),
    ("deck_proposals", "reported_status", "TEXT"),
    # Player-written knowledge entries alongside the seeded ones.
    ("knowledge_entries", "source", "TEXT"),
    # Per-turn token accounting.
    ("turn", "llm_calls", "INTEGER"),
    ("turn", "prompt_tokens", "INTEGER"),
    ("turn", "completion_tokens", "INTEGER"),
    ("turn", "reasoning_tokens", "INTEGER"),
    ("turn", "cancel_requested", "INTEGER"),
]


@lru_cache(maxsize=1)
def get_engine():
    return create_engine(f"sqlite:///{settings.db_path}", connect_args={"check_same_thread": False})


def _apply_additive_columns(engine) -> None:
    with engine.begin() as conn:
        for table, column, coltype in _ADDITIVE_COLUMNS:
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            # An empty PRAGMA means the table does not exist, not that it has no
            # columns — so ALTERing it would raise. That happens whenever this
            # runs against a DB predating a table (create_all makes missing
            # tables, but ordering is not guaranteed relative to this patch).
            # Skip rather than fail: create_all will build the table with the
            # column already in the model.
            if not existing:
                continue
            if column not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}"))


def _enable_wal(engine) -> None:
    """Put SQLite in WAL mode so readers don't block on the writer.

    Under the default `delete` journal a write transaction blocks every reader
    for its duration. That was invisible while a chat turn was one serialized
    generator, but durable turns add a background writer running concurrently
    with SSE readers tailing the event log — precisely the pattern rollback
    journaling handles worst, and a reliable source of `database is locked`.

    WAL allows concurrent readers alongside a single writer. The setting is a
    property of the database file and persists, so this is idempotent.
    """
    with engine.begin() as conn:
        mode = conn.execute(text("PRAGMA journal_mode=WAL")).scalar()
    if mode is not None and str(mode).lower() != "wal":
        # Don't fail startup: the app is still correct under `delete`, just more
        # lock-prone. Surface it so a locked-up deploy has a breadcrumb.
        logger.warning("Could not enable WAL journal mode; got %r", mode)


def init_db() -> None:
    engine = get_engine()
    _enable_wal(engine)
    SQLModel.metadata.create_all(engine)
    _apply_additive_columns(engine)


def get_session() -> Session:
    return Session(get_engine())
