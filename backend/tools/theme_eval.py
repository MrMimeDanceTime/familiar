"""Theme requests measured against public decklists.

The held-out eval in jev_eval.py takes its ground truth from the player's own
decks, most of them built with this engine, so it partly measures agreement
with the engine's own past picks; and its theme cases ("cards with no generic
role") were not defined by a theme at all. Every theme decision in
docs/PIPELINE.md was judged on that.

This takes ground truth from Archidekt instead: for a spread of commanders
(new releases, off-meta picks, two of the player's), the most-viewed public
decks carrying the commander's most common mechanical tag. For each deck, the
cards that define the theme (common in that tag's decks, uncommon in the
commander's decks overall) are held out, and the pipeline is asked for "more
<theme> cards" on the rest. None of it has ever seen this engine.

    python tools/theme_eval.py fetch        # pull and cache decks (read-only, polite)
    python tools/theme_eval.py run          # measure; calls Jev, not the LLM

Decks cache in tools/eval_decks/archidekt/ so runs repeat on the same data.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CACHE = Path(__file__).resolve().parent / "eval_decks" / "archidekt"
SEARCH = "https://archidekt.com/api/decks/v3/"
DECK = "https://archidekt.com/api/decks/{id}/"

COMMANDERS = [
    "Vivi Ornitier", "Sephiroth, Fabled SOLDIER // Sephiroth, One-Winged Angel",
    "Wan Shi Tong, Librarian", "Electro, Assaulting Battery", "Tataru Taru",
    "Anikthea, Hand of Erebos", "Hashaton, Scarab's Fist", "Kess, Dissident Mage",
    "Tatsunari, Toad Rider", "Zhulodok, Void Gorger", "Lord Windgrace",
    "Isshin, Two Heavens as One", "Gishath, Sun's Avatar", "Yuriko, the Tiger's Shadow",
    "Massacre Girl, Known Killer", "Prosper, Tome-Bound",
]
# Tags that say how a deck plays or what it costs, not what it is about.
GENERIC_TAGS = {
    "budget", "casual", "good stuff", "goodstuff", "commander matters", "draw", "boardwipes",
    "control", "combo", "aggro", "midrange", "salty", "stax", "politics", "group slug",
    "cedh", "competitive", "high power", "precon", "upgraded precon", "big mana", "ramp",
    "removal", "tutors", "cantrips", "value", "tempo", "fun", "jank", "voltron", "creatures",
    "legends", "pauper", "blinged", "primer", "alternate wincon", "turbo", "optimized",
}
THEME_DECKS = 8
BASELINE_DECKS = 20
MIN_UPDATED = "2024-09-01"
HOLD_MAX = 8
HOLD_MIN = 3


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.split(" // ")[0].lower()).strip("-")


def _get(client, url, params=None):
    time.sleep(0.6)  # polite: one request a little over every half second
    r = client.get(url, params=params)
    r.raise_for_status()
    return r.json()


def _cards(client, deck_id: int) -> tuple[list[str], list[str]]:
    """(non-commander card names, commander names) of a public deck."""
    data = _get(client, DECK.format(id=deck_id))
    # Categories are the deck owner's own; the deck marks which ones count.
    outside = {(c.get("name") or "").lower() for c in data.get("categories") or []
               if c.get("includedInDeck") is False}
    cards, commanders = [], []
    for entry in data.get("cards") or []:
        cats = [c.lower() for c in entry.get("categories") or []]
        if cats and cats[0] in outside:
            continue
        name = ((entry.get("card") or {}).get("oracleCard") or {}).get("name")
        if not name:
            continue
        (commanders if "commander" in cats else cards).append(name)
    return cards, commanders


def fetch() -> None:
    import httpx

    CACHE.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(headers={"User-Agent": "Familiar/1.0 (personal deckbuilder eval)",
                                   "Accept": "application/json"}, timeout=30, follow_redirects=True)
    for commander in COMMANDERS:
        path = CACHE / f"{_slug(commander)}.json"
        if path.exists():
            print(f"cached: {commander}")
            continue
        search_name = commander.split(" // ")[0]
        top = _get(client, SEARCH, dict(commanderName=search_name, formats=3, orderBy="-viewCount"))
        decks = [d for d in top.get("results") or []
                 if d.get("size") == 100 and (d.get("updatedAt") or "") >= MIN_UPDATED]
        tags = Counter(t["name"] for d in decks for t in d.get("tags") or []
                       if t.get("name") and t["name"].lower() not in GENERIC_TAGS)
        if not tags:
            print(f"skip {commander}: no mechanical tags among {len(decks)} decks")
            continue
        tag = tags.most_common(1)[0][0]
        tagged = _get(client, SEARCH, dict(commanderName=search_name, formats=3,
                                           orderBy="-viewCount", deckTagName=tag))
        theme_meta = [d for d in tagged.get("results") or []
                      if d.get("size") == 100 and (d.get("updatedAt") or "") >= MIN_UPDATED][:THEME_DECKS]
        theme_ids = {d["id"] for d in theme_meta}
        base_meta = [d for d in decks if d["id"] not in theme_ids][:BASELINE_DECKS]
        record = {"commander": commander, "tag": tag, "tag_counts": tags.most_common(6),
                  "theme": [], "baseline": []}
        for key, metas in (("theme", theme_meta), ("baseline", base_meta)):
            for meta in metas:
                cards, commanders = _cards(client, meta["id"])
                if search_name.lower() not in " ".join(commanders).lower():
                    continue  # the search matched a partner or a cameo
                record[key].append({"id": meta["id"], "name": meta["name"],
                                    "views": meta.get("viewCount"), "cards": cards})
        path.write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{commander}: tag {tag!r}, {len(record['theme'])} theme decks, "
              f"{len(record['baseline'])} baseline")


def _rates(decks: list[dict]) -> dict[str, float]:
    counts = Counter(n.lower() for d in decks for n in set(d["cards"]))
    return {n: c / len(decks) for n, c in counts.items()} if decks else {}


def theme_cards(record: dict, deck: dict, lands: set[str]) -> list[str]:
    """The cards in ``deck`` that define the theme: in at least 30% of the
    OTHER theme decks and at least 1.5x as common there as across the
    commander's decks overall. Lands excluded."""
    others = [d for d in record["theme"] if d["id"] != deck["id"]]
    theme_rate = _rates(others)
    base_rate = _rates(record["baseline"] + others)
    out = []
    for name in deck["cards"]:
        n = name.lower()
        if n in lands:
            continue
        rt = theme_rate.get(n, 0.0)
        if rt >= 0.3 and rt / max(base_rate.get(n, 0.0), 0.05) >= 1.5:
            out.append(name)
    return sorted(set(out))


# What a theme's cards say, for the strict reading of the ground truth: a
# held-out card counts there only if its own text, type or keywords name the
# theme. The broad reading (distinctive in the tag's decks) also catches cards
# tuned decks run more of, such as Lord Windgrace "Lands Matter" decks' board
# wipes, which "more Lands Matter cards" rightly does not return.
THEME_TEXT = {
    "spellslinger": r"instant|sorcery|noncreature spell|magecraft|prowess",
    "+1/+1 counters": r"\+1/\+1 counter|proliferate",
    "-1/-1 counters": r"-1/-1|wither|proliferate",
    "storm": r"storm|copy|instant|sorcery|noncreature spell|add \{r\}\{r\}",
    "group hug": r"each player|each opponent may|target player draws|all players",
    "enchantments": r"enchantment",
    "reanimator": r"graveyard|mill|discard",
    "eldrazi": r"eldrazi|colorless|cascade|annihilator|emerge",
    "lands matter": r"\blands?\b|landfall",
    "attack triggers": r"attack|combat",
    "dinosaurs": r"dinosaur|enrage",
    "ninjutsu": r"ninjutsu|ninja|unblocked|combat damage to a player",
    "treasure": r"treasure|artifact",
}


def strict(tag: str, card: dict | None) -> bool:
    pattern = THEME_TEXT.get(tag.lower())
    if not pattern or not card:
        return False
    text = " ".join(str(card.get(k) or "") for k in ("oracle_text", "type_line", "keywords")).lower()
    return re.search(pattern, text) is not None


VARIANTS = {
    "current": {},
    "theme signal": {"theme_signal": True},
    "whole page 120": {"theme_whole_page": True, "theme_pool_cap": 120},
}


def run(limit_commanders: int | None, only: str | None) -> list[dict]:
    from app.config import settings
    from app.db.session import get_engine, init_db
    from tools.behaviour_eval import _scratch_copy

    settings.familiar_db_path = str(_scratch_copy(settings.db_path))
    get_engine.cache_clear()
    init_db()
    from sqlmodel import Session

    from app.cards import store
    from app.db import repository as repo
    from app.llm.factory import get_fast_model, get_provider
    from app.pipeline import jev, service
    from app.tools import deck_tools

    provider, client, model = get_provider(), jev.get_client(), get_fast_model()
    records = []
    files = sorted(CACHE.glob("*.json"))[:limit_commanders]
    with Session(get_engine()) as session:
        for path in files:
            record = json.loads(path.read_text(encoding="utf-8"))
            if only and only.lower() not in record["commander"].lower():
                continue
            names = {n for d in record["theme"] for n in d["cards"]}
            known = store.by_names(list(names))
            lands = {n for n, c in known.items() if "Land" in (c.get("type_line") or "")}
            for deck in record["theme"]:
                theme = theme_cards(record, deck, lands)
                if len(theme) < HOLD_MIN:
                    continue
                held = sorted(random.Random(f"{deck['id']}").sample(theme, min(HOLD_MAX, len(theme))))
                held_lower = {h.lower() for h in held}
                kept = [n for n in deck["cards"] if n.lower() not in held_lower]
                built = repo.create_deck(session, name=f"eval {deck['id']}", commander=record["commander"])
                deck_tools.import_decklist(session, built.id, "\n".join(f"1 {n}" for n in kept))
                repo.update_deck(session, built.id, themes=[record["tag"].lower()])
                intent = f"more {record['tag']} cards"
                held_cards = store.by_names(held)
                row = {"commander": record["commander"], "tag": record["tag"], "deck": deck["id"],
                       "held": held, "variants": {},
                       "strict": [h for h in held if strict(record["tag"], held_cards.get(h.lower()))]}
                for label, kwargs in VARIANTS.items():
                    t = time.perf_counter()
                    prepared = service.prepare_pool(session, built.id, intent, provider, model=model, **kwargs)
                    legal = [c for c in prepared.shaped if c.legal_in_deck and c.name]
                    in_pool = held_lower & {c.name.lower() for c in legal}
                    selection, backend = service.run_selection(
                        prepared, intent, provider, backend="jev", model=model, max_picks=10,
                        jev_client=client,
                    )
                    picks = [p.name.lower() for p in selection.picks]
                    by_rate = sorted(legal, key=lambda c: float((c.edhrec or {}).get("inclusion_rate") or 0),
                                     reverse=True)
                    row["variants"][label] = {
                        "picks": picks, "pool": sorted({c.name.lower() for c in legal}),
                        "reach": len(in_pool) / len(held),
                        "picked": len(held_lower & set(picks)) / len(held),
                        "edhrec_top10": len(held_lower & {c.name.lower() for c in by_rate[:10]}) / len(held),
                        "backend": backend, "seconds": round(time.perf_counter() - t, 1),
                    }
                records.append(row)
                v = row["variants"]
                print(f"{record['commander'][:28]:<28} {record['tag'][:16]:<16} held {len(held)}  "
                      + "  ".join(f"{k}: {x['picked']:.0%} (reach {x['reach']:.0%})" for k, x in v.items()),
                      flush=True)
    return records


def summarize(records: list[dict]) -> None:
    import statistics

    print(f"\n{len(records)} decks across {len({r['commander'] for r in records})} commanders")
    for label in VARIANTS:
        rows = [r["variants"][label] for r in records]
        print(f"  {label:<16} picked {statistics.mean(x['picked'] for x in rows):.0%}   "
              f"reach {statistics.mean(x['reach'] for x in rows):.0%}   "
              f"EDHREC top 10 of the same pool {statistics.mean(x['edhrec_top10'] for x in rows):.0%}   "
              f"{statistics.median(x['seconds'] for x in rows):.1f}s")
    scored = [r for r in records if r.get("strict")]
    print(f"strict reading: {len(scored)} decks, {sum(len(r['strict']) for r in scored)} held cards "
          "whose own text names the theme")
    for label in VARIANTS:
        picked = [len({s.lower() for s in r["strict"]} & set(r["variants"][label]["picks"])) / len(r["strict"])
                  for r in scored]
        reach = [len({s.lower() for s in r["strict"]} & set(r["variants"][label]["pool"])) / len(r["strict"])
                 for r in scored]
        print(f"  {label:<16} picked {statistics.mean(picked):.0%}   reach {statistics.mean(reach):.0%}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("fetch")
    rp = sub.add_parser("run")
    rp.add_argument("--limit", type=int, default=None)
    rp.add_argument("--only", default=None)
    args = parser.parse_args(argv)
    if args.command == "fetch":
        fetch()
        return 0
    records = run(args.limit, args.only)
    out = Path(__file__).resolve().parent / "traces" / f"theme_eval_{time.strftime('%Y%m%dT%H%M%S')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(records, indent=1), encoding="utf-8")
    summarize(records)
    print(f"trace: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
