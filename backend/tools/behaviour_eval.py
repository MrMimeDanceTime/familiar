"""Score the chat loop's behaviour on real turns.

Prompt changes used to be judged by whether the next live deck misbehaved.
This replays stored user turns through the loop against a scratch copy of
the database and scores each reply on the rules the prompt asks for and the
code cannot enforce, so a prompt edit becomes a before/after number instead
of a hunch.

    python tools/behaviour_eval.py replay --limit-turns 3       # calls the LLM, costs money
    python tools/behaviour_eval.py replay --conversation 12 --save
    python tools/behaviour_eval.py score traces/last.jsonl       # re-score a saved trace, no LLM

Replay copies the live DB (SQLite online backup) to a temp file, points the
app at it, and for each stored conversation creates a fresh conversation on
the same deck and re-sends its user messages in order. The deck is replayed
from its CURRENT state, not the state it had at the time, which is a known
limitation: the scores measure "given this deck and this message, does the
model behave", not a byte-for-byte reconstruction.

Every run compares against the saved baseline and prints deltas. ``--save``
is deliberately separate: a run that shows a regression should not quietly
overwrite the evidence.
"""

from __future__ import annotations

import argparse
import atexit
import json
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASELINE_PATH = Path(__file__).resolve().parent / "behaviour_baseline.json"
TRACE_DIR = Path(__file__).resolve().parent / "traces"

# A user message that asks for a role batch. When one of these is present and
# the turn produced adds, the adds should have come through suggest_cards.
_ROLE_WORDS = re.compile(
    r"\b(ramp|draw|removal|interaction|wipes?|counterspells?|lands?|manabase|"
    r"tutors?|finishers?|protection|recursion|package)\b",
    re.IGNORECASE,
)
_BRACKETED = re.compile(r"\[\[([^\][]+)\]\]")
_PROPOSAL_TOOLS = {"propose_deck_changes", "suggest_cards"}
_DECK_READ_TOOLS = {"deck_get_current", "deck_get_stats"}


# What the UI's Done reviewing button sends (frontend/src/lib/deckPrompts.ts).
# The player decided on the batch before pressing it; a replay has no player,
# so without this the model met "let's continue" with the whole batch still
# pending and spent up to 8,000 reasoning tokens working out what it meant.
REVIEW_DONE_MESSAGE = "I've reviewed the proposals. Let's continue."


def _approve_pending(session, deck_id: int) -> None:
    from app.db import repository as repo

    snapshot = repo.deck_snapshot(session, deck_id)
    for p in (snapshot.get("pending_proposals") or {}).get("proposals") or []:
        if p.get("id") is not None:
            try:
                repo.apply_proposal(session, p["id"])
            except Exception:  # noqa: BLE001 - an unappliable card stays pending, as in the UI
                pass


def score_turn(record: dict[str, Any]) -> dict[str, Any]:
    """Score one turn. Pure, so the trace format is the only contract.

    ``record`` carries: user_text, final_text, tool_calls (ordered list of
    {name, arguments, ok, result}), plan_was_set (bool), deck_card_names,
    proposed_names, usage (optional), rounds (optional).
    """
    calls = record.get("tool_calls") or []
    names = [c.get("name") for c in calls]
    final = record.get("final_text") or ""

    refusals = sum(1 for c in calls if str(c.get("result") or "").startswith("REFUSED"))
    # Calls the engine would not run: a second batch, research after a batch,
    # a tool that does not exist. Each is a wasted round.
    gate_refusals = sum(1 for c in calls if str(c.get("result") or "").startswith(("Not run", "There is no tool")))
    errors = sum(1 for c in calls if c.get("ok") is False)

    proposed_adds = any(
        c.get("name") == "propose_deck_changes"
        and any(
            (ch.get("action", "add") == "add") and not ch.get("player_named")
            for ch in (c.get("arguments") or {}).get("changes") or []
            if isinstance(ch, dict)
        )
        for c in calls
    )
    used_pipeline = "suggest_cards" in names
    role_request = bool(_ROLE_WORDS.search(record.get("user_text") or ""))
    role_batch_via_pipeline: bool | None
    if role_request and (proposed_adds or used_pipeline):
        role_batch_via_pipeline = used_pipeline and not proposed_adds
    else:
        role_batch_via_pipeline = None

    plan_set_before_batch: bool | None = None
    if not record.get("plan_was_set"):
        # A commander-only proposal is not a card batch (the engine agrees).
        first_batch = next((
            i for i, c in enumerate(calls)
            if c.get("name") in _PROPOSAL_TOOLS and not (
                c.get("name") == "propose_deck_changes"
                and all(ch.get("action") == "set_commander"
                        for ch in (c.get("arguments") or {}).get("changes") or [{}])
            )
        ), None)
        if first_batch is not None:
            # The app drafts the plan when a commander is proposed, so a
            # set_commander proposal before the batch counts as setting it.
            before = calls[:first_batch]
            plan_set_before_batch = "deck_set_plan" in names[:first_batch] or any(
                c.get("name") == "propose_deck_changes"
                and any(ch.get("action") == "set_commander"
                        for ch in (c.get("arguments") or {}).get("changes") or [])
                for c in before
            )

    # Card names the reply described without their text in any tool result
    # this turn. The engine grounds and re-writes such a reply, so this
    # counts how often the model still reached for memory first — the round
    # the correction costs is the thing to drive down.
    results_blob = " ".join(str(c.get("result") or "") for c in calls).lower()
    ungrounded = sorted(
        n for n in {m.strip().lower() for m in _BRACKETED.findall(final)}
        if n and n not in results_blob
    )

    known = {n.lower() for n in (record.get("deck_card_names") or [])}
    known |= {n.lower() for n in (record.get("proposed_names") or [])}
    bracketed = {m.lower() for m in _BRACKETED.findall(final)}
    stripped = _BRACKETED.sub(" ", final).lower()
    unbracketed = sorted(n for n in known if n and n in stripped and n not in bracketed)

    sends = record.get("sends") or []
    return {
        "hand_pick_refusals": refusals,
        "gate_refusals": gate_refusals,
        "reasoning_tokens": sum(s.get("reasoning_tokens") or 0 for s in sends),
        "think_cap_hits": sum(1 for s in sends if s.get("finish") == "length"),
        "seconds": record.get("seconds"),
        "checks": record.get("checks") or [],
        "tool_errors": errors,
        "tool_calls": len(calls),
        # The deck_state header exists so the model need not open every turn
        # with a deck read; this is how to tell whether it worked.
        "deck_reads": sum(1 for n in names if n in _DECK_READ_TOOLS),
        "rounds": record.get("rounds"),
        "role_batch_via_pipeline": role_batch_via_pipeline,
        "plan_set_before_batch": plan_set_before_batch,
        "reply_words": len(final.split()),
        "ungrounded_card_mentions": ungrounded,
        "grounding": record.get("grounding") or {},
        "unbracketed_card_names": unbracketed,
        "usage": record.get("usage") or {},
    }


def _median(values: list[float]) -> float | None:
    import statistics

    return round(statistics.median(values), 1) if values else None


def aggregate(scores: list[dict[str, Any]]) -> dict[str, Any]:
    def rate(key: str) -> float | None:
        vals = [s[key] for s in scores if s.get(key) is not None]
        return round(sum(1 for v in vals if v) / len(vals), 3) if vals else None

    n = max(len(scores), 1)
    return {
        "turns": len(scores),
        "refusals_per_turn": round(sum(s["hand_pick_refusals"] for s in scores) / n, 3),
        "gate_refusals_per_turn": round(sum(s.get("gate_refusals", 0) for s in scores) / n, 3),
        "checks_passed": (
            f"{sum(c['passed'] for s in scores for c in s.get('checks') or [])}"
            f"/{sum(len(s.get('checks') or []) for s in scores)}"
        ),
        "think_cap_hits": sum(s.get("think_cap_hits", 0) for s in scores),
        "median_reasoning_tokens": _median([s.get("reasoning_tokens", 0) for s in scores]),
        "median_turn_seconds": _median([s["seconds"] for s in scores if s.get("seconds") is not None]),
        "errors_per_turn": round(sum(s["tool_errors"] for s in scores) / n, 3),
        "calls_per_turn": round(sum(s["tool_calls"] for s in scores) / n, 2),
        "deck_reads_per_turn": round(sum(s.get("deck_reads", 0) for s in scores) / n, 2),
        "role_batch_via_pipeline_rate": rate("role_batch_via_pipeline"),
        "plan_set_before_batch_rate": rate("plan_set_before_batch"),
        "mean_reply_words": round(sum(s["reply_words"] for s in scores) / n, 1),
        "turns_with_unbracketed_names": sum(1 for s in scores if s["unbracketed_card_names"]),
        "ungrounded_mentions_per_turn": round(
            sum(len(s.get("ungrounded_card_mentions") or []) for s in scores) / n, 2
        ),
        # From the engine itself, which also sees the card facts it injected
        # into the prompt; the transcript-only count above cannot.
        "engine_named_per_turn": round(
            sum((s.get("grounding") or {}).get("named", 0) for s in scores) / n, 2),
        "engine_ungrounded_per_turn": round(
            sum((s.get("grounding") or {}).get("ungrounded", 0) for s in scores) / n, 2),
        "rewrites_per_turn": round(
            sum(1 for s in scores if (s.get("grounding") or {}).get("rewrote")) / n, 3),
        "mean_turn_seconds": round(
            sum((s.get("grounding") or {}).get("seconds", 0.0) for s in scores) / n, 1),
        "prompt_tokens": sum((s.get("usage") or {}).get("prompt_tokens") or 0 for s in scores),
        "completion_tokens": sum((s.get("usage") or {}).get("completion_tokens") or 0 for s in scores),
    }


# ── replay ────────────────────────────────────────────────────────────────


def _scratch_copy(src: Path) -> Path:
    dest = Path(tempfile.mkdtemp(prefix="familiar-eval-")) / "familiar.db"

    # The copy is the size of the live DB (~450MB) and was never removed: a day
    # of eval runs left 14GB in temp. The engine is disposed first because
    # Windows will not delete a file SQLite still has open.
    def _cleanup() -> None:
        try:
            from app.db.session import get_engine

            get_engine().dispose()
        except Exception:  # noqa: BLE001 - best effort at interpreter exit
            pass
        shutil.rmtree(dest.parent, ignore_errors=True)

    atexit.register(_cleanup)
    a = sqlite3.connect(str(src))
    b = sqlite3.connect(str(dest))
    try:
        a.backup(b)
    finally:
        b.close()
        a.close()
    return dest


def _collect_record(session, conversation_id: int, user_text: str, plan_was_set: bool,
                    deck_card_names: list[str], usage: dict | None) -> dict[str, Any]:
    from app.db import repository as repo

    # Only this turn: from the last user message on. Collecting the whole
    # replay conversation scored every later turn on the earlier turns' calls
    # too, so one refusal in turn 1 counted again in turns 2 and 3.
    messages = repo.list_messages(session, conversation_id)
    last_user = max((i for i, m in enumerate(messages) if m.role == "user"), default=0)
    messages = messages[last_user:]
    turn_message_ids = {m.id for m in messages}
    results_by_call: dict[str, dict] = {}
    for m in messages:
        for r in m.tool_results or []:
            results_by_call[r.get("call_id")] = r
    calls: list[dict[str, Any]] = []
    rounds = 0
    for m in messages:
        if m.role == "assistant" and m.tool_calls:
            rounds += 1
            for c in m.tool_calls:
                r = results_by_call.get(c.get("id"), {})
                content = str(r.get("content") or "")
                calls.append({
                    "name": c.get("name"),
                    "arguments": c.get("arguments") or {},
                    "ok": not content.startswith("Tool '") and "failed" not in content[:40],
                    "result": content[:400],
                })
    final = next(
        (m.text_content for m in reversed(messages) if m.role == "assistant" and m.text_content),
        "",
    )
    proposed = [
        p.card_name or p.commander_name or ""
        for p in repo.list_proposals(session, conversation_id)
        if p.message_id is None or p.message_id in turn_message_ids
    ]
    return {
        "user_text": user_text,
        "final_text": final,
        "tool_calls": calls,
        "rounds": rounds,
        "plan_was_set": plan_was_set,
        "deck_card_names": deck_card_names,
        "proposed_names": proposed,
        "usage": usage or {},
    }


class _Harness:
    """A scratch copy of the live DB with the engine's grounding and per-send
    timing logs captured, shared by replay and scripted."""

    def __init__(self) -> None:
        from app.config import settings
        from app.db.session import get_engine, init_db

        settings.familiar_db_path = str(_scratch_copy(settings.db_path))
        get_engine.cache_clear()
        init_db()
        self.engine = get_engine()
        self.grounding: list[dict] = []
        self.sends: list[dict] = []
        import logging

        harness = self

        class _Collect(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                data = getattr(record, "grounding", None)
                if isinstance(data, dict):
                    harness.grounding.append(data)
                # Each send's timing and reasoning, so a slow turn in the
                # trace shows what the model spent its thinking on.
                timing = getattr(record, "send_timing", None)
                if isinstance(timing, dict):
                    harness.sends.append(timing)

        for name in ("app.chat.grounding", "app.llm.timing"):
            log = logging.getLogger(name)
            log.setLevel(logging.INFO)
            log.addHandler(_Collect())

    def turn(self, session, conversation_id: int, deck_id: int, text: str) -> dict:
        """Run one turn and return its record."""
        from app import deckplan
        from app.chat.engine import run_chat_turn
        from app.db import repository as repo
        from app.llm.factory import get_provider

        snapshot = repo.deck_snapshot(session, deck_id)
        plan_was_set = deckplan.build_plan(snapshot).has_plan()
        card_names = [c["name"] for c in snapshot["cards"]]
        provider = get_provider()
        seen, sends_seen, started = len(self.grounding), len(self.sends), time.time()
        for _ in run_chat_turn(session, provider, conversation_id, text, deck_id):
            pass
        record = _collect_record(session, conversation_id, text, plan_was_set, card_names,
                                 getattr(provider, "usage", None))
        record["grounding"] = self.grounding[-1] if len(self.grounding) > seen else {}
        record["seconds"] = round(time.time() - started, 1)
        record["sends"] = self.sends[sends_seen:]
        return record


def scripted(repeats: int, only: str | None) -> list[dict]:
    """Run the scenarios in eval_scenarios.py; the player approves every
    proposal after each turn. Each record carries its scenario's checks."""
    harness = _Harness()
    from sqlmodel import Session

    from app.db import repository as repo
    from tools import eval_scenarios

    records: list[dict] = []
    with Session(harness.engine) as session:
        for _ in range(repeats):
            for scenario in eval_scenarios.SCENARIOS:
                if only and only.lower() not in scenario["name"].lower():
                    continue
                deck_id = eval_scenarios.build_deck(session, scenario.get("deck"))
                convo = repo.create_conversation(session, title=f"scenario: {scenario['name']}")
                repo.set_conversation_deck(session, convo.id, deck_id)
                turns = []
                for text in scenario["turns"]:
                    record = harness.turn(session, convo.id, deck_id, text)
                    record["scenario"] = scenario["name"]
                    turns.append(record)
                    _approve_pending(session, deck_id)
                results = []
                for check in scenario["checks"]:
                    try:
                        passed, detail = check(session, deck_id, turns)
                    except Exception as exc:  # noqa: BLE001 - a broken check is a failed check
                        passed, detail = False, f"check raised {exc!r}"
                    results.append({"check": check.__name__, "passed": passed, "detail": detail})
                turns[-1]["checks"] = results
                failed = [r for r in results if not r["passed"]]
                print(f"{scenario['name']}: {len(results) - len(failed)}/{len(results)} checks, "
                      f"{sum(t['seconds'] for t in turns):.0f}s")
                for r in failed:
                    print(f"   FAILED {r['check']}: {r['detail']}")
                records.extend(turns)
    return records


def replay(conversation_id: int | None, limit_turns: int, limit_conversations: int) -> list[dict]:
    harness = _Harness()
    from sqlmodel import Session

    from app.db import repository as repo

    records: list[dict] = []
    with Session(harness.engine) as session:
        sources = (
            [repo.get_conversation(session, conversation_id)] if conversation_id
            else repo.list_conversations(session)
        )
        # A conversation can outlive its deck; replaying one crashed the run.
        # Filtered before the limit so orphans do not use up the sample.
        sources = [
            s for s in sources
            if s is not None and s.deck_id is not None and repo.get_deck(session, s.deck_id)
        ][:limit_conversations]
        for source in sources:
            user_turns = [m.text_content for m in repo.list_messages(session, source.id)
                          if m.role == "user" and m.text_content][:limit_turns]
            if not user_turns:
                continue
            replay_convo = repo.create_conversation(session, title=f"eval of {source.id}")
            repo.set_conversation_deck(session, replay_convo.id, source.deck_id)
            print(f"conversation {source.id} ({source.title!r}): {len(user_turns)} turn(s)")
            for text in user_turns:
                if text.strip() == REVIEW_DONE_MESSAGE:
                    _approve_pending(session, source.deck_id)
                record = harness.turn(session, replay_convo.id, source.deck_id, text)
                record["source_conversation"] = source.id
                records.append(record)
                print(f"  turn scored: {json.dumps(score_turn(record), default=str)[:160]}")
    return records


# ── reporting ─────────────────────────────────────────────────────────────


SCRIPTED_BASELINE_PATH = Path(__file__).resolve().parent / "scenario_baseline.json"


def _load_baseline(path: Path = BASELINE_PATH) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def report(records: list[dict], save: bool, baseline_path: Path = BASELINE_PATH) -> int:
    scores = [score_turn(r) for r in records]
    summary = aggregate(scores)
    baseline = _load_baseline(baseline_path).get("summary", {})

    print()
    print(f"{'metric':36} {'now':>10} {'baseline':>10}")
    for key, value in summary.items():
        prev = baseline.get(key)
        print(f"{key:36} {str(value):>10} {str(prev) if prev is not None else '—':>10}")

    for s in scores:
        if s["unbracketed_card_names"]:
            print(f"unbracketed: {', '.join(s['unbracketed_card_names'][:5])}")

    if save:
        baseline_path.write_text(json.dumps({
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "summary": summary,
        }, indent=2) + "\n", encoding="utf-8")
        print(f"\nbaseline saved to {baseline_path.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    rp = sub.add_parser("replay", help="re-run stored user turns through the live loop")
    rp.add_argument("--conversation", type=int, default=None)
    rp.add_argument("--limit-turns", type=int, default=3, help="user turns per conversation")
    rp.add_argument("--limit-conversations", type=int, default=5)
    rp.add_argument("--save", action="store_true", help="accept this run as the baseline")
    sp = sub.add_parser("scripted", help="run the fixed scenarios in eval_scenarios.py")
    sp.add_argument("--repeats", type=int, default=1)
    sp.add_argument("--only", default=None, help="run scenarios whose name contains this")
    sp.add_argument("--save", action="store_true", help="accept this run as the scripted baseline")
    sc = sub.add_parser("score", help="score a saved trace without calling the model")
    sc.add_argument("trace", type=Path)
    sc.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "score":
        records = [json.loads(line) for line in args.trace.read_text(encoding="utf-8").splitlines() if line.strip()]
        return report(records, args.save)

    if args.command == "scripted":
        records = scripted(args.repeats, args.only)
        baseline_path = SCRIPTED_BASELINE_PATH
    else:
        records = replay(args.conversation, args.limit_turns, args.limit_conversations)
        baseline_path = BASELINE_PATH
    TRACE_DIR.mkdir(exist_ok=True)
    trace = TRACE_DIR / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
    trace.write_text("\n".join(json.dumps(r, default=str) for r in records) + "\n", encoding="utf-8")
    print(f"\ntrace written to {trace}")
    return report(records, args.save, baseline_path)


if __name__ == "__main__":
    raise SystemExit(main())
