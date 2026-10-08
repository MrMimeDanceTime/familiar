"""Does the engine find the card that completes a combo?

For every combo already in the player's decks (Commander Spellbook), one
non-commander piece is removed, along with this deck's proposal history for
it. Then:

  * scan  - does ``combos.near_combos`` (the deck context's "one card from a
            combo" line, top 5) name the removed piece?
  * batch - does suggest_cards for a combo / win-condition request return it?
            Calls Jev; ``--no-batch`` skips it.

There is no taste in this: the removed card completes a known combo with
cards the deck still holds, so a request for a combo should surface it.

    python tools/combo_eval.py
    python tools/combo_eval.py --no-batch

Runs against a scratch copy of the DB; a JSON trace lands in tools/traces/.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from tools.jev_eval import TRACE_DIR, _HeldOut  # noqa: E402

INTENT = "a combo or a way to win for this deck"
PER_DECK = 3


def cases(session) -> list[tuple[int, str, list[str]]]:
    """(deck, removed piece, the combo's cards), up to PER_DECK per deck."""
    from app.cards import combos
    from app.db import repository as repo

    out = []
    for deck in repo.list_decks(session):
        snapshot = repo.deck_snapshot(session, deck.id)
        commanders = {(n or "").lower() for n in (deck.commander, deck.partner_commander) if n}
        names = [c["name"] for c in snapshot["cards"]]
        taken: set[str] = set()
        for combo in combos.combos_in_deck(names):
            pieces = [n for n in combo["cards"] if n.lower() not in commanders and n.lower() not in taken]
            if not pieces:
                continue
            piece = sorted(pieces)[0]
            taken.add(piece.lower())
            out.append((deck.id, piece, combo["cards"]))
            if sum(1 for d, _, _ in out if d == deck.id) >= PER_DECK:
                break
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-batch", action="store_true")
    args = parser.parse_args(argv)

    import logging

    logging.basicConfig(level=logging.ERROR)
    from app.config import settings
    from app.db.session import get_engine
    from tools.behaviour_eval import _scratch_copy

    settings.familiar_db_path = str(_scratch_copy(settings.db_path))
    get_engine.cache_clear()
    from sqlmodel import Session

    from app.cards import combos, store
    from app.db import repository as repo
    from app.db.session import init_db
    from app.llm.factory import get_fast_model, get_provider
    from app.pipeline import jev, service

    init_db()
    provider, model = get_provider(), get_fast_model()
    client = None if args.no_batch else jev.get_client()
    records = []
    with Session(get_engine()) as session:
        for deck_id, piece, combo_cards in cases(session):
            with _HeldOut(session, deck_id, [piece]):
                snapshot = repo.deck_snapshot(session, deck_id)
                commanders = [n for n in (snapshot.get("commander"), snapshot.get("partner_commander")) if n]
                identity = "".join(
                    "".join((store.by_name(n) or {}).get("color_identity") or []) for n in commanders)
                every = combos.near_combos([c["name"] for c in snapshot["cards"]], identity,
                                           commanders=commanders, limit=10_000)
                near = every[:5]
                completes = {c["missing"].lower(): c for c in every}
                scan = piece.lower() in {c["missing"].lower() for c in near}
                batch = None
                picks: list[str] = []
                in_pool, combo_cards_in_pool, piece_judgment = None, None, None
                combo_picks, winning_picks = [], []
                if client is not None:
                    prepared = service.prepare_pool(session, deck_id, INTENT, provider, model=model)
                    selection, backend = service.run_selection(
                        prepared, INTENT, provider, backend="jev", model=model, max_picks=6,
                        jev_client=client,
                    )
                    picks = [p.name for p in selection.picks]
                    batch = piece.lower() in {p.lower() for p in picks}
                    legal = [c for c in prepared.shaped if c.legal_in_deck]
                    in_pool = piece.lower() in {c.name.lower() for c in legal}
                    combo_cards_in_pool = sum(1 for c in legal if c.completes_combo_with)
                    judged = {j.get("name", "").lower(): j for j in (selection.raw or {}).get("judgments") or []}
                    piece_judgment = judged.get(piece.lower())
                    combo_picks = [p for p in picks if p.lower() in completes]
                    winning_picks = [p for p in combo_picks if completes[p.lower()]["wins"]]
            records.append({"deck_id": deck_id, "commander": snapshot.get("commander"), "piece": piece,
                            "combo": combo_cards, "scan": scan, "batch": batch, "picks": picks,
                            "near": [c["missing"] for c in near],
                            "in_pool": in_pool, "combo_cards_in_pool": combo_cards_in_pool,
                            "piece_judgment": piece_judgment,
                            "combo_picks": combo_picks, "winning_picks": winning_picks})
            print(f"deck {deck_id:>3} {piece[:30]:<30} scan {'yes' if scan else 'no ':<3} "
                  f"batch {'-' if batch is None else ('yes' if batch else 'no')}  in pool {in_pool} "
                  f"| batch: {len(combo_picks)}/{len(picks)} complete a combo, {len(winning_picks)} winning", flush=True)

    n = len(records)
    print(f"\n{n} cases: scan names the piece {sum(r['scan'] for r in records)}/{n}", end="")
    if client is not None:
        print(f", batch returns it {sum(bool(r['batch']) for r in records)}/{n}")
        picks = sum(len(r["picks"]) for r in records)
        print(f"batch picks completing a combo: {sum(len(r['combo_picks']) for r in records)}/{picks}, "
              f"winning: {sum(len(r['winning_picks']) for r in records)}; "
              f"batches with at least one: {sum(bool(r['combo_picks']) for r in records)}/{n}")
    else:
        print()
    TRACE_DIR.mkdir(exist_ok=True)
    out = TRACE_DIR / f"combo_eval_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps(records, indent=1), encoding="utf-8")
    print(f"trace: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
