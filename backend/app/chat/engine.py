"""The agentic chat loop: load history, call the provider, dispatch any
tool calls, persist everything, and yield SSE-ready events as it goes.

Per the v1 streaming decision, only the final text-bearing turn streams
token-by-token (simulated here by yielding the whole text as one token
event, since neither provider SDK is wired for token-level streaming
yet — intermediate tool-calling turns are non-streamed request/response
with a tool_call event for the UI's loading indicator).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Iterator

from sqlmodel import Session

from app.chat.prompt import build_system_prompt
from app.chat.streaming import (
    deck_proposal_event,
    deck_updated_event,
    done_event,
    error_event,
    token_event,
    tool_call_event,
)
from app.db import repository as repo
from app.llm.base import ChatProvider, ToolResult
from app.tools.dispatch import DECK_MUTATION_TOOLS, DECK_SCOPED_TOOLS, PROPOSAL_TOOLS, dispatch
from app.tools.schemas import TOOL_SPECS

logger = logging.getLogger("app.chat.engine")

MAX_TOOL_ITERATIONS = 12

# After a batch is proposed, the only tool the model may still call is
# withdraw_pending_proposals — so it can TRIM cards it reconsiders, but never
# add another batch or run more research. This is what stops the
# propose -> withdraw -> propose churn that used to burn the iteration budget.
WITHDRAW_ONLY_TOOLS = [t for t in TOOL_SPECS if t.name == "withdraw_pending_proposals"]


def _load_history(session: Session, conversation_id: int) -> list[dict[str, Any]]:
    messages = repo.list_messages(session, conversation_id)
    history: list[dict[str, Any]] = []
    for msg in messages:
        history.extend(msg.provider_native or [])
    return history


def run_chat_turn(
    session: Session,
    provider: ChatProvider,
    conversation_id: int,
    user_text: str,
    deck_id: int | None,
) -> Iterator[str]:
    """Run one user turn through the agentic loop, yielding formatted SSE strings."""
    history = _load_history(session, conversation_id)
    history = provider.append_user_message(history, user_text)

    sequence = repo.next_sequence(session, conversation_id)
    if sequence == 0:
        repo.set_conversation_title(session, conversation_id, _derive_title(user_text))
    repo.add_message(
        session,
        conversation_id,
        role="user",
        sequence=sequence,
        text_content=user_text,
        provider_native=[{"role": "user", "content": user_text}],
    )
    sequence += 1

    # Determine format for the system prompt. Default to commander if the
    # deck isn't found or no deck_id is set (shouldn't happen in practice
    # since every conversation gets a deck at creation time).
    format_key = "commander"
    if deck_id is not None:
        deck = repo.get_deck(session, deck_id)
        if deck:
            format_key = deck.format

    prefs = repo.get_or_create_preferences(session)
    system_prompt = build_system_prompt(
        format_key,
        preferred_bracket=prefs.preferred_bracket,
        preferred_power=prefs.preferred_power,
        budget=prefs.budget,
        rule0_notes=prefs.rule0_notes,
        build_preferences=prefs.build_preferences,
    )
    grounding = _deck_grounding(session, deck_id)
    if grounding:
        system_prompt = f"{system_prompt}\n\n{grounding}"

    # Proposals are created mid-turn (during a tool-call iteration) but the
    # message the player actually sees for that turn is the *final* assistant
    # text response ("here's the ramp package"), not the internal, text-less
    # tool-call message. Accumulate proposal ids across the whole turn and
    # anchor them to that final message so the UI renders the batch inline
    # under the bubble that introduced it, rather than pooling at the bottom.
    proposal_ids_this_turn: list[int] = []
    # Once a batch of proposals has been emitted this turn, the turn is done
    # ADDING to the deck — the player reviews via UI buttons. The prompt asks
    # the model to stop, but it sometimes churns (propose -> withdraw -> propose
    # ...), burning the iteration budget and dropping the turn. So we ENFORCE a
    # softer version of "stop": after a batch exists, the model is offered ONLY
    # withdraw_pending_proposals, so it can trim a card or two it reconsiders but
    # cannot add another batch or run more research. A pre-proposal withdraw
    # (clearing a prior turn's stale batch) is untouched.
    proposals_emitted = False
    # Deferred reveal: proposals are NOT streamed to the UI the moment they're
    # created, because the model may still trim some this turn — the player
    # would otherwise watch cards appear then vanish. Instead we hold the batch
    # summary and emit ONE deck_proposal event at turn end, carrying only the
    # proposals still pending after any trims.
    pending_summary = ""
    try:
        turn_started = time.perf_counter()
        for iteration in range(MAX_TOOL_ITERATIONS):
            # Thinking mode roughly doubles per-call latency and the chat loop is
            # mostly mechanical tool-dispatch plus narration of decisions already
            # made through the tool sequence — the deep reasoning lives in the
            # tool choices and in the pipeline/nuance calls (which keep thinking
            # on). Turning it off here is the biggest lever on perceived turn lag.
            tools_for_turn = WITHDRAW_ONLY_TOOLS if proposals_emitted else TOOL_SPECS
            send_started = time.perf_counter()
            turn = provider.send(system_prompt, history, tools_for_turn, thinking=False)
            send_dt = time.perf_counter() - send_started
            tool_names = [c.name for c in turn.tool_calls] if turn.tool_calls else []
            logger.info(
                "chat: turn iter %d send done in %.2fs (%s)",
                iteration, send_dt,
                f"calls: {', '.join(tool_names)}" if tool_names else "final text",
            )

            if not turn.tool_calls:
                logger.info(
                    "chat: turn complete in %.2fs over %d LLM call(s)",
                    time.perf_counter() - turn_started, iteration + 1,
                )
                yield from _finalize_turn(
                    session, conversation_id, sequence,
                    turn.text, turn.raw_assistant_message,
                    proposal_ids_this_turn, pending_summary,
                )
                return

            results: list[ToolResult] = []
            tool_calls_log = []
            tool_results_log = []
            for call in turn.tool_calls:
                yield tool_call_event(call.name, call.arguments)

                args = dict(call.arguments)
                # The conversation's deck is authoritative — override whatever
                # deck_id the model supplied. deck_id is a required tool param,
                # so the model always guesses one; deferring to its guess made
                # deck-scoped tools read the wrong deck (or none).
                if call.name in DECK_SCOPED_TOOLS and deck_id is not None:
                    args["deck_id"] = deck_id

                if call.name in PROPOSAL_TOOLS:
                    args["conversation_id"] = conversation_id

                result = dispatch(call.name, args, session, provider=provider)
                content = str(result.content) if not result.ok else _serialize(result.content)
                results.append(ToolResult(call_id=call.id, content=content))

                tool_calls_log.append({"id": call.id, "name": call.name, "arguments": call.arguments})
                tool_results_log.append({"call_id": call.id, "content": content})

                if result.ok and call.name in PROPOSAL_TOOLS:
                    batch = result.content.get("proposals", [])
                    for p in batch:
                        if p.get("id") is not None:
                            proposal_ids_this_turn.append(p["id"])
                    # Only a batch that actually created proposals restricts the
                    # turn to withdraw-only. An empty batch (e.g. the pipeline
                    # found nothing) shouldn't strand the model.
                    if batch:
                        proposals_emitted = True
                        pending_summary = result.content.get("summary") or pending_summary
                    # NB: no deck_proposal event here — the batch is revealed once,
                    # settled, at turn end (see _settled_proposal_batch). This
                    # keeps trimmed cards from flashing into the UI and back out.
                elif result.ok and call.name in DECK_MUTATION_TOOLS and deck_id is not None:
                    yield deck_updated_event(result.content)

            new_history = provider.append_tool_results(history, turn, results)
            # provider.append_tool_results appends the assistant's tool-call
            # message followed by one or more tool-result-bearing messages
            # (Anthropic bundles all results into a single user message;
            # OpenAI-style providers emit one tool message per call). Persist
            # the assistant entry and all result entries in that same native
            # shape so replay can feed this conversation back into the same
            # provider exactly.
            appended = new_history[len(history) :]
            assistant_native, tool_result_natives = appended[0], appended[1:]

            repo.add_message(
                session,
                conversation_id,
                role="assistant",
                sequence=sequence,
                tool_calls=tool_calls_log,
                provider_native=[assistant_native],
            )
            sequence += 1
            repo.add_message(
                session,
                conversation_id,
                role="tool",
                sequence=sequence,
                tool_results=tool_results_log,
                provider_native=tool_result_natives,
            )
            sequence += 1

            history = new_history

        # The loop exhausted its tool-call budget without the model ending on a
        # text-only turn. Rather than dropping the turn with a bare error, force
        # one final toolless send: the model must now produce a text response
        # (it can't call another tool), so it summarizes what it found. This is
        # what the player expects when the model has effectively finished its
        # reasoning but kept a trailing tool call attached. Thinking on — this is
        # the synthesis turn, not mechanical dispatch.
        logger.warning(
            "chat: hit MAX_TOOL_ITERATIONS (%d) after %.2fs — forcing toolless wrap-up",
            MAX_TOOL_ITERATIONS, time.perf_counter() - turn_started,
        )
        wrap = provider.send(system_prompt, history, [], thinking=True)
        yield from _finalize_turn(
            session, conversation_id, sequence,
            wrap.text or _MAX_ITER_FALLBACK_TEXT, wrap.raw_assistant_message,
            proposal_ids_this_turn, pending_summary,
        )
    except Exception as exc:  # noqa: BLE001 - surface to client instead of crashing the stream
        yield error_event(str(exc))


_MAX_ITER_FALLBACK_TEXT = (
    "I ran out of research steps before wrapping up. Here's where I got to — "
    "ask me to continue and I'll pick up from here."
)


def _finalize_turn(
    session: Session,
    conversation_id: int,
    sequence: int,
    text: str | None,
    raw_assistant_message: dict[str, Any],
    proposal_ids_this_turn: list[int],
    pending_summary: str,
) -> Iterator[str]:
    """Emit the final assistant message for a turn: stream its text, persist it,
    anchor this turn's proposals to it, reveal the settled batch, and close the
    turn. Shared by the normal (model ended on text) path and the max-iteration
    wrap-up path so both deliver a real response instead of one erroring out."""
    if text:
        yield token_event(text)
    message = repo.add_message(
        session,
        conversation_id,
        role="assistant",
        sequence=sequence,
        text_content=text,
        provider_native=[raw_assistant_message],
    )
    repo.anchor_proposals_to_message(session, proposal_ids_this_turn, message.id)
    # Now that trims are final, reveal the settled batch: only the proposals from
    # this turn that survived as pending, anchored to the message just written.
    # Emitted before `done` so the UI has the batch when the turn closes.
    settled = _settled_proposal_batch(
        session, proposal_ids_this_turn, pending_summary
    )
    if settled["proposals"]:
        yield deck_proposal_event(settled)
    repo.touch_conversation(session, conversation_id)
    yield done_event(message.id, conversation_id)


# Maps a deficiency category from deck stats to (knowledge category, query)
# used to proactively pull the relevant grounding entry into the turn.
_DEFICIENCY_KNOWLEDGE = {
    "lands": ("land-base", "land count formula commander"),
    "ramp": ("ramp", "ramp package sizing commander"),
    "draw": ("card-draw", "card draw density commander"),
    "removal": ("removal", "removal suite composition commander"),
}


def _deck_grounding(session: Session, deck_id: int | None) -> str:
    """Proactively retrieve knowledge for the attached deck's weak spots.

    Retrieval is otherwise model-elective (it must choose to call the search
    tool), so a deck with off-target ramp/draw/removal often gets advice from
    training data instead of the curated knowledge base. When a deck has cards
    and any deficiency is off-target, pull the matching knowledge entry and the
    deck's own stat summary into the system prompt so the model reasons from
    grounded numbers on its first turn. Best-effort: never break the turn.
    """
    if deck_id is None:
        return ""
    try:
        from app.knowledge.store import search_knowledge
        from app.tools.deck_tools import compute_deck_stats

        stats = compute_deck_stats(session, deck_id)
        if stats.get("total_cards", 0) == 0:
            return ""

        off_target = [d for d in stats.get("deficiencies", []) if d["status"] != "OK"]
        if not off_target:
            return ""

        blocks: list[str] = []
        seen: set[str] = set()
        for d in off_target:
            mapping = _DEFICIENCY_KNOWLEDGE.get(d["category"])
            if not mapping:
                continue
            kb_category, query = mapping
            if kb_category in seen:
                continue
            seen.add(kb_category)
            hits = search_knowledge(query, top_k=1, format="commander", category=kb_category)
            if hits:
                blocks.append(f"- {hits[0]['title']}: {hits[0]['body']}")

        summary = ", ".join(
            f"{d['category']} {d['count']} ({d['status']} vs {d['target_low']}-{d['target_high']})"
            for d in off_target
        )
        if not blocks:
            return ""
        return (
            "<deck_grounding>\n"
            "The attached deck has counts outside typical targets: "
            f"{summary}. Relevant deckbuilding guidance retrieved for you "
            "(prefer this over training-data assumptions; call deck_get_stats "
            "for the full breakdown before quoting numbers):\n"
            + "\n".join(blocks)
            + "\n</deck_grounding>"
        )
    except Exception:  # noqa: BLE001 - grounding is best-effort, never fatal
        return ""


def _settled_proposal_batch(
    session: Session, proposal_ids: list[int], summary: str
) -> dict[str, Any]:
    """Build the deck_proposal payload revealed at turn end: only this turn's
    proposals that are STILL pending (trimmed ones were denied and are dropped),
    in creation order. Same shape propose_deck_changes returns, so the SSE
    consumer needs no special case."""
    proposals: list[dict[str, Any]] = []
    for pid in proposal_ids:
        p = repo.get_proposal(session, pid)
        if p is None or p.status != "pending":
            continue
        proposals.append({
            "id": p.id,
            "deck_id": p.deck_id,
            "status": p.status,
            "action": p.action,
            "card_name": p.card_name,
            "quantity": p.quantity,
            "category": p.category,
            "commander_name": p.commander_name,
            "reasoning": p.reasoning,
        })
    return {"ok": True, "summary": summary, "proposals": proposals}


def _serialize(value: Any) -> str:
    import json

    return json.dumps(value)


_NAME_SYSTEM_PROMPT = (
    "You produce a short, natural, evocative name for a Magic: The Gathering "
    "Commander deck. Lead with the commander, then a couple of words naming the "
    "deck's strategy or theme — the way a player would title their own list. "
    "Examples: \"Korvold Jund Aristocrats\", \"Yuriko Ninja Tribal\", "
    "\"Winota Boros Stax\", \"Mass of Mysteries Elemental Myriad\". "
    "You MAY use a guild/shard/clan name (Jund, Boros, Dimir…) when it reads "
    "naturally, but never emit a raw color-letter string like \"WUBRG\" or "
    "\"WUB\". Prefer the player's stated goal over guessing from a thin card "
    "list. Do not invent a strategy the inputs don't support — if the theme "
    "isn't clear, name it after the commander and its color identity only. "
    "Keep it under about six words. Output ONLY the name — no quotes, no "
    "explanation, no commentary."
)

_GUILD_NAMES = {
    "W": "Mono-White", "U": "Mono-Blue", "B": "Mono-Black", "R": "Mono-Red",
    "G": "Mono-Green", "WU": "Azorius", "UB": "Dimir", "BR": "Rakdos",
    "RG": "Gruul", "GW": "Selesnya", "WB": "Orzhov", "UR": "Izzet",
    "BG": "Golgari", "RW": "Boros", "GU": "Simic", "WUB": "Esper",
    "UBR": "Grixis", "BRG": "Jund", "RGW": "Naya", "GWU": "Bant",
    "WBG": "Abzan", "URW": "Jeskai", "BGU": "Sultai", "RWB": "Mardu",
    "GUR": "Temur", "WUBRG": "Five-Color",
}


def _color_word(colors: set[str]) -> str:
    """Human-readable color-identity name (guild/shard/wedge), never a raw
    letter string — the old code fed 'WUBRG' straight into the name."""
    if not colors:
        return "Colorless"
    key = "".join(c for c in "WUBRG" if c in colors)
    return _GUILD_NAMES.get(key, key)


def _generate_deck_name(
    provider: ChatProvider,
    snapshot: dict,
    format_key: str,
    user_intent: str | None = None,
) -> str:
    """Ask the LLM for a short descriptive name.

    Naming fires as soon as a commander is approved, when the card list is
    usually just the commander — so the player's stated goal (``user_intent``)
    is the primary signal, and the card list only refines it once it exists.
    """
    commander = snapshot.get("commander")
    partner = snapshot.get("partner_commander")
    cards: list[dict] = snapshot.get("cards", [])

    by_category: dict[str, list[str]] = {}
    for c in cards:
        cat = c.get("category", "Other")
        by_category.setdefault(cat, []).append(c["name"])

    colors: set[str] = set()
    for c in cards:
        for ch in c.get("color_identity", ""):
            if ch in "WUBRG":
                colors.add(ch)
    color_str = _color_word(colors)

    # Surface the strategy-revealing categories prominently so the LLM can
    # pick up on the deck's archetype (aristocrats, stax, spellslinger, etc.).
    strategy_cats = {k: v for k, v in by_category.items() if k not in ("Commander", "Land")}
    top_cats = sorted(strategy_cats.items(), key=lambda kv: -len(kv[1]))

    lines = []
    if commander:
        cmd_line = commander
        if partner:
            cmd_line += f" / {partner}"
        lines.append(f"Commander: {cmd_line}")
    lines.append(f"Color identity: {color_str}")
    lines.append(f"Format: {format_key}")
    if user_intent:
        lines.append(f"Player's stated goal for this deck: {user_intent}")
    if top_cats:
        lines.append("Strategy categories (name the archetype these suggest):")
        for cat, names in top_cats[:6]:
            lines.append(f"  {cat} ({len(names)} cards): {', '.join(names[:5])}")
    elif not user_intent:
        lines.append(
            "No cards or stated goal yet — name it from the commander and "
            "color identity only; do not invent a strategy."
        )

    user_prompt = "\n".join(lines)

    try:
        turn = provider.send(
            system_prompt=_NAME_SYSTEM_PROMPT,
            history=[{"role": "user", "content": user_prompt}],
            tools=[],
        )
        name = (turn.text or "").strip().strip('"').strip("'")
        for prefix in ("Name: ", "Deck Name: ", "name: "):
            if name.startswith(prefix):
                name = name[len(prefix) :]
        if not name:
            return "Untitled Deck"
        if len(name) > 80:
            name = name[:77].rstrip() + "…"
        return name
    except Exception:
        return "Untitled Deck"
def _derive_title(user_text: str, max_length: int = 60) -> str:
    title = " ".join(user_text.split())
    if len(title) <= max_length:
        return title
    return title[: max_length - 1].rstrip() + "…"
