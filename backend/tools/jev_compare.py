"""Compare stage-4 selection backends on identical pools: the thinking LLM
against TypeSafe's Jev.

Stages 1-3 run once per case; both backends then pick from the same shaped
pool, so any difference is the selector and nothing else. Per case it prints
both pick lists, their overlap, where the LLM's picks land in Jev's full
ranking, and each backend's latency.

    python tools/jev_compare.py                          # the default cases
    python tools/jev_compare.py --deck 1 --intent "more ramp"
    python tools/jev_compare.py --jev-only               # no LLM call, no DeepSeek cost

Needs TYPESAFE_API_KEY in .env; the LLM side needs DEEPSEEK_API_KEY and costs
money (one selection call per case). Runs against a scratch copy of the DB and
in preview mode, so no proposals are written. A JSON trace of every run lands
in tools/traces/.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TRACE_DIR = Path(__file__).resolve().parent / "traces"

DEFAULT_CASES = [
    (1, "more ramp"),
    (2, "token payoffs that reward going wide"),
    (9, "cards that deal damage to each opponent"),
    (21, "wither and -1/-1 counter synergy"),
    (17, "cheap interaction: removal and counterspells"),
]


def _timed(fn):
    start = time.monotonic()
    value = fn()
    return value, round(time.monotonic() - start, 2)


def run_case(session, deck_id: int, intent: str, provider, jev_client, model: str,
             max_picks: int, jev_only: bool) -> dict:
    from app.pipeline import service

    prepared, prep_s = _timed(lambda: service.prepare_pool(
        session, deck_id, intent, provider, model=model,
    ))
    jev_sel, jev_s = _timed(lambda: service.run_selection(
        prepared, intent, provider, backend="jev", model=model,
        max_picks=max_picks, jev_client=jev_client,
    ))
    jev_selection, jev_backend = jev_sel
    if jev_backend != "jev":
        raise SystemExit("Jev call failed and fell back to the LLM; see the warning above.")

    record = {
        "deck_id": deck_id,
        "commander": prepared.snapshot.get("commander"),
        "intent": intent,
        "legal_pool": sum(1 for c in prepared.shaped if c.legal_in_deck),
        "prepare_s": prep_s,
        "jev_s": jev_s,
        "jev_picks": [{"name": p.name, "reason": p.reason} for p in jev_selection.picks],
        "jev_ranking": jev_selection.raw.get("judgments", []),
    }
    if jev_only:
        return record

    llm_sel, llm_s = _timed(lambda: service.run_selection(
        prepared, intent, provider, backend="llm", model=model, max_picks=max_picks,
    ))
    llm_selection = llm_sel[0]
    rank_of = {j["name"].lower(): i + 1 for i, j in enumerate(record["jev_ranking"])}
    jev_names = {p.name.lower() for p in jev_selection.picks}
    record.update({
        "llm_s": llm_s,
        "llm_picks": [{"name": p.name, "reason": p.reason} for p in llm_selection.picks],
        "overlap": sum(1 for p in llm_selection.picks if p.name.lower() in jev_names),
        "llm_pick_jev_ranks": {p.name: rank_of.get(p.name.lower()) for p in llm_selection.picks},
    })
    return record


def print_case(r: dict) -> None:
    print(f"\n== deck {r['deck_id']} ({r['commander']}): {r['intent']!r}")
    print(f"   legal pool {r['legal_pool']} | stages 1-3 {r['prepare_s']}s | jev {r['jev_s']}s"
          + (f" | llm {r['llm_s']}s" if "llm_s" in r else ""))
    llm = r.get("llm_picks") or []
    width = max([len(p["name"]) for p in r["jev_picks"]] + [4]) + 2
    print(f"   {'JEV':<{width}}LLM (its rank in Jev's ordering)")
    for i in range(max(len(r["jev_picks"]), len(llm))):
        left = r["jev_picks"][i]["name"] if i < len(r["jev_picks"]) else ""
        right = ""
        if i < len(llm):
            name = llm[i]["name"]
            right = f"{name} (#{r['llm_pick_jev_ranks'].get(name)})"
        print(f"   {left:<{width}}{right}")
    if llm:
        ranks = [v for v in r["llm_pick_jev_ranks"].values() if v]
        median = sorted(ranks)[len(ranks) // 2] if ranks else None
        print(f"   overlap {r['overlap']}/{len(llm)} | median Jev rank of LLM picks: #{median}"
              f" of {r['legal_pool']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--deck", type=int)
    parser.add_argument("--intent")
    parser.add_argument("--max-picks", type=int, default=10)
    parser.add_argument("--jev-only", action="store_true")
    args = parser.parse_args(argv)
    if (args.deck is None) != (args.intent is None):
        parser.error("--deck and --intent go together")
    cases = [(args.deck, args.intent)] if args.deck is not None else DEFAULT_CASES

    import logging

    logging.basicConfig(level=logging.WARNING)

    from app.config import settings
    from app.db.session import get_engine
    from tools.behaviour_eval import _scratch_copy

    settings.familiar_db_path = str(_scratch_copy(settings.db_path))
    get_engine.cache_clear()

    from sqlmodel import Session

    from app.db.session import init_db
    from app.llm.factory import get_fast_model, get_provider
    from app.pipeline import jev

    init_db()
    jev_client = jev.get_client()
    provider = get_provider()
    model = get_fast_model()

    records = []
    with Session(get_engine()) as session:
        for deck_id, intent in cases:
            record = run_case(session, deck_id, intent, provider, jev_client, model,
                              args.max_picks, args.jev_only)
            print_case(record)
            records.append(record)

    TRACE_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = TRACE_DIR / f"jev_compare_{stamp}.json"
    out.write_text(json.dumps(records, indent=2, default=str), encoding="utf-8")
    print(f"\ntrace: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
