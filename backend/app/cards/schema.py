"""Schema for the local card index.

Three tables plus one virtual table, all created idempotently so a restart or a
partial import never leaves the DB in a shape the app can't read:

``cards``
    One row per Scryfall Oracle ID. Carries the columns the pipeline queries AND
    the complete raw Scryfall JSON blob. The blob is deliberate over-collection:
    extracting a field we didn't anticipate becomes a local re-extract instead of
    a re-download plus a schema argument. At ~200MB uncompressed for the raw side
    that trade is cheap.

    Every card is imported, including commander-illegal ones. Legality is a
    column to filter on at query time, not a reason to drop rows — an illegal
    card still has to be *nameable* so the app can explain why it can't go in a
    deck.

``card_tags``
    ``(oracle_id, slug)`` from Scryfall's Oracle Tags bulk file. This replaces
    the in-memory dict the tag cache used to rebuild on every cold start by
    walking all ~229k taggings. One import writes both tables, so there is a
    single cache with a single refresh path.

``card_index_meta``
    Import bookkeeping: which bulk file version landed and when. Drives the
    24h staleness check without stat-ing a cache file.

``cards_fts``
    FTS5 over name + oracle text. Contentless-external (``content='cards'``), so
    the index stores no duplicate copy of the text. Kept in sync by explicit
    writes in the importer rather than triggers: the import is a single bulk
    transaction, and triggers would fire per row for no benefit.
"""

from __future__ import annotations

from sqlalchemy import text

from app.db.session import get_engine

# Columns extracted from the raw blob for querying. Chosen to cover what
# _project_pipeline_card returns (so a local search can substitute for the
# Scryfall API path) plus what filtering and the brain map need. Anything not
# listed here is still available in `raw` without a re-download.
_CARDS_DDL = """
CREATE TABLE IF NOT EXISTS cards (
    oracle_id       TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    mana_cost       TEXT,
    cmc             REAL,
    type_line       TEXT,
    oracle_text     TEXT,
    color_identity  TEXT,
    colors          TEXT,
    keywords        TEXT,
    power           TEXT,
    toughness       TEXT,
    loyalty         TEXT,
    rarity          TEXT,
    edhrec_rank     INTEGER,
    penny_rank      INTEGER,
    layout          TEXT,
    reserved        INTEGER NOT NULL DEFAULT 0,
    game_changer    INTEGER NOT NULL DEFAULT 0,
    legal_commander INTEGER NOT NULL DEFAULT 0,
    playable        INTEGER NOT NULL DEFAULT 1,
    produced_mana   TEXT,
    image_url       TEXT,
    scryfall_uri    TEXT,
    raw             TEXT NOT NULL
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_cards_name_lower ON cards (lower(name))",
    "CREATE INDEX IF NOT EXISTS idx_cards_legal ON cards (legal_commander)",
    "CREATE INDEX IF NOT EXISTS idx_cards_playable ON cards (playable)",
    "CREATE INDEX IF NOT EXISTS idx_cards_edhrec ON cards (edhrec_rank)",
    "CREATE INDEX IF NOT EXISTS idx_cards_cmc ON cards (cmc)",
    "CREATE INDEX IF NOT EXISTS idx_card_tags_slug ON card_tags (slug)",
    "CREATE INDEX IF NOT EXISTS idx_cooc_slug ON tag_cooccurrence (slug, lift DESC)",
)

_CARD_TAGS_DDL = """
CREATE TABLE IF NOT EXISTS card_tags (
    oracle_id TEXT NOT NULL,
    slug      TEXT NOT NULL,
    PRIMARY KEY (oracle_id, slug)
)
"""

_META_DDL = """
CREATE TABLE IF NOT EXISTS card_index_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
)
"""

# Which oracle tags appear together on the same card, and how much more often
# than chance. This is what lets a commander's own tags name the cards that work
# with it, without anyone authoring a theme list.
#
# `lift` is P(partner | slug) / P(partner): 1.0 means "co-occurs exactly as often
# as chance", 50x means "strongly related". Measured, `synergy-exile-cast` pairs
# with `repeatable-impulsive-draw` at 57.5x — Prosper's enabler package, derived
# rather than declared.
#
# Slug NAMES do not encode this. Substring matching pairs only 65% of
# relationship slugs and produces `draw-matters` -> `drawback` and
# `name-matters` -> `punny-name`, so morphology is an accident of naming.
_TAG_COOC_DDL = """
CREATE TABLE IF NOT EXISTS tag_cooccurrence (
    slug      TEXT NOT NULL,
    partner   TEXT NOT NULL,
    shared    INTEGER NOT NULL,
    lift      REAL NOT NULL,
    PRIMARY KEY (slug, partner)
)
"""

# Tags whose closest neighbours are the five colour tags describe a card's
# COLOUR rather than what it does. Torbran carries `synergy-red`, which is true
# and useless: 80% of its top partners are the other colour tags, where every
# genuine mechanic scores 0%. Flagged at build time so scoring can skip them.
_TAG_TRAITS_DDL = """
CREATE TABLE IF NOT EXISTS tag_traits (
    slug          TEXT PRIMARY KEY,
    card_count    INTEGER NOT NULL,
    colour_share  REAL NOT NULL
)
"""

_FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS cards_fts USING fts5(
    name,
    oracle_text,
    type_line,
    content='cards',
    content_rowid='rowid'
)
"""


def ensure_schema() -> None:
    """Create the card-index tables and FTS index if absent. Idempotent."""
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text(_CARDS_DDL))
        conn.execute(text(_CARD_TAGS_DDL))
        conn.execute(text(_META_DDL))
        conn.execute(text(_TAG_COOC_DDL))
        conn.execute(text(_TAG_TRAITS_DDL))
        conn.execute(text(_FTS_DDL))
        for stmt in _INDEXES:
            conn.execute(text(stmt))


def get_meta(key: str) -> str | None:
    with get_engine().begin() as conn:
        row = conn.execute(
            text("SELECT value FROM card_index_meta WHERE key = :k"), {"k": key}
        ).first()
    return row[0] if row else None


def set_meta(key: str, value: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO card_index_meta (key, value) VALUES (:k, :v) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
            ),
            {"k": key, "v": value},
        )


def card_count() -> int:
    with get_engine().begin() as conn:
        return conn.execute(text("SELECT count(*) FROM cards")).scalar() or 0


def tag_count() -> int:
    with get_engine().begin() as conn:
        return conn.execute(text("SELECT count(*) FROM card_tags")).scalar() or 0
