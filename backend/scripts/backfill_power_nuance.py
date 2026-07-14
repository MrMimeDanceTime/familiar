"""Backfill the LLM power-level nuance for finished decks.

Computing the nuance (see app/tools/power_nuance.py) caches it on the Deck row,
so every later read reuses it for free until the deck is edited. This script
warms that cache for decks you're done building, so the nuanced power level is
already present the first time you open them — no first-read latency.

It's idempotent: a deck whose cached nuance already matches its current content
is skipped unless --force is given. One Pro call per deck actually computed.

Usage (from backend/):
    python -m scripts.backfill_power_nuance                 # all substantial decks
    python -m scripts.backfill_power_nuance --min-cards 40  # lower the threshold
    python -m scripts.backfill_power_nuance --deck-id 8 --deck-id 12   # specific decks
    python -m scripts.backfill_power_nuance --force         # recompute even fresh caches
    python -m scripts.backfill_power_nuance --dry-run       # show what would run, no calls
"""

from __future__ import annotations

import argparse

from sqlmodel import select

from app.db.models import Deck, DeckCard
from app.db.session import get_session, init_db
from app.llm.factory import get_provider
from app.tools.deck_tools import compute_deck_stats
from app.tools.power_nuance import deck_content_hash


def _card_count(session, deck_id: int) -> int:
    return len(list(session.exec(select(DeckCard).where(DeckCard.deck_id == deck_id))))


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill LLM power-level nuance for decks.")
    parser.add_argument("--min-cards", type=int, default=60,
                        help="Only backfill decks with at least this many cards (default 60). "
                             "Ignored when --deck-id is given.")
    parser.add_argument("--deck-id", type=int, action="append", dest="deck_ids",
                        help="Backfill only these deck id(s). Repeatable. Overrides --min-cards.")
    parser.add_argument("--force", action="store_true",
                        help="Recompute even when the cached nuance is already fresh.")
    parser.add_argument("--dry-run", action="store_true",
                        help="List what would be computed/skipped without making any LLM calls.")
    args = parser.parse_args()

    init_db()  # applies the additive-column migration, exactly like app startup

    provider = None if args.dry_run else get_provider()
    computed = skipped = 0

    with get_session() as session:
        if args.deck_ids:
            decks = [d for d in (session.get(Deck, i) for i in args.deck_ids) if d]
        else:
            decks = list(session.exec(select(Deck)))

        for deck in sorted(decks, key=lambda d: d.id or 0):
            n = _card_count(session, deck.id)
            reason_skip = None
            if deck.format != "commander":
                reason_skip = f"not commander ({deck.format})"
            elif not args.deck_ids and n < args.min_cards:
                reason_skip = f"{n} cards < {args.min_cards}"
            else:
                snapshot_key = deck_content_hash(_snapshot(session, deck.id))
                if not args.force and deck.power_nuance_key == snapshot_key \
                        and deck.power_nuance_adj is not None:
                    reason_skip = "cache fresh"

            if reason_skip:
                skipped += 1
                print(f"[{deck.id:>2}] {(deck.name or '')[:28]:<28} SKIP ({reason_skip})")
                continue

            if args.dry_run:
                computed += 1
                print(f"[{deck.id:>2}] {(deck.name or '')[:28]:<28} WOULD COMPUTE ({n} cards)")
                continue

            stats = compute_deck_stats(session, deck.id, provider)
            adj = stats["power_nuance_adj"]
            adj_str = f"{adj:+}" if adj else " 0 "
            computed += 1
            print(f"[{deck.id:>2}] {(deck.name or '')[:28]:<28} "
                  f"base {stats['power_level_base']} -> {stats['power_level']} ({adj_str})")
            if stats["power_nuance_reason"]:
                print(f"       {stats['power_nuance_reason']}")

    verb = "would compute" if args.dry_run else "computed"
    print(f"\ndone: {computed} {verb}, {skipped} skipped")


def _snapshot(session, deck_id: int) -> dict:
    from app.db import repository as repo
    return repo.deck_snapshot(session, deck_id)


if __name__ == "__main__":
    main()
