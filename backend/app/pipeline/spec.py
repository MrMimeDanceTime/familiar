"""Stage 1 — query-spec generation. The first of the two bounded LLM calls.

The model's entire job here is to turn a user's intent ("I need cheap removal
and some card draw") into a small set of tight Scryfall query strings. It does
NOT drive retrieval, pick cards, or see the pool — it only emits queries. The
harness then enforces legality on every query (appends ``id<=`` / ``f:commander``
regardless of what the model wrote) so correctness never depends on the model
getting the DSL right.

See ``docs/SCRYFALL_QUERY_CHEATSHEET.md`` for the canonical operator reference; the
few-shot block below is a trimmed embed of it for the runtime prompt.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

# WUBRG order for rendering an identity token deterministically.
_WUBRG = "WUBRG"

# Guild/shard/wedge nicknames Scryfall accepts, keyed by the sorted WUBRG letters.
# Only used to make queries human-readable; bare letters (``id<=br``) work too.
_IDENTITY_NICKNAMES: dict[str, str] = {
    "WU": "azorius", "UB": "dimir", "BR": "rakdos", "RG": "gruul", "GW": "selesnya",
    "WB": "orzhov", "UR": "izzet", "BG": "golgari", "RW": "boros", "GU": "simic",
    "WUB": "esper", "UBR": "grixis", "BRG": "jund", "RGW": "naya", "GWU": "bant",
    "WBG": "abzan", "URW": "jeskai", "BGU": "sultai", "RWB": "mardu", "GUR": "temur",
    "WUBR": "artifice", "UBRG": "chaos", "BRGW": "aggression", "RGWU": "altruism",
    "GWUB": "growth",
    "WUBRG": "wubrg",
}


def identity_token(identity: frozenset[str]) -> str:
    """Render a colour identity as a Scryfall ``id<=`` token.

    Colourless is ``c`` (``id<=c``). One or five colours and any combo use the
    WUBRG-ordered letters; a nickname is used when one exists purely for
    readability. The result is always a valid right-hand side for ``id<=``.
    """
    letters = "".join(c for c in _WUBRG if c in identity)
    if not letters:
        return "c"
    return _IDENTITY_NICKNAMES.get(letters, letters.lower())


@dataclass
class QuerySpec:
    """The validated output of stage 1: the queries to run, plus the intent
    metadata the later stages use for narration and cut suggestions."""

    queries: list[str]
    intent_summary: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


# Trimmed few-shot block embedded in the prompt. The full reference lives in
# docs/SCRYFALL_QUERY_CHEATSHEET.md; this is the runtime-facing subset (§B/§C).
FEW_SHOT_BLOCK = """\
Emit Scryfall query strings, one per role/intent. Rules:
- Prefer several tight queries over one broad OR-soup.
- Layer otag: on top of text/type filters, never alone.
- Quote multi-word oracle phrases; use o:/regex/ for activated abilities.
- Do NOT exclude the commander or owned cards — the harness does that.
- If a query would return very few cards, loosen it.

Examples (substitute the deck's identity for id<=rakdos):
  spot removal:      id<=rakdos otag:removal (t:instant or t:sorcery) f:commander
  board wipe:        id<=rakdos o:"destroy all creatures" f:commander
  ramp under 3:      id<=rakdos otag:ramp mv<=3 f:commander
  card advantage:    id<=rakdos otag:card-advantage f:commander
  tutors:            id<=rakdos o:"search your library" f:commander
  sacrifice outlet:  id<=rakdos o:/sacrifice a creature:/ f:commander
  finisher:          id<=rakdos t:creature mv>=6 (keyword:trample or keyword:flying) f:commander
  fixing lands:      id<=rakdos t:land (is:dual or is:fetchland) f:commander
"""

_SYSTEM_PROMPT = """\
You are the query-planning stage of a Magic: The Gathering Commander deckbuilding
assistant. Given the player's intent, their deck's colour identity, and (when
given) the deck's commander and gameplan, output a small set of Scryfall search
queries that will surface good candidate cards FOR THAT DECK: cards whose rules
text advances its plan and works with its commander, not generic staples.

You do NOT pick cards or write prose. Output ONLY a JSON object of this shape:
{{
  "intent_summary": "<one short line restating what the player wants>",
  "queries": ["<scryfall query>", "<scryfall query>", ...]
}}

{few_shot}

The deck's colour identity token is: id<={identity}
Every query you emit MUST include `id<={identity}` and `f:commander`.
Emit between 1 and {max_queries} queries. Nothing outside the JSON object.\
"""


def build_prompt(
    user_intent: str, identity: frozenset[str], *, max_queries: int = 6, deck_brief: str = "",
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for the stage-1 call. ``deck_brief``
    (commander rules text, gameplan, themes) is what lets a request like "what
    fits my deck" become queries for the deck's actual mechanics: without it
    this stage saw only the request's words and the colour identity."""
    token = identity_token(identity)
    system = _SYSTEM_PROMPT.format(
        few_shot=FEW_SHOT_BLOCK, identity=token, max_queries=max_queries
    )
    brief = f"{deck_brief.strip()}\n" if deck_brief.strip() else ""
    user = f"{brief}Player intent: {user_intent}\nDeck colour identity: id<={token}"
    return system, user


_ID_RE = re.compile(r"\bid\s*(?:<=|:|=)\s*[a-z]+", re.IGNORECASE)
_FMT_RE = re.compile(r"\bf(?:ormat)?\s*:\s*commander\b", re.IGNORECASE)


def enforce_query(query: str, identity: frozenset[str]) -> str:
    """Belt-and-suspenders: guarantee a query carries the identity + format
    filters, regardless of what the model emitted (cheatsheet §D). Appends the
    missing filter rather than trusting the model, so legality can never leak."""
    token = identity_token(identity)
    q = query.strip()
    if not _ID_RE.search(q):
        q = f"{q} id<={token}".strip()
    if not _FMT_RE.search(q):
        q = f"{q} f:commander".strip()
    return q


def parse_spec(raw_json: str, identity: frozenset[str], *, max_queries: int = 6) -> QuerySpec:
    """Parse and repair the stage-1 JSON into a QuerySpec.

    Tolerant of model sloppiness: missing fields default, non-list queries are
    coerced, blank/duplicate queries are dropped, every query is legality-enforced,
    and the count is capped. Raises ValueError only when the JSON itself is
    unparseable or contains zero usable queries.
    """
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        # The model writes Scryfall regex into JSON strings and sometimes
        # leaves a backslash unescaped ("o:/\d+/"), which json rejects as an
        # invalid escape. Double every backslash that does not start a valid
        # escape and try once more.
        repaired = re.sub(r'\\(?![\\"/bfnrtu])', r"\\\\", raw_json)
        try:
            data = json.loads(repaired)
        except json.JSONDecodeError:
            raise ValueError(f"stage-1 output was not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("stage-1 output was not a JSON object")

    raw_queries = data.get("queries")
    if isinstance(raw_queries, str):
        raw_queries = [raw_queries]
    elif not isinstance(raw_queries, list):
        raw_queries = []

    seen: set[str] = set()
    queries: list[str] = []
    for q in raw_queries:
        if not isinstance(q, str) or not q.strip():
            continue
        enforced = enforce_query(q, identity)
        key = enforced.lower()
        if key in seen:
            continue
        seen.add(key)
        queries.append(enforced)
        if len(queries) >= max_queries:
            break

    if not queries:
        raise ValueError("stage-1 produced no usable queries")

    summary = data.get("intent_summary")
    return QuerySpec(
        queries=queries,
        intent_summary=summary if isinstance(summary, str) else "",
        raw=data,
    )


def generate_query_spec(
    provider: Any,
    user_intent: str,
    identity: frozenset[str],
    deck_brief: str = "",
    *,
    model: str | None = None,
    max_queries: int = 6,
    thinking: bool = True,
) -> QuerySpec:
    """Run stage 1: prompt the provider, parse+repair its JSON into a QuerySpec.

    Turning intent into a few Scryfall queries is a mechanical mapping, not deep
    reasoning, so callers can pass ``thinking=False`` (with Flash) for speed —
    see build_suggestions."""
    system, user = build_prompt(user_intent, identity, max_queries=max_queries, deck_brief=deck_brief)
    raw = provider.complete_json(system, user, model=model, thinking=thinking)
    return parse_spec(raw, identity, max_queries=max_queries)
