"""Scripted scenarios for ``behaviour_eval.py scripted``.

Replaying stored conversations measured drift more than behaviour: each ran
against its deck as it is NOW, so one conversation was replayed on a deck
that had become a different commander, and another's since-last-turn note
carried months of decisions the model had to reconcile. These start from a
fresh deck or a decklist checked into ``eval_decks/``, play the player as a
script that approves every proposal, and end with checks on the outcome.

A check takes ``(session, deck_id, turns)`` and returns ``(passed, detail)``;
``turns`` are the scored turn records in order.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

DECKS = Path(__file__).resolve().parent / "eval_decks"
REVIEW_DONE = "I've reviewed the proposals. Let's continue."

Check = Callable[[Any, int, list[dict]], tuple[bool, str]]


def _calls(turn: dict) -> list[str]:
    return [c.get("name") for c in turn.get("tool_calls") or []]


def _snapshot(session, deck_id):
    from app.db import repository as repo

    return repo.deck_snapshot(session, deck_id)


def _deck_proposals(session, deck_id):
    from sqlmodel import select

    from app.db.models import DeckProposal

    return list(session.exec(select(DeckProposal).where(DeckProposal.deck_id == deck_id)))


def used(tool: str, turn_index: int) -> Check:
    def check(session, deck_id, turns):
        names = _calls(turns[turn_index]) if turn_index < len(turns) else []
        return tool in names, f"turn {turn_index + 1} calls: {names}"
    check.__name__ = f"turn {turn_index + 1} used {tool}"
    return check


def commander_is(name: str) -> Check:
    def check(session, deck_id, turns):
        got = _snapshot(session, deck_id).get("commander")
        return (got or "").lower() == name.lower(), f"commander {got!r}"
    check.__name__ = f"commander is {name}"
    return check


def power_is(level: str) -> Check:
    def check(session, deck_id, turns):
        got = _snapshot(session, deck_id).get("power_level")
        return str(got) == level, f"power_level {got!r}"
    check.__name__ = f"power level {level}"
    return check


def at_most_100(session, deck_id, turns):
    total = _snapshot(session, deck_id).get("total_cards") or 0
    return total <= 100, f"{total} cards"


def no_proposed_type(word: str) -> Check:
    def check(session, deck_id, turns):
        from app.cards import store

        names = [p.card_name for p in _deck_proposals(session, deck_id)
                 if p.action == "add" and p.card_name]
        cards = store.by_names(names)
        bad = [n for n in names if word.lower() in ((cards.get(n.lower()) or {}).get("type_line") or "").lower()]
        return not bad, f"{len(names)} adds proposed, {word}: {bad}"
    check.__name__ = f"no {word} proposed"
    return check


def restriction_recorded(word: str) -> Check:
    def check(session, deck_id, turns):
        types = (_snapshot(session, deck_id).get("restrictions") or {}).get("exclude_types") or []
        return any(word.lower() in t.lower() for t in types), f"exclude_types {types}"
    check.__name__ = f"restriction on {word} recorded"
    return check


def land_target_met(session, deck_id, turns):
    from app.pipeline import manabase

    gap = manabase.land_gap(_snapshot(session, deck_id))
    return gap <= 1, f"land gap {gap}"


def cuts_are_lands(session, deck_id, turns):
    """Asked to trim lands, any cut must be a land. Pushing back instead (the
    deck's land count is on target) is a judgment call, not a failure."""
    from app.cards import store

    cuts = [p.card_name for p in _deck_proposals(session, deck_id) if p.action == "remove" and p.card_name]
    cards = store.by_names(cuts)
    wrong = [c for c in cuts if "Land" not in ((cards.get(c.lower()) or {}).get("type_line") or "")]
    return not wrong, f"cuts {cuts}"


def no_proposals(turn_index: int) -> Check:
    def check(session, deck_id, turns):
        names = _calls(turns[turn_index])
        made = [n for n in names if n in ("suggest_cards", "propose_deck_changes")]
        return not made, f"turn {turn_index + 1} calls: {names}"
    check.__name__ = f"turn {turn_index + 1} proposes nothing"
    return check


def ungrounded_at_most(limit: int) -> Check:
    def check(session, deck_id, turns):
        worst = max(((t.get("grounding") or {}).get("ungrounded", 0) for t in turns), default=0)
        return worst <= limit, f"most ungrounded names in a turn: {worst}"
    check.__name__ = f"ungrounded names per turn <= {limit}"
    return check


def no_think_cap(session, deck_id, turns):
    capped = sum(1 for t in turns for s in t.get("sends") or [] if s.get("finish") == "length")
    return capped == 0, f"{capped} send(s) hit the thinking cap"


SCENARIOS: list[dict[str, Any]] = [
    {
        "name": "setup and role batches",
        "turns": [
            "I want to build a Prosper, Tome-Bound commander deck focused on exile-and-cast value. Aim for bracket 3.",
            "Looks good, lock in the commander. Give me the ramp package.",
            "Now card draw, preferably things that play well with exiling cards.",
            "How many cards are in the deck now, and what's it still missing?",
        ],
        "checks": [commander_is("Prosper, Tome-Bound"), used("suggest_cards", 1),
                   used("suggest_cards", 2), no_proposals(3), at_most_100, no_think_cap],
    },
    {
        "name": "a restriction holds",
        "turns": [
            "Build me a mono-black Sheoldred, the Apocalypse deck, bracket 3. No Demons at all, I'm sick of them.",
            "Lock in Sheoldred and give me some creatures that punish opponents.",
            "More creatures please, the bigger the better.",
        ],
        "checks": [restriction_recorded("Demon"), no_proposed_type("Demon"), power_is("7"), no_think_cap],
    },
    {
        "name": "manabase fill",
        "turns": [
            "Let's build Prosper, Tome-Bound at bracket 3. Lock in the commander.",
            "Fill out the manabase.",
        ],
        "checks": [used("suggest_cards", 1), land_target_met, at_most_100],
    },
    {
        "name": "commander discovery",
        "turns": [
            "I wanna go back to some of the mythology inspired MtG sets and run some mythology decks, like "
            "the greek god stuff. Doesn't have to be one of the gods, and nothing overplayed.",
        ],
        "checks": [used("find_commanders", 0), no_proposals(0), ungrounded_at_most(1)],
    },
    {
        "name": "trim lands for draw on a full deck",
        "deck": "korlash.json",
        "turns": ["Lets remove a land or two and add some card draw."],
        "checks": [cuts_are_lands, at_most_100, no_think_cap],
    },
    {
        "name": "rules question",
        "deck": "korlash.json",
        "turns": ["Does Cabal Coffers count itself as a Swamp when I tap it?"],
        "checks": [no_proposals(0), ungrounded_at_most(0)],
    },
    {
        "name": "review done",
        "deck": "korlash.json",
        "turns": ["Suggest a few cheap removal spells.", REVIEW_DONE],
        "checks": [used("suggest_cards", 0), no_think_cap],
    },
]


def build_deck(session, fixture: str | None) -> int:
    from app.db import repository as repo

    if fixture is None:
        return repo.create_deck(session).id
    from app.tools import deck_tools

    data = json.loads((DECKS / fixture).read_text(encoding="utf-8"))
    deck = repo.create_deck(session, name=data["name"], commander=data["commander"])
    deck_tools.import_decklist(session, deck.id, data["decklist"])
    repo.update_deck(session, deck.id, themes=data.get("themes"), plan_notes=data.get("plan_notes"),
                     power_level=data.get("power_level"))
    return deck.id
