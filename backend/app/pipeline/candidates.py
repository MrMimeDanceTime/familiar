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
import time
from dataclasses import dataclass, field
from typing import Any

from app.pipeline.broaden import search_with_broadening
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


@dataclass
class CandidateResult:
    """The stage-2 pool plus how it was obtained.

    ``broadened`` maps an original query to the relaxed queries that were tried
    after it underfilled, so a suggestion that quietly widened its search is
    inspectable in debug output rather than only in the logs.
    """

    cards: list[dict[str, Any]]
    broadened: dict[str, list[str]] = field(default_factory=dict)


def gather_candidates(
    spec: QuerySpec,
    commander_name: str | None = None,
    *,
    scryfall: Any | None = None,
    edhrec: Any | None = None,
    per_query_limit: int = 50,
    cap: int = 120,
    min_hits: int = 8,
    broaden: bool = True,
) -> list[dict[str, Any]]:
    """Backward-compatible wrapper returning just the card pool.

    Prefer ``gather_candidates_detailed`` when the caller wants the broadening
    trail; this keeps the original signature for existing callers and tests.
    """
    return gather_candidates_detailed(
        spec, commander_name, scryfall=scryfall, edhrec=edhrec,
        per_query_limit=per_query_limit, cap=cap, min_hits=min_hits,
        broaden=broaden,
    ).cards


def gather_candidates_detailed(
    spec: QuerySpec,
    commander_name: str | None = None,
    *,
    scryfall: Any | None = None,
    edhrec: Any | None = None,
    per_query_limit: int = 50,
    cap: int = 120,
    min_hits: int = 8,
    broaden: bool = True,
) -> CandidateResult:
    """Run the spec's queries, merge/dedupe/annotate/cap into a raw candidate pool.

    Dedupe is by oracle_id, keeping the first occurrence — since each query is
    EDHREC-ordered and queries are run best-intent-first, the first hit is the
    best-ranked. The merged pool is sorted by EDHREC rank (most-played first,
    unranked last) and capped. Each card is annotated with an ``edhrec`` dict
    (synergy/inclusion/category) when the commander's page lists it.

    An underfilled query is retried with one constraint relaxed (see
    ``pipeline.broaden``). Stage 1 emits queries blind — it never sees a result
    count — so without this an over-tight query silently contributes nothing and
    nothing downstream can recover. Broadening never touches colour identity or
    format, so it cannot leak an illegal card into the pool.
    """
    scryfall = scryfall or get_scryfall_client()
    edhrec = edhrec or get_edhrec_client()

    edhrec_start = time.monotonic()
    synergy = _edhrec_synergy_map(commander_name, edhrec)
    edhrec_dt = time.monotonic() - edhrec_start
    if edhrec_dt > 5:
        logger.warning("EDHREC lookup for %r took %.2fs", commander_name, edhrec_dt)

    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    broadened: dict[str, list[str]] = {}
    for query in spec.queries:
        q_start = time.monotonic()
        if broaden:
            hits, trail = search_with_broadening(
                lambda q: scryfall.search_pipeline(q, limit=per_query_limit),
                query,
                min_hits=min_hits,
            )
            if len(trail) > 1:
                broadened[query] = trail[1:]
        else:
            hits = scryfall.search_pipeline(query, limit=per_query_limit)
        q_dt = time.monotonic() - q_start
        if q_dt > 5:
            logger.warning("Scryfall query took %.2fs: %s", q_dt, query)
        for card in hits:
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
    if broadened:
        logger.info("broadened %d of %d queries", len(broadened), len(spec.queries))
    return CandidateResult(cards=merged[:cap], broadened=broadened)
