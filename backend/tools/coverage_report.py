"""How much of a candidate pool the brain map can actually score.

Run this after changing scoring. It is the regression test for "did that get
better or just different" — a change that raises coverage on one deck and
craters it on another is a wash, and only a spread of real decks shows that.

    .venv/Scripts/python.exe tools/coverage_report.py
    .venv/Scripts/python.exe tools/coverage_report.py --deck 19

Uses each deck's real commander and a generic intent, so it measures the
scoring layers rather than the query planner. No LLM calls: the pool comes from
EDHREC plus a fixed Scryfall query, which keeps runs comparable.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deck", type=int, help="measure a single deck")
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

    header = f"{'deck':<26}{'pool':>6}{'scored':>8}{'cons':>7}{'mech':>7}{'pers':>7}"
    print(header)
    print("-" * len(header))
    for row in rows:
        pct = 100 * row["scored"] / row["pool"] if row["pool"] else 0
        print(
            f"{row['deck'][:25]:<26}{row['pool']:>6}"
            f"{row['scored']:>6} {pct:>3.0f}%"
            f"{row['consensus']:>7}{row['mechanical']:>7}{row['personal']:>7}"
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
    print(f"mechanical layer covers {100 * total_mech / total_pool:.0f}% of all pooled cards")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
