"""A local combo database, from Commander Spellbook.

The bracket rules turn on two-card infinite combos and nothing in the code
could see one: the bracket estimate matched card names against hand lists
and the pipeline scored fit from tags. Commander Spellbook publishes its
variant data openly, so the combos become a table the app can query.

What it answers:

- ``combos_in_deck``: every combo the deck already contains, for the stats
  panel and for the bracket estimate (a two-card combo lifts a deck to at
  least bracket 3).
- ``combos_one_short``: for a candidate pool, which candidates complete a
  combo with cards already in the deck. The pool render says so, and the
  selection model gets a real reason to pick a card instead of a vibe.

The source format is Spellbook's, which is undocumented from our side and
parsed defensively: a variant is kept when it has an id, at least two named
cards, and is Commander-legal; every other field is optional. Anything the
parser cannot read is skipped with a count in the log, never a crash.
Variants of more than four cards are dropped: they are not what the bracket
rules mean by a combo and they swamp the one-short query.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx
from sqlalchemy import text

from app.cards import schema
from app.config import settings
from app.db.session import get_engine

logger = logging.getLogger(__name__)

MAX_COMBO_CARDS = 4
REFRESH_SECONDS = 7 * 86400

_COMBOS_DDL = """
CREATE TABLE IF NOT EXISTS combos (
    id           TEXT PRIMARY KEY,
    identity     TEXT,
    description  TEXT,
    produces     TEXT NOT NULL,
    card_names   TEXT NOT NULL,
    card_count   INTEGER NOT NULL,
    popularity   INTEGER,
    bracket_tag  TEXT
)
"""
_COMBO_CARDS_DDL = """
CREATE TABLE IF NOT EXISTS combo_cards (
    combo_id   TEXT NOT NULL,
    name_lower TEXT NOT NULL,
    PRIMARY KEY (combo_id, name_lower)
)
"""
_COMBO_INDEX = "CREATE INDEX IF NOT EXISTS idx_combo_cards_name ON combo_cards (name_lower)"


def ensure_schema() -> None:
    with get_engine().begin() as conn:
        conn.execute(text(_COMBOS_DDL))
        conn.execute(text(_COMBO_CARDS_DDL))
        conn.execute(text(_COMBO_INDEX))


def parse_variant(raw: dict[str, Any]) -> dict[str, Any] | None:
    """One Spellbook variant to a row, or None when it is not usable."""
    combo_id = raw.get("id")
    if combo_id is None:
        return None
    names: list[str] = []
    for use in raw.get("uses") or []:
        card = use.get("card") if isinstance(use, dict) else None
        name = (card or {}).get("name") if isinstance(card, dict) else None
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    names = list(dict.fromkeys(names))
    if len(names) < 2 or len(names) > MAX_COMBO_CARDS:
        return None
    legalities = raw.get("legalities") or {}
    if isinstance(legalities, dict) and legalities.get("commander") not in (None, "legal", True):
        return None
    produces = [
        (p.get("feature") or {}).get("name")
        for p in raw.get("produces") or []
        if isinstance(p, dict) and isinstance((p.get("feature") or {}).get("name"), str)
    ]
    popularity = raw.get("popularity")
    return {
        "id": str(combo_id),
        "identity": raw.get("identity") if isinstance(raw.get("identity"), str) else None,
        "description": raw.get("description") if isinstance(raw.get("description"), str) else None,
        "produces": json.dumps(produces),
        "card_names": json.dumps(names),
        "card_count": len(names),
        "popularity": int(popularity) if isinstance(popularity, (int, float)) else None,
        "bracket_tag": raw.get("bracketTag") if isinstance(raw.get("bracketTag"), str) else None,
    }


def _iter_variants(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, dict):
        payload = payload.get("variants") or payload.get("results") or []
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item


def import_combos(client: httpx.Client, url: str | None = None, *, batch_size: int = 2000) -> int:
    """Download the variants file and replace the combo tables. Returns rows."""
    ensure_schema()
    source = url or settings.combo_source_url
    resp = client.get(source)
    resp.raise_for_status()
    rows: list[dict[str, Any]] = []
    skipped = 0
    for raw in _iter_variants(resp.json()):
        row = parse_variant(raw)
        if row is None:
            skipped += 1
            continue
        rows.append(row)
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM combos"))
        conn.execute(text("DELETE FROM combo_cards"))
    insert = text(
        "INSERT OR REPLACE INTO combos (id, identity, description, produces, card_names, "
        "card_count, popularity, bracket_tag) VALUES (:id, :identity, :description, "
        ":produces, :card_names, :card_count, :popularity, :bracket_tag)"
    )
    insert_card = text(
        "INSERT OR IGNORE INTO combo_cards (combo_id, name_lower) VALUES (:combo_id, :name_lower)"
    )
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        cards = [
            {"combo_id": r["id"], "name_lower": n.lower()}
            for r in batch for n in json.loads(r["card_names"])
        ]
        with engine.begin() as conn:
            conn.execute(insert, batch)
            if cards:
                conn.execute(insert_card, cards)
    schema.set_meta("combos_imported_at", datetime.now(timezone.utc).isoformat())
    logger.info("combos: wrote %d combo(s), skipped %d unusable variant(s)", len(rows), skipped)
    return len(rows)


def is_stale() -> bool:
    ensure_schema()
    with get_engine().begin() as conn:
        count = conn.execute(text("SELECT count(*) FROM combos")).scalar() or 0
    if count == 0:
        return True
    stamp = schema.get_meta("combos_imported_at")
    if not stamp:
        return True
    try:
        imported = datetime.fromisoformat(stamp)
    except ValueError:
        return True
    if imported.tzinfo is None:
        imported = imported.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - imported).total_seconds() > REFRESH_SECONDS


def refresh_if_stale(*, force: bool = False, client: httpx.Client | None = None) -> bool:
    """Import when missing or older than a week. Guarded like the card index."""
    if not settings.combo_source_url:
        return False
    if not force and not is_stale():
        return False
    owned = client is None
    client = client or httpx.Client(
        headers={"User-Agent": "familiar/0.1", "Accept": "application/json"},
        timeout=180.0, follow_redirects=True,
    )
    start = time.monotonic()
    try:
        import_combos(client)
    except Exception as exc:  # noqa: BLE001 - combos are a bonus, never a precondition
        logger.warning("combos: refresh failed, keeping existing data: %s", exc)
        return False
    finally:
        if owned:
            client.close()
    logger.info("combos: refresh complete in %.1fs", time.monotonic() - start)
    return True


# ── queries ────────────────────────────────────────────────────────────────


def _row_to_combo(row: Any) -> dict[str, Any]:
    combo_id, identity, description, produces, card_names, card_count, popularity, bracket_tag = row
    return {
        "id": combo_id,
        "identity": identity,
        "description": description,
        "produces": json.loads(produces or "[]"),
        "cards": json.loads(card_names or "[]"),
        "card_count": card_count,
        "popularity": popularity,
        "bracket_tag": bracket_tag,
    }


_SELECT = "SELECT id, identity, description, produces, card_names, card_count, popularity, bracket_tag FROM combos"


def combos_in_deck(card_names: Iterable[str], *, limit: int = 50) -> list[dict[str, Any]]:
    """Combos whose every card is in the deck, most popular first."""
    names = sorted({n.lower() for n in card_names if n})
    if not names:
        return []
    out: list[dict[str, Any]] = []
    try:
        with get_engine().begin() as conn:
            for i in range(0, len(names), 400):
                chunk = names[i : i + 400]
                placeholders = ", ".join(f":n{j}" for j in range(len(chunk)))
                params: dict[str, Any] = {f"n{j}": v for j, v in enumerate(chunk)}
                rows = conn.execute(text(f"""
                    {_SELECT}
                    WHERE id IN (
                        SELECT cc.combo_id FROM combo_cards cc
                        JOIN combos c ON c.id = cc.combo_id
                        WHERE cc.name_lower IN ({placeholders})
                        GROUP BY cc.combo_id
                        HAVING count(*) = c.card_count
                    )
                """), params).fetchall()
                out.extend(_row_to_combo(r) for r in rows)
    except Exception:  # noqa: BLE001 - no table yet, or a bad row
        return []
    seen: set[str] = set()
    unique = [c for c in out if not (c["id"] in seen or seen.add(c["id"]))]
    unique.sort(key=lambda c: (-(c["popularity"] or 0), c["card_count"]))
    return unique[:limit]


def combos_one_short(
    deck_card_names: Iterable[str], candidate_names: Iterable[str]
) -> dict[str, list[dict[str, Any]]]:
    """For each candidate, the combos it would complete with the deck's cards.

    Keyed by the candidate's lowercased name. A combo counts when every card
    but the candidate is already in the deck, so the answer is "add this and
    you have it", not "this is in a combo with something you might get".
    """
    deck = {n.lower() for n in deck_card_names if n}
    candidates = sorted({n.lower() for n in candidate_names if n} - deck)
    if not deck or not candidates:
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    try:
        with get_engine().begin() as conn:
            for i in range(0, len(candidates), 400):
                chunk = candidates[i : i + 400]
                placeholders = ", ".join(f":c{j}" for j in range(len(chunk)))
                params: dict[str, Any] = {f"c{j}": v for j, v in enumerate(chunk)}
                rows = conn.execute(text(f"""
                    SELECT cc.name_lower, c.id, c.identity, c.description, c.produces,
                           c.card_names, c.card_count, c.popularity, c.bracket_tag
                    FROM combo_cards cc JOIN combos c ON c.id = cc.combo_id
                    WHERE cc.name_lower IN ({placeholders})
                """), params).fetchall()
                for row in rows:
                    candidate = row[0]
                    combo = _row_to_combo(row[1:])
                    others = {n.lower() for n in combo["cards"]} - {candidate}
                    if others and others <= deck:
                        out.setdefault(candidate, []).append(combo)
    except Exception:  # noqa: BLE001
        return {}
    for combos in out.values():
        combos.sort(key=lambda c: -(c["popularity"] or 0))
    return out
