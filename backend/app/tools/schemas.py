from app.llm.base import ToolSpec

TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        name="scryfall_search",
        description=(
            "Search Magic: The Gathering cards using Scryfall's query syntax "
            "(e.g. 'c:red t:creature', 'o:\"draw a card\"'). Returns up to `limit` "
            "matching cards with name, mana cost, type, oracle text, and color identity. "
            "Use this to explore options, not to verify a single known card name."
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
        name="deck_get_current",
        description="Get the current in-progress deck: commander, card list, quantities, categories, and notes.",
        parameters={
            "type": "object",
            "properties": {"deck_id": {"type": "integer"}},
            "required": ["deck_id"],
        },
    ),
    ToolSpec(
        name="deck_add_card",
        description=(
            "Add a card to the in-progress deck (or update its quantity/category/notes "
            "if already present). Only call this once the user has actually agreed to "
            "include the card — not while still discussing options."
        ),
        parameters={
            "type": "object",
            "properties": {
                "deck_id": {"type": "integer"},
                "card_name": {"type": "string"},
                "qty": {"type": "integer", "default": 1},
                "category": {
                    "type": "string",
                    "description": "Free-text role, e.g. ramp, removal, draw, win-con, synergy-piece, land, other",
                },
                "notes": {"type": "string"},
            },
            "required": ["deck_id", "card_name"],
        },
    ),
    ToolSpec(
        name="deck_remove_card",
        description="Remove a card from the in-progress deck.",
        parameters={
            "type": "object",
            "properties": {
                "deck_id": {"type": "integer"},
                "card_name": {"type": "string"},
            },
            "required": ["deck_id", "card_name"],
        },
    ),
    ToolSpec(
        name="deck_set_commander",
        description="Set or change the commander for the in-progress deck.",
        parameters={
            "type": "object",
            "properties": {
                "deck_id": {"type": "integer"},
                "commander_name": {"type": "string"},
            },
            "required": ["deck_id", "commander_name"],
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
