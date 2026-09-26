"""The pipeline orchestrator: build_suggestions() wires stages 1 through 5.

This is the single entry point the chat engine (or a future /suggest route) calls.
It is deliberately decoupled from the chat loop — it takes a session, a deck, an
intent, and a provider, and returns a SuggestionResult. How it gets *triggered*
(auto-router, explicit command, tool call) is a separate, deferred decision; the
pipeline itself is pure orchestration over the stage modules.

Flow:
  1. spec       generate_query_spec(provider, intent, identity)      -> QuerySpec
  2. candidates gather_candidates(spec, commander)                   -> [raw card]
  3. shaping    shape(pool, deck_ctx, tags) + render_pool            -> [ShapedCard]
  4. selection  select(provider, shaped, intent)                     -> Selection
  5. validate   validate_to_proposals(session, deck, selection)      -> proposals

Both LLM stages route to the provider's fast model by default (see
``build_suggestions`` and PIPELINE.md): stage 1 with thinking off, stage 4 with
thinking on at a capped reasoning effort.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlmodel import Session

from app import deckplan
from app.brainmap import layers as brain_layers
from app.brainmap import map as brain_map
from app.cards import schema as card_schema
from app.cards import store as card_store
from app.db import repository as repo
from app.knowledge.tag_lookup import get_tag_lookup
from app.pipeline import candidates as candidates_stage
from app.pipeline import local_retrieval
from app.pipeline import selection as selection_stage
from app.pipeline import spec as spec_stage
from app.pipeline.shaping import DeckContext, shape
from app.pipeline.validate import validate_to_proposals

logger = logging.getLogger(__name__)


@contextmanager
def _timed(stage: str, timings: dict[str, float]):
    """Time a pipeline stage and record it. Logs a WARNING if a single stage
    runs long (>20s) so an intermittent stall is obvious in the console and
    points at exactly which stage hung."""
    start = time.monotonic()
    logger.info("pipeline: %s start", stage)
    try:
        yield
    finally:
        dt = time.monotonic() - start
        timings[stage] = round(dt, 2)
        level = logging.WARNING if dt > 20 else logging.INFO
        logger.log(level, "pipeline: %s done in %.2fs", stage, dt)


@dataclass
class SuggestionResult:
    """The pipeline's output. ``proposals`` is the exact shape the engine already
    streams as deck_proposal events, so terminal integration adds no new SSE
    vocabulary. ``selection`` is kept for preview/inspection; ``debug`` carries
    the intermediate sizes for logging and golden tests."""

    summary: str
    proposals: list[dict[str, Any]] = field(default_factory=list)
    selection: selection_stage.Selection | None = None
    debug: dict[str, Any] = field(default_factory=dict)


def _commander_identity(snapshot: dict[str, Any], scryfall: Any | None = None) -> frozenset[str]:
    """Union the colour identities of the deck's commander card(s).

    Reads first from the snapshot's own cards (each carries its stored
    color_identity string, e.g. "BR"), so the common path needs no extra call.
    A deck with no commander yet yields the empty (colourless) identity, which
    legal_in_deck treats as "only colourless cards are legal" — correct for an
    unset commander.

    Robustness for imports: if a commander IS named but its identity comes back
    empty from the decklist (some import paths may not populate color_identity,
    or the commander card isn't in the list), fall back to a Scryfall lookup.
    Without this, an empty identity would silently mark every colored suggestion
    illegal and return an empty pool — a total, silent failure on exactly the
    partial-import decks this needs to work for.
    """
    commander_names = [
        n for n in (snapshot.get("commander"), snapshot.get("partner_commander")) if n
    ]
    commander_lower = {n.lower() for n in commander_names}

    letters: set[str] = set()
    for card in snapshot.get("cards", []):
        if (card.get("name") or "").lower() in commander_lower:
            for ch in (card.get("color_identity") or ""):
                if ch.strip():
                    letters.add(ch.upper())

    if not letters and commander_names:
        from app.tools.scryfall_client import ScryfallError, get_scryfall_client

        client = scryfall or get_scryfall_client()
        for name in commander_names:
            try:
                card = client.named(name, fuzzy=True)
            except ScryfallError:
                continue
            for ch in (card.get("color_identity") or []):
                if ch and ch.strip():
                    letters.add(ch.upper())

    return frozenset(letters)


def _render_deck_context(snapshot: dict[str, Any], max_cards: int = 120) -> str:
    """A compact description of the deck for the selection stage: commander(s)
    with oracle text (the key combo/fit signal), the current cards grouped by
    category (names only — full oracle text for 99 cards would blow up the
    prompt), and any strategy notes. Small on purpose so it adds context without
    the latency of dumping the whole deck's rules text."""
    lines: list[str] = []

    commander = snapshot.get("commander")
    partner = snapshot.get("partner_commander")
    cards = snapshot.get("cards", [])
    by_name = {(c.get("name") or "").lower(): c for c in cards}

    def _commander_line(name: str) -> str:
        card = by_name.get(name.lower())
        text = (card.get("oracle_text") or "").strip() if card else ""
        text = " ".join(text.split())  # collapse newlines for compactness
        return f"- {name}: {text}" if text else f"- {name}"

    if commander:
        lines.append("Commander:")
        lines.append(_commander_line(commander))
        if partner:
            lines.append(_commander_line(partner))
    else:
        lines.append("Commander: not set yet.")

    commander_lower = {n.lower() for n in (commander, partner) if n}
    body = [c for c in cards if (c.get("name") or "").lower() not in commander_lower]
    if body:
        by_category: dict[str, list[str]] = {}
        for c in body[:max_cards]:
            cat = c.get("category") or "Other"
            by_category.setdefault(cat, []).append(c.get("name") or "")
        lines.append("")
        lines.append(f"Current deck ({len(body)} cards):")
        for cat in sorted(by_category):
            names = ", ".join(n for n in by_category[cat] if n)
            lines.append(f"- {cat}: {names}")
    else:
        lines.append("")
        lines.append("Current deck: empty (just the commander so far).")

    notes = (snapshot.get("notes") or "").strip()
    if notes:
        lines.append("")
        lines.append(f"Strategy notes: {notes}")

    # The plan: targets, direction, and what the deck is still short on. Without
    # this the model can only see what the deck CONTAINS and has to guess what it
    # WANTS, so a suggestion aimed at a filled role looked as good as one filling
    # a gap.
    plan = deckplan.build_plan(snapshot)
    lines.append("")
    lines.append(deckplan.render_plan(plan))

    # Why the existing cards are there. Each card's stored note is the reasoning
    # from the proposal that added it, so the model can build on prior decisions
    # instead of re-deriving them.
    rationale = deckplan.render_card_rationale(snapshot.get("cards", []))
    if rationale:
        lines.append("")
        lines.append(rationale)

    return "\n".join(lines)


def _deck_off_meta(snapshot: dict[str, Any]) -> float:
    """How far off-consensus this deck wants to be, 0.0 to 1.0.

    Defaults to 0.25: mostly follows what people play, but leans far enough
    toward commander-specific synergy that two decks on the same commander do
    not return identical pools. A deck can set its own value.
    """
    value = snapshot.get("off_meta")
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    return 0.25


def _apply_brain_map(
    session: Session,
    pool: list[dict[str, Any]],
    snapshot: dict[str, Any],
    identity: frozenset[str],
    deck_id: int,
    off_meta: float,
) -> list[dict[str, Any]]:
    """Rank the pool through the brain map before it is capped.

    Order matters: shaping caps the pool, so scoring has to happen first or the
    cap would discard cards the map would have ranked highest. Fully guarded —
    a scoring failure leaves the pool in its original order, which is the
    pre-brain-map behaviour and still perfectly usable.
    """
    try:
        context = brain_layers.ScoringContext(
            commander=snapshot.get("commander"),
            identity=identity,
            themes=[t for t in (snapshot.get("themes") or []) if t],
            deck_card_names=frozenset(
                (c.get("name") or "").lower() for c in snapshot.get("cards", [])
            ),
            deck_id=deck_id,
        )
        ranked = brain_map.score_pool(
            pool, context,
            store=card_store, engine=session.get_bind(), off_meta=off_meta,
        )
        return brain_map.annotate_pool(pool, ranked)
    except Exception as exc:  # noqa: BLE001 — ranking is an improvement, not a precondition
        logger.warning("brain map scoring failed, using unranked pool: %s", exc)
        return pool


def _with_combo_partners(
    ctx: DeckContext, snapshot: dict[str, Any], pool: list[dict[str, Any]]
) -> DeckContext:
    """Annotate the context with the combos each candidate would complete.

    A candidate that finishes a combo with cards already in the deck is the
    strongest fit signal the pipeline has, and the one the model most often
    guessed at. Guarded: no combo table, no annotation.
    """
    try:
        from dataclasses import replace

        from app.cards import combos as combo_db

        deck_names = [c.get("name") or "" for c in snapshot.get("cards", [])]
        one_short = combo_db.combos_one_short(deck_names, [c.get("name") or "" for c in pool])
        if not one_short:
            return ctx
        partners = {
            name: sorted({
                partner for combo in combos for partner in combo["cards"]
                if partner.lower() != name
            })[:3]
            for name, combos in one_short.items()
        }
        return replace(ctx, combo_partners=partners)
    except Exception as exc:  # noqa: BLE001 - combos are a bonus
        logger.warning("combo annotation skipped: %s", exc)
        return ctx


def _tags_for_pool(pool: list[dict[str, Any]]) -> dict[str, set[str]]:
    """Build the oracle_id -> tag slugs map shaping needs, for just this pool's
    cards.

    Reads the local card index, which stores taggings as rows, so this touches
    the few dozen cards in the pool instead of rebuilding a ~229k-entry dict in
    memory on first call. Falls back to the legacy gzip-backed lookup when the
    index hasn't been built yet (first run, or a failed refresh) so a cold start
    still produces roles rather than an untagged pool.
    """
    oracle_ids = [c.get("oracle_id") for c in pool if c.get("oracle_id")]
    if not oracle_ids:
        return {}

    try:
        if card_schema.tag_count() > 0:
            return card_store.tags_for_many(oracle_ids)
    except Exception as exc:  # noqa: BLE001 — index problems must not sink retrieval
        logger.warning("card index tag lookup failed, falling back to bulk cache: %s", exc)

    lookup = get_tag_lookup()
    return {oid: lookup.get(oid, set()) for oid in oracle_ids}


@dataclass
class PreparedPool:
    """Stages 1-3 done: everything stage 4 needs, so two selection backends can
    be run against the identical pool (see tools/jev_compare.py)."""

    snapshot: dict[str, Any]
    spec: spec_stage.QuerySpec
    gathered: Any
    local_pool: list[dict[str, Any]]
    stage1_skipped: bool
    pool: list[dict[str, Any]]
    shaped: list[Any]
    deck_context: str
    timings: dict[str, float]


def prepare_pool(
    session: Session,
    deck_id: int,
    user_intent: str,
    provider: Any,
    *,
    model: str,
    spec_thinking: bool = False,
    max_queries: int = 6,
    pool_cap: int = 60,
    scryfall: Any | None = None,
    edhrec: Any | None = None,
    off_meta: float | None = None,
    local_store: Any | None = None,
    local_pool_min: int = 25,
    local_role_limit: int = 80,
    edhrec_by_role: bool = True,
    theme_whole_page: bool = False,
    theme_pool_cap: int | None = None,
    # Off: measured +1 point role, 0 theme over commander-wide EDHREC
    # (docs/PIPELINE.md), not worth a second set of page fetches per commander.
    theme_signal: bool = False,
    timings: dict[str, float] | None = None,
) -> PreparedPool:
    """Run stages 1-3: retrieve, merge, score, and shape the candidate pool."""
    timings = {} if timings is None else timings
    snapshot = repo.deck_snapshot(session, deck_id)
    # The prompt has the model batch set_commander with the opening cards, and
    # the hand-pick guard sends those cards here. At that moment the commander
    # is a PENDING proposal, not a deck field, so the identity resolved to
    # colourless and every coloured candidate was marked illegal — the opening
    # batch came back as Sol Ring and friends. A proposed commander is the best
    # available statement of what the deck is, so use it until it is decided.
    if not snapshot.get("commander"):
        proposed = repo.pending_commander_for_deck(session, deck_id)
        if proposed:
            snapshot["commander"] = proposed
    identity = _commander_identity(snapshot, scryfall)
    # The budget in effect: the deck's own ceiling, else the one the player's
    # standing preference implies. Read here so the plan render and the
    # legality check agree on the number.
    snapshot["budget_preference"] = repo.get_or_create_preferences(session).budget
    ceiling = deckplan.price_ceiling(
        snapshot.get("max_card_price"), snapshot.get("budget_preference")
    )
    ctx = DeckContext.from_snapshot(snapshot, identity, max_card_price=ceiling)

    # Local first. The index answers the common intents (a role, a phrase in
    # rules text) in milliseconds with no model call and no network; the
    # model's query planning and Scryfall's API are the fallback for an intent
    # the index cannot fill. A pool that never leaves the machine is also a
    # pool that cannot be rate-limited or time out.
    with _timed("stage1_local", timings):
        local_pool: list[dict[str, Any]] = []
        try:
            local_pool = local_retrieval.retrieve(
                user_intent, identity, store=local_store or card_store,
                per_role_limit=local_role_limit,
            )
        except Exception as exc:  # noqa: BLE001 - fall back to the model
            logger.warning("local retrieval failed, falling back to query planning: %s", exc)
        logger.info("pipeline: local retrieval found %d candidate(s)", len(local_pool))

    stage1_skipped = len(local_pool) >= local_pool_min
    if stage1_skipped:
        spec = spec_stage.QuerySpec(queries=[], intent_summary=user_intent)
    else:
        with _timed("stage1_spec", timings):
            try:
                spec = spec_stage.generate_query_spec(
                    provider, user_intent, identity,
                    model=model, max_queries=max_queries, thinking=spec_thinking,
                )
            except ValueError as exc:
                # Unusable query planning used to fail the whole suggestion.
                # EDHREC's page and the local pool still stand without it.
                logger.warning("stage 1 failed, continuing without planned queries: %s", exc)
                spec = spec_stage.QuerySpec(queries=[], intent_summary=user_intent)

    intent_roles = local_retrieval.intent_roles(user_intent)
    whole_page = theme_whole_page and not intent_roles
    if theme_pool_cap and not intent_roles:
        pool_cap = theme_pool_cap
    with _timed("stage2_candidates", timings):
        gathered = candidates_stage.gather_candidates_detailed(
            spec, snapshot.get("commander"),
            scryfall=scryfall, edhrec=edhrec,
            identity=identity,
            off_meta=off_meta if off_meta is not None else _deck_off_meta(snapshot),
            edhrec_roles=intent_roles or None if edhrec_by_role else None,
            edhrec_whole_page=whole_page,
            # The raw cap was 120, and most of a page's top cards are already
            # in the deck (so illegal to suggest): a whole page capped at 120
            # left ~50 legal cards, fewer than the top-40 source it replaced.
            **({"cap": 500} if whole_page else {}),
        )
        # EDHREC's commander-specific picks lead, then the local hits, then
        # whatever the fallback queries added; dedupe keeps the first seen.
        seen = {c.get("oracle_id") for c in gathered.cards if c.get("oracle_id")}
        pool = list(gathered.cards)
        for card in local_pool:
            oid = card.get("oracle_id")
            if oid in seen:
                continue
            seen.add(oid)
            pool.append(card)

    if theme_signal:
        with _timed("stage2b_themes", timings):
            from app.pipeline import theme_fit
            from app.tools.edhrec_client import get_edhrec_client

            profile = theme_fit.build_profile(
                snapshot.get("commander"),
                [c.get("name") or "" for c in snapshot.get("cards", [])],
                edhrec or get_edhrec_client(),
            )
            theme_fit.annotate(pool, profile)

    with _timed("stage3_shape", timings):
        pool = _apply_brain_map(
            session, pool, snapshot, identity, deck_id,
            off_meta if off_meta is not None else _deck_off_meta(snapshot),
        )
        ctx = _with_combo_partners(ctx, snapshot, pool)
        shaped = shape(pool, ctx, _tags_for_pool(pool), cap=pool_cap)

    return PreparedPool(
        snapshot=snapshot, spec=spec, gathered=gathered, local_pool=local_pool,
        stage1_skipped=stage1_skipped, pool=pool, shaped=shaped,
        deck_context=_render_deck_context(snapshot), timings=timings,
    )


def run_selection(
    prepared: PreparedPool,
    user_intent: str,
    provider: Any,
    *,
    backend: str,
    model: str,
    select_thinking: bool = True,
    max_picks: int = 10,
    player_message: str | None = None,
    jev_client: Any | None = None,
    jev_mode: str | None = None,
    jev_samples: int | None = None,
    jev_explain: bool | None = None,
) -> tuple[selection_stage.Selection, str]:
    """Run stage 4 on the requested backend. Returns (selection, backend used):
    a failed Jev call logs and falls back to the LLM rather than failing the
    suggestion, and the second value says which one actually answered."""
    from app.config import settings

    if backend == "jev":
        from app.pipeline import jev

        selection = None
        try:
            client = jev_client or jev.get_client()
            selection = jev.select_jev(
                client, prepared.shaped, user_intent,
                max_picks=max_picks, deck_context=prepared.deck_context,
                player_message=player_message,
                mode=jev_mode or settings.jev_mode or "verdict",
                samples=jev_samples or settings.jev_samples or 1,
                max_similar=settings.jev_max_similar or None,
                min_probability=settings.jev_min_probability or None,
                cut_cards=jev.cut_candidates(prepared.snapshot) if settings.jev_cuts else None,
                cut_evidence_map=(jev.cut_evidence(prepared.snapshot.get("commander"))
                                  if settings.jev_cuts else None),
                deck_total=prepared.snapshot.get("total_cards"),
            )
        except Exception as exc:  # noqa: BLE001 - the LLM path is the fallback
            logger.warning("jev selection failed, falling back to llm: %s", exc)
        if selection is not None:
            explain_picks = settings.jev_explain if jev_explain is None else jev_explain
            if explain_picks:
                from app.pipeline import explain

                start = time.monotonic()
                try:
                    selection = explain.explain(
                        provider, selection, prepared.shaped, user_intent, model=model,
                        deck_context=prepared.deck_context, player_message=player_message,
                    )
                except Exception as exc:  # noqa: BLE001 - fact-built reasons still stand
                    logger.warning("explaining jev picks failed, keeping fact reasons: %s", exc)
                selection.raw["explain_seconds"] = round(time.monotonic() - start, 2)
            return selection, "jev"

    return selection_stage.select(
        provider, prepared.shaped, user_intent,
        model=model, max_picks=max_picks, thinking=select_thinking,
        deck_context=prepared.deck_context,
        reasoning_effort=settings.select_reasoning_effort or None,
        player_message=player_message,
    ), "llm"


def build_suggestions(
    session: Session,
    deck_id: int,
    user_intent: str,
    provider: Any,
    *,
    conversation_id: int | None = None,
    message_id: int | None = None,
    model: str | None = None,
    spec_thinking: bool | None = None,
    select_thinking: bool | None = None,
    max_queries: int = 6,
    pool_cap: int = 60,
    max_picks: int = 10,
    scryfall: Any | None = None,
    edhrec: Any | None = None,
    off_meta: float | None = None,
    local_store: Any | None = None,
    local_pool_min: int = 25,
    player_message: str | None = None,
    select_backend: str | None = None,
    jev_client: Any | None = None,
) -> SuggestionResult:
    """Run the full retrieval pipeline for a deck and intent.

    When ``conversation_id`` is given, the selection is turned into pending
    proposals (stage 5); without it, the result carries the raw selection for
    preview and ``proposals`` stays empty. ``scryfall``/``edhrec`` are injectable
    for testing.

    Model/thinking policy — the two LLM stages are NOT symmetric:
      * Stage 1 (query planning) is a mechanical intent->Scryfall-query mapping;
        the harness enforces legality regardless of what it writes. So it runs
        FAST model, thinking OFF.
      * Stage 4 (selection) is where deck-aware FIT judgment happens — the one
        thing Python can't do. It gets the commander + current deck as context
        (see _render_deck_context) and runs with thinking ON so it can actually
        reason about combos/synergy, still on the FAST model to stay responsive.
    Defaults trigger this policy; pass ``model``/``spec_thinking``/
    ``select_thinking`` to override (e.g. tests pin a fake provider).
    """
    if model is None:
        from app.llm.factory import get_fast_model
        model = get_fast_model()
    if spec_thinking is None:
        spec_thinking = False
    if select_thinking is None:
        select_thinking = True
    if select_backend is None:
        from app.config import settings
        select_backend = settings.select_backend or "llm"

    timings: dict[str, float] = {}
    overall_start = time.monotonic()
    logger.info(
        "pipeline: build_suggestions deck=%s intent=%r model=%s",
        deck_id, user_intent[:80], model,
    )

    prepared = prepare_pool(
        session, deck_id, user_intent, provider,
        model=model, spec_thinking=spec_thinking, max_queries=max_queries,
        pool_cap=pool_cap, scryfall=scryfall, edhrec=edhrec, off_meta=off_meta,
        local_store=local_store, local_pool_min=local_pool_min, timings=timings,
    )
    spec, gathered, shaped = prepared.spec, prepared.gathered, prepared.shaped
    local_pool, pool = prepared.local_pool, prepared.pool

    with _timed("stage4_select", timings):
        selection, backend_used = run_selection(
            prepared, user_intent, provider,
            backend=select_backend, model=model, select_thinking=select_thinking,
            max_picks=max_picks, player_message=player_message, jev_client=jev_client,
        )

    debug = {
        "queries": spec.queries,
        "intent_summary": spec.intent_summary,
        "broadened": gathered.broadened,
        "local_pool": len(local_pool),
        "stage1_skipped": prepared.stage1_skipped,
        "select_backend": backend_used,
        "pool_size": len(pool),
        "shaped_size": len(shaped),
        "legal_shaped": sum(1 for c in shaped if c.legal_in_deck),
        "pick_count": len(selection.picks),
        "timings": timings,
    }

    if conversation_id is None:
        logger.info(
            "pipeline: done (preview) in %.2fs timings=%s",
            time.monotonic() - overall_start, timings,
        )
        return SuggestionResult(
            summary=selection.summary, proposals=[], selection=selection, debug=debug,
        )

    # The brain map's verdict per card, so the review surface can show why a
    # pick scored the way it did. Built from the shaped pool because that is
    # the last point the scores and the card names are together.
    scores_by_name = {
        card.name.lower(): card.brainmap
        for card in shaped
        if card.name and card.brainmap
    }

    with _timed("stage5_validate", timings):
        result = validate_to_proposals(
            session, deck_id, selection,
            conversation_id=conversation_id, message_id=message_id,
            scores_by_name=scores_by_name,
        )
    logger.info(
        "pipeline: done in %.2fs timings=%s",
        time.monotonic() - overall_start, timings,
    )
    return SuggestionResult(
        summary=result["summary"],
        proposals=result["proposals"],
        selection=selection,
        debug=debug,
    )
