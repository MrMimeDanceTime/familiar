"""Measure stage-4 selectors for accuracy and consistency against ground truth
from the player's own decks.

Accuracy (leave-out recovery). For a deck and a role it fills (ramp, removal,
draw...), up to six of the deck's cards in that role are removed, along with
this deck's proposal history for them so the brain map's personal layer cannot
leak the answer. The pipeline is then asked for "more <role>". Synergy cases do
the same with cards that fill no generic role (the theme pieces), asking for
"cards that synergize with my commander and what the deck is doing". A
selector is accurate to the degree it puts the removed cards, the ones the
player actually chose, back in its batch. Reported per selector:

  * recall@10 - share of the removed cards that made the pool which the
    selector returned in its batch (at most 10);
  * percentile - mean position of those cards in the selector's full ranking,
    0.0 top, 0.5 no better than chance (full-ranking selectors only).

The ground truth is one player's choices, so a good card the player never ran
counts as a miss. That is the point: it measures fit to THIS player's decks.

Consistency. Every selector runs again on the same pool in shuffled order
(stability), and Jev once more in the original order (determinism); reported
as batch overlap between runs.

Pool diagnosis. For held-out cards that miss the pool, whether retrieval never
found them, the pool cap cut them, or they were marked illegal.

Every run is compared against tools/jev_eval_baseline.json; ``--save`` moves
the baseline, and belongs in a commit that says why.

    python tools/jev_eval.py                 # all decks, LLM included (costs money)
    python tools/jev_eval.py --no-llm        # Jev and the baselines only
    python tools/jev_eval.py --deck 1 --deck 9
    python tools/jev_eval.py --pool-cap 150  # a wider pool for every selector
    python tools/jev_eval.py --all-selectors # also the modes that lost earlier rounds
    python tools/jev_eval.py --save          # move the baseline
    python tools/jev_eval.py --cuts          # rank cuts against the player's own removals

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
BASELINE_PATH = Path(__file__).resolve().parent / "jev_eval_baseline.json"

SYNERGY_INTENT = "cards that synergize with my commander and what the deck is doing"
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
# Roles any deck fills; a card with none of them is there for the theme.
GENERIC_ROLES = frozenset({
    "ramp", "fast-mana", "fixing", "card-draw", "card-selection", "wheel",
    "spot-removal", "board-wipe", "counterspell", "land-destruction", "tutor",
    "recursion", "graveyard-hate", "protection", "land",
})

# name -> select_jev keyword arguments
JEV_SELECTORS = {
    "verdict3": {"mode": "verdict", "samples": 3},
    "similar2": {"mode": "verdict", "samples": 3, "max_similar": 2},
    "similar3": {"mode": "verdict", "samples": 3, "max_similar": 3},
    "p>=0.5": {"mode": "verdict", "samples": 3, "min_probability": 0.5},
}
ALL_SELECTORS = {
    **JEV_SELECTORS,
    "blind": {"mode": "blind", "samples": 1},
    "informed": {"mode": "informed", "samples": 1},
    "verdict": {"mode": "verdict", "samples": 1},
    "choice3": {"mode": "choice", "samples": 3},
    "ensemble": {"mode": "ensemble", "samples": 3},
}
BASELINES = ("brainmap", "edhrec")
MAX_HELD = 6
MIN_HELD = 4
ROLES_PER_DECK = 2
TOP = 10


def _intent(role: str) -> str:
    return SYNERGY_INTENT if role == "synergy" else ROLE_INTENTS[role]


def _held_out_cases(session, deck_ids: list[int] | None) -> list[tuple[int, str, list[str]]]:
    """(deck, role, held-out names): each deck's two best-represented roles,
    plus a synergy case from its roleless theme cards."""
    from app.db import repository as repo
    from app.pipeline import roles

    cases = []
    for deck in repo.list_decks(session):
        if deck_ids and deck.id not in deck_ids:
            continue
        commanders = {n.lower() for n in (deck.commander, deck.partner_commander) if n}
        by_role: dict[str, list[str]] = {}
        theme: list[str] = []
        for card in repo.list_deck_cards(session, deck.id):
            if card.card_name.lower() in commanders or "Land" in (card.type_line or ""):
                continue
            fine = roles.fine_roles_for_tags(card.tags or [], card.type_line)
            for role in fine & set(ROLE_INTENTS):
                by_role.setdefault(role, []).append(card.card_name)
            if not fine & GENERIC_ROLES:
                theme.append(card.card_name)
        ranked = sorted(by_role.items(), key=lambda kv: len(kv[1]), reverse=True)
        chosen = [kv for kv in ranked if len(kv[1]) >= MIN_HELD][:ROLES_PER_DECK]
        if len(theme) >= MIN_HELD:
            chosen.append(("synergy", theme))
        for role, names in chosen:
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


def _score(picks: list[str], held: set[str], full_order: list[str] | None = None) -> dict:
    hits = sum(1 for n in picks[:TOP] if n.lower() in held)
    result = {"hits": hits, "recall": round(hits / len(held), 3) if held else None}
    if full_order and held and len(full_order) > 1:
        lowered = [n.lower() for n in full_order]
        positions = [lowered.index(n) / (len(lowered) - 1) for n in held if n in lowered]
        result["percentile"] = round(sum(positions) / len(positions), 3) if positions else None
    return result


def run_case(session, deck_id: int, role: str, held: list[str], provider, jev_client,
             model: str, with_llm: bool, selectors: dict, pool_cap: int,
             role_limit: int) -> dict:
    from app.pipeline import explain, jev, service

    intent = _intent(role)
    with _HeldOut(session, deck_id, held):
        prepared, prep_s = _timed(lambda: service.prepare_pool(
            session, deck_id, intent, provider, model=model, pool_cap=pool_cap,
            local_role_limit=role_limit,
        ))
    legal = [c for c in prepared.shaped if c.legal_in_deck and c.name]
    in_pool = {n.lower() for n in held} & {c.name.lower() for c in legal}
    shuffled = list(prepared.shaped)
    random.Random(f"shuffle:{deck_id}:{role}").shuffle(shuffled)

    held_lower = {n.lower() for n in held}
    raw_names = {(c.get("name") or "").lower() for c in prepared.pool}
    shaped_by_name = {c.name.lower(): c for c in prepared.shaped if c.name}
    record = {
        "deck_id": deck_id, "commander": prepared.snapshot.get("commander"),
        "role": role, "intent": intent, "held_out": held,
        "held_in_pool": sorted(in_pool), "legal_pool": len(legal),
        "prepare_s": prep_s, "selectors": {},
        "missed": {
            "never_retrieved": sorted(held_lower - raw_names),
            "cut_by_cap": sorted((held_lower & raw_names) - set(shaped_by_name)),
            "illegal": {n: shaped_by_name[n].illegal_reasons
                        for n in held_lower & set(shaped_by_name)
                        if not shaped_by_name[n].legal_in_deck},
        },
    }

    def total(c):
        return float((c.brainmap or {}).get("total") or 0.0)

    def rate(c):
        return float((c.edhrec or {}).get("inclusion_rate") or 0.0)

    for name, key in (("brainmap", total), ("edhrec", rate)):
        order = [c.name for c in sorted(legal, key=key, reverse=True)]
        record["selectors"][name] = {"order": order, **_score(order, in_pool, order)}

    def jev_run(pool, kwargs):
        selection = jev.select_jev(
            jev_client, pool, intent, max_picks=TOP,
            deck_context=prepared.deck_context, **kwargs,
        )
        order = [j["name"] for j in selection.raw["judgments"]]
        return selection, [p.name for p in selection.picks], order

    for name, kwargs in selectors.items():
        (selection, picks, order), secs = _timed(lambda: jev_run(prepared.shaped, kwargs))
        _, again, _ = jev_run(prepared.shaped, kwargs)
        _, reshuffled, _ = jev_run(shuffled, kwargs)
        record["selectors"][name] = {
            "picks": picks, "order": order, "seconds": secs, "batch": len(picks),
            **_score(picks, in_pool, order),
            "stable": _overlap(picks, reshuffled), "determinism": _overlap(picks, again),
        }
        if name == "verdict3" and with_llm:
            explained, explain_s = _timed(lambda: explain.explain(
                provider, selection, prepared.shaped, intent, model=model,
                deck_context=prepared.deck_context,
            ))
            record["explain"] = {
                "seconds": explain_s, "summary": explained.summary,
                "picks": [{"name": p.name, "reason": p.reason} for p in explained.picks],
                "cuts": [{"name": c.name, "reason": c.reason} for c in explained.cuts],
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
            "picks": picks, "seconds": secs, "batch": len(picks), **_score(picks, in_pool),
            "stable": _overlap(picks, reshuffled),
        }
    return record


CUT_INTENT = "tighten the deck: find the cards that are weakest for its plan"


_LLM_CUT_PROMPT = """\
You are helping trim a Magic: The Gathering Commander deck. Given the deck,
name the {k} cards currently in it that are the weakest for its plan and
commander: the ones to cut first. Choose only from the cut candidates listed,
by exact name. Output ONLY a JSON object: {{"cuts": ["<name>", ...]}}\
"""


def _llm_cuts(provider, model, candidates, deck_context, k: int, thinking: bool) -> list[str]:
    lines = []
    for c in candidates:
        text = " ".join(str(c.get("oracle_text") or "").split())
        lines.append(f"- {c['name']} | {c.get('type_line') or ''} | {text}")
    user = f"{deck_context}\n\nCut candidates:\n" + "\n".join(lines)
    raw = provider.complete_json(
        _LLM_CUT_PROMPT.format(k=k), user, model=model, thinking=thinking,
        reasoning_effort="low" if thinking else None,
    )
    names = json.loads(raw).get("cuts") or []
    valid = {c["name"].lower(): c["name"] for c in candidates}
    return [valid[n.lower()] for n in names if isinstance(n, str) and n.lower() in valid][:k]


def run_cut_eval(session, jev_client, samples: int, min_removed: int = 5,
                 provider=None, model: str | None = None) -> list[dict]:
    """Cut accuracy against the player's own history. For each deck with
    enough approved removals, the removed cards go back into the deck and Jev
    ranks every cut candidate. Accurate means the cards the player actually
    removed rank highest, and the removals they DENIED rank low."""
    from sqlalchemy import text

    from app.cards import store
    from app.db import repository as repo
    from app.pipeline import jev, service

    rows = session.connection().execute(text(
        "SELECT deck_id, card_name, status FROM deck_proposals "
        "WHERE action = 'remove' AND status IN ('approved', 'denied') AND card_name IS NOT NULL"
    )).fetchall()
    by_deck: dict[int, dict[str, set[str]]] = {}
    for deck_id, name, status in rows:
        by_deck.setdefault(deck_id, {"approved": set(), "denied": set()})[status].add(name)

    records = []
    for deck_id, history in sorted(by_deck.items()):
        if repo.get_deck(session, deck_id) is None:
            continue
        snapshot = repo.deck_snapshot(session, deck_id)
        in_deck = {c["name"].lower() for c in snapshot["cards"]}
        restored = []
        for name in sorted(history["approved"]):
            if name.lower() in in_deck:
                continue  # removed and later re-added: not a clean verdict
            card = store.by_name(name)
            if card:
                restored.append({"name": card["name"], "type_line": card.get("type_line"),
                                 "oracle_text": card.get("oracle_text"), "tags": card.get("tags") or []})
        snapshot["cards"] = snapshot["cards"] + restored
        candidates = jev.cut_candidates(snapshot)
        names = {c["name"].lower() for c in candidates}
        removed = {c["name"].lower() for c in restored} & names
        denied = {n.lower() for n in history["denied"]} & names - removed
        if len(removed) < min_removed:
            continue
        state = jev.build_state(service._render_deck_context(snapshot), CUT_INTENT, None)
        evidence = jev.cut_evidence(snapshot.get("commander"))
        k = len(removed)

        def score(order: list[str]) -> dict:
            span = max(1, len(order) - 1)
            return {
                "recall_at_k": sum(1 for n in order[:k] if n in removed) / k,
                "removed_percentile": sum(order.index(n) for n in removed) / k / span,
                "denied_percentile": (sum(order.index(n) for n in denied) / len(denied) / span
                                      if denied else None),
            }

        def rate(card):
            seen = evidence.get(card["name"].lower()) or {}
            return seen.get("inclusion_rate") or 0.0

        variants = {}
        # Baseline: cut the cards this commander's decks play least.
        variants["edhrec_low"] = score([c["name"].lower() for c in sorted(candidates, key=rate)])
        for label, ev in (("jev", None), ("jev+edhrec", evidence)):
            ranking, secs = _timed(lambda: jev.rank_cuts(
                jev_client, candidates, state, samples=samples, evidence=ev,
            ))
            variants[label] = {**score([n.lower() for n, _ in ranking]), "seconds": secs,
                               "top": ranking[:k]}
        if provider is not None:
            context = service._render_deck_context(snapshot)
            for label, thinking in (("llm", False), ("llm-think", True)):
                picks, secs = _timed(lambda: _llm_cuts(provider, model, candidates, context, k, thinking))
                # A pick list, not a ranking: recall only, the rest of the
                # order is unknown.
                variants[label] = {
                    "recall_at_k": sum(1 for n in picks if n.lower() in removed) / k,
                    "removed_percentile": None,
                    "denied_percentile": None,
                    "denied_picked": sum(1 for n in picks if n.lower() in denied),
                    "seconds": secs, "top": picks,
                }
        records.append({
            "deck_id": deck_id, "commander": snapshot.get("commander"),
            "candidates": len(candidates), "removed": sorted(removed), "denied": sorted(denied),
            "chance": k / len(candidates), "variants": variants,
        })
    return records


def print_cut_records(records: list[dict]) -> None:
    for r in records:
        print(f"   {str(r['commander'])[:30]:<32} {r['candidates']} candidates, "
              f"{len(r['removed'])} removed, {len(r['denied'])} denied, chance {r['chance']:.0%}")
        for label, v in r["variants"].items():
            print(f"      {label:<12} recall@k {v['recall_at_k']:>4.0%}  removed pct "
                  f"{_fmt(v['removed_percentile'], '.2f')}  denied pct {_fmt(v['denied_percentile'], '.2f')}"
                  + (f"  denied picked {v['denied_picked']}" if "denied_picked" in v else ""))
    removed = sum(len(r["removed"]) for r in records) or 1
    chance = sum(r["chance"] * len(r["removed"]) for r in records) / removed
    print(f"\n   pooled over {removed} removed cards (chance recall@k {chance:.0%}; "
          f"percentile 0 = cut first, 0.5 = chance):")
    for label in records[0]["variants"] if records else []:
        hits = sum(r["variants"][label]["recall_at_k"] * len(r["removed"]) for r in records)
        pct = _mean([r["variants"][label]["removed_percentile"] for r in records]) or float("nan")
        den = _mean([r["variants"][label]["denied_percentile"] for r in records])
        print(f"      {label:<12} recall@k {hits / removed:.0%}  removed pct {pct:.2f}  "
              f"denied pct {_fmt(den, '.2f')}")


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def summarize(records: list[dict], selectors: dict, kind: str | None = None) -> dict:
    if kind == "synergy":
        records = [r for r in records if r["role"] == "synergy"]
    elif kind == "role":
        records = [r for r in records if r["role"] != "synergy"]
    scored = [r for r in records if len(r["held_in_pool"]) >= 2]
    names = [*BASELINES, *selectors]
    if any("llm" in r["selectors"] for r in records):
        names.append("llm")
    held = sum(len(r["held_out"]) for r in records) or 1
    summary = {
        "cases": len(records), "scored_cases": len(scored),
        "pool_recall": _mean([len(r["held_in_pool"]) / len(r["held_out"]) for r in records]),
        "chance_recall": _mean([TOP / r["legal_pool"] for r in scored if r["legal_pool"]]),
        "missed": {
            key: sum(len(r["missed"][key]) for r in records) / held
            for key in ("never_retrieved", "cut_by_cap", "illegal")
        },
        "explain_seconds": _mean([r["explain"]["seconds"] for r in records if "explain" in r]),
        "selectors": {},
    }
    for name in names:
        rows = [r["selectors"][name] for r in scored if name in r["selectors"]]
        # Paired against the stronger baseline, case by case: an average can
        # hide a selector that wins big on a few decks and loses on the rest.
        paired = [(r["selectors"][name]["hits"], r["selectors"]["edhrec"]["hits"])
                  for r in scored if name in r["selectors"]]
        # End to end: of every card held out, the share that came back in the
        # batch. recall@10 is conditional on reaching the pool, so a wider pool
        # can raise pool recall while lowering this; this is the one to trust.
        summary["selectors"][name] = {
            "end_to_end": sum(r["selectors"][name]["hits"] for r in records if name in r["selectors"])
                          / held,
            "recall@10": _mean([s["recall"] for s in rows]),
            "percentile": _mean([s.get("percentile") for s in rows]),
            "stable": _mean([s.get("stable") for s in rows]),
            "determinism": _mean([s.get("determinism") for s in rows]),
            "batch": _mean([s.get("batch") for s in rows]),
            "seconds": _mean([s.get("seconds") for s in rows]),
            "vs_edhrec": [sum(a > b for a, b in paired), sum(a == b for a, b in paired),
                          sum(a < b for a, b in paired)],
        }
    return summary


def _fmt(value, spec):
    return format(value, spec) if value is not None else "-"


def print_summary(summary: dict, title: str, baseline: dict | None) -> None:
    print(f"\n== {title}: {summary['scored_cases']} scored cases of {summary['cases']} "
          f"(held-out cards reaching the pool: {_fmt(summary['pool_recall'], '.0%')}; "
          f"chance recall@10: {_fmt(summary['chance_recall'], '.0%')})")
    missed = summary["missed"]
    print(f"   lost before selection: never retrieved {missed['never_retrieved']:.0%}, "
          f"cut by the pool cap {missed['cut_by_cap']:.0%}, illegal {missed['illegal']:.0%}")
    if summary.get("explain_seconds") is not None:
        print(f"   explainer (DeepSeek, thinking off): mean {summary['explain_seconds']:.1f}s")
    print(f"   {'selector':<10}{'end2end':>9}{'recall@10':>10}{'e2e vs base':>12}{'percentile':>12}{'stable':>9}"
          f"{'determ.':>9}{'batch':>7}{'secs':>7}   W-T-L vs edhrec")
    base = (baseline or {}).get("selectors", {})
    for name, s in summary["selectors"].items():
        wtl = "-".join(str(n) for n in s["vs_edhrec"])
        prior = (base.get(name) or {}).get("end_to_end")
        delta = (f"{(s['end_to_end'] - prior) * 100:+.0f}pt"
                 if prior is not None and s.get("end_to_end") is not None else "-")
        print(f"   {name:<10}{_fmt(s.get('end_to_end'), '.0%'):>9}{_fmt(s['recall@10'], '.0%'):>10}{delta:>12}"
              f"{_fmt(s['percentile'], '.2f'):>12}{_fmt(s['stable'], '.1f'):>9}"
              f"{_fmt(s['determinism'], '.1f'):>9}{_fmt(s['batch'], '.1f'):>7}"
              f"{_fmt(s['seconds'], '.1f'):>7}   {wtl}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--deck", type=int, action="append")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--pool-cap", type=int, default=60)
    parser.add_argument("--role-limit", type=int, default=80,
                        help="cards fetched per role from the local index")
    parser.add_argument("--all-selectors", action="store_true")
    parser.add_argument("--save", action="store_true", help="write this run as the baseline")
    parser.add_argument("--cuts", action="store_true",
                        help="evaluate cut ranking against approved and denied removals")
    parser.add_argument("--samples", type=int, default=3, help="Jev samples for --cuts")
    args = parser.parse_args(argv)
    selectors = ALL_SELECTORS if args.all_selectors else JEV_SELECTORS

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

    if args.cuts:
        with Session(get_engine()) as session:
            cut_records = run_cut_eval(
                session, jev_client, args.samples,
                provider=None if args.no_llm else provider, model=model,
            )
        print_cut_records(cut_records)
        TRACE_DIR.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = TRACE_DIR / f"jev_cuts_{stamp}.json"
        out.write_text(json.dumps(cut_records, indent=2, default=str), encoding="utf-8")
        print(f"\ntrace: {out}")
        return 0

    records = []
    with Session(get_engine()) as session:
        cases = _held_out_cases(session, args.deck)
        print(f"{len(cases)} case(s), pool cap {args.pool_cap}, role limit {args.role_limit}, "
              f"jev model {jev_client.model}")
        for deck_id, role, held in cases:
            try:
                record = run_case(session, deck_id, role, held, provider, jev_client, model,
                                  not args.no_llm, selectors, args.pool_cap,
                                  args.role_limit)
            except Exception as exc:  # noqa: BLE001 - one failed case must not sink the run
                print(f"  deck {deck_id:>2} {role:<13} FAILED: {exc}")
                continue
            sel = record["selectors"]
            line = " ".join(f"{n}={sel[n]['hits']}" for n in sel)
            print(f"  deck {deck_id:>2} {role:<13} held {len(held)} in pool "
                  f"{len(record['held_in_pool'])}/{record['legal_pool']} | hits {line}")
            records.append(record)

    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8")) if BASELINE_PATH.exists() else {}
    summary = {"pool_cap": args.pool_cap, "role_limit": args.role_limit,
               "jev_model": jev_client.model}
    for kind in ("role", "synergy", "all"):
        summary[kind] = summarize(records, selectors, None if kind == "all" else kind)
        print_summary(summary[kind], kind, baseline.get(kind))

    TRACE_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = TRACE_DIR / f"jev_eval_{stamp}.json"
    out.write_text(json.dumps({"summary": summary, "records": records}, indent=2, default=str),
                   encoding="utf-8")
    print(f"\ntrace: {out}")
    if args.save:
        BASELINE_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"baseline saved: {BASELINE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
