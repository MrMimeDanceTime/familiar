from app.deckplan import DEFAULT_POWER, DEFAULT_TARGETS
from app.llm.base import ToolSpec

# Rendered from the plan module so the model is told the defaults the scorer
# actually uses. A hand-typed copy here drifted to numbers two changes old.
_DEFAULT_TARGETS_TEXT = (
    f"{DEFAULT_TARGETS['land']} land / {DEFAULT_TARGETS['ramp']} ramp / "
    f"{DEFAULT_TARGETS['draw']} draw / {DEFAULT_TARGETS['removal']} removal, "
    f"the targets for power {DEFAULT_POWER}"
)

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
            "type, oracle text, and color identity, most played first. Answered "
            "from the local card index for the common syntax (o:, t:, name, c:, "
            "id:, legal:commander, cmc/pow/tou, r:, set:, b:, kw:, otag:, "
            "is:commander, OR, parentheses, -); anything else goes to Scryfall. "
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
        name="search_card_index",
        description=(
            "Full-text search over the local copy of every Magic card: name, "
            "rules text, and type line, all terms required. Instant and "
            "offline, so prefer it for quick 'what cards do X' lookups "
            "(e.g. 'sacrifice a creature draw', 'legendary dragon haste'). "
            "Results carry oracle text and functional tags. Use scryfall_search "
            "when you need Scryfall's query syntax (mana value, rarity, set, "
            "otag:), and scryfall_card_by_name to confirm one specific card."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Words to match in name, rules text, or type line"},
                "limit": {"type": "integer", "default": 10, "description": "Max results, up to 50"},
                "color_identity": {
                    "type": "string",
                    "description": "Restrict to cards within this colour identity, e.g. 'BR'. Omit for any.",
                },
            },
            "required": ["query"],
        },
    ),
    ToolSpec(
        name="find_commanders",
        description=(
            "Find cards that can lead a Commander deck (legendary creatures and "
            "cards that say they can be your commander) in the local card index, "
            "with their rules text. Use it whenever the player is choosing a "
            "commander (a theme, a set, a creature type, colours, 'something "
            "less played') instead of listing commanders from memory. Filters "
            "combine; run a few focused searches rather than one broad one. "
            "set_name matches every set a card was printed in, by set name, set "
            "code, or block ('Theros' finds the whole Theros block, reprints "
            "included). Any commander you mention that no search returned, look "
            "up first."
        ),
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": (
                    "Words that must all appear in the name, rules text or type line, "
                    "e.g. 'sacrifice token', 'exile cast', 'God'."
                )},
                "colors": {"type": "string", "description": "Colour identity letters, e.g. 'BR'. Omit for any."},
                "exact_colors": {"type": "boolean", "description": (
                    "True: exactly these colours. False (default): within them."
                )},
                "creature_type": {"type": "string", "description": "e.g. 'Sphinx', 'Elemental', 'God'."},
                "set_name": {"type": "string", "description": (
                    "A set or block name or code, or several separated by commas, "
                    "e.g. 'Theros', 'jou', 'Kaldheim, Theros Beyond Death'."
                )},
                "popularity": {"type": "string", "enum": ["any", "popular", "less_popular"], "description": (
                    "less_popular when the player wants something novel or not overplayed."
                )},
                "limit": {"type": "integer", "default": 20, "description": "Up to 40."},
            },
        },
    ),
    ToolSpec(
        name="scryfall_card_by_name",
        description=(
            "Look up a single Magic card by name (fuzzy match by default). Use this "
            "whenever you need to confirm a specific card's mana cost, oracle text, "
            "or legality before mentioning it to the user. Returns the card's "
            "rulings too; cite them for timing and interaction questions rather "
            "than reasoning from memory."
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
            "rate reflects popularity, not correctness, especially for off-meta "
            "builds. The head of each category arrives WITH oracle_text, type_line, "
            "mana_cost and tags, so reason about those cards from the text in the "
            "payload rather than from memory. Cards further down a long list carry "
            "\"needs_lookup\": true and have NO rules text here — look one up with "
            "scryfall_card_by_name before describing what it does. A card marked "
            "\"unverified\": true could not be resolved against Scryfall at all; "
            "do not build around it."
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
            "is authoritative — prefer it over training-data assumptions. An "
            "entry with source 'user' was written by the player (house rules, "
            "their playgroup's expectations, their own conclusions) and "
            "outranks a seeded entry when the two disagree."
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
                        "synergy, format-specific, power-level, playgroup (the "
                        "player's own notes about their table)."
                    ),
                    "enum": [
                        "mana-curve", "ramp", "removal", "card-draw", "land-base",
                        "color-pie", "commander", "synergy", "format-specific",
                        "power-level",
                        "playgroup",
                    ],
                },
                "top_k": {"type": "integer", "default": 5, "description": "Max results to return"},
            },
            "required": ["query"],
        },
    ),
    ToolSpec(
        name="deck_get_current",
        description=(
            "Get the current in-progress deck: commander, card list, quantities, "
            "categories, and notes. Also returns pending_proposals — the batch "
            "still awaiting the player's approve/deny, read live from the "
            "database. That field is the ONLY accurate source for what is "
            "outstanding: the player approves and denies in the UI, which this "
            "conversation never sees, so an earlier propose_deck_changes result "
            "in your history may name cards that were resolved long ago. Trust "
            "pending_proposals over your own memory of what you proposed. Note "
            "pending cards are NOT counted in total_cards — a proposal is not "
            "yet part of the deck. Oracle text comes back for the commander(s) "
            "only; set include_oracle_text=true when you need to reason about "
            "the rules text of the whole list, or look up specific cards with "
            "scryfall_card_collection."
        ),
        parameters={
            "type": "object",
            "properties": {
                "deck_id": {"type": "integer"},
                "include_oracle_text": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Include oracle_text for every card, not just the "
                        "commander(s). Costs a lot of context on a full deck; "
                        "use it when you genuinely need the whole list's text."
                    ),
                },
            },
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
            "to prioritise what a deck needs instead of re-deriving targets. "
            "'mana_sources' compares each colour's share of mana sources with its "
            "share of coloured pips and flags a colour that is LOW; "
            "'total_price_usd' is the deck's price at the index's printing; "
            "'combos' lists the combos the deck already contains (from Commander "
            "Spellbook), which the bracket estimate also uses."
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
            "deny; you cannot modify the deck directly. This is for changes "
            "already decided: the commander (set_commander), cards the player "
            "named (player_named: true), and cuts. Cards you would choose "
            "yourself go through suggest_cards; three or more of them here are "
            "redirected there. Proposing only the commander does not use up the "
            "turn's card batch, so 'lock in the commander and give me ramp' is "
            "this call, then suggest_cards. Give each change reasoning the "
            "player can evaluate; add names are checked against the card index."
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
                            "player_named": {
                                "type": "boolean",
                                "description": (
                                    "TRUE only when the PLAYER named this exact card. "
                                    "Role batches you assembled must go through "
                                    "suggest_cards, and a batch of 3+ adds without this "
                                    "flag is refused — but a card the player asked for "
                                    "by name is their decision, not a suggestion to "
                                    "score, so mark it and it goes through. Never set "
                                    "this to route around the check on cards you chose."
                                ),
                            },
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
            "Withdraw (deny) pending proposals for the deck. Two uses: "
            "(1) TRIM — right after a batch, remove a card that breaks something "
            "the player explicitly asked for (an excluded type or card, their "
            "budget, a named restriction) by passing its name in `card_names`; "
            "the rest of the batch stays. Not for fit or strength doubts: say "
            "those in the reply and let the player decide. "
            "(2) CLEAR — omit `card_names` to withdraw the WHOLE pending batch, "
            "when the player rejects it and wants nothing in its place. A new "
            "batch replaces a stale one on its own; no need to clear first. "
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
                "card_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Withdraw only these cards from the pending batch (exact "
                        "card names, as proposed). Omit to withdraw the entire "
                        "pending batch."
                    ),
                },
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
            "Run the card-suggestion pipeline: it builds a candidate pool for "
            "the intent from the local card index and EDHREC, drops anything "
            "illegal for this deck (off-colour, banned, already in or awaiting "
            "a decision, over budget, excluded by the player), ranks the rest "
            "against this deck and its plan, and returns a batch of pending ADD "
            "proposals with each card's rules text and reasoning. Missing "
            "format staples that fill the requested role join the batch. A "
            "land request fills the manabase to the plan's land target, basics "
            "included, whatever the count. "
            "THESE ARE LIVE PROPOSALS, NOT A SHORTLIST. They go straight to the "
            "player's approve/deny queue. Read what comes back BEFORE you write "
            "your reply. Withdraw a pick only if it breaks something the player "
            "explicitly asked for (an excluded type or card, their budget, a named "
            "restriction). Do not withdraw for fit or strength: the ranking already "
            "weighed those against the deck. Mention a doubt in one line instead; "
            "the player approves or denies every card. "
            "USE THIS whenever the player asks you to suggest, recommend, find, or "
            "add cards to fill a role or gap — e.g. 'suggest some ramp', 'what "
            "removal should I run', 'help me find card draw', 'fill out the "
            "manabase'. It is how every batch of cards you would choose reaches "
            "the player. "
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
                        "What the player wants, in a focused phrase naming the "
                        "role or theme, e.g. 'cheap instant-speed removal under "
                        "3 mana', 'ramp that fixes colors', 'card draw on a budget'. "
                        "Include constraints the player stated."
                    ),
                },
                "count": {
                    "type": "integer",
                    "default": 5,
                    "description": (
                        "How many cards to propose, 1-10. Match the batch size "
                        "you told the player (three to six is the usual batch); "
                        "asking for more and trimming wastes picks."
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
    ToolSpec(
        name="deck_set_plan",
        description=(
            "Record what the PLAYER stated about the deck: a power level, a budget, "
            "excluded types or cards, how off-meta to build, themes they spelled "
            "out, or a change of direction. The app drafts the deck's themes and "
            "gameplan itself when you propose the commander, so do not compose "
            "them here. The plan drives card suggestions and comes back on every "
            "deck read. Pass only the fields you are changing."
        ),
        parameters={
            "type": "object",
            "properties": {
                "deck_id": {"type": "integer"},
                "themes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Only themes the player stated or a pivot they asked for, e.g. "
                        '["cat and dog tribal"]. The app drafts the initial themes '
                        "itself when the commander is proposed."
                    ),
                },
                "role_targets": {
                    "type": "object",
                    "description": (
                        "How many cards each role should end up with. Omit a role "
                        f"to keep its default ({_DEFAULT_TARGETS_TEXT}; a stated "
                        "power_level changes them). Match the deck's actual plan: "
                        "a low-curve aggro deck wants fewer lands than a big-mana "
                        "deck."
                    ),
                    "properties": {
                        "land": {"type": "integer"},
                        "ramp": {"type": "integer"},
                        "draw": {"type": "integer"},
                        "removal": {"type": "integer"},
                    },
                },
                "plan_notes": {
                    "type": "string",
                    "description": (
                        "The gameplan in the player's own words, only when they "
                        "described one. Leave it unset otherwise: the app drafts "
                        "the gameplan itself when the commander is proposed."
                    ),
                },
                "power_level": {
                    "type": "string",
                    "description": (
                        "Target power level on the 1-10 scale, as the player stated "
                        "it (\"7\", \"~7\", \"7-8\"). SET THIS whenever the player "
                        "names a power level: the role targets are derived from it "
                        "using the same formula deck_get_stats scores with. A "
                        "bracket is not a power level; put it in bracket instead."
                    ),
                },
                "bracket": {
                    "type": "integer",
                    "description": (
                        "The Commander bracket (1-5) the player named, when they "
                        "named a bracket rather than a power level. The app turns it "
                        "into the power target."
                    ),
                },
                "max_card_price": {
                    "type": "number",
                    "description": (
                        "Budget ceiling per card in US dollars. Set it when the "
                        "player names a budget ('nothing over $10', 'keep it "
                        "cheap' = 5). Candidates above it are excluded from "
                        "suggest_cards. 0 removes the ceiling. Unset, the player's "
                        "standing budget preference applies (budget = $5, "
                        "mid-range = $25, unlimited = none)."
                    ),
                },
                "exclude_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Card types or creature types the player ruled out for this "
                        "deck, singular: 'no dragons or demons' = [\"Dragon\", "
                        "\"Demon\"]; 'no planeswalkers' = [\"Planeswalker\"]. SET "
                        "THIS the moment the player states one: it is enforced on "
                        "every later suggestion and proposal, so it holds after the "
                        "message that said it has scrolled away. Pass the full list; "
                        "[] clears it."
                    ),
                },
                "exclude_cards": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Specific cards the player never wants in this deck, by "
                        "exact name. Enforced like exclude_types. Pass the full "
                        "list; [] clears it."
                    ),
                },
                "off_meta": {
                    "type": "number",
                    "description": (
                        "0.0-1.0. How far from the popular consensus list to build. "
                        "0 follows what most decks with this commander run; 1 "
                        "favours cards specific to this commander even when few "
                        "decks play them. Default 0.25. Raise it when the player "
                        "wants something distinctive or dislikes netdecked lists."
                    ),
                },
            },
            "required": ["deck_id"],
        },
    ),
]
