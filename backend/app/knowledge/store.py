"""Full-text search over the deckbuilding knowledge base via SQLite FTS5."""

from __future__ import annotations

from sqlmodel import Session, text

from app.db.session import get_engine


def search_knowledge(
    query: str, top_k: int = 5, format: str | None = None,
    category: str | None = None,
) -> list[dict]:
    """Search the knowledge base with BM25 relevance ranking.

    Args:
        query: Natural-language search query (passed directly to FTS5 MATCH).
        top_k: Max results to return.
        format: If set, only return entries tagged for this format or ``"any"``.
        category: If set, only return entries in this category (e.g. "ramp",
            "removal", "power-level"). Narrows results when a topic keyword
            also appears in unrelated entries.

    Returns:
        List of ``{title, body, category, format}`` dicts ordered by relevance.
    """
    # Escape double-quotes and build a safe FTS5 query.  We wrap each
    # token in quotes so FTS5 treats them as term prefixes, giving a
    # soft prefix-match behaviour without needing the FTS5 prefix `*`
    # syntax on every token (which can hit performance).
    safe = query.replace('"', '""')
    fts_query = " OR ".join(f'"{token}"*' for token in safe.split() if token)

    # Over-fetch before applying the format filter below — otherwise
    # filtering out non-matching-format rows from a LIMIT-capped result
    # set can return fewer than top_k (or zero) even when good matches
    # exist further down the BM25 ranking.
    sql = """
        SELECT ke.title, ke.body, ke.category, ke.format, ke.source
        FROM knowledge_fts kf
        JOIN knowledge_entries ke ON ke.id = kf.rowid
        WHERE knowledge_fts MATCH :q
        ORDER BY rank
        LIMIT :limit
    """
    fetch_limit = top_k if not (format or category) else max(top_k * 5, 50)

    with Session(get_engine()) as session:
        rows = session.exec(
            text(sql),
            params={"q": fts_query, "limit": fetch_limit},
        ).fetchall()

    results: list[dict] = []
    for row in rows:
        title, body, entry_category, entry_format, source = row
        if format and entry_format not in (format, "any"):
            continue
        if category and entry_category != category:
            continue
        results.append({
            "title": title,
            "body": body,
            "category": entry_category,
            "format": entry_format,
            # The player's own entries carry their playgroup's rules and
            # preferences; the model should know which advice is theirs.
            "source": source or "seed",
        })
        if len(results) >= top_k:
            break

    return results
