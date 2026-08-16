"""How much of a candidate pool the brain map can actually score.

Run this after ANY change to scoring, the card index, or the tag pipeline. It
is the regression test for "did that get better or just different" — a change
that raises coverage on one deck and craters another is a wash, and only a
spread of real decks shows that.

    .venv/Scripts/python.exe tools/coverage_report.py           # measure + compare
    .venv/Scripts/python.exe tools/coverage_report.py --save    # accept as baseline
    .venv/Scripts/python.exe tools/coverage_report.py --deck 19 # one deck

Every run compares against the last saved baseline and prints the deltas, so
regressions are visible without anyone remembering what the numbers used to be.
``--save`` is deliberately separate: a run that shows a drop should not quietly
overwrite the evidence.

Uses each deck's real commander and a fixed generic query, so it measures the
scoring layers rather than the query planner, and no LLM is called — runs stay
comparable and cost nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows consoles default to cp1252, which cannot encode the symbols this
# report prints — without this the tool dies on its own header rather than on
# anything it measured.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlmodel import Session  # noqa: E402

from app.brainmap.layers import CONSENSUS, MECHANICAL, PERSONAL, ScoringContext  # noqa: E402
from app.brainmap.map import score_pool  # noqa: E402
from app.cards import store as card_store  # noqa: E402
from app.db import repository as repo  # noqa: E402
from app.db.session import get_engine  # noqa: E402
from app.pipeline.candidates import gather_candidates_detailed  # noqa: E402
from app.pipeline.service import _commander_identity  # noqa: E402
from app.pipeline.spec import QuerySpec, identity_token  # noqa: E402


def measure(session: Session, deck_id: int) -> dict | None:
    snapshot = repo.deck_snapshot(session, deck_id)
    commander = snapshot.get("commander")
    if not commander:
        return None

    identity = _commander_identity(snapshot)
    # A deliberately generic query: this measures the SCORING layers, so the
    # pool should not vary with how cleverly a query was written.
    token = identity_token(identity)
    spec = QuerySpec(queries=[f"id<={token} f:commander"])

    pool = gather_candidates_detailed(
        spec, commander, identity=identity,
        off_meta=snapshot.get("off_meta") or 0.25,
    ).cards
    if not pool:
        return None

    context = ScoringContext(
        commander=commander,
        identity=identity,
        themes=[t for t in (snapshot.get("themes") or []) if t],
        deck_id=deck_id,
    )
    ranked = score_pool(
        pool, context, store=card_store, engine=session.get_bind(),
        off_meta=snapshot.get("off_meta") or 0.25,
    )
    by_name = {s.name: s for s in ranked}

    scored = [s for s in ranked if s.total > 0]
    return {
        "deck": snapshot.get("name") or f"deck {deck_id}",
        "commander": commander,
        "pool": len(pool),
        "scored": len(scored),
        "consensus": sum(1 for s in by_name.values() if s.get(CONSENSUS) > 0),
        "mechanical": sum(1 for s in by_name.values() if s.get(MECHANICAL) > 0),
        "personal": sum(1 for s in by_name.values() if s.get(PERSONAL) > 0),
    }


# Committed, so a baseline travels with the branch that changed it and shows up
# in review as a diff of the actual numbers.
BASELINE_PATH = Path(__file__).resolve().parent / "coverage_baseline.json"


def load_baseline() -> dict:
    if not BASELINE_PATH.exists():
        return {}
    try:
        return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_baseline(rows: list[dict]) -> None:
    payload = {
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "decks": {r["deck"]: r for r in rows},
    }
    BASELINE_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _delta(current: int, previous: int | None) -> str:
    """Render a change against the baseline, or blank when there is none."""
    if previous is None:
        return "   new"
    diff = current - previous
    if diff == 0:
        return "     ·"
    return f"{diff:+6d}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deck", type=int, help="measure a single deck")
    parser.add_argument(
        "--save", action="store_true",
        help="accept these numbers as the new baseline",
    )
    args = parser.parse_args()

    with Session(get_engine()) as session:
        if args.deck:
            deck_ids = [args.deck]
        else:
            deck_ids = [
                d.id for d in repo.list_decks(session)
                if d.commander and d.id is not None
            ]

        rows = []
        for deck_id in deck_ids:
            try:
                row = measure(session, deck_id)
            except Exception as exc:  # noqa: BLE001 — one bad deck must not stop the sweep
                print(f"  deck {deck_id}: FAILED ({exc})")
                continue
            if row:
                rows.append(row)

    if not rows:
        print("no decks measured")
        return 1

    baseline = load_baseline().get("decks", {})

    header = (
        f"{'deck':<26}{'pool':>6}{'scored':>8}{'cons':>7}"
        f"{'mech':>7}{'Δmech':>7}{'pers':>7}"
    )
    print(header)
    print("-" * len(header))
    regressed: list[str] = []
    for row in rows:
        pct = 100 * row["scored"] / row["pool"] if row["pool"] else 0
        was = baseline.get(row["deck"])
        prev_mech = was.get("mechanical") if was else None
        if prev_mech is not None and row["mechanical"] < prev_mech:
            regressed.append(row["deck"])
        print(
            f"{row['deck'][:25]:<26}{row['pool']:>6}"
            f"{row['scored']:>6} {pct:>3.0f}%"
            f"{row['consensus']:>7}{row['mechanical']:>7}"
            f"{_delta(row['mechanical'], prev_mech):>7}{row['personal']:>7}"
        )

    total_pool = sum(r["pool"] for r in rows)
    total_scored = sum(r["scored"] for r in rows)
    total_mech = sum(r["mechanical"] for r in rows)
    print("-" * len(header))
    print(
        f"{'TOTAL':<26}{total_pool:>6}{total_scored:>6} "
        f"{100 * total_scored / total_pool:>3.0f}%"
        f"{'':>7}{total_mech:>7}"
    )
    print()
    print(
        f"mechanical layer covers {100 * total_mech / total_pool:.0f}% "
        f"of all pooled cards"
    )

    if not baseline:
        print("\nNo baseline yet — run with --save to record one.")
    elif regressed:
        # Named rather than summarised: "3 decks regressed" sends you hunting,
        # and a per-deck drop is exactly the case a single total would hide.
        print(f"\n⚠ mechanical coverage DROPPED on: {', '.join(regressed)}")
    else:
        print("\nNo mechanical-coverage regressions against the baseline.")

    if args.save:
        save_baseline(rows)
        print(f"Baseline saved to {BASELINE_PATH.name}")

    return 1 if regressed and not args.save else 0


if __name__ == "__main__":
    raise SystemExit(main())
