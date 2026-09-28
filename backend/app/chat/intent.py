"""Is this message a plain role request, the one turn the pipeline carries?

A short request for a batch of cards in one role ("give me the ramp
package") on a deck with a plan: Jev chooses the cards, so the chat model's
think on that turn was a shortlist nobody used plus one intent phrase. Such
turns skip it (settings.chat_role_batch_thinking). Every other turn thinks.

Jev decides, with two yes/no questions in one call. A word-matching rule was
shipped first and measured on 134 real player messages plus 40 written ones:
it would have skipped the think on 30 of 152 messages that need one, among
them "Lets trim those lands and add some draw yeah", a cut decision. The Jev
gate below skipped none of the 152 and found 15 of 22 plain requests, at a
median 0.14s. A miss only means the turn thinks, so the thresholds lean that
way, and so does any failure: no key, an error, a timeout.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

PLAIN_MIN = 0.6
ASKS_MAX = 0.5
# The gate sits in front of the first send; a slow answer is worse than none.
_TIMEOUT_SECONDS = 3.0

_PLAIN = (
    "The player's message only asks for a batch of cards that fill one deck role "
    "or function (for example ramp, card draw, removal, lands, protection, tokens), "
    "possibly with simple limits such as a count, a budget or a mana value, and asks "
    "for nothing else: no cuts or swaps, no question, no analysis or explanation, no "
    "complaint, no change of direction, and nothing that depends on an earlier message "
    "to know which cards are meant."
)
_ASKS = (
    "The player's message asks the assistant a question, or asks it to count, check, "
    "review, compare, or explain something about the deck or a card."
)
_CONTEXT = "A player talking to a Magic: The Gathering Commander deckbuilding assistant."


def plain_role_request(text: str, client: object | None = None) -> bool:
    try:
        if client is None:
            from app.config import settings
            from app.pipeline import jev

            client = jev.JevClient(
                settings.typesafe_api_key, model=settings.typesafe_model or jev.DEFAULT_MODEL,
                timeout=_TIMEOUT_SECONDS, retries=0,
            )
        response = client.system_one(
            {"player_message": text, "context": _CONTEXT},
            {
                "plain": {"type": "noul", "instructions": {"statement": _PLAIN}},
                "asks": {"type": "noul", "instructions": {"statement": _ASKS}},
            },
        )
        answers = response["answers"]
        plain, asks = float(answers["plain"]["noul"]), float(answers["asks"]["noul"])
    except Exception as exc:  # noqa: BLE001 - no answer means the turn thinks
        logger.info("intent gate unavailable, thinking: %s", exc)
        return False
    logger.info("intent gate: plain=%.2f asks=%.2f", plain, asks)
    return plain >= PLAIN_MIN and asks < ASKS_MAX
