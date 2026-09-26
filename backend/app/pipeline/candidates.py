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

from app.cards import store as card_store
from app.pipeline import edhrec_source, roles
from app.pipeline.broaden import search_with_broadening
from app.pipeline.spec import QuerySpec
from app.tools.edhrec_client import EdhrecError, get_edhrec_client
from app.tools.scryfall_client import get_scryfall_client

logger = logging.getLogger(__name__)

_RANK_LAST = float("inf")


def _edhrec_recommendations(
    commander_name: str | None,
    client: Any,
    identity: frozenset[str] | None,
    *,
    off_meta: float,
    cap: int,
    roles_wanted: set[str] | None = None,
    role_cap: int = 60,
    whole_page: bool = False,
) -> list[dict[str, Any]]:
    """Pull the commander's EDHREC page in as candidates, hydrated locally.

    ``roles_wanted`` makes it answer the request. The top ``cap`` of the page
    is intent-blind: for "more ramp" it is the commander's best cards of every
    kind, and the commander-specific ramp further down the page never entered
    the pool. With roles given, every card on the whole page that fills one of
    them goes in first (up to ``role_cap``). Measured: 54 of 84 held-out cards
    that never reached the pool were on the page past the per-list cut.

    This is the half that was missing: the page was fetched only to annotate
    cards Scryfall had already returned, so a card EDHREC recommends that no
    query matched could never enter the pool. Degrades to an empty list on any
    failure — EDHREC is the best signal available but must never be a hard
    dependency of retrieval.
    """
    if not commander_name:
        return []
    try:
        recs = client.commander_recs(commander_name)
    except EdhrecError:
        return []
    except Exception as exc:  # defensive: EDHREC's shape is unofficial
        logger.warning("EDHREC recommendations failed for %r: %s", commander_name, exc)
        return []

    try:
        collected = edhrec_source.collect(recs)
        ranked = edhrec_source.rank(collected, off_meta=off_meta)
        if whole_page:
            # A request that names no role ("what fits my commander") is a
            # request for the page itself: 46 of 72 held-out theme cards that
            # never reached the pool were on it, past the top-``cap`` cut.
            return edhrec_source.hydrate(
                edhrec_source.rank(
                    edhrec_source.collect(recs, per_list_cap=10_000), off_meta=off_meta,
                ),
                card_store, identity,
            )
        general = edhrec_source.hydrate(ranked[:cap], card_store, identity)
        if not roles_wanted:
            return general
        whole_page = edhrec_source.rank(
            edhrec_source.collect(recs, per_list_cap=10_000), off_meta=off_meta,
        )
        hydrated = edhrec_source.hydrate(whole_page, card_store, identity)
        tags = card_store.tags_for_many([c["oracle_id"] for c in hydrated if c.get("oracle_id")])
        on_request = [
            c for c in hydrated
            if roles.fine_roles_for_tags(tags.get(c.get("oracle_id"), set()), c.get("type_line"))
            & roles_wanted
        ][:role_cap]
        seen = {c.get("oracle_id") for c in on_request}
        return on_request + [c for c in general if c.get("oracle_id") not in seen]
    except Exception as exc:  # noqa: BLE001 — never sink retrieval
        logger.warning("EDHREC hydration failed for %r: %s", commander_name, exc)
        return []


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
    identity: frozenset[str] | None = None,
    off_meta: float = 0.0,
    edhrec_cap: int = 40,
    edhrec_roles: set[str] | None = None,
    edhrec_whole_page: bool = False,
) -> CandidateResult:
    """Run the spec's queries, merge/dedupe/annotate/cap into a raw candidate pool.

    Two sources feed the pool. EDHREC's recommendations for the commander go in
    FIRST — they are the only signal that knows what actually goes in this
    specific deck, and they used to be unable to contribute a candidate at all.
    Scryfall query results follow. Dedupe is by oracle_id keeping the first
    occurrence, so an EDHREC-sourced card keeps its synergy annotation.

    ``off_meta`` (0.0-1.0) trades EDHREC play rate against commander-specific
    synergy when ranking recommendations. At 0 it follows consensus; at 1 it
    surfaces cards specific to this commander that few decks run. This is what
    keeps decks from converging on the same hundred cards.

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
    recommendations = _edhrec_recommendations(
        commander_name, edhrec, identity, off_meta=off_meta, cap=edhrec_cap,
        roles_wanted=edhrec_roles, whole_page=edhrec_whole_page,
    )
    edhrec_dt = time.monotonic() - edhrec_start
    if edhrec_dt > 5:
        logger.warning("EDHREC lookup for %r took %.2fs", commander_name, edhrec_dt)

    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    broadened: dict[str, list[str]] = {}

    # EDHREC first: these carry the commander-specific signal, and being first
    # means dedupe keeps their annotation rather than a bare Scryfall hit.
    # `_edhrec_position` preserves the off-meta ranking through the merge sort
    # and is stripped before the pool is returned.
    for position, card in enumerate(recommendations):
        oid = card.get("oracle_id")
        if oid is not None:
            if oid in seen:
                continue
            seen.add(oid)
        card["_edhrec_position"] = position
        merged.append(card)
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

    # Sort EDHREC-sourced cards ahead of query hits, preserving the order
    # `edhrec_source.rank` put them in; query hits then follow by global EDHREC
    # rank. A recommendation is ranked by how well it fits THIS commander, while
    # `edhrec_rank` is global popularity that knows nothing about the deck, so
    # ranking the two together scatters the commander-specific picks among
    # generically-popular ones and undoes the off-meta weighting entirely.
    #
    # Source is tracked explicitly rather than inferred from the presence of an
    # `edhrec` key: query hits ALSO carry one (from the synergy map), so a
    # truthiness check put both groups in the same bucket and sorted generic
    # ramp above the high-synergy picks.
    def _sort_key(card: dict[str, Any]) -> tuple[int, float]:
        position = card.get("_edhrec_position")
        if position is not None:
            return (0, float(position))
        rank = card.get("edhrec_rank")
        return (1, rank if rank is not None else _RANK_LAST)

    merged.sort(key=_sort_key)
    for card in merged:
        card.pop("_edhrec_position", None)
    if recommendations:
        logger.info(
            "EDHREC contributed %d candidate(s) for %r (off_meta=%.2f)",
            len(recommendations), commander_name, off_meta,
        )
    if broadened:
        logger.info("broadened %d of %d queries", len(broadened), len(spec.queries))
    return CandidateResult(cards=merged[:cap], broadened=broadened)
