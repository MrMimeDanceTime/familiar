"""Bulk import of Scryfall oracle cards, oracle tags, and rulings.

Streams the gzipped JSONL bulk files straight into SQLite. Three files:

- ``oracle_cards`` — one object per Oracle ID (no duplicate printings), the
  working table.
- ``oracle_tags``  — the community functional tags, joined into ``card_tags``.
- ``rulings``      — per-card rulings, attached to card lookups so the model
  answers "does X work with Y" from Scryfall's text rather than its memory.

``default_cards`` is read for one thing only: which sets each card was
printed in (``card_printings``), with Scryfall's set list for names and
blocks (``card_sets``). The rest of a printing (rarity, prices, finishes,
artist) is dead weight for a deck played online and with proxies, and the
cards table stays one row per card.

The whole import runs in a single transaction per file so a crash mid-stream
leaves the previous index intact rather than a half-replaced one.
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Iterator

import httpx

from app.cards import schema
from app.db.session import get_engine
from sqlalchemy import text

logger = logging.getLogger(__name__)

BULK_ENDPOINT = "https://api.scryfall.com/bulk-data/{type}"

# Refresh cadence, matching the tag cache's old 24h behaviour.
MAX_AGE_SECONDS = 86400

_UA = {"User-Agent": "familiar/0.1", "Accept": "application/json"}

# Columns written per card, in bind order. Kept adjacent to the INSERT so the
# two can't drift.
_CARD_COLUMNS = (
    "oracle_id", "name", "mana_cost", "cmc", "type_line", "oracle_text",
    "color_identity", "colors", "keywords", "power", "toughness", "loyalty",
    "rarity", "edhrec_rank", "penny_rank", "layout", "reserved", "game_changer",
    "legal_commander", "playable", "produced_mana", "price_usd", "image_url",
    "scryfall_uri", "raw", "oracle_plain",
)

_INSERT_CARD = (
    f"INSERT OR REPLACE INTO cards ({', '.join(_CARD_COLUMNS)}) "
    f"VALUES ({', '.join(':' + c for c in _CARD_COLUMNS)})"
)


class CardImportError(RuntimeError):
    """Raised when a bulk file cannot be fetched or parsed."""


def _resolve_bulk(client: httpx.Client, bulk_type: str) -> tuple[str, str]:
    """Return ``(download_url, updated_at)`` for a Scryfall bulk-data type.

    Scryfall moved from ``download_uri`` (plain JSON array) to
    ``jsonl_download_uri`` (gzipped JSONL); the old key is still accepted so a
    stale mirror or a rollback keeps working. The API has also renamed its size
    field before (``size`` -> ``compressed_size``), so nothing here depends on
    size being present.
    """
    resp = client.get(BULK_ENDPOINT.format(type=bulk_type))
    resp.raise_for_status()
    data = resp.json()
    url = data.get("jsonl_download_uri") or data.get("download_uri")
    if not url:
        raise CardImportError(
            f"Scryfall bulk-data response for {bulk_type!r} carried no download "
            f"URI (keys: {sorted(data)})"
        )
    return url, str(data.get("updated_at") or "")


def _stream_jsonl(client: httpx.Client, url: str) -> Iterator[dict[str, Any]]:
    """Yield objects from a gzipped JSONL bulk file without buffering it whole.

    The response is streamed through an incremental gzip decompressor, so peak
    memory stays at the chunk size rather than the ~200MB expanded file.
    """
    with client.stream("GET", url) as resp:
        resp.raise_for_status()
        decompressor = gzip.GzipFile(fileobj=_ChunkReader(resp.iter_bytes()), mode="rb")
        reader = io.TextIOWrapper(decompressor, encoding="utf-8")
        for line in reader:
            line = line.strip().rstrip(",")
            if not line or line in ("[", "]"):
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


class _ChunkReader(io.RawIOBase):
    """Adapt httpx's byte-chunk iterator to a file-like object for GzipFile."""

    def __init__(self, chunks: Iterator[bytes]) -> None:
        self._chunks = chunks
        self._buf = b""

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:  # type: ignore[override]
        while not self._buf:
            try:
                self._buf = next(self._chunks)
            except StopIteration:
                return 0
        n = min(len(b), len(self._buf))
        b[:n] = self._buf[:n]
        self._buf = self._buf[n:]
        return n


# Scryfall layouts that are not playable Magic cards. The bulk file mixes them
# in with real cards, and several *share a name with a real card* — the
# art_series "Gigantosaurus // Gigantosaurus" (type line "Card // Card", no
# rules text) sits right next to the real one. None are commander-legal, so
# legality filtering already keeps them out of suggestions, but a name lookup
# or an FTS hit could still return the wrong object and silently produce a card
# with no text, cost, or type.
#
# Marked rather than dropped: keeping the rows means tokens stay available if
# the brain map later wants to reason about what a card *creates*, and it
# matches the decision to keep illegal cards nameable.
_NON_PLAYABLE_LAYOUTS = frozenset({
    "art_series", "token", "double_faced_token", "emblem", "scheme",
    "planar", "vanguard", "augment", "host",
})


def _as_json_list(value: Any) -> str:
    """Serialize a list-valued Scryfall field for column storage."""
    if not value:
        return "[]"
    if isinstance(value, list):
        return json.dumps(value)
    return json.dumps([value])


def _image_url(raw: dict[str, Any]) -> str | None:
    """Pick the display image, falling back to the front face for DFCs."""
    uris = raw.get("image_uris") or {}
    if not uris and raw.get("card_faces"):
        uris = (raw["card_faces"][0] or {}).get("image_uris") or {}
    return uris.get("normal")


def _oracle_text(raw: dict[str, Any]) -> str | None:
    """Oracle text, joining both faces for double-faced cards.

    A DFC carries no top-level ``oracle_text``; its rules text lives per-face.
    Without this join, every DFC would be invisible to FTS and would render as
    a blank card in the selection pool.
    """
    if raw.get("oracle_text") is not None:
        return raw.get("oracle_text")
    faces = raw.get("card_faces") or []
    parts = [
        f.get("oracle_text") for f in faces
        if isinstance(f, dict) and f.get("oracle_text")
    ]
    return "\n//\n".join(parts) if parts else None


_REMINDER = re.compile(r"\s*\([^()]*\)")


def _oracle_plain(oracle_text: str | None) -> str | None:
    return _REMINDER.sub("", oracle_text) if oracle_text else oracle_text


def _colors(raw: dict[str, Any]) -> str:
    """A card's colours; a double-faced card's are its front face's. A DFC has
    no top-level ``colors``, which stored it as colourless and let a blue card
    through a white-black filter. Front face rather than both, because that
    is what Scryfall's c: matches (measured on the model's own queries)."""
    if raw.get("colors") is not None:
        return "".join(raw["colors"])
    faces = raw.get("card_faces") or [{}]
    return "".join((faces[0] or {}).get("colors") or [])


def _mana_cost(raw: dict[str, Any]) -> str | None:
    if raw.get("mana_cost"):
        return raw.get("mana_cost")
    faces = raw.get("card_faces") or []
    parts = [
        f.get("mana_cost") for f in faces
        if isinstance(f, dict) and f.get("mana_cost")
    ]
    return " // ".join(parts) if parts else None


def _price_usd(raw: dict[str, Any]) -> float | None:
    """The cheapest USD price Scryfall lists for the representative printing.

    oracle_cards carries one printing per card with its prices object; a
    non-foil price is preferred, foil or etched only when that is all there is.
    Good enough for a budget ceiling, not for a shopping list.
    """
    prices = raw.get("prices") or {}
    for key in ("usd", "usd_foil", "usd_etched"):
        value = prices.get(key)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _card_row(raw: dict[str, Any]) -> dict[str, Any] | None:
    oracle_id = raw.get("oracle_id")
    name = raw.get("name")
    if not oracle_id or not name:
        return None
    legalities = raw.get("legalities") or {}
    return {
        "oracle_id": oracle_id,
        "name": name,
        "mana_cost": _mana_cost(raw),
        "cmc": raw.get("cmc"),
        "type_line": raw.get("type_line"),
        "oracle_text": _oracle_text(raw),
        "oracle_plain": _oracle_plain(_oracle_text(raw)),
        "color_identity": "".join(raw.get("color_identity") or []),
        "colors": _colors(raw),
        "keywords": _as_json_list(raw.get("keywords")),
        "power": raw.get("power"),
        "toughness": raw.get("toughness"),
        "loyalty": raw.get("loyalty"),
        "rarity": raw.get("rarity"),
        "edhrec_rank": raw.get("edhrec_rank"),
        "penny_rank": raw.get("penny_rank"),
        "layout": raw.get("layout"),
        "reserved": 1 if raw.get("reserved") else 0,
        "game_changer": 1 if raw.get("game_changer") else 0,
        "legal_commander": 1 if legalities.get("commander") == "legal" else 0,
        "playable": 0 if raw.get("layout") in _NON_PLAYABLE_LAYOUTS else 1,
        "produced_mana": _as_json_list(raw.get("produced_mana")),
        "price_usd": _price_usd(raw),
        "image_url": _image_url(raw),
        "scryfall_uri": raw.get("scryfall_uri"),
        "raw": json.dumps(raw, separators=(",", ":")),
    }


def import_cards(client: httpx.Client, *, batch_size: int = 2000) -> int:
    """Import ``oracle_cards`` into the ``cards`` table and rebuild FTS.

    Returns the number of cards written. Runs as one transaction: a crash
    mid-stream rolls back and leaves the previous index intact.
    """
    url, updated_at = _resolve_bulk(client, "oracle_cards")
    logger.info("card index: importing oracle_cards (%s)", updated_at or "unknown")

    # Parse the whole stream BEFORE opening a write transaction. SQLite allows
    # exactly one writer, so holding a transaction across a ~10s network read
    # blocks every other write in the process — which is what a background
    # refresh at startup does to live traffic (`database is locked`). The rows
    # are ~40k dicts; holding them briefly is much cheaper than holding the lock.
    rows = [r for r in (_card_row(raw) for raw in _stream_jsonl(client, url)) if r]

    written = 0
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM cards"))
    # Short per-batch transactions, so a concurrent writer only ever waits for
    # one batch rather than the whole import.
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        with engine.begin() as conn:
            conn.execute(text(_INSERT_CARD), batch)
        written += len(batch)

    # Rebuild FTS from the freshly written content table. Cheaper and less
    # error-prone than per-row trigger maintenance during a bulk load.
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO cards_fts(cards_fts) VALUES('rebuild')"))

    schema.set_meta("cards_updated_at", updated_at)
    schema.set_meta("cards_imported_at", datetime.now(timezone.utc).isoformat())
    schema.set_meta("index_version", schema.INDEX_VERSION)
    logger.info("card index: wrote %d cards", written)
    return written


def import_tags(client: httpx.Client, *, batch_size: int = 5000) -> int:
    """Import ``oracle_tags`` into ``card_tags``. Returns rows written.

    Replaces the old in-memory dict that walked ~229k taggings on every cold
    start. One import writes cards and tags, so there is a single cache with a
    single refresh path.
    """
    url, updated_at = _resolve_bulk(client, "oracle_tags")
    logger.info("card index: importing oracle_tags (%s)", updated_at or "unknown")

    # Parse before writing, for the same reason as import_cards: never hold the
    # single SQLite write lock across a network read.
    seen: set[tuple[str, str]] = set()
    rows: list[dict[str, str]] = []
    for entry in _stream_jsonl(client, url):
        slug = entry.get("slug")
        if not slug:
            continue
        for tagging in entry.get("taggings") or []:
            oid = tagging.get("oracle_id")
            if not oid:
                continue
            key = (oid, slug)
            if key in seen:
                continue
            seen.add(key)
            rows.append({"oracle_id": oid, "slug": slug})

    insert = text(
        "INSERT OR IGNORE INTO card_tags (oracle_id, slug) VALUES (:oracle_id, :slug)"
    )
    written = 0
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM card_tags"))
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        with engine.begin() as conn:
            conn.execute(insert, batch)
        written += len(batch)

    schema.set_meta("tags_updated_at", updated_at)
    logger.info("card index: wrote %d taggings", written)
    return written


def import_rulings(client: httpx.Client, *, batch_size: int = 5000) -> int:
    """Import ``rulings`` into ``card_rulings``. Returns rows written.

    Each object carries oracle_id, published_at, and comment; the source
    field (wotc / scryfall) is dropped. Parsed before writing, like the other
    imports, so the write lock is never held across the network.
    """
    url, updated_at = _resolve_bulk(client, "rulings")
    logger.info("card index: importing rulings (%s)", updated_at or "unknown")
    rows = [
        {
            "oracle_id": r["oracle_id"],
            "published_at": r.get("published_at"),
            "comment": r["comment"],
        }
        for r in _stream_jsonl(client, url)
        if r.get("oracle_id") and r.get("comment")
    ]
    insert = text(
        "INSERT INTO card_rulings (oracle_id, published_at, comment) "
        "VALUES (:oracle_id, :published_at, :comment)"
    )
    written = 0
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM card_rulings"))
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        with engine.begin() as conn:
            conn.execute(insert, batch)
        written += len(batch)
    schema.set_meta("rulings_updated_at", updated_at)
    logger.info("card index: wrote %d rulings", written)
    return written


SETS_ENDPOINT = "https://api.scryfall.com/sets"


def import_printings(client: httpx.Client, *, batch_size: int = 20000) -> int:
    """Import set membership from ``default_cards`` and the set list. Returns
    printings written. Parsed before writing, like the other imports."""
    resp = client.get(SETS_ENDPOINT)
    resp.raise_for_status()
    sets = [
        {"code": s["code"], "name": s["name"], "block_code": s.get("block_code"),
         "block": s.get("block"), "released_at": s.get("released_at"), "set_type": s.get("set_type")}
        for s in resp.json().get("data") or [] if s.get("code") and s.get("name")
    ]
    url, updated_at = _resolve_bulk(client, "default_cards")
    logger.info("card index: importing printings (%s)", updated_at or "unknown")
    pairs = {
        (p["oracle_id"], p["set"])
        for p in _stream_jsonl(client, url)
        if p.get("oracle_id") and p.get("set")
    }
    rows = [{"oracle_id": o, "set_code": s} for o, s in pairs]
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM card_sets"))
        conn.execute(text(
            "INSERT INTO card_sets (code, name, block_code, block, released_at, set_type) "
            "VALUES (:code, :name, :block_code, :block, :released_at, :set_type)"
        ), sets)
        conn.execute(text("DELETE FROM card_printings"))
    insert = text("INSERT INTO card_printings (oracle_id, set_code) VALUES (:oracle_id, :set_code)")
    for i in range(0, len(rows), batch_size):
        with engine.begin() as conn:
            conn.execute(insert, rows[i : i + batch_size])
    schema.set_meta("printings_updated_at", updated_at)
    logger.info("card index: wrote %d printings across %d sets", len(rows), len(sets))
    return len(rows)


def _import_printings_guarded(client: httpx.Client) -> None:
    # Set membership serves set and block searches only; a failure leaves the
    # finder on each card's own printing and the app serving.
    try:
        import_printings(client)
    except (httpx.HTTPError, CardImportError, OSError, gzip.BadGzipFile, KeyError, ValueError) as exc:
        logger.warning("card index: printings import failed, continuing without: %s", exc)


def is_stale(max_age_seconds: int = MAX_AGE_SECONDS) -> bool:
    """True when the index is missing, empty, or older than the max age."""
    if schema.card_count() == 0:
        return True
    if schema.get_meta("index_version") != schema.INDEX_VERSION:
        return True
    stamp = schema.get_meta("cards_imported_at")
    if not stamp:
        return True
    try:
        imported = datetime.fromisoformat(stamp)
    except ValueError:
        return True
    if imported.tzinfo is None:
        imported = imported.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - imported).total_seconds()
    return age > max_age_seconds


def refresh_if_stale(*, force: bool = False, client: httpx.Client | None = None) -> bool:
    """Import cards + tags when the index is stale. Returns True if it imported.

    Guarded end to end: an import failure logs and leaves the existing index in
    place. A stale index is still a usable index, and the app must serve either
    way.
    """
    schema.ensure_schema()
    if not force and not is_stale():
        if schema.card_count() and not schema.printing_count():
            # An index built before printings existed: add them without
            # re-importing everything else.
            with httpx.Client(headers=_UA, timeout=120.0, follow_redirects=True) as c:
                _import_printings_guarded(c)
        return False

    owned = client is None
    client = client or httpx.Client(headers=_UA, timeout=120.0, follow_redirects=True)
    start = time.monotonic()
    try:
        import_cards(client)
        import_tags(client)
        # Derived from the tags that just landed, so it has to follow them.
        from app.cards import cooccurrence

        cooccurrence.rebuild()
        # Rulings are a grounding bonus, not a precondition: a failure here
        # leaves cards and tags in place and the app serving.
        try:
            import_rulings(client)
        except (httpx.HTTPError, CardImportError, OSError, gzip.BadGzipFile) as exc:
            logger.warning("card index: rulings import failed, continuing without: %s", exc)
        _import_printings_guarded(client)
    except (httpx.HTTPError, CardImportError, OSError, gzip.BadGzipFile) as exc:
        logger.warning("card index refresh failed, keeping existing index: %s", exc)
        return False
    finally:
        if owned:
            client.close()

    logger.info(
        "card index: refresh complete in %.1fs (%d cards, %d taggings)",
        time.monotonic() - start, schema.card_count(), schema.tag_count(),
    )
    return True
