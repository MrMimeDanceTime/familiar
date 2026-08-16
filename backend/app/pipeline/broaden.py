"""Query broadening — the recovery path stage 1 never had.

The cheatsheet tells the model "if a query would return < ~5 cards, loosen it",
but the model emits queries blind: it never sees a result count, so it cannot
act on that instruction. A single over-tight query therefore silently
contributes nothing, and because stage 2 only ever ran each query once, nothing
downstream could recover. That is the largest single cause of a thin pool.

This module makes the instruction actionable by the harness instead: run a
query, count the hits, and if it underfilled, relax ONE constraint and re-run.

The relaxation order is deliberate — drop the most arbitrary constraint first,
since those are what the model over-specifies:

1. ``mv``/``cmc`` bounds     — a guessed number, almost always the culprit
2. ``keyword:``/``kw:``      — narrows hard and is rarely load-bearing
3. ``pow``/``tou`` bounds    — same
4. ``otag:``                 — the tag may simply not exist (see below)
5. ``r:``/``rarity:``        — almost never intended as a real constraint

Two things are NEVER relaxed: ``id<=`` (colour identity) and ``f:commander``.
Those are the legality filters the harness enforces in ``spec.enforce_query``,
and broadening must not be able to leak an illegal card into the pool.

The ``otag:`` step matters more than it looks. Tagger's vocabulary is a
hierarchy and its slugs are not guessable — verified 2026-08-15, ``otag:removal``
resolves on Scryfall but ``otag:sacrifice-outlet`` is the *ancestor* form, while
the leaf slugs are ``sacrifice-outlet-creature``, ``-artifact``, ``-land`` and so
on. A model inventing a plausible-sounding tag is a normal failure mode, and it
returns exactly zero cards, so dropping the tag is often the whole fix.
"""

from __future__ import annotations

import logging
import re
from typing import Callable

logger = logging.getLogger(__name__)

# How few results counts as "underfilled" and triggers a broadening pass.
DEFAULT_MIN_HITS = 8

# Max relaxation rounds per query. Each round drops one constraint class, so
# this bounds the extra searches per query rather than looping to empty.
DEFAULT_MAX_ROUNDS = 3

# Constraint patterns, in the order they are relaxed. Each entry is
# (label, regex). The regex must match a whole whitespace-delimited term so
# removing it can't corrupt neighbouring syntax.
_RELAXATIONS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("mana value bound", re.compile(r"(?:^|\s)(?:mv|cmc|manavalue)\s*(?:<=|>=|<|>|=|:)\s*\d+", re.I)),
    ("keyword filter", re.compile(r"(?:^|\s)(?:keyword|kw)\s*:\s*[\"']?[\w-]+[\"']?", re.I)),
    ("power/toughness bound", re.compile(r"(?:^|\s)(?:pow|power|tou|toughness)\s*(?:<=|>=|<|>|=|:)\s*\w+", re.I)),
    ("oracle tag", re.compile(r"(?:^|\s)(?:otag|function)\s*:\s*[\"']?[\w-]+[\"']?", re.I)),
    ("rarity filter", re.compile(r"(?:^|\s)(?:r|rarity)\s*(?:<=|>=|<|>|=|:)\s*[\w-]+", re.I)),
)


def _tidy(query: str) -> str:
    """Collapse the whitespace left behind by removing a term."""
    return re.sub(r"\s{2,}", " ", query).strip()


# Terms that carry the query's actual INTENT, as opposed to its legality
# filters. A relaxed query that has lost all of these is no longer a search for
# what the player asked about — it is "every legal card in these colours", which
# returns generic staples and quietly replaces the intent with noise.
_INTENT_TERMS = re.compile(
    r"(?:^|\s)(?:o|oracle|fo|t|type|otag|function|is|produces|kw|keyword)\s*:", re.I
)


def has_intent(query: str) -> bool:
    """True when a query still constrains what a card DOES, not just its legality.

    Verified against the live API 2026-08-15: an invented tag
    (``otag:sacrifice-outlet-invented``) 404s, and relaxing it away leaves bare
    ``id<=br f:commander`` — which happily returns Sol Ring and Command Tower.
    Those are real cards, so nothing errors; the search has just silently
    stopped being about the thing the player asked for. Better to contribute
    nothing than to contribute confident noise.
    """
    return bool(_INTENT_TERMS.search(query))


def relax_once(query: str) -> tuple[str, str] | None:
    """Drop the single most-arbitrary constraint from a query.

    Returns ``(relaxed_query, what_was_dropped)``, or None when nothing is left
    that may safely be relaxed. Colour identity and format are never candidates.
    """
    for label, pattern in _RELAXATIONS:
        match = pattern.search(query)
        if not match:
            continue
        relaxed = _tidy(query[: match.start()] + " " + query[match.end() :])
        # Removing a term can leave an empty or dangling group; if that happens,
        # this relaxation isn't safe, so try the next class instead.
        if relaxed.count("(") != relaxed.count(")"):
            continue
        relaxed = re.sub(r"\(\s*\)", "", relaxed)
        relaxed = _tidy(relaxed)
        if not relaxed:
            continue
        return relaxed, label
    return None


def search_with_broadening(
    search: Callable[[str], list],
    query: str,
    *,
    min_hits: int = DEFAULT_MIN_HITS,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
) -> tuple[list, list[str]]:
    """Run ``query``, broadening while it underfills.

    ``search`` takes a query string and returns a list of cards. Returns the
    best result set found and the trail of queries actually run (for debug
    output — a suggestion that quietly broadened should be inspectable).

    Keeps the LARGEST result set seen, not the last: a relaxation can in
    principle return fewer cards, and there is no reason to take a worse pool
    than one already in hand.
    """
    trail = [query]
    best = search(query)
    if len(best) >= min_hits:
        return best, trail

    current = query
    for _ in range(max_rounds):
        step = relax_once(current)
        if step is None:
            break
        candidate, dropped = step
        # Stop before the query stops being about anything. Relaxing past the
        # last intent term turns the search into "every legal card in these
        # colours", which returns staples that match the identity and nothing
        # the player asked for.
        if not has_intent(candidate):
            logger.info(
                "broadening: stopping, dropping %s would leave no intent in %r",
                dropped, current,
            )
            break
        current = candidate
        trail.append(current)
        logger.info(
            "broadening: dropped %s -> %r (had %d hits)", dropped, current, len(best)
        )
        results = search(current)
        if len(results) > len(best):
            best = results
        if len(best) >= min_hits:
            break

    return best, trail
