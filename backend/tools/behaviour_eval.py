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
import json
import re
import sqlite3
import sys
import tempfile
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
        first_batch = next((i for i, n in enumerate(names) if n in _PROPOSAL_TOOLS), None)
        if first_batch is not None:
            plan_set_before_batch = "deck_set_plan" in names[:first_batch]

    known = {n.lower() for n in (record.get("deck_card_names") or [])}
    known |= {n.lower() for n in (record.get("proposed_names") or [])}
    bracketed = {m.lower() for m in _BRACKETED.findall(final)}
    stripped = _BRACKETED.sub(" ", final).lower()
    unbracketed = sorted(n for n in known if n and n in stripped and n not in bracketed)

    return {
        "hand_pick_refusals": refusals,
        "tool_errors": errors,
        "tool_calls": len(calls),
        # The deck_state header exists so the model need not open every turn
        # with a deck read; this is how to tell whether it worked.
        "deck_reads": sum(1 for n in names if n in _DECK_READ_TOOLS),
        "rounds": record.get("rounds"),
        "role_batch_via_pipeline": role_batch_via_pipeline,
        "plan_set_before_batch": plan_set_before_batch,
        "reply_words": len(final.split()),
        "unbracketed_card_names": unbracketed,
        "usage": record.get("usage") or {},
    }


def aggregate(scores: list[dict[str, Any]]) -> dict[str, Any]:
    def rate(key: str) -> float | None:
        vals = [s[key] for s in scores if s.get(key) is not None]
        return round(sum(1 for v in vals if v) / len(vals), 3) if vals else None

    n = max(len(scores), 1)
    return {
        "turns": len(scores),
        "refusals_per_turn": round(sum(s["hand_pick_refusals"] for s in scores) / n, 3),
        "errors_per_turn": round(sum(s["tool_errors"] for s in scores) / n, 3),
        "calls_per_turn": round(sum(s["tool_calls"] for s in scores) / n, 2),
        "deck_reads_per_turn": round(sum(s.get("deck_reads", 0) for s in scores) / n, 2),
        "role_batch_via_pipeline_rate": rate("role_batch_via_pipeline"),
        "plan_set_before_batch_rate": rate("plan_set_before_batch"),
        "mean_reply_words": round(sum(s["reply_words"] for s in scores) / n, 1),
        "turns_with_unbracketed_names": sum(1 for s in scores if s["unbracketed_card_names"]),
        "prompt_tokens": sum((s.get("usage") or {}).get("prompt_tokens") or 0 for s in scores),
        "completion_tokens": sum((s.get("usage") or {}).get("completion_tokens") or 0 for s in scores),
    }


# ── replay ────────────────────────────────────────────────────────────────


def _scratch_copy(src: Path) -> Path:
    dest = Path(tempfile.mkdtemp(prefix="familiar-eval-")) / "familiar.db"
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

    messages = repo.list_messages(session, conversation_id)
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
    proposed = [p.card_name or p.commander_name or "" for p in repo.list_proposals(session, conversation_id)]
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


def replay(conversation_id: int | None, limit_turns: int, limit_conversations: int) -> list[dict]:
    from app.config import settings
    from app.db.session import get_engine

    scratch = _scratch_copy(settings.db_path)
    settings.familiar_db_path = str(scratch)
    get_engine.cache_clear()

    from sqlmodel import Session

    from app import deckplan
    from app.chat.engine import run_chat_turn
    from app.db import repository as repo
    from app.db.session import init_db
    from app.llm.factory import get_provider

    init_db()
    records: list[dict] = []
    with Session(get_engine()) as session:
        sources = (
            [repo.get_conversation(session, conversation_id)] if conversation_id
            else repo.list_conversations(session)[:limit_conversations]
        )
        for source in sources:
            if source is None or source.deck_id is None:
                continue
            user_turns = [m.text_content for m in repo.list_messages(session, source.id)
                          if m.role == "user" and m.text_content][:limit_turns]
            if not user_turns:
                continue
            replay_convo = repo.create_conversation(session, title=f"eval of {source.id}")
            repo.set_conversation_deck(session, replay_convo.id, source.deck_id)
            print(f"conversation {source.id} ({source.title!r}): {len(user_turns)} turn(s)")
            for text in user_turns:
                snapshot = repo.deck_snapshot(session, source.deck_id)
                plan_was_set = deckplan.build_plan(snapshot).has_plan()
                card_names = [c["name"] for c in snapshot["cards"]]
                provider = get_provider()
                for _ in run_chat_turn(session, provider, replay_convo.id, text, source.deck_id):
                    pass
                record = _collect_record(
                    session, replay_convo.id, text, plan_was_set, card_names,
                    getattr(provider, "usage", None),
                )
                record["source_conversation"] = source.id
                records.append(record)
                print(f"  turn scored: {json.dumps(score_turn(record), default=str)[:160]}")
    return records


# ── reporting ─────────────────────────────────────────────────────────────


def _load_baseline() -> dict:
    if not BASELINE_PATH.exists():
        return {}
    try:
        return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def report(records: list[dict], save: bool) -> int:
    scores = [score_turn(r) for r in records]
    summary = aggregate(scores)
    baseline = _load_baseline().get("summary", {})

    print()
    print(f"{'metric':36} {'now':>10} {'baseline':>10}")
    for key, value in summary.items():
        prev = baseline.get(key)
        print(f"{key:36} {str(value):>10} {str(prev) if prev is not None else '—':>10}")

    for s in scores:
        if s["unbracketed_card_names"]:
            print(f"unbracketed: {', '.join(s['unbracketed_card_names'][:5])}")

    if save:
        BASELINE_PATH.write_text(json.dumps({
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "summary": summary,
        }, indent=2) + "\n", encoding="utf-8")
        print(f"\nbaseline saved to {BASELINE_PATH.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    rp = sub.add_parser("replay", help="re-run stored user turns through the live loop")
    rp.add_argument("--conversation", type=int, default=None)
    rp.add_argument("--limit-turns", type=int, default=3, help="user turns per conversation")
    rp.add_argument("--limit-conversations", type=int, default=5)
    rp.add_argument("--save", action="store_true", help="accept this run as the baseline")
    sc = sub.add_parser("score", help="score a saved trace without calling the model")
    sc.add_argument("trace", type=Path)
    sc.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "score":
        records = [json.loads(line) for line in args.trace.read_text(encoding="utf-8").splitlines() if line.strip()]
        return report(records, args.save)

    records = replay(args.conversation, args.limit_turns, args.limit_conversations)
    TRACE_DIR.mkdir(exist_ok=True)
    trace = TRACE_DIR / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
    trace.write_text("\n".join(json.dumps(r, default=str) for r in records) + "\n", encoding="utf-8")
    print(f"\ntrace written to {trace}")
    return report(records, args.save)


if __name__ == "__main__":
    raise SystemExit(main())
