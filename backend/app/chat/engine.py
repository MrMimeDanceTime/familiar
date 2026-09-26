"""The agentic chat loop: load history, call the provider, dispatch any
tool calls, persist everything, and yield chat events as it goes.

Text streams token-by-token whenever the provider can stream (DeepSeek
does); a tool-calling iteration still emits a tool_call event for the UI's
loading indicator, and any text the model wrote alongside the call streams
too.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator

from sqlmodel import Session

from app.chat import card_facts, context
from app.config import settings
from app.chat.prompt import build_system_prompt
from app.chat.streaming import (
    ChatEvent,
    deck_proposal_event,
    message_reset_event,
    deck_updated_event,
    done_event,
    error_event,
    token_event,
    tool_call_event,
)
from app.db import repository as repo
from app.llm.base import AssistantTurn, ChatProvider, ToolResult
from app.tools.dispatch import DECK_MUTATION_TOOLS, DECK_SCOPED_TOOLS, PROPOSAL_TOOLS, dispatch
from app.tools import render
from app.tools.render import render_result
from app.tools.schemas import TOOL_SPECS

# One record per finished reply: how many cards it names, how many of those
# it described without their real text in context, and whether a rewrite
# round ran. tools/behaviour_eval.py collects these; the prompt-injected card
# facts are invisible to anything reading only the transcript.
grounding_log = logging.getLogger("app.chat.grounding")

logger = logging.getLogger("app.chat.engine")

MAX_TOOL_ITERATIONS = 12

# After a batch is proposed, the only tool the model may still call is
# withdraw_pending_proposals — so it can TRIM cards it reconsiders, but never
# add another batch or run more research. This is what stops the
# propose -> withdraw -> propose churn that used to burn the iteration budget.
WITHDRAW_ONLY_TOOLS = [t for t in TOOL_SPECS if t.name == "withdraw_pending_proposals"]

# A hand-picked batch of this many cards or more is the model choosing cards
# itself instead of running the scoring pipeline.
#
# The prompt asks for this routing and was ignored: on a live deck, 68 proposals
# were built with propose_deck_changes and suggest_cards was called ZERO times,
# so every card reached the player with no play rate, no mechanical fit against
# the commander, and nothing for the deck to learn from. Adding more prompt text
# did not move it, because propose_deck_changes remained available for the job.
#
# Set from what the live deck actually did: of 13 hand-picked calls, the add
# counts were 3, 4, and 6 (ten of them at 6). Nothing at 1 or 2. So 3 catches
# every real role batch while leaving small explicit requests alone — a player
# naming two cards ("add Sol Ring and Arcane Signet") is a normal direct
# proposal and must not be refused.
_HANDPICK_LIMIT = 3


def _split_handpicked_adds(
    tool_name: str, args: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    """Strip a hand-picked ADD batch out of a call, keeping the rest.

    Returns ``(kept_args, stripped_adds)``, or None to let the call through
    untouched.

    Splitting rather than refusing the whole call matters because the prompt
    tells the model to batch `set_commander` WITH its opening cards. Refusing
    outright discarded the commander too, and a deck with no commander blocks
    everything downstream — so the model followed its instructions, got refused,
    and had no way forward.

    Deliberately narrow otherwise: single named cards, cuts, and commander
    proposals all pass through, as does any batch whose adds are below the limit.
    """
    if tool_name != "propose_deck_changes":
        return None

    changes = args.get("changes")
    if not isinstance(changes, list):
        return None

    # A card the player named is their decision, not a suggestion to score.
    # Without this escape hatch the guard trapped the model: asked for four
    # specific staples by name, it could not propose them as a batch and had to
    # smuggle them through one at a time. A rule with no legitimate way past it
    # gets worked around, which is worse than the behaviour it prevents.
    adds = [
        c for c in changes
        if isinstance(c, dict)
        and c.get("action", "add") == "add"
        and not c.get("player_named")
    ]
    if len(adds) < _HANDPICK_LIMIT:
        return None

    kept = [c for c in changes if c not in adds]
    return {**args, "changes": kept}, adds


def _redirect_to_pipeline(tool_name: str, args: dict[str, Any]) -> str | None:
    """The refusal text for a hand-picked ADD batch, or None to allow the call."""
    split = _split_handpicked_adds(tool_name, args)
    if split is None:
        return None
    _kept, adds = split

    named = ", ".join(str(c.get("card_name")) for c in adds[:4] if c.get("card_name"))
    return (
        f"REFUSED: {len(adds)} hand-picked cards ({named}...). A batch filling a "
        "role or gap must go through suggest_cards, which scores every candidate "
        "against this deck — play rate, mechanical fit with the commander, and "
        "the player's own history. Cards proposed directly arrive with none of "
        "that and the player sees 'no scoring data'.\n\n"
        "Call suggest_cards with a focused intent describing what this batch is "
        "for (e.g. 'ramp package', 'cheap interaction', 'card draw that fits the "
        "commander'). It returns approval-ready proposals.\n\n"
        "Use propose_deck_changes only for a card the player named explicitly, a "
        "cut, or a commander."
    )


def _load_history(session: Session, conversation_id: int) -> list[dict[str, Any]]:
    messages = repo.list_messages(session, conversation_id)
    history: list[dict[str, Any]] = []
    for msg in messages:
        history.extend(msg.provider_native or [])
    return history


# How many of the most recent tool results are replayed in full. Everything
# older is cut down to a stub before each provider call.
#
# Nothing bounded the context before this. Every call replayed the whole
# conversation, and a deck read carries up to a hundred cards, so a build done
# in 3-6 card batches (twenty-odd turns, several tool calls each) grew without
# limit and the oldest, least relevant results cost the most. Four keeps the
# current reasoning intact — a turn rarely needs more than the last couple of
# reads — while the transcript in the database stays complete for replay.
KEEP_RECENT_TOOL_RESULTS = 4
# A result shorter than this is kept whatever its age: small results are the
# ones the model actually refers back to (a proposal batch, a stats summary)
# and eliding them saves nothing worth the confusion.
KEEP_SHORT_RESULT_CHARS = 600
# How much of an elided result survives, so the model can still see what the
# call was and what it returned in outline.
ELIDED_RESULT_HEAD_CHARS = 200


def bound_history(
    history: list[dict[str, Any]],
    *,
    keep_recent: int = KEEP_RECENT_TOOL_RESULTS,
    keep_short: int = KEEP_SHORT_RESULT_CHARS,
) -> list[dict[str, Any]]:
    """A copy of the history with old, large tool results cut to a stub.

    Applied at send time only. The persisted ``provider_native`` record is
    untouched, so a conversation can still be replayed in full, and the stub
    tells the model the result was elided rather than leaving a hole it might
    read as an empty result.
    """
    tool_positions = [i for i, m in enumerate(history) if m.get("role") == "tool"]
    to_elide = set(tool_positions[:-keep_recent]) if keep_recent > 0 else set(tool_positions)

    bounded: list[dict[str, Any]] = []
    for i, message in enumerate(history):
        content = message.get("content")
        if i in to_elide and isinstance(content, str) and len(content) > keep_short:
            head = content[:ELIDED_RESULT_HEAD_CHARS]
            message = {
                **message,
                "content": (
                    f"{head}… [{len(content) - ELIDED_RESULT_HEAD_CHARS} more characters "
                    "elided: this is an earlier result. Call the tool again if you need "
                    "the current data.]"
                ),
            }
        bounded.append(message)
    return bounded


# While a reply streams, the cancel flag is checked once per this many
# chunks: often enough that Stop feels immediate, rare enough that the check
# (a small query) never shows in the token rate.
STOP_CHECK_EVERY_CHUNKS = 8

STOPPED_NOTE = "(stopped here by the player)"


@dataclass
class _TurnState:
    """Everything one turn carries between iterations.

    The loop used to keep this in six local flags whose interactions were
    documented in comments beside each one; holding them together makes the
    rules readable as methods.
    """

    session: Session
    provider: ChatProvider
    conversation_id: int
    deck_id: int | None
    user_text: str
    system_prompt: str
    history: list[dict[str, Any]]
    sequence: int
    should_stop: Callable[[], bool]
    # Proposals created this turn, anchored to the final message at the end.
    proposal_ids: list[int] = field(default_factory=list)
    # The summary of the batch to reveal at turn end. Proposals are revealed
    # once, settled, so the player never watches a trimmed card flash in and
    # out.
    pending_summary: str = ""
    # Once a batch exists the model is offered only withdraw_pending_proposals,
    # so it can trim but cannot propose again or research further. That is
    # what stopped the propose -> withdraw -> propose churn.
    proposals_emitted: bool = False
    # A redirect that still applied part of a call (commander, cuts) tells the
    # model to re-issue the adds through suggest_cards, which must be possible
    # in the same turn: the restriction is held off for exactly one iteration.
    pipeline_followup_allowed: bool = False
    # Text streamed so far, so a stopped turn can persist what it had and a
    # later send starts a new paragraph rather than running into the last.
    streamed_parts: list[str] = field(default_factory=list)
    # A thinking send that returns nothing (its reasoning spent the budget)
    # is retried once without thinking; the flags make that retry fast and
    # single.
    force_fast: bool = False
    retried_empty: bool = False
    # Card grounding. `base_prompt` is everything but the card facts, so the
    # facts block can be rebuilt as names appear without disturbing the rest.
    # `grounded` is every card name whose real text is in the model's context
    # right now, from the facts block or from a tool result.
    base_prompt: str = ""
    grounded: set[str] = field(default_factory=set)
    unknown_names: set[str] = field(default_factory=set)
    corrected_once: bool = False

    @property
    def streamed_text(self) -> str:
        return "".join(self.streamed_parts)

    def ground_names(self, names: Iterable[str]) -> None:
        """Put the real text of these cards in front of the model.

        Called with every card name that turns up anywhere in the turn. The
        lookup is a local index read, so this is cheap enough to run on each
        iteration rather than only when the model asks.
        """
        fresh = [
            n for n in names
            if n.lower() not in self.grounded and n.lower() not in self.unknown_names
        ]
        if not fresh or not card_facts.is_available():
            return
        found, unknown = card_facts.resolve(fresh)
        if not found and not unknown:
            return
        self._facts.update(found)
        self.grounded.update(found)
        self.unknown_names.update(n.lower() for n in unknown)
        self._unknown_display.extend(
            n for n in unknown if n not in self._unknown_display
        )
        self._rebuild_prompt()

    def ground_deck(self, rows: Iterable[Any]) -> None:
        """Put the deck's own cards in front of the model, every turn.

        Most replies are about the deck, and its cards were the largest
        source of text the model had to recall rather than read. The rows
        carry the oracle text captured when each card was added, so this
        needs neither the index nor the network.
        """
        facts = card_facts.from_deck_cards(rows)
        if not facts:
            return
        self._deck_facts.update(facts)
        self.grounded.update(facts)
        self._rebuild_prompt()

    def _rebuild_prompt(self) -> None:
        block = card_facts.render_block(self._facts, self._unknown_display, self._deck_facts)
        self.system_prompt = f"{self.base_prompt}\n\n{block}" if block else self.base_prompt

    def ground_tool_result(self, content: Any) -> None:
        """A tool that returned card text has grounded those cards itself."""
        self.grounded.update(render.grounded_card_names(content))

    def ungrounded_in(self, text: str | None) -> list[str]:
        """Card names in *text* whose real text the model never saw.

        Includes names the index does not know: a card that does not exist is
        the worst version of the same failure.
        """
        if not card_facts.is_available():
            return []
        return [
            n for n in card_facts.names_in_text(text)
            if n.lower() not in self.grounded
        ]

    _facts: dict[str, dict[str, Any]] = field(default_factory=dict)
    _deck_facts: dict[str, dict[str, Any]] = field(default_factory=dict)
    _unknown_display: list[str] = field(default_factory=list)

    def tools_for_send(self) -> tuple[list[Any], bool]:
        """The tool list for the next send and whether it should think.

        Two sends carry judgment: the first, where the model decides what the
        turn is for and what to hand the pipeline, and the one after a batch
        lands, where it decides which picks to stand behind and writes the
        reply. Those think; the tool-dispatch sends between them stay fast.
        """
        restrict = self.proposals_emitted and not self.pipeline_followup_allowed
        self.pipeline_followup_allowed = False
        first_send = not self.history_has_tool_round
        thinking = restrict or (first_send and settings.chat_plan_thinking)
        if self.force_fast:
            thinking = False
            self.force_fast = False
        return (WITHDRAW_ONLY_TOOLS if restrict else TOOL_SPECS), thinking

    @property
    def history_has_tool_round(self) -> bool:
        return self._rounds > 0

    _rounds: int = 0

    def record_batch(self, content: dict[str, Any], *, followup_allowed: bool = False) -> None:
        """Note a proposal batch a tool created. Only a batch that actually
        created proposals restricts the turn; an empty one (the pipeline found
        nothing) must not strand the model."""
        batch = content.get("proposals") or []
        for p in batch:
            if p.get("id") is not None:
                self.proposal_ids.append(p["id"])
        if not batch:
            return
        self.proposals_emitted = True
        self.pipeline_followup_allowed = followup_allowed
        self.pending_summary = content.get("summary") or self.pending_summary
        _supersede_stale_batches(self.session, self.deck_id, self.proposal_ids)

    def persist_exchange(
        self, turn: AssistantTurn, results: list[ToolResult],
        calls_log: list[dict[str, Any]], results_log: list[dict[str, Any]],
    ) -> None:
        """Append the tool round to the history and the transcript.

        provider.append_tool_results appends the assistant's tool-call message
        followed by one or more result-bearing messages (one per call on
        OpenAI-style providers). Both are persisted in that native shape so
        replay feeds the same provider exactly. Never assume a count here;
        see PROVIDER_SHAPES.md.
        """
        new_history = self.provider.append_tool_results(self.history, turn, results)
        appended = new_history[len(self.history):]
        assistant_native, tool_result_natives = appended[0], appended[1:]
        # Text written alongside a tool call streamed to the player; keep it
        # so a reload shows the same transcript the live turn did.
        repo.add_message(
            self.session, self.conversation_id, role="assistant", sequence=self.sequence,
            text_content=turn.text or None, tool_calls=calls_log,
            provider_native=[assistant_native],
        )
        self.sequence += 1
        repo.add_message(
            self.session, self.conversation_id, role="tool", sequence=self.sequence,
            tool_results=results_log, provider_native=tool_result_natives,
        )
        self.sequence += 1
        self.history = new_history
        self._rounds += 1


def _prepare_turn(
    session: Session,
    provider: ChatProvider,
    conversation_id: int,
    user_text: str,
    deck_id: int | None,
    should_stop: Callable[[], bool],
) -> _TurnState:
    """Load the history, persist the user's message, and build the prompt."""
    history = _load_history(session, conversation_id)
    # What the player decided since the last reply rides with their message
    # so the model reads it as part of the turn; the stored transcript keeps
    # the raw text for the UI and the annotated one for replay.
    since = context.since_last_turn(session, deck_id)
    model_text = f"{since}\n\n{user_text}" if since else user_text
    history = provider.append_user_message(history, model_text)

    sequence = repo.next_sequence(session, conversation_id)
    if sequence == 0:
        repo.set_conversation_title(session, conversation_id, _derive_title(user_text))
    repo.add_message(
        session, conversation_id, role="user", sequence=sequence,
        text_content=user_text, provider_native=[{"role": "user", "content": model_text}],
    )
    sequence += 1

    format_key = "commander"
    if deck_id is not None:
        deck = repo.get_deck(session, deck_id)
        if deck:
            format_key = deck.format

    prefs = repo.get_or_create_preferences(session)
    deck_state = context.deck_state(session, deck_id)
    state_card_names = deck_state.card_names
    system_prompt = build_system_prompt(
        format_key,
        preferred_bracket=prefs.preferred_bracket,
        preferred_power=prefs.preferred_power,
        budget=prefs.budget,
        rule0_notes=prefs.rule0_notes,
        build_preferences=prefs.build_preferences,
        plan_is_set=deck_state.plan_is_set,
        total_cards=deck_state.total_cards,
    )
    # The variable blocks go last so the stable part of the prompt stays a
    # cacheable prefix across turns.
    for block in (context.player_history(session), deck_state.block):
        if block:
            system_prompt = f"{system_prompt}\n\n{block}"

    state = _TurnState(
        session=session, provider=provider, conversation_id=conversation_id,
        deck_id=deck_id, user_text=user_text, system_prompt=system_prompt,
        history=history, sequence=sequence, should_stop=should_stop,
        base_prompt=system_prompt,
    )
    # Everything already on the table gets its text before the first send.
    # A correction round is for a card the model reached for on its own; it
    # must never be the cost of discussing the deck, a staple the app said
    # was missing, or a card the player just named.
    if deck_id is not None:
        try:
            state.ground_deck(repo.list_deck_cards(session, deck_id))
        except Exception:  # noqa: BLE001 - grounding is a bonus, never a precondition
            logger.exception("could not ground the deck's own cards")
    state.ground_names([
        *card_facts.names_in_text(user_text),
        *state_card_names,
        *_recent_reply_names(session, conversation_id),
    ])
    _anticipate_cards(state, deck_state_text=deck_state.block or "")
    return state


_ANTICIPATE_PROMPT = """\
You help a Magic: The Gathering Commander deckbuilding assistant prepare a reply.
Given the deck and the player's message, list the real card names the reply is
likely to mention, suggest, or compare that are NOT already in the deck: the
cards someone answering well would bring up. Exact English card names only.
Output ONLY a JSON object: {"cards": ["<card name>", ...]} with at most 20 names,
or {"cards": []} when the message is not about specific cards.\
"""
_ANTICIPATE_MAX = 20


def _anticipate_cards(state: "_TurnState", *, deck_state_text: str) -> None:
    """Ground the cards the reply will probably reach for, before it is written.

    The reply used to be checked after it was written, and a draft that
    described cards without their text was withdrawn and rewritten: a whole
    second reply, streamed over the first. Guessing the cards up front and
    loading their real text costs one short call instead. Only names the
    index knows are loaded, so a guess the model invents adds nothing.
    """
    if not settings.chat_anticipate_cards or not card_facts.is_available():
        return
    try:
        from app.llm.factory import get_fast_model

        user = f"{deck_state_text[:4000]}\n\nPlayer's message: {state.user_text[:2000]}"
        raw = state.provider.complete_json(
            _ANTICIPATE_PROMPT, user, model=get_fast_model(), thinking=False,
        )
        names = json.loads(raw).get("cards") or []
        names = [n for n in names if isinstance(n, str)][:_ANTICIPATE_MAX]
        found, _ = card_facts.resolve(names)
        state.ground_names([card["name"] for card in found.values() if card.get("name")])
    except Exception as exc:  # noqa: BLE001 - anticipation is a head start, never a precondition
        logger.info("chat: card anticipation skipped: %s", exc)


# How far back to look for cards the conversation is still about. A follow-up
# question ("is that better than the other one?") lands a turn later, and the
# text that answered it has since been elided from the replayed context.
_RECENT_REPLY_MESSAGES = 4


def _recent_reply_names(session: Session, conversation_id: int) -> list[str]:
    """Cards named in the last few replies, which a follow-up is about."""
    try:
        messages = repo.list_messages(session, conversation_id)
    except Exception:  # noqa: BLE001
        return []
    names: list[str] = []
    for message in messages[-_RECENT_REPLY_MESSAGES:]:
        names.extend(card_facts.names_in_text(message.text_content))
    return names


def _stream_send(
    state: _TurnState, tools: list[Any], *, thinking: bool
) -> Iterator[ChatEvent | AssistantTurn]:
    """One provider call: token events as text arrives, then the turn.

    Yields no turn when the player stops the reply mid-stream; the caller
    treats that as the signal to finish with what was streamed.
    """
    started = time.perf_counter()
    first_chunk = True
    chunks = 0
    for item in _send(
        state.provider, state.system_prompt, bound_history(state.history), tools,
        thinking=thinking,
    ):
        if isinstance(item, AssistantTurn):
            names = [c.name for c in item.tool_calls] if item.tool_calls else []
            logger.info(
                "chat: send done in %.2fs (%s)", time.perf_counter() - started,
                f"calls: {', '.join(names)}" if names else "final text",
            )
            yield item
            return
        if first_chunk and state.streamed_parts:
            # A paragraph break keeps this send's text from running into the
            # text streamed alongside an earlier tool call.
            state.streamed_parts.append("\n\n")
            yield token_event("\n\n")
        first_chunk = False
        state.streamed_parts.append(item)
        yield token_event(item)
        chunks += 1
        if chunks % STOP_CHECK_EVERY_CHUNKS == 0 and state.should_stop():
            logger.info("chat: turn stopped by the player mid-reply")
            return


def _run_tool(state: _TurnState, call: Any) -> tuple[ToolResult, ChatEvent | None]:
    """Dispatch one tool call and return its result plus any event to emit."""
    args = dict(call.arguments)
    # The conversation's deck is authoritative: deck_id is a required tool
    # param, so the model always guesses one, and its guess must not decide
    # which deck a tool touches.
    if call.name in DECK_SCOPED_TOOLS and state.deck_id is not None:
        args["deck_id"] = state.deck_id
    if call.name in PROPOSAL_TOOLS:
        args["conversation_id"] = state.conversation_id
    if call.name == "suggest_cards":
        # The selection stage sees the player's own words, not only the intent
        # the model distilled from them.
        args["player_message"] = state.user_text

    # A hand-picked ADD batch is stripped out and redirected to the scoring
    # pipeline; anything else in the same call (the commander, cuts) still
    # runs, because refusing the whole call used to discard the commander and
    # leave the deck unable to proceed.
    split = _split_handpicked_adds(call.name, args)
    if split is not None:
        kept_args, stripped = split
        redirect = _redirect_to_pipeline(call.name, args) or ""
        logger.info(
            "chat: redirected %d hand-picked add(s) to suggest_cards%s", len(stripped),
            "; running the rest of the call" if kept_args["changes"] else "",
        )
        if kept_args["changes"]:
            kept = dispatch(call.name, kept_args, state.session, provider=state.provider)
            if kept.ok:
                state.record_batch(kept.content, followup_allowed=True)
                if kept.content.get("proposals"):
                    redirect += (
                        "\n\nThe non-add changes in this call (commander, cuts) WERE "
                        "applied — do not re-send them; only re-issue the adds via "
                        "suggest_cards, which you may call now."
                    )
        return ToolResult(call_id=call.id, content=redirect), None

    result = dispatch(call.name, args, state.session, provider=state.provider)
    content = str(result.content) if not result.ok else render_result(call.name, result.content)
    if result.ok:
        state.ground_tool_result(result.content)
    event: ChatEvent | None = None
    if result.ok and call.name in PROPOSAL_TOOLS:
        # No deck_proposal event here: the batch is revealed once, settled, at
        # turn end, so a card the model trims never flashes into the UI.
        state.record_batch(result.content)
    elif result.ok and call.name in DECK_MUTATION_TOOLS and state.deck_id is not None:
        event = deck_updated_event(result.content)
    return ToolResult(call_id=call.id, content=content), event


def run_chat_turn(
    session: Session,
    provider: ChatProvider,
    conversation_id: int,
    user_text: str,
    deck_id: int | None,
    *,
    should_stop: Callable[[], bool] | None = None,
) -> Iterator[ChatEvent]:
    """Run one user turn through the agentic loop, yielding chat events.

    ``should_stop`` is polled between provider calls and while a reply
    streams; when it returns True the turn is finished with whatever it has,
    persisted so the transcript stays consistent, and the ``done`` event is
    emitted as usual.
    """
    state = _prepare_turn(
        session, provider, conversation_id, user_text, deck_id, should_stop or (lambda: False),
    )
    try:
        turn_started = time.perf_counter()
        for iteration in range(MAX_TOOL_ITERATIONS):
            if state.should_stop():
                logger.info("chat: turn stopped by the player before iteration %d", iteration)
                yield from _stop_turn(state)
                return

            tools, thinking = state.tools_for_send()
            turn: AssistantTurn | None = None
            for item in _stream_send(state, tools, thinking=thinking):
                if isinstance(item, AssistantTurn):
                    turn = item
                else:
                    yield item
            if turn is None:
                yield from _stop_turn(state)
                return

            if not turn.tool_calls:
                text = turn.text
                ungrounded = state.ungrounded_in(text)
                if ungrounded and not settings.chat_correct_ungrounded_replies:
                    logger.warning(
                        "chat: reply describes %d unlooked-up card(s) and correction "
                        "is off: %s", len(ungrounded), ", ".join(ungrounded[:6]),
                    )
                elif ungrounded and not state.corrected_once:
                    # The reply talks about cards the model never looked up,
                    # which is where wrong rules text comes from. Withdraw the
                    # draft, put the real text in front of it, and have it
                    # write again. The player sees one reply, the correct one.
                    logger.info(
                        "chat: withdrawing a draft that described %d unlooked-up card(s): %s",
                        len(ungrounded), ", ".join(ungrounded[:6]),
                    )
                    state.corrected_once = True
                    state.ground_names(ungrounded)
                    state.history = state.provider.append_user_message(
                        state.history, _correction_request(text, ungrounded),
                    )
                    if state.streamed_parts:
                        yield message_reset_event(_RESET_NOTE)
                        state.streamed_parts.clear()
                    state.force_fast = True
                    continue
                if not (text or "").strip():
                    # Nothing came back. A thinking send can spend its whole
                    # output budget on reasoning; one fast retry usually
                    # gets the reply. Never persist the empty message: one
                    # in the transcript fails every later call.
                    if thinking and not state.retried_empty:
                        logger.warning(
                            "chat: empty reply from a thinking send (stop=%s); retrying "
                            "without thinking", turn.stop_reason,
                        )
                        state.retried_empty = True
                        state.force_fast = True
                        continue
                    text = _EMPTY_REPLY_TEXT
                logger.info(
                    "chat: turn complete in %.2fs over %d LLM call(s)",
                    time.perf_counter() - turn_started, iteration + 1,
                )
                grounding_log.info(
                    "grounding", extra={"grounding": {
                        "named": len(card_facts.names_in_text(text)),
                        "ungrounded": len(state.ungrounded_in(text)),
                        "rewrote": state.corrected_once,
                        "seconds": round(time.perf_counter() - turn_started, 2),
                    }},
                )
                yield from _finalize_turn(
                    state, text, turn.raw_assistant_message,
                    already_streamed=bool(state.streamed_parts) and text == turn.text,
                )
                return

            # Cards the model named alongside its tool calls are cards it is
            # about to write about; ground them before the next send.
            state.ground_names(card_facts.names_in_text(turn.text))

            results: list[ToolResult] = []
            calls_log: list[dict[str, Any]] = []
            results_log: list[dict[str, Any]] = []
            for call in turn.tool_calls:
                yield tool_call_event(call.name, call.arguments)
                result, event = _run_tool(state, call)
                results.append(result)
                calls_log.append({"id": call.id, "name": call.name, "arguments": call.arguments})
                results_log.append({"call_id": call.id, "content": result.content})
                if event is not None:
                    yield event
            state.persist_exchange(turn, results, calls_log, results_log)

        # The loop exhausted its tool-call budget without the model ending on
        # text. Rather than dropping the turn, force one toolless send: the
        # model must now write, so it summarises what it found. Thinking stays
        # off; this send exists to get a reply out.
        logger.warning(
            "chat: hit MAX_TOOL_ITERATIONS (%d) after %.2fs — forcing toolless wrap-up",
            MAX_TOOL_ITERATIONS, time.perf_counter() - turn_started,
        )
        wrap: AssistantTurn | None = None
        streamed_before = len(state.streamed_parts)
        for item in _stream_send(state, [], thinking=False):
            if isinstance(item, AssistantTurn):
                wrap = item
            else:
                yield item
        if wrap is None:
            yield from _stop_turn(state)
            return
        yield from _finalize_turn(
            state, wrap.text or _MAX_ITER_FALLBACK_TEXT, wrap.raw_assistant_message,
            already_streamed=len(state.streamed_parts) > streamed_before and bool(wrap.text),
        )
    except Exception as exc:  # noqa: BLE001 - surface to client instead of crashing the stream
        # Reveal any proposals this turn already created before reporting the
        # error. They are real pending rows, so without this the player is
        # told something failed while approvable cards sit invisible.
        try:
            settled = _settled_proposal_batch(session, state.proposal_ids, state.pending_summary)
            if settled["proposals"]:
                logger.info(
                    "chat: turn failed but revealing %d pending proposal(s)",
                    len(settled["proposals"]),
                )
                yield deck_proposal_event(settled)
        except Exception:  # noqa: BLE001 - never let recovery mask the real error
            logger.exception("chat: could not reveal proposals after a failed turn")
        yield error_event(str(exc))


def _stop_turn(state: _TurnState) -> Iterator[ChatEvent]:
    """Finish a turn the player stopped.

    Persists an assistant message so the transcript still alternates and the
    model sees, next turn, that its reply was cut short rather than that it
    said nothing. Proposals already created are revealed as usual: they are
    real rows, and hiding them would read as data loss.
    """
    partial = state.streamed_text.strip()
    if partial:
        ui_text = f"{partial}\n\n{STOPPED_NOTE}"
        yield token_event(f"\n\n{STOPPED_NOTE}")
    else:
        ui_text = STOPPED_NOTE
        yield token_event(STOPPED_NOTE)
    model_text = (
        f"{partial}\n\n[The player stopped this reply here.]" if partial
        else "[The player stopped this reply before it was written.]"
    )
    yield from _finalize_turn(
        state, ui_text, {"role": "assistant", "content": model_text}, already_streamed=True,
    )


def _supersede_stale_batches(
    session: Session, deck_id: int | None, keep_ids: list[int]
) -> None:
    """Withdraw card proposals left pending by earlier turns once this turn
    has produced its own batch.

    The prompt asked the model to withdraw a stale batch before proposing a new
    one, and the UI used to pretend it had. Doing it here makes the rule hold
    whether or not the model remembers: one live card batch per deck, and a
    pending commander proposal untouched. Best-effort, never fatal to the turn.
    """
    if deck_id is None or not keep_ids:
        return
    try:
        count = repo.supersede_pending_card_proposals(session, deck_id, keep_ids)
    except Exception:  # noqa: BLE001 - housekeeping must not sink the turn
        logger.exception("chat: could not supersede stale proposals")
        return
    if count:
        logger.info("chat: superseded %d stale pending proposal(s)", count)


_RESET_NOTE = "Rewritten after checking the real card text."

# How much of the withdrawn draft to quote back. Enough that the model can
# keep the parts that were right; not so much that a long reply doubles the
# send it is about to make.
_DRAFT_ECHO_CHARS = 2000


def _correction_request(draft: str | None, ungrounded: list[str]) -> str:
    """The message that turns a withdrawn draft into a corrected one."""
    named = ", ".join(ungrounded[:12])
    echo = " ".join((draft or "").split())[:_DRAFT_ECHO_CHARS]
    return (
        "HOLD — that reply was not sent to the player. It described these "
        f"cards without their text in front of you: {named}. Their real text "
        "is now in the card_facts block of your instructions; a name listed "
        "there as NO SUCH CARD does not exist and must not be described.\n\n"
        f"Your draft was:\n---\n{echo}\n---\n\n"
        "Write the reply again from the real text. Keep what was right, fix "
        "whatever the card text contradicts, and drop any card that does not "
        "exist. Do not mention this correction — the player never saw the "
        "draft."
    )


_EMPTY_REPLY_TEXT = (
    "I didn't manage to write a reply that time. Ask again and I'll pick up "
    "from here."
)

_MAX_ITER_FALLBACK_TEXT = (
    "I ran out of research steps before wrapping up. Here's where I got to — "
    "ask me to continue and I'll pick up from here."
)


def _send(
    provider: ChatProvider,
    system_prompt: str,
    history: list[dict[str, Any]],
    tools: list[Any],
    *,
    thinking: bool = False,
) -> Iterator[str | AssistantTurn]:
    """One provider call, streaming when the provider supports it.

    Yields text chunks as they arrive and the AssistantTurn last. A provider
    without ``send_stream`` (the test fakes, a future backend) is called
    through ``send`` and yields only the turn, so the engine treats both the
    same way.
    """
    stream = getattr(provider, "send_stream", None)
    if stream is None:
        yield provider.send(system_prompt, history, tools, thinking=thinking)
        return
    yield from stream(system_prompt, history, tools, thinking=thinking)


def _finalize_turn(
    state: _TurnState,
    text: str | None,
    raw_assistant_message: dict[str, Any],
    *,
    already_streamed: bool = False,
) -> Iterator[ChatEvent]:
    """Emit the final assistant message for a turn: stream its text, persist
    it, anchor this turn's proposals to it, reveal the settled batch, and close
    the turn. ``already_streamed`` means the text reached the client as it was
    generated and must not be sent a second time."""
    if text and not already_streamed:
        yield token_event(text)
    raw = raw_assistant_message or {"role": "assistant"}
    if not raw.get("content") and not raw.get("tool_calls"):
        # A bare assistant message is rejected by DeepSeek on every later
        # call, so the persisted record always carries the text shown.
        raw = {**raw, "content": text or _EMPTY_REPLY_TEXT}
    message = repo.add_message(
        state.session, state.conversation_id, role="assistant", sequence=state.sequence,
        text_content=text, provider_native=[raw],
    )
    repo.anchor_proposals_to_message(state.session, state.proposal_ids, message.id)
    # Now that trims are final, reveal the settled batch: only this turn's
    # proposals still pending, anchored to the message just written. Emitted
    # before `done` so the UI has the batch when the turn closes.
    settled = _settled_proposal_batch(state.session, state.proposal_ids, state.pending_summary)
    if settled["proposals"]:
        yield deck_proposal_event(settled)
    repo.touch_conversation(state.session, state.conversation_id)
    yield done_event(message.id, state.conversation_id)


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
            # The brain map's verdict. This payload is what the review UI
            # actually renders, so omitting it here made every proposal read
            # "no scoring data" even when the row carried full scores — the
            # database was right and the wire format was lying.
            "scores": p.scores,
            "price_usd": p.price_usd,
        })
    return {"ok": True, "summary": summary, "proposals": proposals}


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
        # A six-word name does not need the Pro model reasoning about it.
        from app.llm.factory import get_fast_model

        turn = provider.send(
            system_prompt=_NAME_SYSTEM_PROMPT,
            history=[{"role": "user", "content": user_prompt}],
            tools=[],
            thinking=False,
            model=get_fast_model(),
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
