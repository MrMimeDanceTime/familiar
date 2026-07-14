"""Stage 2 — candidate generation. Deterministic, no LLM.

Runs the stage-1 queries against Scryfall, merges the results into one pool,
dedupes by oracle_id, and annotates each card with EDHREC synergy/inclusion
where the commander's page has it. The merged pool is capped by EDHREC rank so
the downstream shaping stage never sees hundreds of cards.

Clients are injected so this is testable with fakes; EDHREC failures degrade to
an empty synergy map rather than sinking the whole retrieval — a commander with
no EDHREC page still gets a Scryfall-driven pool.
"""

from __future__ import annotations

import logging
from typing import Any

from app.pipeline.spec import QuerySpec
from app.tools.edhrec_client import EdhrecError, get_edhrec_client
from app.tools.scryfall_client import get_scryfall_client

logger = logging.getLogger(__name__)

_RANK_LAST = float("inf")


def _edhrec_synergy_map(commander_name: str | None, client: Any) -> dict[str, dict[str, Any]]:
    """Return ``lower(name) -> {synergy, inclusion, category}`` for a commander.

    Empty on any failure (no page, shape drift, network) — synergy is a bonus
    signal, never a hard dependency of retrieval.
    """
    if not commander_name:
        return {}
    try:
        recs = client.commander_recs(commander_name)
    except EdhrecError:
        return {}
    except Exception as exc:  # defensive: EDHREC's shape is unofficial
        logger.warning("EDHREC lookup failed for %r: %s", commander_name, exc)
        return {}

    out: dict[str, dict[str, Any]] = {}
    for category in (recs.get("categories") or {}).values():
        header = category.get("header") if isinstance(category, dict) else None
        for card in (category.get("cards") or []):
            name = (card.get("name") or "").lower()
            if not name or name in out:
                continue
            out[name] = {
                "synergy": card.get("synergy"),
                "inclusion": card.get("inclusion"),
                "category": header,
            }
    return out


def gather_candidates(
    spec: QuerySpec,
    commander_name: str | None = None,
    *,
    scryfall: Any | None = None,
    edhrec: Any | None = None,
    per_query_limit: int = 50,
    cap: int = 120,
) -> list[dict[str, Any]]:
    """Run the spec's queries, merge/dedupe/annotate/cap into a raw candidate pool.

    Dedupe is by oracle_id, keeping the first occurrence — since each query is
    EDHREC-ordered and queries are run best-intent-first, the first hit is the
    best-ranked. The merged pool is sorted by EDHREC rank (most-played first,
    unranked last) and capped. Each card is annotated with an ``edhrec`` dict
    (synergy/inclusion/category) when the commander's page lists it.
    """
    scryfall = scryfall or get_scryfall_client()
    edhrec = edhrec or get_edhrec_client()

    synergy = _edhrec_synergy_map(commander_name, edhrec)

    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for query in spec.queries:
        for card in scryfall.search_pipeline(query, limit=per_query_limit):
            oid = card.get("oracle_id")
            if oid is not None:
                if oid in seen:
                    continue
                seen.add(oid)
            annotated = dict(card)
            annotated["edhrec"] = synergy.get((card.get("name") or "").lower())
            merged.append(annotated)

    merged.sort(
        key=lambda c: c["edhrec_rank"] if c.get("edhrec_rank") is not None else _RANK_LAST
    )
    return merged[:cap]
