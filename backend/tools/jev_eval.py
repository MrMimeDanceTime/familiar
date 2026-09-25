"""Measure stage-4 selectors for accuracy and consistency against ground truth
from the player's own decks.

Accuracy (leave-out recovery). For a deck and a role it fills (ramp, removal,
draw...), up to six of the deck's cards in that role are removed, along with
this deck's proposal history for them so the brain map's personal layer cannot
leak the answer. The pipeline is then asked for "more <role>". A selector is
accurate to the degree it puts the removed cards, the ones the player actually
chose, back on top. Reported per selector:

  * recall@10 - share of the removed cards that made the pool which the
    selector put in its top 10;
  * percentile - mean position of those cards in the selector's full ranking,
    0.0 top, 0.5 no better than chance (full-ranking selectors only).

The ground truth is one player's choices, so a good card the player never ran
counts as a miss. That is the point: it measures fit to THIS player's decks.

Consistency. Every selector runs again on the same pool in shuffled order
(stability), and Jev once more in the original order (determinism); reported
as top-10 overlap between runs.

    python tools/jev_eval.py                 # all decks, LLM included (costs money)
    python tools/jev_eval.py --no-llm        # Jev and the baselines only
    python tools/jev_eval.py --deck 1 --deck 9

Runs against a scratch copy of the DB; nothing live is touched. A JSON trace
lands in tools/traces/.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TRACE_DIR = Path(__file__).resolve().parent / "traces"

ROLE_INTENTS = {
    "ramp": "more ramp",
    "card-draw": "more card draw",
    "spot-removal": "more removal",
    "board-wipe": "a board wipe or two",
    "counterspell": "more counterspells",
    "recursion": "graveyard recursion",
    "protection": "protection for my key pieces",
    "tutor": "tutors",
}
# name -> (mode, samples)
JEV_SELECTORS = {
    "blind": ("blind", 1),
    "informed": ("informed", 1),
    "verdict": ("verdict", 1),
    "verdict3": ("verdict", 3),
    "choice3": ("choice", 3),
    "ensemble": ("ensemble", 3),
}
BASELINES = ("brainmap", "edhrec")
MAX_HELD = 6
MIN_HELD = 4
ROLES_PER_DECK = 2
TOP = 10


def _held_out_cases(session, deck_ids: list[int] | None) -> list[tuple[int, str, list[str]]]:
    """(deck, role, held-out names) for each deck's best-represented roles."""
    from app.db import repository as repo
    from app.pipeline import roles

    cases = []
    for deck in repo.list_decks(session):
        if deck_ids and deck.id not in deck_ids:
            continue
        commanders = {n.lower() for n in (deck.commander, deck.partner_commander) if n}
        by_role: dict[str, list[str]] = {}
        for card in repo.list_deck_cards(session, deck.id):
            if card.card_name.lower() in commanders or "Land" in (card.type_line or ""):
                continue
            for role in roles.fine_roles_for_tags(card.tags or [], card.type_line) & set(ROLE_INTENTS):
                by_role.setdefault(role, []).append(card.card_name)
        ranked = sorted(by_role.items(), key=lambda kv: len(kv[1]), reverse=True)
        for role, names in [kv for kv in ranked if len(kv[1]) >= MIN_HELD][:ROLES_PER_DECK]:
            rng = random.Random(f"{deck.id}:{role}")
            cases.append((deck.id, role, sorted(rng.sample(sorted(names), min(MAX_HELD, len(names))))))
    return cases


class _HeldOut:
    """Remove the held-out cards and this deck's proposals for them; put
    everything back on exit so later cases see the deck intact."""

    def __init__(self, session, deck_id: int, names: list[str]):
        self.session, self.deck_id, self.names = session, deck_id, names
        self.saved: dict[str, list[dict]] = {}

    def _rows(self, table: str) -> list[dict]:
        from sqlalchemy import bindparam, text

        stmt = text(
            f"SELECT * FROM {table} WHERE deck_id = :d AND card_name IN :names"
        ).bindparams(bindparam("names", expanding=True))
        conn = self.session.connection()
        return [dict(r) for r in conn.execute(stmt, {"d": self.deck_id, "names": self.names}).mappings()]

    def __enter__(self):
        from sqlalchemy import bindparam, text

        conn = self.session.connection()
        for table in ("deckcard", "deck_proposals"):
            self.saved[table] = self._rows(table)
            stmt = text(
                f"DELETE FROM {table} WHERE deck_id = :d AND card_name IN :names"
            ).bindparams(bindparam("names", expanding=True))
            conn.execute(stmt, {"d": self.deck_id, "names": self.names})
        self.session.commit()
        return self

    def __exit__(self, *exc):
        from sqlalchemy import text

        self.session.rollback()
        conn = self.session.connection()
        for table, rows in self.saved.items():
            for row in rows:
                cols = ", ".join(row)
                marks = ", ".join(f":{c}" for c in row)
                conn.execute(text(f"INSERT INTO {table} ({cols}) VALUES ({marks})"), row)
        self.session.commit()


def _timed(fn):
    start = time.monotonic()
    value = fn()
    return value, round(time.monotonic() - start, 2)


def _overlap(a: list[str], b: list[str]) -> int:
    return len({x.lower() for x in a[:TOP]} & {x.lower() for x in b[:TOP]})


def _score(order: list[str], held: set[str], full: bool) -> dict:
    lowered = [n.lower() for n in order]
    hits = sum(1 for n in lowered[:TOP] if n in held)
    result = {"hits": hits, "recall": round(hits / len(held), 3) if held else None}
    if full and held and len(lowered) > 1:
        positions = [lowered.index(n) / (len(lowered) - 1) for n in held if n in lowered]
        result["percentile"] = round(sum(positions) / len(positions), 3) if positions else None
    return result


def run_case(session, deck_id: int, role: str, held: list[str], provider, jev_client,
             model: str, with_llm: bool) -> dict:
    from app.pipeline import jev, service

    intent = ROLE_INTENTS[role]
    with _HeldOut(session, deck_id, held):
        prepared, prep_s = _timed(lambda: service.prepare_pool(
            session, deck_id, intent, provider, model=model,
        ))
    legal = [c for c in prepared.shaped if c.legal_in_deck and c.name]
    legal_names = {c.name.lower() for c in legal}
    in_pool = {n.lower() for n in held} & legal_names
    shuffled = list(prepared.shaped)
    random.Random(f"shuffle:{deck_id}:{role}").shuffle(shuffled)

    record = {
        "deck_id": deck_id, "commander": prepared.snapshot.get("commander"),
        "role": role, "intent": intent, "held_out": held,
        "held_in_pool": sorted(in_pool), "legal_pool": len(legal),
        "prepare_s": prep_s, "selectors": {},
    }

    def total(c):
        return float((c.brainmap or {}).get("total") or 0.0)

    def rate(c):
        return float((c.edhrec or {}).get("inclusion_rate") or 0.0)

    for name, key in (("brainmap", total), ("edhrec", rate)):
        order = [c.name for c in sorted(legal, key=key, reverse=True)]
        record["selectors"][name] = {"order": order, **_score(order, in_pool, True)}

    def jev_order(pool, mode, samples):
        selection = jev.select_jev(
            jev_client, pool, intent, max_picks=TOP,
            deck_context=prepared.deck_context, mode=mode, samples=samples,
        )
        return [j["name"] for j in selection.raw["judgments"]]

    for name, (mode, samples) in JEV_SELECTORS.items():
        order, secs = _timed(lambda: jev_order(prepared.shaped, mode, samples))
        again = jev_order(prepared.shaped, mode, samples)
        reshuffled = jev_order(shuffled, mode, samples)
        record["selectors"][name] = {
            "order": order, "seconds": secs, **_score(order, in_pool, True),
            "stable": _overlap(order, reshuffled), "determinism": _overlap(order, again),
        }

    if with_llm:
        def llm_picks(pool):
            prep = service.PreparedPool(**{**prepared.__dict__, "shaped": pool})
            selection, _ = service.run_selection(
                prep, intent, provider, backend="llm", model=model, max_picks=TOP,
            )
            return [p.name for p in selection.picks]

        picks, secs = _timed(lambda: llm_picks(prepared.shaped))
        reshuffled = llm_picks(shuffled)
        record["selectors"]["llm"] = {
            "order": picks, "seconds": secs, **_score(picks, in_pool, False),
            "stable": _overlap(picks, reshuffled),
        }
    return record


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def summarize(records: list[dict]) -> dict:
    scored = [r for r in records if len(r["held_in_pool"]) >= 2]
    names = [*BASELINES, *JEV_SELECTORS] + (["llm"] if any("llm" in r["selectors"] for r in records) else [])
    summary = {"cases": len(records), "scored_cases": len(scored),
               "pool_recall": _mean([len(r["held_in_pool"]) / len(r["held_out"]) for r in records]),
               "chance_recall": _mean([TOP / r["legal_pool"] for r in scored]),
               "selectors": {}}
    for name in names:
        rows = [r["selectors"][name] for r in scored if name in r["selectors"]]
        # Paired against the stronger baseline, case by case: an average can
        # hide a selector that wins big on a few decks and loses on the rest.
        paired = [(r["selectors"][name]["hits"], r["selectors"]["edhrec"]["hits"])
                  for r in scored if name in r["selectors"]]
        summary["selectors"][name] = {
            "vs_edhrec": [sum(a > b for a, b in paired), sum(a == b for a, b in paired),
                          sum(a < b for a, b in paired)],
            "recall@10": _mean([s["recall"] for s in rows]),
            "percentile": _mean([s.get("percentile") for s in rows]),
            "stable": _mean([s.get("stable") for s in rows]),
            "determinism": _mean([s.get("determinism") for s in rows]),
            "seconds": _mean([s.get("seconds") for s in rows]),
        }
    return summary


def _fmt(value, spec):
    return format(value, spec) if value is not None else "-"


def print_summary(summary: dict) -> None:
    print(f"\n== {summary['scored_cases']} scored cases of {summary['cases']} "
          f"(held-out cards reaching the pool: {_fmt(summary['pool_recall'], '.0%')}; "
          f"chance recall@10: {_fmt(summary['chance_recall'], '.0%')})")
    print(f"   {'selector':<10}{'recall@10':>10}{'percentile':>12}{'stable':>9}{'determ.':>9}"
          f"{'secs':>7}   W-T-L vs edhrec")
    for name, s in summary["selectors"].items():
        wtl = "-".join(str(n) for n in s["vs_edhrec"])
        print(f"   {name:<10}{_fmt(s['recall@10'], '.0%'):>10}{_fmt(s['percentile'], '.2f'):>12}"
              f"{_fmt(s['stable'], '.1f'):>9}{_fmt(s['determinism'], '.1f'):>9}{_fmt(s['seconds'], '.1f'):>7}"
              f"   {wtl}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--deck", type=int, action="append")
    parser.add_argument("--no-llm", action="store_true")
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
    from app.pipeline import jev

    init_db()
    jev_client = jev.get_client()
    provider = get_provider()
    model = get_fast_model()

    records = []
    with Session(get_engine()) as session:
        cases = _held_out_cases(session, args.deck)
        print(f"{len(cases)} case(s)")
        for deck_id, role, held in cases:
            try:
                record = run_case(session, deck_id, role, held, provider, jev_client, model,
                                  not args.no_llm)
            except Exception as exc:  # noqa: BLE001 - one failed case must not sink the run
                print(f"  deck {deck_id:>2} {role:<13} FAILED: {exc}")
                continue
            sel = record["selectors"]
            line = " ".join(f"{n}={sel[n]['hits']}" for n in sel)
            print(f"  deck {deck_id:>2} {role:<13} held {len(held)} in pool "
                  f"{len(record['held_in_pool'])}/{record['legal_pool']} | hits@10 {line}")
            records.append(record)

    summary = summarize(records)
    print_summary(summary)
    TRACE_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = TRACE_DIR / f"jev_eval_{stamp}.json"
    out.write_text(json.dumps({"summary": summary, "records": records}, indent=2, default=str),
                   encoding="utf-8")
    print(f"\ntrace: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
