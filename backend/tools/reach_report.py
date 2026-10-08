"""Where the held-out cards come from, and where the ones that never arrive are lost.

Selectors can only pick what reaches the pool. jev_eval.py says how often a
held-out card misses the pool; this says which source found each one that
arrived, and for each one that did not, which source could have:

  * edhrec        - in the pool from stage 2 (EDHREC's commander page, or a
                    Scryfall query when stage 1 ran)
  * local         - in the pool from the local index only
  * cut by cap / illegal - retrieved, then lost in shaping
  * missed, on page   - never retrieved, but on the commander's EDHREC page
                    past the 40 the pipeline draws
  * missed, role tag  - never retrieved, but the index tags it with the role:
                    the role query's limit is applied before the colour filter
                    (``rank`` is its position among on-colour role cards)
  * missed, nowhere   - no source reached it

and whether each held card is on the commander's EDHREC page at all: a card
off the page is one only the index can find.

    python tools/reach_report.py
    python tools/reach_report.py --deck 1 --role-limit 300

Same cases as jev_eval.py, on a scratch copy of the DB. No selector runs; a
theme case may make one fast-model query-planning call.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from tools.jev_eval import (  # noqa: E402
    TRACE_DIR, _edhrec_page_names, _held_out_cases, _HeldOut, _intent,
)

BUCKETS = ("edhrec", "local", "cut by cap", "illegal", "missed, on page",
           "missed, role tag", "missed, nowhere")


def _role_ranks(role: str, identity: frozenset[str]) -> dict[str, int]:
    """Position of every on-colour card the index files under ``role``."""
    from app.cards import store
    from app.pipeline import local_retrieval, roles

    exact, prefixes, suffixes = roles.slug_rules_for(role)
    cards = store.cards_matching_slug_rules(exact, prefixes, suffixes, limit=100_000)
    on = [c for c in cards if local_retrieval._within_identity(c, identity)]
    return {(c.get("name") or "").lower(): i for i, c in enumerate(on)}


def run_case(session, deck_id: int, role: str, held: list[str], provider, model: str,
             role_limit: int, with_plan_tags: bool = False) -> dict:
    from app.cards import store
    from app.db import repository as repo
    from app.pipeline import plan_tags, service

    intent = _intent(role, deck_id)
    tags = None
    with _HeldOut(session, deck_id, held):
        if with_plan_tags:
            # Drafted from the deck without the held-out cards, as a plan
            # written before they were added would be.
            snapshot = repo.deck_snapshot(session, deck_id)
            tags = plan_tags.draft(provider, snapshot, service._commander_mechanic_tags(snapshot), store,
                                   model=model)
        prepared = service.prepare_pool(session, deck_id, intent, provider, model=model,
                                        local_role_limit=role_limit, plan_tags=tags)
    identity = prepared.ctx.identity
    gathered = {(c.get("name") or "").lower() for c in prepared.gathered.cards}
    local = {(c.get("name") or "").lower() for c in prepared.local_pool}
    shaped = {c.name.lower(): c for c in prepared.shaped if c.name}
    page = _edhrec_page_names(prepared.snapshot.get("commander"))
    ranks = _role_ranks(role, identity) if role != "synergy" else {}

    cards = []
    for name in held:
        n = name.lower()
        if n in shaped:
            bucket = ("edhrec" if n in gathered else "local") if shaped[n].legal_in_deck else "illegal"
        elif n in gathered or n in local:
            bucket = "cut by cap"
        elif n in page:
            bucket = "missed, on page"
        elif n in ranks:
            bucket = "missed, role tag"
        else:
            bucket = "missed, nowhere"
        cards.append({"name": name, "bucket": bucket, "on_page": n in page, "role_rank": ranks.get(n)})
    return {"deck_id": deck_id, "commander": prepared.snapshot.get("commander"), "role": role,
            "plan_tags": tags,
            "intent": intent, "stage1_skipped": prepared.stage1_skipped,
            "legal_pool": sum(1 for c in prepared.shaped if c.legal_in_deck), "cards": cards}


def summarize(records: list[dict]) -> None:
    for kind in ("role", "theme"):
        rows = [r for r in records if (r["role"] == "synergy") == (kind == "theme")]
        cards = [c for r in rows for c in r["cards"]]
        if not cards:
            continue
        counts = Counter(c["bucket"] for c in cards)
        off = [c for c in cards if not c["on_page"]]
        reached = sum(counts[b] for b in ("edhrec", "local"))
        print(f"\n{kind}: {len(rows)} cases, {len(cards)} held cards, reach {reached / len(cards):.0%}, "
              f"median legal pool {statistics.median(r['legal_pool'] for r in rows)}")
        for b in BUCKETS:
            print(f"  {b:<18} {counts[b]:>4}  {counts[b] / len(cards):>4.0%}")
        off_reached = sum(1 for c in off if c["bucket"] in ("edhrec", "local"))
        print(f"  off the EDHREC page: {len(off)} ({len(off) / len(cards):.0%}), "
              f"{off_reached} of them reached the pool")
        tagged = [c["role_rank"] for c in cards if c["bucket"] == "missed, role tag"]
        if tagged:
            print(f"  missed role-tag cards' rank among on-colour role cards: median "
                  f"{statistics.median(tagged)}, max {max(tagged)}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--deck", type=int, action="append")
    parser.add_argument("--role-limit", type=int, default=80)
    parser.add_argument("--plan-tags", action="store_true",
                        help="draft the plan as Tagger slugs and retrieve through them (one fast-model call per case)")
    parser.add_argument("--theme-only", action="store_true")
    args = parser.parse_args(argv)

    import logging

    logging.basicConfig(level=logging.ERROR)
    from app.config import settings
    from app.db.session import get_engine
    from tools.behaviour_eval import _scratch_copy

    settings.familiar_db_path = str(_scratch_copy(settings.db_path))
    get_engine.cache_clear()
    from sqlmodel import Session

    from app.db.session import init_db
    from app.llm.factory import get_fast_model, get_provider

    init_db()
    provider, model = get_provider(), get_fast_model()
    records = []
    with Session(get_engine()) as session:
        for deck_id, role, held in _held_out_cases(session, args.deck):
            if args.theme_only and role != "synergy":
                continue
            r = run_case(session, deck_id, role, held, provider, model, args.role_limit, args.plan_tags)
            records.append(r)
            c = Counter(x["bucket"] for x in r["cards"])
            print(f"deck {deck_id:>4} {role:<13} " + ", ".join(f"{b} {c[b]}" for b in BUCKETS if c[b])
                  + (f"  tags {r['plan_tags']}" if r["plan_tags"] else ""), flush=True)
    summarize(records)
    TRACE_DIR.mkdir(exist_ok=True)
    out = TRACE_DIR / f"reach_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps(records, indent=1), encoding="utf-8")
    print(f"\ntrace: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
