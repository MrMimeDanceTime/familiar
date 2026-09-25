"""Stage 4, System One variant: rank the pool with TypeSafe's Jev instead of a
thinking LLM.

The LLM selection stage spends most of its time GENERATING (thousands of
completion tokens of thinking) to pick ten cards from a pool Python already
retrieved, filtered, and ranked. Jev answers typed questions about a state in
one parallel pass in well under a second, and cannot write prose at all. So
Jev ranks the legal pool, Python takes the top N and writes each pick's reason
from facts it already holds. No model invents a justification.

Modes, compared by tools/jev_compare.py and tools/jev_eval.py:

  * ``blind``: per card, a 0-4 fit Score from rules text only, gated by a
    Noul for "answers the request", blended with the brain map in Python.
  * ``informed``: as blind, but the Score is an add verdict and Jev also sees
    role tags, EDHREC play rate and synergy, and brain map layer scores.
  * ``verdict``: per card, ONE calibrated Noul ("should be added in answer to
    this request") over the same evidence. The probability is the rank.
  * ``choice``: ONE Choice across the whole pool ("the single best addition").
    The probability each candidate gets is the rank, so candidates are judged
    against each other rather than each in isolation.
  * ``ensemble``: verdict and choice together, ranked on the mean of each
    card's rank position in the two. Different framings, partly independent
    errors.

``samples`` repeats verdict (identical requests; Jev is not deterministic and
swaps about one card in ten between runs) or reorders choice (to cancel
position bias), and averages. Requests run concurrently, so samples cost
tokens rather than time.

None produces cuts or a summary; the chat model, which already has to endorse
or drop each pick, carries the prose.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import httpx

from app.pipeline.selection import Pick, Selection
from app.pipeline.shaping import ShapedCard

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.typesafe.ai"
DEFAULT_MODEL = "jev-1.13.0"
MODES = ("blind", "informed", "verdict", "choice", "ensemble")

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
_WEIGH = (
    "Weigh all the evidence: its rules text, its role tags, any combo it "
    "completes, how often decks with this commander play it, how specific it is "
    "to this commander, and the brain map's fit scores against the deck. The "
    "signals can disagree, and a popular card is not automatically right for "
    "this build."
)
_VERDICT_TASK = (
    "Decide whether this candidate should be added to THIS deck for the "
    f"player's current request. {_WEIGH} You are the final judge."
)
_DUP_STATEMENT = (
    "This candidate mostly repeats a job that cards already in the deck do, "
    "adding little the deck lacks."
)

# The API caps state plus all questions at 64k tokens, and state plus the
# single longest question at 32k. This JSON measured ~3.4 chars per token (an
# informed 60-card pool: ~41k tokens), so these keep well inside both.
_MAX_REQUEST_CHARS = 120_000
_MAX_QUESTION_CHARS = 90_000
_MAX_CHOICES = 255
_MAX_WORKERS = 4
_RETRY_STATUSES = {429, 500, 502, 503, 504}


class JevError(RuntimeError):
    """The System One call failed or returned something unusable."""


class JevClient:
    """The one endpoint this needs, over the project's own httpx.

    The official SDK depends on ``httpx2`` (a separate fork) plus tenacity; the
    wire protocol is a single POST, so it is not worth a second HTTP stack.
    """

    def __init__(
        self, api_key: str, *, model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL, timeout: float = 15.0, retries: int = 3,
    ) -> None:
        if not api_key:
            raise JevError("TYPESAFE_API_KEY is not set")
        self.model = model
        self.retries = retries
        self._client = httpx.Client(
            base_url=base_url, timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        )

    def system_one(self, state: Any, questions: dict[str, dict]) -> dict[str, Any]:
        # Early access returns transient 503s ("upstream connect error") and
        # rate-limits with 429; both clear within a second or two, so retry
        # them with backoff before the caller falls back to the LLM.
        body = {"state": state, "model": self.model, "questions": questions}
        for attempt in range(self.retries + 1):
            last = attempt == self.retries
            try:
                response = self._client.post("/v1/systemone", json=body)
            except httpx.TransportError as exc:
                if last:
                    raise JevError(f"Jev request failed: {exc}") from exc
                time.sleep(self._backoff(attempt, None))
                continue
            except httpx.HTTPError as exc:
                raise JevError(f"Jev request failed: {exc}") from exc
            if response.status_code in _RETRY_STATUSES and not last:
                time.sleep(self._backoff(attempt, response.headers.get("retry-after")))
                continue
            if response.status_code >= 400:
                raise JevError(f"Jev returned {response.status_code}: {response.text[:200]}")
            return response.json()
        raise JevError("Jev retries exhausted")

    @staticmethod
    def _backoff(attempt: int, retry_after: str | None) -> float:
        try:
            if retry_after is not None:
                return min(5.0, max(0.0, float(retry_after)))
        except ValueError:
            pass
        return 0.5 * (3 ** attempt)


@dataclass
class Judgment:
    """Jev's answers for one candidate. ``score`` is the mode's primary
    answer: the 0-4 Score (blind, informed) or a probability (verdict,
    choice). ``rank`` is the key the pool is sorted on."""

    name: str
    score: float
    confidence: float | None = None
    asked: float | None = None
    duplicate: float | None = None
    rank: float = 0.0


def build_state(deck_context: str, user_intent: str, player_message: str | None) -> str:
    words = " ".join((player_message or "").split())
    said = f"\nPlayer's own words: {words[:600]}" if words else ""
    return f"{deck_context.strip()}\n\nWhat the player wants now: {user_intent}{said}"


def _request(user_intent: str) -> str:
    return user_intent.strip() or "what the player is asking for right now"


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


def _evidence(card: ShapedCard, *, explain: bool = True) -> dict[str, Any]:
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
        fit = {
            "total_0_to_1": brainmap.get("total"),
            "consensus": brainmap.get("consensus"),
            "mechanical_synergy_with_deck": brainmap.get("mechanical"),
            "personal_history": brainmap.get("personal"),
        }
        if explain:
            fit["why"] = brainmap.get("explain")
        evidence["brain_map_fit"] = fit
    return evidence


def build_questions(
    cards: list[ShapedCard], user_intent: str = "", mode: str = "blind"
) -> dict[str, dict]:
    """Per-card questions (blind, informed, verdict), keyed ``<kind>_<i>`` by
    position in ``cards``. Every mode asks ``ask``, a Noul for "answers the
    request", which feeds the reason line; blind and informed also gate on it."""
    request = _request(user_intent)
    questions: dict[str, dict] = {}
    for i, card in enumerate(cards):
        # The ask and dup questions judge what the card DOES, so they get the
        # card alone; popularity is not evidence that a card is removal.
        plain = _card_facts(card)
        evidenced = {**plain, **_evidence(card)}
        if mode == "blind":
            questions[f"fit_{i}"] = {
                "type": "score",
                "instructions": {"task": _FIT_TASK, "candidate": plain},
                "criteria": FIT_LEVELS,
            }
        elif mode == "informed":
            questions[f"fit_{i}"] = {
                "type": "score",
                "instructions": {"task": _VERDICT_TASK, "candidate": evidenced},
                "criteria": VERDICT_LEVELS,
            }
            questions[f"dup_{i}"] = {
                "type": "noul",
                "instructions": {"statement": _DUP_STATEMENT, "candidate": plain},
            }
        elif mode == "verdict":
            questions[f"add_{i}"] = {
                "type": "noul",
                "instructions": {
                    "statement": (
                        "This candidate should be one of the cards added to this "
                        f"deck in answer to the request: {request}"
                    ),
                    "guidance": _WEIGH,
                    "candidate": evidenced,
                },
            }
        else:
            raise JevError(f"mode {mode!r} has no per-card questions")
        # The request goes IN the statement: left in the state alone, a plainly
        # on-request card (Burnished Hart for "more ramp") measured 0.49.
        questions[f"ask_{i}"] = {
            "type": "noul",
            "instructions": {
                "statement": f"This candidate is an answer to the request: {request}",
                "candidate": plain,
            },
        }
    return questions


def build_choice_question(cards: list[ShapedCard], user_intent: str) -> dict[str, dict]:
    """One Choice over the whole pool. Labels are positional (``c<i>``) so a
    card name can never collide with the label syntax; each label's
    description carries the card and its evidence."""
    if len(cards) > _MAX_CHOICES:
        raise JevError(f"{len(cards)} candidates exceeds the {_MAX_CHOICES}-choice limit")

    def build(explain: bool) -> dict[str, dict]:
        return {"best": {
            "type": "choice",
            "instructions": (
                "Pick the single candidate that would be the best addition to "
                f"THIS deck in answer to the request: {_request(user_intent)}. "
                f"{_WEIGH}"
            ),
            "criteria": {
                f"c{i}": {**_card_facts(c), **_evidence(c, explain=explain)}
                for i, c in enumerate(cards)
            },
        }}

    questions = build(explain=True)
    if len(str(questions)) > _MAX_QUESTION_CHARS:
        questions = build(explain=False)
    if len(str(questions)) > _MAX_QUESTION_CHARS:
        raise JevError("pool too large for a single choice question")
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


def _judge_per_card(
    client: Any, cards: list[ShapedCard], state: str, user_intent: str, mode: str,
) -> tuple[list[Judgment], dict[str, Any]]:
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
            ask = answers.get(f"ask_{local}") or {}
            if "noul" not in ask:
                raise JevError(f"Jev answer missing for {cards[index].name!r}")
            if mode == "verdict":
                add = answers.get(f"add_{local}") or {}
                if "noul" not in add:
                    raise JevError(f"Jev answer missing for {cards[index].name!r}")
                judgments.append(Judgment(
                    name=cards[index].name, score=float(add["noul"]), asked=float(ask["noul"]),
                ))
                continue
            fit = answers.get(f"fit_{local}") or {}
            dup = answers.get(f"dup_{local}") or {}
            if "score" not in fit:
                raise JevError(f"Jev answer missing for {cards[index].name!r}")
            judgments.append(Judgment(
                name=cards[index].name,
                score=float(fit["score"]),
                confidence=float(fit.get("confidence", 0.0)),
                asked=float(ask["noul"]),
                duplicate=float(dup["noul"]) if "noul" in dup else None,
            ))
    return judgments, {"requests": len(chunks), "input_tokens": input_tokens}


def _judge_choice(
    client: Any, cards: list[ShapedCard], state: str, user_intent: str, samples: int,
) -> tuple[list[Judgment], dict[str, Any]]:
    """Ask the pool-wide Choice ``samples`` times, each with the options in a
    different fixed order, and average each card's probability. One ordering
    alone measured position-biased: only 5.5 of the top 10 survived a shuffle.
    The requests run concurrently, so extra samples cost tokens, not time."""
    import random

    orders = [list(range(len(cards)))]
    for seed in range(1, samples):
        order = list(range(len(cards)))
        random.Random(seed).shuffle(order)
        orders.append(order)

    def run(order: list[int]) -> tuple[list[int], dict[str, Any]]:
        questions = build_choice_question([cards[i] for i in order], user_intent)
        return order, client.system_one(state, questions)

    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(orders))) as pool:
        results = list(pool.map(run, orders))

    totals = [0.0] * len(cards)
    confidences: list[float] = []
    input_tokens = 0
    for order, response in results:
        best = (response.get("answers") or {}).get("best") or {}
        probabilities = best.get("probabilities")
        if not isinstance(probabilities, dict):
            raise JevError("Jev choice answer missing probabilities")
        for position, index in enumerate(order):
            totals[index] += float(probabilities.get(f"c{position}", 0.0))
        if best.get("confidence") is not None:
            confidences.append(float(best["confidence"]))
        input_tokens += int((response.get("usage") or {}).get("input_tokens") or 0)

    confidence = sum(confidences) / len(confidences) if confidences else None
    judgments = [
        Judgment(name=c.name, score=totals[i] / len(orders), confidence=confidence)
        for i, c in enumerate(cards)
    ]
    return judgments, {"requests": len(orders), "input_tokens": input_tokens}


def _mean(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


def _average(runs: list[list[Judgment]]) -> list[Judgment]:
    return [
        Judgment(
            name=group[0].name,
            score=sum(j.score for j in group) / len(group),
            confidence=_mean([j.confidence for j in group]),
            asked=_mean([j.asked for j in group]),
            duplicate=_mean([j.duplicate for j in group]),
        )
        for group in zip(*runs)
    ]


def _positions(judgments: list[Judgment]) -> list[float]:
    """Each card's rank position scaled to 1.0 (top) .. 0.0 (bottom)."""
    order = sorted(range(len(judgments)), key=lambda i: judgments[i].score, reverse=True)
    span = max(1, len(judgments) - 1)
    positions = [0.0] * len(judgments)
    for place, index in enumerate(order):
        positions[index] = 1.0 - place / span
    return positions


def _merge_usage(*usages: dict[str, Any]) -> dict[str, Any]:
    return {
        "requests": sum(u.get("requests", 0) for u in usages),
        "input_tokens": sum(u.get("input_tokens", 0) for u in usages),
    }


def judge(
    client: Any, cards: list[ShapedCard], state: str,
    user_intent: str = "", mode: str = "blind", samples: int = 1,
) -> tuple[list[Judgment], dict[str, Any]]:
    """Ask Jev about every card. Returns judgments in pool order plus usage."""
    samples = max(1, samples)
    if mode == "choice":
        return _judge_choice(client, cards, state, user_intent, samples)
    if mode == "ensemble":
        with ThreadPoolExecutor(max_workers=2) as pool:
            verdict = pool.submit(judge, client, cards, state, user_intent, "verdict", samples)
            choice = pool.submit(judge, client, cards, state, user_intent, "choice", samples)
            (verdicts, v_usage), (choices, c_usage) = verdict.result(), choice.result()
        v_pos, c_pos = _positions(verdicts), _positions(choices)
        judgments = [
            Judgment(name=v.name, score=(v_pos[i] + c_pos[i]) / 2, asked=v.asked)
            for i, v in enumerate(verdicts)
        ]
        return judgments, _merge_usage(v_usage, c_usage)
    if samples == 1:
        return _judge_per_card(client, cards, state, user_intent, mode)
    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, samples)) as pool:
        runs = list(pool.map(
            lambda _: _judge_per_card(client, cards, state, user_intent, mode), range(samples),
        ))
    return _average([r[0] for r in runs]), _merge_usage(*(r[1] for r in runs))


def _rank_key(judgment: Judgment, card: ShapedCard, mode: str, brainmap_weight: float) -> float:
    if mode in ("verdict", "choice", "ensemble"):
        jev = judgment.score
    else:
        # Score gated by the ask: a core piece that ignores the request still
        # ranks, but below a strong card that answers it.
        jev = (judgment.score / _SCORE_MAX) * (0.3 + 0.7 * (judgment.asked or 0.0))
    total = (card.brainmap or {}).get("total")
    if not brainmap_weight or not isinstance(total, (int, float)):
        return jev
    return (1 - brainmap_weight) * jev + brainmap_weight * max(0.0, min(1.0, float(total)))


def reason_for(judgment: Judgment, card: ShapedCard, mode: str = "blind",
               position: int = 0, pool_size: int = 0) -> str:
    if mode in ("choice", "ensemble"):
        head = f"Jev ranked it #{position} of {pool_size} candidates for this request"
    elif mode == "verdict":
        head = f"Jev: {judgment.score:.0%} that it belongs in this batch"
    else:
        levels = VERDICT_LEVELS if mode == "informed" else FIT_LEVELS
        level = levels[min(_SCORE_MAX, max(0, round(judgment.score)))].split(":")[0]
        label = "verdict" if mode == "informed" else "fit"
        head = (f"{level} (Jev {label} {judgment.score:.1f}/{_SCORE_MAX}"
                f", answers the ask {(judgment.asked or 0.0):.0%})")
    parts = [head]
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
    samples: int = 1,
    max_similar: int | None = None,
    min_probability: float | None = None,
) -> Selection:
    """Stage 4 on Jev. Same contract as ``selection.select``: picks are legal
    pool cards only. Illegal cards are never sent, so they cannot be picked.
    Raises ``JevError`` on any failure so the caller can fall back.

    ``brainmap_weight`` defaults to 0.25 when blind and 0 otherwise: the other
    modes have already weighed the brain map, and blending it in again would
    count it twice."""
    if mode not in MODES:
        raise JevError(f"unknown Jev mode {mode!r}")
    if brainmap_weight is None:
        brainmap_weight = 0.25 if mode == "blind" else 0.0

    legal = [c for c in pool if c.legal_in_deck and c.name]
    if not legal:
        return Selection(picks=[], summary="", raw={"backend": "jev", "mode": mode, "judgments": []})

    state = build_state(deck_context, user_intent, player_message)
    judgments, usage = judge(client, legal, state, user_intent, mode, samples)
    for judgment, card in zip(judgments, legal):
        judgment.rank = _rank_key(judgment, card, mode, brainmap_weight)

    order = sorted(range(len(legal)), key=lambda i: judgments[i].rank, reverse=True)
    chosen = shape_batch(
        order, legal, judgments, max_picks,
        max_similar=max_similar,
        min_probability=min_probability if mode == "verdict" else None,
    )
    position_of = {index: position for position, index in enumerate(order)}
    picks = [
        Pick(name=legal[i].name,
             reason=reason_for(judgments[i], legal[i], mode, position_of[i] + 1, len(legal)))
        for i in chosen
    ]

    def _round(value: float | None, places: int) -> float | None:
        return None if value is None else round(value, places)

    return Selection(
        picks=picks,
        summary="",
        raw={
            "backend": "jev",
            "mode": mode,
            "samples": samples,
            "model": getattr(client, "model", None),
            "usage": usage,
            "judgments": [
                {"name": judgments[i].name, "score": _round(judgments[i].score, 4),
                 "confidence": _round(judgments[i].confidence, 3),
                 "asked": _round(judgments[i].asked, 3),
                 "duplicate": _round(judgments[i].duplicate, 3),
                 "rank": round(judgments[i].rank, 5)}
                for i in order
            ],
        },
    )


_TYPE_ORDER = ("Land", "Creature", "Planeswalker", "Battle", "Artifact",
               "Enchantment", "Instant", "Sorcery")


def signature(card: ShapedCard) -> tuple[str, frozenset[str]]:
    """What makes two candidates interchangeable: the same primary card type
    and the same functional roles. Four fetchlands share one; a fetchland and
    Cultivate do not."""
    type_line = card.type_line or ""
    primary = next((t for t in _TYPE_ORDER if t in type_line), type_line)
    return primary, frozenset(card.fine_roles)


def shape_batch(
    order: list[int], cards: list[ShapedCard], judgments: list[Judgment], max_picks: int,
    *, max_similar: int | None = None, min_probability: float | None = None,
) -> list[int]:
    """Walk the ranking and take picks, skipping a card once ``max_similar``
    interchangeable cards are already taken, and stopping at the first card
    below ``min_probability``. Ranking judges cards one at a time and cannot
    see that the batch is already four fetchlands; this is where the batch as
    a whole gets a say. A batch shorter than ``max_picks`` is the correct
    output when few candidates clear the bar."""
    chosen: list[int] = []
    counts: dict[tuple[str, frozenset[str]], int] = {}
    for index in order:
        if len(chosen) >= max_picks:
            break
        if min_probability is not None and judgments[index].score < min_probability:
            break
        sig = signature(cards[index])
        if max_similar is not None and sig[1] and counts.get(sig, 0) >= max_similar:
            continue
        counts[sig] = counts.get(sig, 0) + 1
        chosen.append(index)
    return chosen


def get_client() -> JevClient:
    from app.config import settings

    return JevClient(
        settings.typesafe_api_key, model=settings.typesafe_model or DEFAULT_MODEL,
    )
