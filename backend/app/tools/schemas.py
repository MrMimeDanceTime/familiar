from app.llm.base import ToolSpec

TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        name="scryfall_search",
        description=(
            "Search Magic: The Gathering cards using Scryfall's query syntax "
            "(e.g. 'c:red t:creature', 'o:\"draw a card\"', 'legal:commander'). "
            "To restrict to Commander-legal cards use 'legal:commander' (not "
            "'commander legal'). Do not add 'game:paper' unless specifically asked "
            "about paper-only availability — it excludes many legal cards and is "
            "almost never what you want. If a query returns no results, broaden it "
            "(fewer o: clauses, less specific wording) rather than retrying narrow "
            "variations. Returns up to `limit` matching cards with name, mana cost, "
            "type, oracle text, and color identity. Use this to explore options, not "
            "to verify a single known card name."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Scryfall search query syntax"},
                "limit": {"type": "integer", "default": 10, "description": "Max results, capped at 175"},
            },
            "required": ["query"],
        },
    ),
    ToolSpec(
        name="scryfall_card_by_name",
        description=(
            "Look up a single Magic card by name (fuzzy match by default). Use this "
            "whenever you need to confirm a specific card's mana cost, oracle text, "
            "or legality before mentioning it to the user."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "fuzzy": {"type": "boolean", "default": True},
            },
            "required": ["name"],
        },
    ),
    ToolSpec(
        name="scryfall_card_collection",
        description=(
            "Look up multiple Magic cards by name in one call (up to 75). Use this "
            "instead of repeated scryfall_card_by_name calls when checking many "
            "cards at once, e.g. validating an entire proposed list."
        ),
        parameters={
            "type": "object",
            "properties": {
                "names": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["names"],
        },
    ),
    ToolSpec(
        name="edhrec_commander_recs",
        description=(
            "Get EDHREC's community data for a commander: cardlists grouped by "
            "category (high synergy cards, top cards, creatures, instants, etc.), "
            "each with a synergy score and inclusion rate across decks on EDHREC. "
            "Treat this as inspiration/grounding data, not a constraint — inclusion "
            "rate reflects popularity, not correctness, especially for off-meta builds."
        ),
        parameters={
            "type": "object",
            "properties": {
                "commander_name": {"type": "string"},
            },
            "required": ["commander_name"],
        },
    ),
    ToolSpec(
        name="edhrec_card_synergy",
        description=(
            "Look up a specific card's synergy score and inclusion stats within a "
            "given commander's EDHREC page. Returns null if the card isn't present "
            "in that commander's cardlists (which can simply mean it's uncommon, "
            "not that it's a bad fit)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "card_name": {"type": "string"},
                "commander_name": {"type": "string"},
            },
            "required": ["card_name", "commander_name"],
        },
    ),
    ToolSpec(
        name="search_deckbuilding_knowledge",
        description=(
            "Search a local knowledge base of MTG deckbuilding best practices "
            "and format-specific rules. Use this whenever you need grounded "
            "advice on topics like ideal land counts, mana curve construction, "
            "ramp package sizing, removal suite composition, color pie "
            "strengths and weaknesses, commander selection heuristics, synergy "
            "vs goodstuff tradeoffs, or sideboard construction. Returns the "
            "top matching entries with their full content. This knowledge base "
            "is authoritative — prefer it over training-data assumptions."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language search query, e.g. 'how many lands in commander', 'ramp curve', 'removal suite sizing'",
                },
                "category": {
                    "type": "string",
                    "description": (
                        "Optional filter to one topic when the query keyword also "
                        "appears in unrelated entries. One of: mana-curve, ramp, "
                        "removal, card-draw, land-base, color-pie, commander, "
                        "synergy, format-specific, power-level."
                    ),
                    "enum": [
                        "mana-curve", "ramp", "removal", "card-draw", "land-base",
                        "color-pie", "commander", "synergy", "format-specific",
                        "power-level",
                    ],
                },
                "top_k": {"type": "integer", "default": 5, "description": "Max results to return"},
            },
            "required": ["query"],
        },
    ),
    ToolSpec(
        name="deck_get_current",
        description="Get the current in-progress deck: commander, card list, quantities, categories, and notes.",
        parameters={
            "type": "object",
            "properties": {"deck_id": {"type": "integer"}},
            "required": ["deck_id"],
        },
    ),
    ToolSpec(
        name="deck_get_stats",
        description=(
            "Get the deck's computed bracket (1-5), power level (1-10), and "
            "the detailed breakdown factors explaining why each score was "
            "assigned. Includes mana curve, type breakdown, color distribution, "
            "ramp/draw/removal counts, game changer detection, tutor count, "
            "MLD presence, and the dimension-by-dimension power level scoring. "
            "Use this whenever discussing the deck's power or bracket. The "
            "'untagged' field lists nonland cards with no functional tags yet "
            "(usually brand-new cards) — when non-empty, the ramp/draw/removal "
            "counts may undercount, so caveat any count-based advice. The "
            "'deficiencies' field (commander only) compares lands/ramp/draw/"
            "removal against target ranges and flags each LOW/OK/HIGH — use it "
            "to prioritise what a deck needs instead of re-deriving targets."
        ),
        parameters={
            "type": "object",
            "properties": {"deck_id": {"type": "integer"}},
            "required": ["deck_id"],
        },
    ),
    ToolSpec(
        name="propose_deck_changes",
        description=(
            "Propose changes to the in-progress deck for the player to approve or "
            "deny. You cannot modify the deck directly — you must use this tool to "
            "make proposals. Aim for about 3-6 CARD adds/removes per batch. A "
            "'set_commander' action does NOT count toward that batch size — when "
            "you open a deck by batching the commander together with cards, include "
            "the full set of cards you intend (e.g. commander + 6 cards is one call "
            "with 7 changes), not 6 changes total with the commander eating a card "
            "slot. Whatever number of cards you describe in your reply, emit exactly "
            "that many 'add' changes here — the count in your prose and the count in "
            "this call must match. For each change, provide clear reasoning the "
            "player can evaluate. For 'add' actions, the card name will be validated "
            "against Scryfall."
        ),
        parameters={
            "type": "object",
            "properties": {
                "deck_id": {"type": "integer"},
                "summary": {
                    "type": "string",
                    "description": "One-line summary of the proposal batch, e.g. 'Adding 3 ramp pieces and cutting 2 overcosted top-end cards'",
                },
                "changes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["add", "remove", "set_commander"],
                            },
                            "card_name": {"type": "string", "description": "Card name (required for add/remove)"},
                            "quantity": {
                                "type": "integer",
                                "default": 1,
                                "description": (
                                    "For 'add', how many copies to add (default 1). For "
                                    "'remove', how many copies to cut — omit this to remove "
                                    "the entire stack (the usual case in singleton formats); "
                                    "set it explicitly to cut only some copies of a card with "
                                    "multiple copies, e.g. removing 2 of 34 Swamp."
                                ),
                            },
                            "category": {"type": "string", "description": "Free-text role (add only), e.g. ramp, removal, draw, win-con"},
                            "reasoning": {"type": "string", "description": "Why this change — shown to the player for approval"},
                        },
                        "required": ["action", "reasoning"],
                    },
                },
            },
            "required": ["deck_id", "summary", "changes"],
        },
    ),
    ToolSpec(
        name="withdraw_pending_proposals",
        description=(
            "Withdraw (deny) pending CARD proposals for the deck. Use this "
            "when the player changes direction, rejects a batch in favour of "
            "a different approach, or explicitly asks to cancel the current "
            "proposals. Call this BEFORE proposing a replacement batch. "
            "A pending set_commander proposal is NOT cleared by this — it is "
            "the deck's identity, not a batch, and must never be cancelled as "
            "a side effect of swapping card batches. Only set "
            "include_commander=true when the player has explicitly decided "
            "against the proposed commander, and say so in your reply."
        ),
        parameters={
            "type": "object",
            "properties": {
                "deck_id": {"type": "integer"},
                "include_commander": {
                    "type": "boolean",
                    "description": (
                        "Also withdraw a pending commander proposal. Default "
                        "false. Set true ONLY when the player has decided "
                        "against the proposed commander."
                    ),
                },
            },
            "required": ["deck_id"],
        },
    ),
    ToolSpec(
        name="suggest_cards",
        description=(
            "Run the deterministic card-suggestion pipeline: it generates focused "
            "Scryfall queries for the intent, retrieves and filters a legal, "
            "on-color candidate pool (EDHREC-ranked, already excluding the "
            "commander, owned cards, and banned/off-identity cards), and returns a "
            "batch of pending ADD proposals with reasoning — the same kind of "
            "proposals propose_deck_changes creates. "
            "USE THIS whenever the player asks you to suggest, recommend, find, or "
            "add cards to fill a role or gap — e.g. 'suggest some ramp', 'what "
            "removal should I run', 'help me find card draw', 'fill out the "
            "manabase'. Prefer it over scryfall_search + manual propose_deck_changes "
            "for open-ended 'what should I add' requests: it does the search, "
            "filtering, and legality-checking for you. "
            "Do NOT use it for questions that aren't asking for card suggestions "
            "(rules questions, explaining a card, discussing the deck's power level, "
            "or when the player names a specific card to add — use "
            "propose_deck_changes directly for a named card). Give a clear, specific "
            "`intent` describing the role and any constraints (budget, mana value, "
            "keywords), since that intent drives the whole search."
        ),
        parameters={
            "type": "object",
            "properties": {
                "deck_id": {"type": "integer"},
                "intent": {
                    "type": "string",
                    "description": (
                        "What the player wants, in a focused phrase the query "
                        "planner can act on, e.g. 'cheap instant-speed removal under "
                        "3 mana', 'ramp that fixes colors', 'card draw on a budget'. "
                        "Include constraints the player stated."
                    ),
                },
            },
            "required": ["deck_id", "intent"],
        },
    ),
    ToolSpec(
        name="deck_update_notes",
        description="Update the free-form notes/strategy summary attached to the in-progress deck.",
        parameters={
            "type": "object",
            "properties": {
                "deck_id": {"type": "integer"},
                "notes": {"type": "string"},
            },
            "required": ["deck_id", "notes"],
        },
    ),
]
