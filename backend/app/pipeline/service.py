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

Stages 1 and 4 run on the provider default (Pro, per the "Pro everywhere"
decision); the Flash seam exists but nothing routes to it here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlmodel import Session

from app.db import repository as repo
from app.knowledge.tag_lookup import get_tag_lookup
from app.pipeline import candidates as candidates_stage
from app.pipeline import selection as selection_stage
from app.pipeline import spec as spec_stage
from app.pipeline.shaping import DeckContext, shape
from app.pipeline.validate import validate_to_proposals


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

    return "\n".join(lines)


def _tags_for_pool(pool: list[dict[str, Any]]) -> dict[str, set[str]]:
    """Build the oracle_id -> tag slugs map shaping needs, for just this pool's
    cards. Reads the shared tag cache once; a card absent from it gets no tags
    (shaping still assigns the land role from its type line)."""
    lookup = get_tag_lookup()
    tags: dict[str, set[str]] = {}
    for card in pool:
        oid = card.get("oracle_id")
        if oid and oid not in tags:
            tags[oid] = lookup.get(oid, set())
    return tags


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

    snapshot = repo.deck_snapshot(session, deck_id)
    identity = _commander_identity(snapshot, scryfall)
    ctx = DeckContext.from_snapshot(snapshot, identity)

    spec = spec_stage.generate_query_spec(
        provider, user_intent, identity,
        model=model, max_queries=max_queries, thinking=spec_thinking,
    )

    pool = candidates_stage.gather_candidates(
        spec, snapshot.get("commander"),
        scryfall=scryfall, edhrec=edhrec,
    )

    shaped = shape(pool, ctx, _tags_for_pool(pool), cap=pool_cap)

    selection = selection_stage.select(
        provider, shaped, user_intent,
        model=model, max_picks=max_picks, thinking=select_thinking,
        deck_context=_render_deck_context(snapshot),
    )

    debug = {
        "queries": spec.queries,
        "intent_summary": spec.intent_summary,
        "pool_size": len(pool),
        "shaped_size": len(shaped),
        "legal_shaped": sum(1 for c in shaped if c.legal_in_deck),
        "pick_count": len(selection.picks),
    }

    if conversation_id is None:
        return SuggestionResult(
            summary=selection.summary, proposals=[], selection=selection, debug=debug,
        )

    result = validate_to_proposals(
        session, deck_id, selection,
        conversation_id=conversation_id, message_id=message_id,
    )
    return SuggestionResult(
        summary=result["summary"],
        proposals=result["proposals"],
        selection=selection,
        debug=debug,
    )
