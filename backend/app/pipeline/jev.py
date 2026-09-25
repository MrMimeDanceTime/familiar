"""Stage 4, System One variant: rank the pool with TypeSafe's Jev instead of a
thinking LLM.

The LLM selection stage spends 40-90s mostly GENERATING (a median ~9.5k
completion tokens of thinking) to pick ten cards from a pool Python already
retrieved, filtered, and ranked. Jev answers typed questions about a state in
one parallel pass in well under a second, and cannot write prose at all. So the
split here is:

  * Jev judges each legal candidate against the deck: a 0-4 fit Score and a
    yes/no Noul for "does this answer what the player asked for".
  * Python blends those with the brain map, takes the top N, and writes each
    pick's reason from facts it already holds (the judgment, combo partners,
    EDHREC play rate). No model invents a justification.

Jev sees the rules text and the combo fact, deliberately NOT the EDHREC numbers
or the brain map score: those are blended in afterwards, so the Jev judgment is
an independent signal that can be compared against them rather than an echo.

It produces no cuts and no summary narration; the chat model, which already has
to endorse or drop each pick, carries the prose.
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

FIT_LEVELS = [
    "Does not belong in this deck: off-plan, or actively works against it.",
    "Generic filler: playable in any deck of these colours, nothing specific to this one.",
    "Solid: does a job this deck needs, with little synergy beyond that.",
    "Strong fit: synergizes with the commander or the deck's existing cards and plan.",
    "Core piece: an engine, payoff, or combo piece this deck is built around.",
]
_FIT_MAX = len(FIT_LEVELS) - 1

# The API caps state plus all questions at 64k tokens. Chunks are sized well
# under that at a rough 4 chars per token, and sent concurrently.
_MAX_REQUEST_CHARS = 150_000
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
    """Jev's verdict on one candidate, plus the blended rank key."""

    name: str
    fit: float
    fit_confidence: float
    asked: float
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


def build_questions(cards: list[ShapedCard]) -> dict[str, dict]:
    """Two questions per card, keyed ``fit_<i>`` / ``ask_<i>`` by pool index."""
    questions: dict[str, dict] = {}
    for i, card in enumerate(cards):
        facts = _card_facts(card)
        questions[f"fit_{i}"] = {
            "type": "score",
            "instructions": {
                "task": (
                    "Rate how well this candidate fits THIS deck: its commander, "
                    "current cards, and plan in the state. Judge fit to this "
                    "deck, not the card's raw power in a vacuum."
                ),
                "candidate": facts,
            },
            "criteria": FIT_LEVELS,
        }
        questions[f"ask_{i}"] = {
            "type": "noul",
            "instructions": {
                "statement": "This candidate does what the player is asking for right now.",
                "candidate": facts,
            },
        }
    return questions


def _chunks(cards: list[ShapedCard], state: str) -> list[list[int]]:
    """Split pool indices so no request crosses the context limit."""
    budget = _MAX_REQUEST_CHARS - len(state)
    chunks: list[list[int]] = [[]]
    used = 0
    for i, card in enumerate(cards):
        cost = 2 * (len(str(_card_facts(card))) + 400)
        if chunks[-1] and used + cost > budget:
            chunks.append([])
            used = 0
        chunks[-1].append(i)
        used += cost
    return [c for c in chunks if c]


def judge(
    client: Any, cards: list[ShapedCard], state: str
) -> tuple[list[Judgment], dict[str, Any]]:
    """Ask Jev about every card. Returns judgments in pool order plus usage."""
    chunks = _chunks(cards, state)

    def run(indices: list[int]) -> dict[str, Any]:
        subset = [cards[i] for i in indices]
        answers = client.system_one(state, build_questions(subset)).get("answers") or {}
        return {"indices": indices, "answers": answers}

    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(chunks) or 1)) as pool:
        results = list(pool.map(run, chunks))

    judgments: list[Judgment] = []
    for result in results:
        answers = result["answers"]
        for local, index in enumerate(result["indices"]):
            fit = answers.get(f"fit_{local}") or {}
            ask = answers.get(f"ask_{local}") or {}
            if "score" not in fit or "noul" not in ask:
                raise JevError(f"Jev answer missing for {cards[index].name!r}")
            judgments.append(Judgment(
                name=cards[index].name,
                fit=float(fit["score"]),
                fit_confidence=float(fit.get("confidence", 0.0)),
                asked=float(ask["noul"]),
                fit_probabilities=fit.get("probabilities") or {},
            ))
    return judgments, {"requests": len(chunks)}


def _rank_key(judgment: Judgment, card: ShapedCard, brainmap_weight: float) -> float:
    # Fit gated by the ask: a core piece that ignores the request still ranks,
    # but below a strong card that answers it.
    jev = (judgment.fit / _FIT_MAX) * (0.3 + 0.7 * judgment.asked)
    total = (card.brainmap or {}).get("total")
    if not isinstance(total, (int, float)):
        return jev
    return (1 - brainmap_weight) * jev + brainmap_weight * max(0.0, min(1.0, float(total)))


def reason_for(judgment: Judgment, card: ShapedCard) -> str:
    level = FIT_LEVELS[min(_FIT_MAX, max(0, round(judgment.fit)))].split(":")[0]
    parts = [f"{level} (Jev fit {judgment.fit:.1f}/{_FIT_MAX}"
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
    brainmap_weight: float = 0.25,
) -> Selection:
    """Stage 4 on Jev. Same contract as ``selection.select``: picks are legal
    pool cards only. Illegal cards are never sent, so they cannot be picked.
    Raises ``JevError`` on any failure so the caller can fall back."""
    legal = [c for c in pool if c.legal_in_deck and c.name]
    if not legal:
        return Selection(picks=[], summary="", raw={"backend": "jev", "judgments": []})

    state = build_state(deck_context, user_intent, player_message)
    judgments, usage = judge(client, legal, state)
    for judgment, card in zip(judgments, legal):
        judgment.rank = _rank_key(judgment, card, brainmap_weight)

    order = sorted(range(len(legal)), key=lambda i: judgments[i].rank, reverse=True)
    picks = [
        Pick(name=legal[i].name, reason=reason_for(judgments[i], legal[i]))
        for i in order[:max_picks]
    ]
    return Selection(
        picks=picks,
        summary="",
        raw={
            "backend": "jev",
            "model": getattr(client, "model", None),
            "usage": usage,
            "judgments": [
                {"name": judgments[i].name, "fit": round(judgments[i].fit, 3),
                 "fit_confidence": round(judgments[i].fit_confidence, 3),
                 "asked": round(judgments[i].asked, 3), "rank": round(judgments[i].rank, 4)}
                for i in order
            ],
        },
    )


def get_client() -> JevClient:
    from app.config import settings

    return JevClient(
        settings.typesafe_api_key, model=settings.typesafe_model or DEFAULT_MODEL,
    )
