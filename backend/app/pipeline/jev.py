"""Stage 4, System One variant: rank the pool with TypeSafe's Jev instead of a
thinking LLM.

The LLM selection stage spends 40-90s mostly GENERATING (a median ~9.5k
completion tokens of thinking) to pick ten cards from a pool Python already
retrieved, filtered, and ranked. Jev answers typed questions about a state in
one parallel pass in well under a second, and cannot write prose at all. So:

  * Jev judges each legal candidate: a 0-4 Score and a yes/no Noul for "is this
    an answer to the request".
  * Python ranks on those, takes the top N, and writes each pick's reason from
    facts it already holds. No model invents a justification.

Two modes, compared by tools/jev_compare.py:

  * ``blind``: Jev sees rules text and the combo fact only, and Python blends
    its fit score with the brain map afterwards. Jev is one more signal.
  * ``informed``: Jev also sees the card's role tags, EDHREC play rate and
    synergy, and the brain map's layer scores, and answers "should this be
    added?" as the final verdict with no blend on top. Jev is the judge that
    weighs the other signals, instead of a signal something else must weigh.

Neither produces cuts or a summary; the chat model, which already has to
endorse or drop each pick, carries the prose.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.pipeline.selection import Pick, Selection
from app.pipeline.shaping import ShapedCard

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.typesafe.ai"
DEFAULT_MODEL = "jev-latest"
MODES = ("blind", "informed")

FIT_LEVELS = [
    "Does not belong in this deck: off-plan, or actively works against it.",
    "Generic filler: playable in any deck of these colours, nothing specific to this one.",
    "Solid: does a job this deck needs, with little synergy beyond that.",
    "Strong fit: synergizes with the commander or the deck's existing cards and plan.",
    "Core piece: an engine, payoff, or combo piece this deck is built around.",
]
VERDICT_LEVELS = [
    "No: wrong for this deck, or does not answer the request.",
    "Only if nothing better exists: marginal here.",
    "Reasonable add: does the job, little beyond that.",
    "Good add: does the job and clearly suits this deck.",
    "Must add: among the best possible answers for this deck and request.",
]
_SCORE_MAX = len(FIT_LEVELS) - 1

_FIT_TASK = (
    "Rate how well this candidate fits THIS deck: its commander, current cards, "
    "and plan in the state. Judge fit to this deck, not the card's raw power in "
    "a vacuum."
)
_VERDICT_TASK = (
    "Decide whether this candidate should be added to THIS deck for the "
    "player's current request. Weigh all the evidence: its rules text, its role "
    "tags, any combo it completes, how often decks with this commander play it, "
    "how specific it is to this commander, and the brain map's fit scores "
    "against the deck. The signals can disagree, and a popular card is not "
    "automatically right for this build; you are the final judge."
)
_DUP_STATEMENT = (
    "This candidate mostly repeats a job that cards already in the deck do, "
    "adding little the deck lacks."
)

# The API caps state plus all questions at 64k tokens. This JSON measured
# ~3.4 chars per token (an informed 60-card pool: ~41k tokens), so the budget
# keeps a chunk near 35k tokens. Chunks are sent concurrently.
_MAX_REQUEST_CHARS = 120_000
_MAX_WORKERS = 4


class JevError(RuntimeError):
    """The System One call failed or returned something unusable."""


class JevClient:
    """The one endpoint this needs, over the project's own httpx.

    The official SDK depends on ``httpx2`` (a separate fork) plus tenacity; the
    wire protocol is a single POST, so it is not worth a second HTTP stack.
    """

    def __init__(
        self, api_key: str, *, model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL, timeout: float = 15.0,
    ) -> None:
        if not api_key:
            raise JevError("TYPESAFE_API_KEY is not set")
        self.model = model
        self._client = httpx.Client(
            base_url=base_url, timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        )

    def system_one(self, state: Any, questions: dict[str, dict]) -> dict[str, Any]:
        try:
            response = self._client.post(
                "/v1/systemone",
                json={"state": state, "model": self.model, "questions": questions},
            )
        except httpx.HTTPError as exc:
            raise JevError(f"Jev request failed: {exc}") from exc
        if response.status_code >= 400:
            raise JevError(f"Jev returned {response.status_code}: {response.text[:200]}")
        return response.json()


@dataclass
class Judgment:
    """Jev's answers for one candidate, plus the rank key built from them.
    ``fit`` is the fit score when blind and the add verdict when informed."""

    name: str
    fit: float
    fit_confidence: float
    asked: float
    duplicate: float | None = None
    rank: float = 0.0
    fit_probabilities: dict[str, float] = field(default_factory=dict)


def build_state(deck_context: str, user_intent: str, player_message: str | None) -> str:
    words = " ".join((player_message or "").split())
    said = f"\nPlayer's own words: {words[:600]}" if words else ""
    return f"{deck_context.strip()}\n\nWhat the player wants now: {user_intent}{said}"


def _card_facts(card: ShapedCard) -> dict[str, Any]:
    facts: dict[str, Any] = {"name": card.name}
    if card.mana_cost:
        facts["mana_cost"] = card.mana_cost
    if card.type_line:
        facts["type_line"] = card.type_line
    if card.oracle_text:
        facts["rules_text"] = " ".join(card.oracle_text.split())
    if card.completes_combo_with:
        facts["completes_a_combo_with_cards_already_in_deck"] = card.completes_combo_with
    return facts


def _evidence(card: ShapedCard) -> dict[str, Any]:
    """The pipeline's own signals, labelled so Jev reads them as evidence."""
    evidence: dict[str, Any] = {}
    if card.fine_roles:
        evidence["role_tags"] = sorted(card.fine_roles)
    edhrec = card.edhrec or {}
    rate = edhrec.get("inclusion_rate")
    if isinstance(rate, (int, float)):
        evidence["played_in_share_of_decks_with_this_commander"] = f"{rate:.0%}"
    synergy = edhrec.get("synergy")
    if isinstance(synergy, (int, float)):
        evidence["edhrec_synergy_vs_same_colour_decks"] = (
            f"{synergy:+.2f} (positive means specific to this commander, "
            "near zero means a generic staple)"
        )
    brainmap = card.brainmap or {}
    if brainmap:
        evidence["brain_map_fit"] = {
            "total_0_to_1": brainmap.get("total"),
            "consensus": brainmap.get("consensus"),
            "mechanical_synergy_with_deck": brainmap.get("mechanical"),
            "personal_history": brainmap.get("personal"),
            "why": brainmap.get("explain"),
        }
    return evidence


def build_questions(
    cards: list[ShapedCard], user_intent: str = "", mode: str = "blind"
) -> dict[str, dict]:
    """Questions per card, keyed ``<kind>_<i>`` by position in ``cards``:
    ``fit`` (a Score: the fit rubric when blind, the add verdict when
    informed), ``ask`` (a Noul), and in informed mode a diagnostic ``dup``."""
    informed = mode == "informed"
    request = user_intent.strip() or "what the player is asking for right now"
    questions: dict[str, dict] = {}
    for i, card in enumerate(cards):
        facts = _card_facts(card)
        # The ask and dup questions judge what the card DOES, so they get the
        # card alone; popularity is not evidence that a card is removal.
        plain = dict(facts)
        if informed:
            facts = {**facts, **_evidence(card)}
        questions[f"fit_{i}"] = {
            "type": "score",
            "instructions": {
                "task": _VERDICT_TASK if informed else _FIT_TASK,
                "candidate": facts,
            },
            "criteria": VERDICT_LEVELS if informed else FIT_LEVELS,
        }
        # The request goes IN the statement: left in the state alone, a plainly
        # on-request card (Burnished Hart for "more ramp") measured 0.49.
        questions[f"ask_{i}"] = {
            "type": "noul",
            "instructions": {
                "statement": f"This candidate is an answer to the request: {request}",
                "candidate": plain,
            },
        }
        if informed:
            questions[f"dup_{i}"] = {
                "type": "noul",
                "instructions": {"statement": _DUP_STATEMENT, "candidate": plain},
            }
    return questions


def _chunks(cards: list[ShapedCard], state: str, user_intent: str, mode: str) -> list[list[int]]:
    """Split pool indices so no request crosses the context limit."""
    budget = _MAX_REQUEST_CHARS - len(state)
    chunks: list[list[int]] = [[]]
    used = 0
    for i, card in enumerate(cards):
        cost = len(str(build_questions([card], user_intent, mode)))
        if chunks[-1] and used + cost > budget:
            chunks.append([])
            used = 0
        chunks[-1].append(i)
        used += cost
    return [c for c in chunks if c]


def judge(
    client: Any, cards: list[ShapedCard], state: str,
    user_intent: str = "", mode: str = "blind",
) -> tuple[list[Judgment], dict[str, Any]]:
    """Ask Jev about every card. Returns judgments in pool order plus usage."""
    chunks = _chunks(cards, state, user_intent, mode)

    def run(indices: list[int]) -> dict[str, Any]:
        questions = build_questions([cards[i] for i in indices], user_intent, mode)
        response = client.system_one(state, questions)
        return {"indices": indices, "answers": response.get("answers") or {},
                "usage": response.get("usage") or {}}

    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(chunks) or 1)) as pool:
        results = list(pool.map(run, chunks))

    judgments: list[Judgment] = []
    input_tokens = 0
    for result in results:
        input_tokens += int(result["usage"].get("input_tokens") or 0)
        answers = result["answers"]
        for local, index in enumerate(result["indices"]):
            fit = answers.get(f"fit_{local}") or {}
            ask = answers.get(f"ask_{local}") or {}
            dup = answers.get(f"dup_{local}") or {}
            if "score" not in fit or "noul" not in ask:
                raise JevError(f"Jev answer missing for {cards[index].name!r}")
            judgments.append(Judgment(
                name=cards[index].name,
                fit=float(fit["score"]),
                fit_confidence=float(fit.get("confidence", 0.0)),
                asked=float(ask["noul"]),
                duplicate=float(dup["noul"]) if "noul" in dup else None,
                fit_probabilities=fit.get("probabilities") or {},
            ))
    return judgments, {"requests": len(chunks), "input_tokens": input_tokens}


def _rank_key(judgment: Judgment, card: ShapedCard, brainmap_weight: float) -> float:
    # Score gated by the ask: a core piece that ignores the request still
    # ranks, but below a strong card that answers it.
    jev = (judgment.fit / _SCORE_MAX) * (0.3 + 0.7 * judgment.asked)
    total = (card.brainmap or {}).get("total")
    if not brainmap_weight or not isinstance(total, (int, float)):
        return jev
    return (1 - brainmap_weight) * jev + brainmap_weight * max(0.0, min(1.0, float(total)))


def reason_for(judgment: Judgment, card: ShapedCard, mode: str = "blind") -> str:
    levels = VERDICT_LEVELS if mode == "informed" else FIT_LEVELS
    level = levels[min(_SCORE_MAX, max(0, round(judgment.fit)))].split(":")[0]
    label = "verdict" if mode == "informed" else "fit"
    parts = [f"{level} (Jev {label} {judgment.fit:.1f}/{_SCORE_MAX}"
             f", answers the ask {judgment.asked:.0%})"]
    if card.completes_combo_with:
        parts.append(f"completes a combo with {' + '.join(card.completes_combo_with)}")
    rate = (card.edhrec or {}).get("inclusion_rate")
    if isinstance(rate, (int, float)):
        parts.append(f"in {rate:.0%} of this commander's decks")
    return "; ".join(parts) + "."


def select_jev(
    client: Any,
    pool: list[ShapedCard],
    user_intent: str,
    *,
    max_picks: int = 10,
    deck_context: str = "",
    player_message: str | None = None,
    mode: str = "blind",
    brainmap_weight: float | None = None,
) -> Selection:
    """Stage 4 on Jev. Same contract as ``selection.select``: picks are legal
    pool cards only. Illegal cards are never sent, so they cannot be picked.
    Raises ``JevError`` on any failure so the caller can fall back.

    ``brainmap_weight`` defaults to 0.25 when blind and 0 when informed: an
    informed verdict has already weighed the brain map, and blending it in
    again would count it twice."""
    if mode not in MODES:
        raise JevError(f"unknown Jev mode {mode!r}")
    if brainmap_weight is None:
        brainmap_weight = 0.0 if mode == "informed" else 0.25

    legal = [c for c in pool if c.legal_in_deck and c.name]
    if not legal:
        return Selection(picks=[], summary="", raw={"backend": "jev", "mode": mode, "judgments": []})

    state = build_state(deck_context, user_intent, player_message)
    judgments, usage = judge(client, legal, state, user_intent, mode)
    for judgment, card in zip(judgments, legal):
        judgment.rank = _rank_key(judgment, card, brainmap_weight)

    order = sorted(range(len(legal)), key=lambda i: judgments[i].rank, reverse=True)
    picks = [
        Pick(name=legal[i].name, reason=reason_for(judgments[i], legal[i], mode))
        for i in order[:max_picks]
    ]
    return Selection(
        picks=picks,
        summary="",
        raw={
            "backend": "jev",
            "mode": mode,
            "model": getattr(client, "model", None),
            "usage": usage,
            "judgments": [
                {"name": judgments[i].name, "fit": round(judgments[i].fit, 3),
                 "fit_confidence": round(judgments[i].fit_confidence, 3),
                 "asked": round(judgments[i].asked, 3),
                 "duplicate": None if judgments[i].duplicate is None else round(judgments[i].duplicate, 3),
                 "rank": round(judgments[i].rank, 4)}
                for i in order
            ],
        },
    )


def get_client() -> JevClient:
    from app.config import settings

    return JevClient(
        settings.typesafe_api_key, model=settings.typesafe_model or DEFAULT_MODEL,
    )
