"""Knowledge-entry model + FTS5 full-text index setup.

The FTS5 virtual table mirrors ``knowledge_entries`` so full-text queries
can rank results by BM25 relevance.  SQLite doesn't auto-sync FTS content
tables, so inserts use a trigger (created in ``_ensure_fts``) that keeps
the FTS index in lockstep.
"""

from sqlmodel import Field, Session, SQLModel, text

from app.db.session import get_engine


# Who wrote an entry. Seeded entries are replaced wholesale whenever the seed
# changes; the player's own are never touched by the seeder.
SOURCE_SEED = "seed"
SOURCE_USER = "user"


class KnowledgeEntry(SQLModel, table=True):
    __tablename__ = "knowledge_entries"

    id: int | None = Field(default=None, primary_key=True)
    title: str
    body: str
    category: str
    format: str = "any"
    source: str = Field(default=SOURCE_SEED, index=True)


def _ensure_fts() -> None:
    """Create the FTS5 virtual table and sync triggers if they don't exist."""
    with Session(get_engine()) as session:
        # Only create if the FTS table doesn't already exist
        exists = session.exec(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_fts'")
        ).first()
        if exists:
            return

        session.exec(text("""
            CREATE VIRTUAL TABLE knowledge_fts USING fts5(
                title,
                body,
                content='knowledge_entries',
                content_rowid='id'
            )
        """))
        session.exec(text("""
            CREATE TRIGGER knowledge_fts_insert AFTER INSERT ON knowledge_entries BEGIN
                INSERT INTO knowledge_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
            END
        """))
        session.exec(text("""
            CREATE TRIGGER knowledge_fts_delete AFTER DELETE ON knowledge_entries BEGIN
                INSERT INTO knowledge_fts(knowledge_fts, rowid, title, body) VALUES('delete', old.id, old.title, old.body);
            END
        """))
        session.exec(text("""
            CREATE TRIGGER knowledge_fts_update AFTER UPDATE ON knowledge_entries BEGIN
                INSERT INTO knowledge_fts(knowledge_fts, rowid, title, body) VALUES('delete', old.id, old.title, old.body);
                INSERT INTO knowledge_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
            END
        """))
        session.commit()
