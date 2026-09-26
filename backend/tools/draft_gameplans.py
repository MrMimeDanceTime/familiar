"""Draft and store a gameplan for each deck that has none.

Search, Jev, and the chat all read a deck's plan (``plan_notes`` and
``themes``), and none of the decks had one. This drafts it once per deck from
the commander's rules text, the current cards, and the deck notes (one
non-thinking DeepSeek call each), writes it through ``deck_set_plan``, and
prints it so the player can correct it in the app or by re-running with
``--overwrite`` after editing the notes.

    python tools/draft_gameplans.py                  # decks without a plan
    python tools/draft_gameplans.py --deck 21        # one deck
    python tools/draft_gameplans.py --dry-run        # print, write nothing
    python tools/draft_gameplans.py --overwrite      # replace existing plans too

Writes to the LIVE database (unlike the eval tools).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--deck", type=int, action="append")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    import logging

    logging.basicConfig(level=logging.WARNING)

    from sqlmodel import Session

    from app.db import repository as repo
    from app.db.session import get_engine, init_db
    from app.llm.factory import get_fast_model, get_provider
    from app.pipeline import gameplan
    from app.tools.deck_tools import deck_set_plan

    init_db()
    provider = get_provider()
    model = get_fast_model()
    with Session(get_engine()) as session:
        for deck in repo.list_decks(session):
            if args.deck and deck.id not in args.deck:
                continue
            snapshot = repo.deck_snapshot(session, deck.id)
            if not snapshot.get("commander") or not snapshot.get("cards"):
                continue
            if (snapshot.get("plan_notes") or snapshot.get("themes")) and not args.overwrite:
                print(f"deck {deck.id} {deck.name!r}: has a plan, skipped")
                continue
            try:
                draft = gameplan.draft(provider, snapshot, model=model)
            except Exception as exc:  # noqa: BLE001 - one deck must not stop the rest
                print(f"deck {deck.id} {deck.name!r}: draft FAILED: {exc}")
                continue
            print(f"\n== deck {deck.id} {deck.name!r} ({snapshot['commander']})")
            print(f"   plan: {draft['plan']}")
            print(f"   themes: {', '.join(draft['themes'])}")
            if not args.dry_run:
                deck_set_plan(session, deck.id, themes=draft["themes"], plan_notes=draft["plan"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
