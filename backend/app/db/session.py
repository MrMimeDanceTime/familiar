from functools import lru_cache

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

# Additive, non-destructive schema patches for existing DBs. SQLModel's
# create_all only creates missing tables/columns on a fresh DB; it never adds a
# column to an existing table. Each entry is (table, column, SQL type); applied
# idempotently, guarded by a PRAGMA table_info check, so the user's decks and
# conversations survive. See CLAUDE.md — no Alembic, single-user local SQLite.
_ADDITIVE_COLUMNS: list[tuple[str, str, str]] = [
    ("deck", "power_nuance_adj", "REAL"),
    ("deck", "power_nuance_reason", "TEXT"),
    ("deck", "power_nuance_key", "TEXT"),
]


@lru_cache(maxsize=1)
def get_engine():
    return create_engine(f"sqlite:///{settings.db_path}", connect_args={"check_same_thread": False})


def _apply_additive_columns(engine) -> None:
    with engine.begin() as conn:
        for table, column, coltype in _ADDITIVE_COLUMNS:
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            if column not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}"))


def init_db() -> None:
    engine = get_engine()
    SQLModel.metadata.create_all(engine)
    _apply_additive_columns(engine)


def get_session() -> Session:
    return Session(get_engine())
