"""Read side of the local card index.

Returns cards in exactly the shape ``scryfall_client._project_pipeline_card``
produces, so the retrieval pipeline can read locally without any downstream
stage knowing the difference.

Every query filters ``playable = 1`` by default. The index deliberately stores
tokens, emblems, schemes, and art-series objects (see ``_NON_PLAYABLE_LAYOUTS``
in the importer), and some of those share a name with a real card, so an
unfiltered lookup can silently return a nameless, textless shadow of the card
the caller asked for.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from app.db.session import get_engine

# Column list shared by every read, so all paths project identically and
# _row_to_card's unpacking stays valid. Rendered with a table alias because
# most reads join, and a bare list would be ambiguous there.
_COLUMN_NAMES = (
    "oracle_id", "name", "mana_cost", "cmc", "type_line", "oracle_text",
    "color_identity", "keywords", "power", "toughness", "loyalty", "rarity",
    "edhrec_rank", "game_changer", "legal_commander", "image_url", "scryfall_uri",
)


def _columns(alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return ", ".join(prefix + c for c in _COLUMN_NAMES)


_SELECT_COLUMNS = _columns()


def _row_to_card(row: Any) -> dict[str, Any]:
    """Project a DB row into the pipeline's card shape.

    Mirrors ``_project_pipeline_card``: same keys, same types. ``color_identity``
    is stored as a compact string ("BR") but the pipeline expects a list, and
    ``keywords`` round-trips through JSON.
    """
    (
        oracle_id, name, mana_cost, cmc, type_line, oracle_text, color_identity,
        keywords, power, toughness, loyalty, rarity, edhrec_rank, game_changer,
        legal_commander, image_url, scryfall_uri,
    ) = row

    try:
        parsed_keywords = json.loads(keywords) if keywords else []
    except (json.JSONDecodeError, TypeError):
        parsed_keywords = []

    return {
        "name": name,
        "oracle_id": oracle_id,
        "mana_cost": mana_cost,
        "cmc": cmc,
        "type_line": type_line,
        "oracle_text": oracle_text,
        "color_identity": list(color_identity or ""),
        "image_url": image_url,
        "legal_commander": bool(legal_commander),
        "scryfall_uri": scryfall_uri,
        "keywords": parsed_keywords,
        "power": power,
        "toughness": toughness,
        "loyalty": loyalty,
        "rarity": rarity,
        "edhrec_rank": edhrec_rank,
        "game_changer": bool(game_changer),
    }


def by_name(name: str) -> dict[str, Any] | None:
    """Exact (case-insensitive) name lookup. None when absent."""
    sql = f"""
        SELECT {_SELECT_COLUMNS} FROM cards
        WHERE lower(name) = lower(:name) AND playable = 1
        LIMIT 1
    """
    with get_engine().begin() as conn:
        row = conn.execute(text(sql), {"name": name}).first()
    return _row_to_card(row) if row else None


def by_names(names: list[str]) -> dict[str, dict[str, Any]]:
    """Bulk name lookup. Returns ``lower(name) -> card`` for those found.

    Used to hydrate a list of card names (an EDHREC cardlist, a decklist) in one
    query instead of N round-trips.
    """
    if not names:
        return {}
    lowered = [n.lower() for n in names if n]
    out: dict[str, dict[str, Any]] = {}
    # Chunked to stay well clear of SQLite's variable limit on large decklists.
    for i in range(0, len(lowered), 400):
        chunk = lowered[i : i + 400]
        placeholders = ", ".join(f":n{j}" for j in range(len(chunk)))
        params = {f"n{j}": v for j, v in enumerate(chunk)}
        sql = f"""
            SELECT {_SELECT_COLUMNS} FROM cards
            WHERE lower(name) IN ({placeholders}) AND playable = 1
        """
        with get_engine().begin() as conn:
            for row in conn.execute(text(sql), params):
                card = _row_to_card(row)
                out[card["name"].lower()] = card
    return out


def by_oracle_ids(oracle_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Bulk lookup keyed by oracle_id."""
    if not oracle_ids:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(oracle_ids), 400):
        chunk = oracle_ids[i : i + 400]
        placeholders = ", ".join(f":o{j}" for j in range(len(chunk)))
        params = {f"o{j}": v for j, v in enumerate(chunk)}
        sql = f"""
            SELECT {_SELECT_COLUMNS} FROM cards
            WHERE oracle_id IN ({placeholders})
        """
        with get_engine().begin() as conn:
            for row in conn.execute(text(sql), params):
                card = _row_to_card(row)
                out[card["oracle_id"]] = card
    return out


def raw_card(oracle_id: str) -> dict[str, Any] | None:
    """The complete stored Scryfall object for a card.

    This is why the index keeps the raw blob: a caller that needs a field the
    extracted columns don't carry gets it locally, with no re-download and no
    schema change.
    """
    with get_engine().begin() as conn:
        row = conn.execute(
            text("SELECT raw FROM cards WHERE oracle_id = :o"), {"o": oracle_id}
        ).first()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return None


def tags_for(oracle_id: str) -> set[str]:
    """Oracle tag slugs for one card."""
    with get_engine().begin() as conn:
        rows = conn.execute(
            text("SELECT slug FROM card_tags WHERE oracle_id = :o"), {"o": oracle_id}
        ).fetchall()
    return {r[0] for r in rows}


def tags_for_many(oracle_ids: list[str]) -> dict[str, set[str]]:
    """Bulk tag lookup: ``oracle_id -> slugs``.

    Replaces the old whole-file in-memory dict. The pipeline only ever needs
    tags for the cards in the current pool, so this reads tens of rows instead
    of rebuilding a 229k-entry mapping on first call.
    """
    if not oracle_ids:
        return {}
    out: dict[str, set[str]] = {oid: set() for oid in oracle_ids}
    for i in range(0, len(oracle_ids), 400):
        chunk = oracle_ids[i : i + 400]
        placeholders = ", ".join(f":o{j}" for j in range(len(chunk)))
        params = {f"o{j}": v for j, v in enumerate(chunk)}
        sql = f"SELECT oracle_id, slug FROM card_tags WHERE oracle_id IN ({placeholders})"
        with get_engine().begin() as conn:
            for oid, slug in conn.execute(text(sql), params):
                out.setdefault(oid, set()).add(slug)
    return out


def cards_with_tag(slug: str, *, limit: int = 200, legal_only: bool = True) -> list[dict[str, Any]]:
    """Every card carrying an oracle tag, EDHREC-ordered.

    The primitive the mechanical layer of the brain map is built on: it asks
    "what cards do X" against a curated functional vocabulary rather than
    regexing oracle text.
    """
    legal_clause = "AND c.legal_commander = 1" if legal_only else ""
    sql = f"""
        SELECT {_columns('c')}
        FROM cards c
        JOIN card_tags t ON t.oracle_id = c.oracle_id
        WHERE t.slug = :slug AND c.playable = 1 {legal_clause}
        ORDER BY CASE WHEN c.edhrec_rank IS NULL THEN 1 ELSE 0 END, c.edhrec_rank
        LIMIT :limit
    """
    with get_engine().begin() as conn:
        rows = conn.execute(text(sql), {"slug": slug, "limit": limit}).fetchall()
    return [_row_to_card(r) for r in rows]


def cards_with_any_tag(
    slugs: list[str], *, limit: int = 200, legal_only: bool = True
) -> list[dict[str, Any]]:
    """Cards carrying ANY of the given tag slugs, EDHREC-ordered, deduped.

    Tagger's vocabulary is finer-grained than it first appears — there is no
    bare ``sacrifice-outlet`` slug, but there are ``sacrifice-outlet-creature``
    (894 cards), ``-artifact`` (311), ``-land`` (238), ``-permanent``,
    ``-token``, plus ``repeatable-sacrifice-outlet`` (580) and
    ``free-sacrifice-outlet`` (183). A functional concept is therefore usually a
    *set* of slugs, not one, which is what this takes.
    """
    if not slugs:
        return []
    placeholders = ", ".join(f":s{j}" for j in range(len(slugs)))
    params: dict[str, Any] = {f"s{j}": s for j, s in enumerate(slugs)}
    params["limit"] = limit
    legal_clause = "AND c.legal_commander = 1" if legal_only else ""
    sql = f"""
        SELECT DISTINCT {_columns('c')}
        FROM cards c
        JOIN card_tags t ON t.oracle_id = c.oracle_id
        WHERE t.slug IN ({placeholders}) AND c.playable = 1 {legal_clause}
        ORDER BY CASE WHEN c.edhrec_rank IS NULL THEN 1 ELSE 0 END, c.edhrec_rank
        LIMIT :limit
    """
    with get_engine().begin() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    return [_row_to_card(r) for r in rows]


def tag_slugs(pattern: str | None = None, *, limit: int = 100) -> list[tuple[str, int]]:
    """Available tag slugs with card counts, optionally filtered by substring.

    Discovery helper: the vocabulary has ~4,500 slugs and does not always use
    the name you would guess, so callers (and the brain map's future rule
    authors) need a way to find the real slug before querying it.
    """
    where = "WHERE slug LIKE :pat" if pattern else ""
    params: dict[str, Any] = {"limit": limit}
    if pattern:
        params["pat"] = f"%{pattern}%"
    sql = f"""
        SELECT slug, count(*) AS n FROM card_tags
        {where}
        GROUP BY slug ORDER BY n DESC LIMIT :limit
    """
    with get_engine().begin() as conn:
        return [(r[0], r[1]) for r in conn.execute(text(sql), params)]


def search_text(
    query: str,
    *,
    limit: int = 50,
    legal_only: bool = True,
    identity: str | None = None,
) -> list[dict[str, Any]]:
    """Full-text search over name, oracle text, and type line.

    ``identity`` is a colour-identity string ("BR"); when given, only cards
    whose identity is a subset are returned. Subset testing is done in Python
    because SQLite has no set operators, and the candidate count after FTS and
    the legality filter is small.
    """
    safe = query.replace('"', '""')
    tokens = [t for t in safe.split() if t]
    if not tokens:
        return []
    fts_query = " AND ".join(f'"{t}"' for t in tokens)

    legal_clause = "AND c.legal_commander = 1" if legal_only else ""
    # Over-fetch when an identity filter applies, since it is applied after.
    fetch = limit * 5 if identity is not None else limit
    sql = f"""
        SELECT {_columns('c')}
        FROM cards_fts f
        JOIN cards c ON c.rowid = f.rowid
        WHERE cards_fts MATCH :q AND c.playable = 1 {legal_clause}
        ORDER BY CASE WHEN c.edhrec_rank IS NULL THEN 1 ELSE 0 END, c.edhrec_rank
        LIMIT :limit
    """
    with get_engine().begin() as conn:
        rows = conn.execute(text(sql), {"q": fts_query, "limit": fetch}).fetchall()

    cards = [_row_to_card(r) for r in rows]
    if identity is None:
        return cards[:limit]

    allowed = set(identity.upper())
    return [c for c in cards if set(c["color_identity"]).issubset(allowed)][:limit]
