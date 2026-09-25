"""Compare stage-4 selectors on identical pools: the thinking LLM against
TypeSafe's Jev in both modes (blind and informed, see app/pipeline/jev.py).

Stages 1-3 run once per case; every selector then picks from the same shaped
pool, so any difference is the selector and nothing else. Per case it prints
the pick lists side by side, then the numbers that decide whether Jev earns a
place:

  * overlap with the LLM's picks, and where the LLM's picks land in Jev's
    full ranking;
  * overlap with the plain brain-map top N and the plain EDHREC top N, plus
    the rank correlation of Jev's scores with each across the whole pool.
    Informed Jev that correlates ~1.0 with the brain map is an expensive
    echo; one that diverges is either adding judgment or adding noise, and the
    side-by-side lists are how to tell which.

    python tools/jev_compare.py                          # the default cases
    python tools/jev_compare.py --deck 1 --intent "more ramp"
    python tools/jev_compare.py --no-llm                 # Jev only, no DeepSeek cost

Needs TYPESAFE_API_KEY in .env; the LLM side needs DEEPSEEK_API_KEY and costs
money (one selection call per case). Runs against a scratch copy of the DB in
preview mode, so no proposals are written. A JSON trace lands in tools/traces/.
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
MODES = ("blind", "informed")


def _timed(fn):
    start = time.monotonic()
    value = fn()
    return value, round(time.monotonic() - start, 2)


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2
        i = j + 1
    return ranks


def spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3:
        return None
    ra, rb = _ranks(a), _ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = sum((x - ma) ** 2 for x in ra) ** 0.5
    vb = sum((y - mb) ** 2 for y in rb) ** 0.5
    return round(cov / (va * vb), 2) if va and vb else None


def _baselines(shaped, max_picks: int) -> dict:
    legal = [c for c in shaped if c.legal_in_deck and c.name]

    def total(c):
        return float((c.brainmap or {}).get("total") or 0.0)

    def rate(c):
        return float((c.edhrec or {}).get("inclusion_rate") or 0.0)

    return {
        "brainmap": {c.name.lower(): total(c) for c in legal},
        "edhrec": {c.name.lower(): rate(c) for c in legal},
        "brainmap_top": [c.name for c in sorted(legal, key=total, reverse=True)[:max_picks]],
        "edhrec_top": [c.name for c in sorted(legal, key=rate, reverse=True)[:max_picks]],
    }


def _overlap(a: list[str], b: list[str]) -> int:
    return len({x.lower() for x in a} & {x.lower() for x in b})


def run_case(session, deck_id: int, intent: str, provider, jev_client, model: str,
             max_picks: int, with_llm: bool) -> dict:
    from app.pipeline import service

    prepared, prep_s = _timed(lambda: service.prepare_pool(
        session, deck_id, intent, provider, model=model,
    ))
    base = _baselines(prepared.shaped, max_picks)
    record = {
        "deck_id": deck_id,
        "commander": prepared.snapshot.get("commander"),
        "intent": intent,
        "legal_pool": len(base["brainmap"]),
        "prepare_s": prep_s,
        "brainmap_top": base["brainmap_top"],
        "edhrec_top": base["edhrec_top"],
        "selectors": {},
    }

    for mode in MODES:
        (selection, used), secs = _timed(lambda: service.run_selection(
            prepared, intent, provider, backend="jev", model=model,
            max_picks=max_picks, jev_client=jev_client, jev_mode=mode,
        ))
        if used != "jev":
            raise SystemExit(f"Jev {mode} call failed and fell back to the LLM; see the warning above.")
        ranking = selection.raw.get("judgments", [])
        names = [j["name"].lower() for j in ranking]
        scores = [j["rank"] for j in ranking]
        record["selectors"][mode] = {
            "seconds": secs,
            "usage": selection.raw.get("usage"),
            "picks": [{"name": p.name, "reason": p.reason} for p in selection.picks],
            "ranking": ranking,
            "rho_brainmap": spearman(scores, [base["brainmap"].get(n, 0.0) for n in names]),
            "rho_edhrec": spearman(scores, [base["edhrec"].get(n, 0.0) for n in names]),
        }

    if with_llm:
        (selection, _), secs = _timed(lambda: service.run_selection(
            prepared, intent, provider, backend="llm", model=model, max_picks=max_picks,
        ))
        record["selectors"]["llm"] = {
            "seconds": secs,
            "picks": [{"name": p.name, "reason": p.reason} for p in selection.picks],
        }
        llm_names = [p.name for p in selection.picks]
        for mode in MODES:
            ranking = record["selectors"][mode]["ranking"]
            rank_of = {j["name"].lower(): i + 1 for i, j in enumerate(ranking)}
            record["selectors"][mode]["llm_pick_ranks"] = {n: rank_of.get(n.lower()) for n in llm_names}
    return record


def print_case(r: dict) -> None:
    sel = r["selectors"]
    columns = [m for m in ("llm", *MODES) if m in sel]
    print(f"\n== deck {r['deck_id']} ({r['commander']}): {r['intent']!r}")
    print(f"   legal pool {r['legal_pool']} | stages 1-3 {r['prepare_s']}s | "
          + " | ".join(f"{m} {sel[m]['seconds']}s" for m in columns))
    width = 30
    print("   " + "".join(f"{m.upper():<{width}}" for m in columns))
    rows = max(len(sel[m]["picks"]) for m in columns)
    for i in range(rows):
        cells = []
        for m in columns:
            picks = sel[m]["picks"]
            cells.append(f"{picks[i]['name'][:width - 2]:<{width}}" if i < len(picks) else " " * width)
        print("   " + "".join(cells).rstrip())
    llm = [p["name"] for p in sel.get("llm", {}).get("picks", [])]
    for mode in MODES:
        s = sel[mode]
        names = [p["name"] for p in s["picks"]]
        line = (f"   {mode:<9} vs brainmap top {_overlap(names, r['brainmap_top'])}/{len(names)}"
                f" (rho {s['rho_brainmap']}) | vs EDHREC top {_overlap(names, r['edhrec_top'])}/{len(names)}"
                f" (rho {s['rho_edhrec']})")
        if llm:
            ranks = sorted(v for v in s["llm_pick_ranks"].values() if v)
            median = ranks[len(ranks) // 2] if ranks else None
            line += f" | vs LLM {_overlap(names, llm)}/{len(llm)}, LLM picks' median rank #{median}"
        print(line)


def summarize(records: list[dict]) -> None:
    print("\n== across cases")
    for mode in MODES:
        rows = [r["selectors"][mode] for r in records]
        secs = sorted(s["seconds"] for s in rows)
        rho_b = [s["rho_brainmap"] for s in rows if s["rho_brainmap"] is not None]
        line = (f"   {mode:<9} median {secs[len(secs) // 2]}s | mean rho brainmap "
                f"{sum(rho_b) / len(rho_b):.2f}" if rho_b else f"   {mode}")
        if all("llm" in r["selectors"] for r in records):
            over = [_overlap([p["name"] for p in r["selectors"][mode]["picks"]],
                             [p["name"] for p in r["selectors"]["llm"]["picks"]]) for r in records]
            line += f" | mean overlap with LLM {sum(over) / len(over):.1f}"
        print(line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--deck", type=int)
    parser.add_argument("--intent")
    parser.add_argument("--max-picks", type=int, default=10)
    parser.add_argument("--no-llm", action="store_true")
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
                              args.max_picks, not args.no_llm)
            print_case(record)
            records.append(record)
    if len(records) > 1:
        summarize(records)

    TRACE_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = TRACE_DIR / f"jev_compare_{stamp}.json"
    out.write_text(json.dumps(records, indent=2, default=str), encoding="utf-8")
    print(f"\ntrace: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
