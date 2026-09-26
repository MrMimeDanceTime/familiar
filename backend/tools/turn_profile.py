"""Profile chat turns end to end: where does a turn's time go?

Runs scripted deck-building turns through the real engine against a scratch
copy of the DB (like behaviour_eval.py replay) and prints, per turn, a
timeline of every LLM send: model, thinking and effort, prompt size and how
much of it hit DeepSeek's cache, reasoning versus reply tokens, and when the
first byte, reasoning token, reply token, and tool call arrived. Time between
sends is tool execution.

    python tools/turn_profile.py                    # the default script
    python tools/turn_profile.py --turns 2          # first two turns only

Calls DeepSeek (and Jev when selected) and costs money. A JSON trace lands in
tools/traces/.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TRACE_DIR = Path(__file__).resolve().parent / "traces"

SCRIPT = [
    "I want to build a Prosper, Tome-Bound commander deck focused on exile-and-cast value. Aim for bracket 3.",
    "Looks good, lock in the commander. Give me the ramp package.",
    "Now card draw, preferably things that play well with exiling cards.",
    "How many cards are in the deck now, and what's it still missing?",
]


class _Collect(logging.Handler):
    def __init__(self, attr: str, sink: list):
        super().__init__()
        self.attr, self.sink = attr, sink

    def emit(self, record: logging.LogRecord) -> None:
        data = getattr(record, self.attr, None)
        if isinstance(data, dict):
            self.sink.append({**data, "at": record.created})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--turns", type=int, default=len(SCRIPT))
    args = parser.parse_args(argv)

    from app.config import settings
    from app.db.session import get_engine
    from tools.behaviour_eval import _scratch_copy

    settings.familiar_db_path = str(_scratch_copy(settings.db_path))
    get_engine.cache_clear()

    from sqlmodel import Session

    from app.chat.engine import run_chat_turn
    from app.db import repository as repo
    from app.db.session import init_db
    from app.llm.factory import get_provider

    init_db()
    sends: list[dict] = []
    grounding: list[dict] = []
    for name, attr, sink in (("app.llm.timing", "send_timing", sends),
                             ("app.chat.grounding", "grounding", grounding)):
        log = logging.getLogger(name)
        log.setLevel(logging.INFO)
        log.addHandler(_Collect(attr, sink))

    print(f"select_backend={settings.select_backend} plan_thinking={settings.chat_plan_thinking} "
          f"effort={settings.chat_reasoning_effort!r} thinking_max={settings.chat_thinking_max_tokens} "
          f"max_tokens={settings.chat_max_tokens}")
    turns = []
    with Session(get_engine()) as session:
        deck = repo.create_deck(session)
        convo = repo.create_conversation(session, title="profile")
        repo.set_conversation_deck(session, convo.id, deck.id)
        for i, message in enumerate(SCRIPT[: args.turns], 1):
            first_send = len(sends)
            started = time.time()
            events: list[str] = []
            first_token = None
            for event in run_chat_turn(session, get_provider(), convo.id, message, deck.id):
                kind = getattr(event, "event", "")
                events.append(kind)
                if kind == "token" and first_token is None:
                    first_token = time.time() - started
                if kind == "deck_proposal":
                    for p in (getattr(event, "data", {}) or {}).get("proposals") or []:
                        if p.get("id") and p.get("status", "pending") == "pending":
                            try:
                                repo.apply_proposal(session, p["id"])
                            except Exception:  # noqa: BLE001 - profiling, not testing approval
                                pass
            total = time.time() - started
            turn_sends = sends[first_send:]
            print(f"\n== turn {i} ({total:.1f}s, first visible token {first_token or 0:.1f}s): {message[:70]}")
            cursor = started
            for s in turn_sends:
                begin = s["at"] - s["seconds"]
                gap = begin - cursor
                if gap > 0.3:
                    print(f"   {'':>6} {gap:5.1f}s  tools / other work between sends")
                cache = s.get("cache_hit_tokens", 0) / max(1, s.get("prompt_tokens", 0))
                print(f"   {s.get('kind', 'send'):<4} {s['seconds']:5.1f}s  think={'Y' if s['thinking'] else 'n'} effort={s.get('effort') or '-':<4} "
                      f"prompt={s.get('prompt_tokens', 0):>6} cached={cache:4.0%} "
                      f"reasoning={s.get('reasoning_tokens', 0):>5} reply={s.get('completion_tokens', 0) - s.get('reasoning_tokens', 0):>5} "
                      f"first_byte={s.get('first_byte', 0):4.1f}s first_text={s.get('first_text', '-')} "
                      f"first_tool={s.get('first_tool_call', '-')} finish={s.get('finish')}")
                cursor = s["at"]
            tail = started + total - cursor
            if tail > 0.3:
                print(f"   {'':>6} {tail:5.1f}s  after the last send")
            turns.append({"message": message, "seconds": round(total, 2),
                          "first_token": first_token, "sends": turn_sends,
                          "grounding": grounding[-1] if grounding else None})

    TRACE_DIR.mkdir(exist_ok=True)
    out = TRACE_DIR / f"turn_profile_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps(turns, indent=2, default=str), encoding="utf-8")
    print(f"\ntrace: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
