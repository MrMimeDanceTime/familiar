# Product philosophy

Familiar is a conversational brainstorming partner for Magic: The Gathering
Commander/EDH deckbuilding, grounded in real Scryfall/EDHREC data.

**It is explicitly NOT a "generate an optimized/meta decklist" tool.** If
the user proposes an off-meta or unusual commander idea, the assistant
should help develop *that* idea further — not steer them toward EDHREC's
most popular list for the commander. This is a product-behavior
requirement, not just a one-off preference, and it's encoded directly in
`backend/app/chat/prompt.py`'s system prompt:

- **GROUNDING** — never assert a card's name/cost/ability without having
  retrieved it via a Scryfall tool call in the conversation; never invent a
  card.
- **COLLABORATIVE POSTURE** — default to discussing the idea (power level,
  budget, theme tightness, playgroup expectations) rather than dumping a
  full 99-card list; propose cards in small batches once direction
  converges.
- **EDHREC AS INSPIRATION, NOT GOSPEL** — fine to pull EDHREC data
  proactively, but frame inclusion rate as a popularity signal, not a
  constraint. Actively surface off-meta or low-inclusion cards when they
  fit the user's stated angle — EDHREC data exists to support non-meta
  builds, not flatten them into the most popular list.
- **DECK-STATE DISCIPLINE** — call `deck_*` tools immediately once the user
  agrees to a change, so the UI panel stays in sync; don't call them for
  cards still under discussion.
- **CLARIFYING-QUESTION BIAS** — if the deck is empty/small and the
  strategy isn't clear yet, ask before acting.
- **RESEARCH PACING** — budget tool-calling turns; reply with partial
  information and clarifying questions rather than silently exhausting the
  iteration limit trying to fully research everything before responding.

## When this matters for code changes

When adjusting Familiar's chat behavior, prompt wording, or tool
descriptions, preserve this philosophy. Concretely: don't add logic that
ranks or filters card suggestions purely by EDHREC inclusion rate or by an
"optimal power level" metric — that would contradict the core product
intent, even if it produces technically stronger decklists.

There is no deck-evaluation feature (mana curve / ramp-on-curve / theme
coherence scoring) yet. The schema already supports it later
(`DeckCard.mana_value` and `color_identity` are denormalized from Scryfall
at insert time for exactly this purpose), but it is future work, not a gap
to fill in unprompted.
