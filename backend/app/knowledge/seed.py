"""Deckbuilding best-practices knowledge base.

Each entry is a concise, self-contained piece of deckbuilding advice
keyed by category and format so the LLM can retrieve grounded guidance
via ``search_deckbuilding_knowledge`` instead of relying on training-data
hallucinations.

Entries are seeded once at startup (idempotent — skips if the table is
already populated).
"""

from sqlmodel import Session, select, text

from app.knowledge.models import KnowledgeEntry
from app.tools.deck_tools import _BANNED_COMMANDER, _GAME_CHANGERS, _FAST_MANA

ENTRIES: list[dict[str, str]] = [
    # ── Mana Curve ───────────────────────────────────────────────────────
    {
        "title": "Land count formula for Commander",
        "body": (
            "Start at 40 lands for a typical Commander deck, then subtract "
            "roughly 1 land for every 2 sources of ramp at mana value 2 or "
            "less. Ramp at 3+ MV doesn't replace lands — it accelerates "
            "once you already have mana. Aggressive decks can cut to 33-35 "
            "lands if their curve tops out at 3-4 MV. Decks with an average "
            "MV above 3.5 should stay at 37-40 lands. Never go below 33 "
            "lands in Commander — missing land drops is worse than flooding "
            "in a format where games run long."
        ),
        "category": "mana-curve",
        "format": "commander",
    },
    {
        "title": "Ideal mana curve shape",
        "body": (
            "A healthy Commander mana curve peaks at 2-3 MV (the 'meat' of "
            "the deck) with a steady decline toward higher costs. For a "
            "typical mid-power deck, aim for: 0-1 MV: 8-12 cards, 2 MV: "
            "18-22, 3 MV: 16-20, 4 MV: 10-14, 5 MV: 6-10, 6+ MV: 4-8. "
            "The curve should shift left (lower) for cEDH and right for "
            "battlecruiser metas. The shape matters more than the average "
            "— a deck with 10 1-drops and 10 6-drops averages 3.5 but "
            "plays terribly because it has no mid-game presence."
        ),
        "category": "mana-curve",
        "format": "commander",
    },
    {
        "title": "Average mana value targets",
        "body": (
            "cEDH/turbo: 1.5-2.0 average MV (excluding lands). "
            "High-power/optimized: 2.2-2.8. "
            "Mid-power/casual: 2.8-3.5. "
            "Battlecruiser/precon: 3.5-4.0. "
            "For 60-card constructed: aggro 1.5-2.0, midrange 2.0-2.8, "
            "control 2.5-3.5. These are heuristics, not laws — a deck with "
            "heavy cost reduction (affinity, delve, reanimation) can run a "
            "higher nominal curve because it rarely pays retail."
        ),
        "category": "mana-curve",
        "format": "any",
    },
    {
        "title": "60-card format land count",
        "body": (
            "Standard/modern/Pioneer aggro: 20-22 lands. Midrange: 24-25. "
            "Control: 26-28. These numbers assume a curve topping out at "
            "4-6 MV and no fast mana. Add 1-2 lands if your curve is "
            "higher, subtract 1 if you run cantrips (4+ Ponder/Preordain "
            "effects effectively count as ~0.5 lands each for hitting "
            "early drops). Limited/sealed: 17 lands for a typical 40-card "
            "deck."
        ),
        "category": "mana-curve",
        "format": "any",
    },

    # ── Ramp ──────────────────────────────────────────────────────────────
    {
        "title": "Ramp package sizing in Commander",
        "body": (
            "The baseline is 10-12 ramp pieces. Adjust by curve: if your "
            "commander costs 4 MV, run 8-10 pieces of ramp at 2 MV so you "
            "can cast it on turn 3. If your commander costs 5+, run 12-14 "
            "ramp sources and prioritize 2 MV ramp heavily. Green decks "
            "can bias toward land-based ramp (Rampant Growth effects — "
            "harder to remove than artifacts). Non-green decks rely on "
            "artifact ramp (signets, talismans, Sol Ring, Arcane Signet, "
            "Fellwar Stone, Mind Stone). Mana dorks (Llanowar Elves, "
            "Birds of Paradise) are faster but fragile — count them as "
            "0.5 ramp pieces if your meta runs wraths."
        ),
        "category": "ramp",
        "format": "commander",
    },
    {
        "title": "Two-MV vs three-MV ramp",
        "body": (
            "Two-MV ramp (signets, talismans, Nature's Lore, Three Visits) "
            "is premium because it lets you cast a 4-MV commander on turn "
            "3. Three-MV ramp (Cultivate, Kodama's Reach, Commander's "
            "Sphere) is slower but provides card advantage or fixing. In "
            "optimized decks, run 6-8 two-MV rocks and 2-4 three-MV ramp "
            "spells. Casual decks can afford more 3-MV ramp because games "
            "go longer and the card advantage matters more. Avoid 3-MV "
            "rocks that only tap for 1 with no upside — they're strictly "
            "worse than signets."
        ),
        "category": "ramp",
        "format": "commander",
    },
    {
        "title": "Ramp in 60-card formats",
        "body": (
            "60-card formats don't use dedicated ramp outside of specific "
            "archetypes. Green midrange decks might run 4 Llanowar Elves "
            "variants. Ramp decks (Tron, Amulet Titan, Nykthos devotion) "
            "are built entirely around their acceleration engine. Control "
            "decks use card selection and land-drop consistency instead of "
            "ramp. Aggro decks want zero ramp — every ramp spell is a "
            "threat you didn't cast."
        ),
        "category": "ramp",
        "format": "any",
    },

    # ── Removal ───────────────────────────────────────────────────────────
    {
        "title": "Removal suite composition for Commander",
        "body": (
            "Target 10-15 interactive pieces total: 6-8 spot removal, "
            "3-5 board wipes, 2-4 flexible answers (counterspells, "
            "disenchant effects, graveyard hate). Your removal should "
            "answer each permanent type — don't run 8 creature-kill spells "
            "and nothing for artifacts/enchantments. Prioritize instant-speed "
            "removal (Swords to Plowshares, Path to Exile, Beast Within, "
            "Generous Gift, Chaos Warp) over sorcery-speed. One-mana "
            "removal is significantly better than two-mana — the tempo "
            "difference of answering a 6-drop for W vs 1W is enormous."
        ),
        "category": "removal",
        "format": "commander",
    },
    {
        "title": "Board wipe count by archetype",
        "body": (
            "Aggro/go-wide decks: 1-2 wipes (you're the beatdown — wipes "
            "hurt you more than opponents). Midrange: 3-4 wipes (reset "
            "when behind, don't over-commit). Control: 5-7 wipes (you win "
            "by exhausting resources). Asymmetric wipes (Cyclonic Rift, "
            "Raise the Palisade, Winds of Abandon) are premium because "
            "they leave your board intact. In 60-card formats, control "
            "decks run 3-5 sweepers in the 75 (main + sideboard)."
        ),
        "category": "removal",
        "format": "any",
    },
    {
        "title": "Graveyard hate and silver bullets",
        "body": (
            "Every Commander deck should run 1-2 pieces of graveyard hate "
            "(Soul-Guide Lantern, Rest in Peace, Scavenger Grounds, "
            "Bojuka Bog). Graveyard strategies are the most common "
            "archetype in Commander and being unable to interact is a "
            "death sentence. In 60-card formats, graveyard hate lives in "
            "the sideboard (2-4 slots). Other silver bullets to consider: "
            "1-2 pieces of artifact/enchantment removal that hit multiple "
            "targets (Farewell, Austere Command, Bane of Progress)."
        ),
        "category": "removal",
        "format": "commander",
    },

    # ── Card Draw ─────────────────────────────────────────────────────────
    {
        "title": "Card draw density in Commander",
        "body": (
            "Target 10-12 sources of card advantage. This includes repeatable "
            "engines (Rhystic Study, Phyrexian Arena, Guardian Project) and "
            "burst draw (Harmonize, Painful Truths, Windfall). Repeatable "
            "draw is better than one-shot effects — a Phyrexian Arena that "
            "sticks for 5 turns draws 5 cards for 3 mana. Burst draw is "
            "still essential to refuel after a wipe or to dig for answers. "
            "Cantrips (Ponder, Preordain, Consider) are NOT card advantage "
            "— they're card selection and should not count toward your draw "
            "density budget."
        ),
        "category": "card-draw",
        "format": "commander",
    },
    {
        "title": "Card draw in 60-card formats",
        "body": (
            "Aggro: 0-4 draw effects (Light Up the Stage, Experimental "
            "Synthesizer — impulse draw that generates tempo). Midrange: "
            "4-8 sources (Fable of the Mirror-Breaker, Reckoner Bankbuster, "
            "planeswalkers that generate value). Control: 8-12 sources "
            "(Memory Deluge, Memory Deluge, Teferi, and dedicated card "
            "advantage engines). The key difference from Commander: in "
            "60-card, card draw competes with interaction slots, so every "
            "draw spell must pull significant weight."
        ),
        "category": "card-draw",
        "format": "any",
    },
    {
        "title": "Wheels versus incremental draw",
        "body": (
            "Wheel effects (Windfall, Wheel of Fortune, Timetwister) are "
            "powerful in Commander because they draw 7 for 3 mana, but "
            "they also refill opponents' hands — symmetrical wheels "
            "benefit the player who can deploy their new hand fastest. "
            "Run wheels in decks that: dump their hand quickly (aggro, "
            "spellslinger), break parity (Notion Thief, Narset Parter of "
            "Veils), or use the graveyard (Underworld Breach combos). "
            "Incremental draw (Rhystic Study, Esper Sentinel, Mystic "
            "Remora) is safer and better in control/midrange shells."
        ),
        "category": "card-draw",
        "format": "commander",
    },

    # ── Land Base ─────────────────────────────────────────────────────────
    {
        "title": "Colored sources math (Karsten tables)",
        "body": (
            "Frank Karsten's foundational article established how many "
            "colored sources you need to consistently cast spells on "
            "curve. Key benchmarks for Commander: to cast a 1-pip spell "
            "(e.g. 2W) on curve, you need ~12 sources of that color. For "
            "a 2-pip spell (e.g. 2WW), ~18 sources. For a 3-pip spell "
            "(e.g. WWW), ~24 sources. These scale with deck size — 99 "
            "cards means roughly 1.5x the numbers for 60-card. Fetch "
            "lands count as a source of any color they can fetch. Duals, "
            "shocks, and triomes each count as one source per color. "
            "Command Tower and Exotic Orchard are perfect fixing."
        ),
        "category": "land-base",
        "format": "commander",
    },
    {
        "title": "Fetch lands and why they matter",
        "body": (
            "Fetch lands (Polluted Delta, Windswept Heath, etc.) are the "
            "best lands in Magic because they: (1) fix colors perfectly "
            "by finding typed duals (shocks, triomes, surveil lands, "
            "battlelands), (2) shuffle the library (synergy with topdeck "
            "manipulation like Brainstorm, Sensei's Divining Top, Scroll "
            "Rack), (3) fill the graveyard for delve, delirium, and "
            "threshold, and (4) effectively thin the deck. In Commander, "
            "off-color fetches are still playable if you have typed duals "
            "to find — a Bloodstained Mire in a Sultai deck can fetch "
            "Watery Grave or Overgrown Tomb."
        ),
        "category": "land-base",
        "format": "any",
    },
    {
        "title": "Budget land base for Commander",
        "body": (
            "If you can't run fetch/shock/triome, build a budget mana base "
            "with: Command Tower, Exotic Orchard, Path of Ancestry (3-color+ "
            "decks), pain lands (Llanowar Wastes cycle — untapped when you "
            "need them, minimal life cost), filter lands (Sunken Ruins "
            "cycle — excellent fixing at no life cost), check lands "
            "(Glacial Fortress cycle — untapped if you reveal a typed "
            "land), bond lands (training center cycle — untapped in "
            "multiplayer), reveal lands (Port Town cycle), slow lands "
            "(Deathcap Glade cycle), and the Thriving lands cycle. Avoid "
            "lands that always enter tapped with no upside — Temples and "
            "gainlands will cost you turns."
        ),
        "category": "land-base",
        "format": "commander",
    },

    # ── Color Pie ────────────────────────────────────────────────────────
    {
        "title": "White in Commander",
        "body": (
            "White's strengths: premier removal (Swords to Plowshares, "
            "Path to Exile), board wipes (Austere Command, Farewell, "
            "Wrath of God), stax/tax effects (Rule of Law, Drannith "
            "Magistrate, Aven Mindcensor), enchantment synergies, and "
            "small-creature recursion. Weaknesses: card draw (though this "
            "has improved with Welcoming Vampire, Tocasia's Welcome, "
            "Trouble in Pairs, Esper Sentinel), ramp (limited to catch-up "
            "ramp like Knight of the White Orchid, Land Tax), and closing "
            "games without creatures. White pairs well with Blue for "
            "control, Black for aristocrats, or Red for aggressive "
            "go-wide strategies."
        ),
        "category": "color-pie",
        "format": "commander",
    },
    {
        "title": "Blue in Commander",
        "body": (
            "Blue's strengths: card draw (Rhystic Study, Mystic Remora, "
            "Consecrated Sphinx), counterspells (counterspell, Swan Song, "
            "Fierce Guardianship, Force of Will), extra turns, artifact "
            "synergies, and bounce/steal effects. Weaknesses: permanent "
            "removal (relies on bounce + counter, or colorless answers), "
            "creature stats, and life gain. Blue is the strongest support "
            "color in Commander — nearly every optimized deck either plays "
            "blue or struggles against it. Pairs well with Green for "
            "ramp-based simic value, White for hard control, or Black for "
            "reanimator/draw."
        ),
        "category": "color-pie",
        "format": "commander",
    },
    {
        "title": "Black in Commander",
        "body": (
            "Black's strengths: unconditional creature removal (Toxic "
            "Deluge, Deadly Rollick, Go for the Throat), tutors "
            "(Demonic Tutor, Vampiric Tutor), reanimation, aristocrat "
            "drain effects, and burst card draw that trades life for "
            "cards (Necropotence, Ad Nauseam, Bolas's Citadel, Phyrexian "
            "Arena). Weaknesses: artifact/enchantment removal (relies on "
            "colorless answers like Feed the Swarm, Chaos Warp in red, "
            "Beast Within in green), and sometimes burning through life "
            "too fast. Black is the second-strongest color in Commander "
            "after Blue because of tutor density and life-as-resource "
            "efficiency. Pairs well with Green for graveyard/survival, "
            "White for aristocrats, or Blue for control/combo."
        ),
        "category": "color-pie",
        "format": "commander",
    },
    {
        "title": "Red in Commander",
        "body": (
            "Red's strengths: artifact destruction (Vandalblast, Abrade, "
            "Shattering Spree), impulse draw (Jeska's Will, Faithless "
            "Looting, Dockside Extortionist — banned), rituals (Mana "
            "Geyser, Seething Song), direct damage, and aggressive "
            "creature strategies. Weaknesses: enchantment removal (red "
            "cannot answer enchantments — must rely on colorless options), "
            "card advantage (impulse draw is temporary), and long-game "
            "grind. Red has improved dramatically in Commander through "
            "impulse draw and treasure synergies. Pairs well with Blue "
            "for spellslinger/storm, Black for Rakdos aggression, or "
            "Green for Gruul stompy."
        ),
        "category": "color-pie",
        "format": "commander",
    },
    {
        "title": "Green in Commander",
        "body": (
            "Green's strengths: ramp (Three Visits, Nature's Lore, "
            "Kodama's Reach, Birds of Paradise, mana dorks), creature "
            "tutors (Green Sun's Zenith, Finale of Devastation, Chord of "
            "Calling, Worldly Tutor), artifact/enchantment removal (Force "
            "of Vigor, Nature's Claim, Bane of Progress, Collector Ouphe), "
            "and the most efficient threats by mana cost. Weaknesses: "
            "creature removal (fight spells are conditional, Beast Within "
            "is the only universal answer), stack interaction (green has "
            "almost no counterspells), and graveyard hate. Green is the "
            "most self-sufficient color but is predictable. Pairs well "
            "with Blue for value (Simic), Black for graveyard (Golgari), "
            r"or White for tokens/\+1/+1 counters (Selesnya)."
        ),
        "category": "color-pie",
        "format": "commander",
    },
    {
        "title": "Two-color pair identities in Commander",
        "body": (
            "Azorius (WU): control, flyers, blink, stax. Dimir (UB): "
            "mill, reanimator, theft, control. Rakdos (BR): sacrifice, "
            "aggro, group slug, reanimator. Gruul (RG): stompy, lands "
            "matter, power-matters. Selesnya (GW): tokens, +1/+1 counters, "
            "enchantress, go-wide. Orzhov (WB): aristocrats, lifegain, "
            "taxes, reanimator. Izzet (UR): spellslinger, storm, artifacts. "
            "Golgari (BG): graveyard, reanimator, aristocrats, -1/-1 "
            "counters. Boros (WR): equipment, combat, go-wide aggro. "
            "Simic (UG): ramp, +1/+1 counters, landfall, value engines. "
            "Simic is the strongest pair because ramp + draw covers the "
            "two most important resources. Boros has historically been "
            "the weakest but has improved through impulse draw and "
            "treasure support."
        ),
        "category": "color-pie",
        "format": "commander",
    },

    # ── Commander Selection and Play ──────────────────────────────────────
    {
        "title": "Commander mana value and recast tax",
        "body": (
            "The mana value of your commander dictates your ramp package "
            "and curve. A 2-3 MV commander can be deployed early and "
            "recast through tax easily. A 4 MV commander wants 8-10 "
            "ramp pieces and benefits from casting on turn 3. A 5+ MV "
            "commander demands heavy ramp (12+ pieces), protection, and "
            "a plan for when it gets removed — recasting an 8-MV commander "
            "with tax costs 10, which is often game-losing. Consider: does "
            "the deck function without the commander? The more the deck "
            "depends on the commander being on the battlefield, the more "
            "protection (Lightning Greaves, Swiftfoot Boots, counterspells, "
            "Teferi's Protection) you need."
        ),
        "category": "commander",
        "format": "commander",
    },
    {
        "title": "Commander protection package",
        "body": (
            "If your deck is commander-centric, run 5-8 protection "
            "pieces: Swiftfoot Boots, Lightning Greaves, and Darksteel "
            "Plate are colorless options. White has Mother of Runes, "
            "Giver of Runes, Teferi's Protection, Flawless Maneuver, "
            "Clever Concealment. Blue has counterspells (Swan Song, "
            "Fierce Guardianship, An Offer You Can't Refuse). Green has "
            "Heroic Intervention, Tamiyo's Safekeeping, Asceticism. "
            "Black has Malakir Rebirth, Undying Malice, Kaya's Ghostform. "
            "Red has Deflecting Swat, Bolt Bend. Phase-out effects "
            "(Teferi's Protection, Clever Concealment) also protect "
            "against board wipes and are the most versatile option."
        ),
        "category": "commander",
        "format": "commander",
    },
    {
        "title": "Partner and Background commanders",
        "body": (
            "Partner commanders give you two cards in the command zone, "
            "effectively starting with an 8-card hand. They expand your "
            "color identity — pair a mono-color partner with another "
            "mono-color partner for a 2-color deck, or with a 2-color "
            "partner for 3-color access. The downside: each individual "
            "partner tends to be weaker than a dedicated commander, and "
            "removing one cuts off part of your color identity. "
            "Backgrounds (from Commander Legends: Battle for Baldur's "
            "Gate) are enchantments that stay in the command zone and "
            "buff your commander — functionally similar to partner but "
            "the background doesn't get cast as a creature."
        ),
        "category": "commander",
        "format": "commander",
    },

    # ── Synergy ──────────────────────────────────────────────────────────
    {
        "title": "Critical mass for tribal and theme decks",
        "body": (
            "A tribal or theme deck needs critical mass to function: aim "
            "for 25-35 cards that share the chosen creature type or "
            "mechanic. Below 25, tribal payoffs (lord effects, Kindred "
            "Discovery, Herald's Horn) don't reliably trigger. Above 35 "
            "and you start cutting interaction and ramp. The remaining "
            "~30 non-land slots go to ramp (10-12), draw (10-12), "
            "removal (8-10), and a few flex/synergy slots. Some tribes "
            "can cheat lower numbers if they have strong token generation "
            "(Zombies, Goblins) or tutors (Elves, Wizards)."
        ),
        "category": "synergy",
        "format": "commander",
    },
    {
        "title": "Enabler-to-payoff ratio",
        "body": (
            "In synergy-driven decks (aristocrats, spellslinger, "
            "enchantress, landfall), balance enablers (cards that produce "
            "the resource) with payoffs (cards that reward you for having "
            "the resource). A healthy ratio is roughly 3:2 enablers to "
            "payoffs. Too many payoffs with no enablers means dead cards; "
            "too many enablers with no payoffs means you generate "
            "resources with nothing to spend them on. For example, in an "
            "aristocrats deck: enablers = sacrifice outlets + token "
            "producers, payoffs = Blood Artist effects + Grave Pact "
            "triggers."
        ),
        "category": "synergy",
        "format": "any",
    },
    {
        "title": "Synergy versus goodstuff",
        "body": (
            "A synergy card is worse than a goodstuff card in isolation "
            "but stronger when the deck is functioning. The threshold: if "
            "a synergy card requires 2+ other specific cards to be "
            "worthwhile, it's too narrow. If it requires 1 or is live "
            "with your commander alone, it's playable. For example, "
            "Skullclamp is 'goodstuff' because it works in any creature "
            "deck. Ashnod's Altar is synergy — it needs token producers "
            "and death-trigger payoffs to justify the slot. The best "
            "Commander decks run ~60% synergy pieces and ~40% universally "
            "good cards (ramp, draw, removal, lands)."
        ),
        "category": "synergy",
        "format": "commander",
    },
    {
        "title": "Engine density and redundancy",
        "body": (
            "If your deck relies on a particular effect (sac outlet, "
            "etb trigger, mill engine), run 6-8 cards that produce that "
            "effect so you reliably see one by turn 5. For critical "
            "engines, run functional duplicates: if your deck needs a "
            "free sacrifice outlet, run Viscera Seer, Carrion Feeder, "
            "Woe Strider, and Yahenni Undying Partisan — not just one. "
            "Tutor density can reduce redundancy requirements: 4+ tutors "
            "that find your engine mean you can run 3-4 copies of the "
            "effect instead of 6-8. This is why black's tutor access is "
            "so powerful in Commander."
        ),
        "category": "synergy",
        "format": "commander",
    },

    # ── Format-Specific ───────────────────────────────────────────────────
    {
        "title": "Commander damage and life totals",
        "body": (
            "Commander damage is 21 combat damage from a single commander "
            "to a single player — it's tracked separately per commander "
            "(partner commanders track separately). Commander damage is a "
            "relevant win condition for voltron and aggressive decks but "
            "rarely matters in combo or control. Starting life is 40. "
            "Multiplayer politics strongly favors defense and incremental "
            "value over aggression — a turn-1 Serra Ascendant is a 6/6 "
            "flying lifelink for W because you start above 30 life."
        ),
        "category": "format-specific",
        "format": "commander",
    },
    {
        "title": "How Brawl differs from Commander",
        "body": (
            "Brawl uses 60-card singleton decks with 1 commander. Only "
            "Standard-legal cards are allowed, which dramatically reduces "
            "the card pool and keeps power level lower. Starting life is "
            "25 in 1v1 (30 in multiplayer Brawl). Because the card pool "
            "is so shallow, Brawl decks are often commander-centric — you "
            "don't have the luxury of backup engines. Removal and "
            "interaction are at a higher premium because every resolved "
            "threat matters more in a smaller format. Mana bases are "
            "weaker (no fetches, limited duals), making 2-color decks "
            "more consistent than 3+."
        ),
        "category": "format-specific",
        "format": "brawl",
    },
    {
        "title": "Oathbreaker signature spell heuristics",
        "body": (
            "Your Oathbreaker's signature spell is an instant or sorcery "
            "that stays in the command zone. Choose a spell that: (1) is "
            "cheap (1-3 MV ideally — signature spell tax adds up fast), "
            "(2) you want to cast every game (not situational), and (3) "
            "synergizes with your Oathbreaker's gameplan. Classic choices "
            "include Thoughtseize (disruption), Lightning Bolt (reach), "
            "Commune with the Gods (graveyard setup), or Fabricate (tutor). "
            "Avoid expensive signature spells — paying 6+ mana for a "
            "signature spell with tax is crippling. The signature spell "
            "is a known quantity, so your opponents will play around it — "
            "reactive spells (counterspells, removal) lose effectiveness "
            "when telegraphed."
        ),
        "category": "format-specific",
        "format": "oathbreaker",
    },
    {
        "title": "Sideboard construction for 60-card formats",
        "body": (
            "A 15-card sideboard should answer the expected meta without "
            "diluting your main plan. Typical structure: 4-5 cards for "
            "aggro matchups (extra removal, sweepers), 4-5 for control "
            "(duress/veil effects, resilient threats, planeswalkers), "
            "2-3 graveyard hate pieces, 2-3 artifact/enchantment answers, "
            "and 1-2 flex slots for your worst matchup. Avoid sideboard "
            "cards that are too narrow — a card you bring in for exactly "
            "one deck is a wasted slot. The best sideboard cards hit "
            "multiple matchups (e.g. Unlicensed Hearse hits graveyard AND "
            "is a growing threat vs control)."
        ),
        "category": "format-specific",
        "format": "any",
    },
    {
        "title": "Multiplayer versus 1v1 dynamics",
        "body": (
            "Cards that trade 1-for-1 (counterspell, Doom Blade) are "
            "weaker in multiplayer because you and the target both lose a "
            "card while two other opponents lose nothing — you're down "
            "relative to the table. Cards that affect all opponents "
            "(Rhystic Study, Smothering Tithe, board wipes) or trade up "
            "in value (Beast Within answering a Blightsteel Colossus) "
            "gain equity in multiplayer. This is why Commander staples "
            "disproportionately generate incremental advantage rather "
            "than tempo — the format rewards engines, not racing."
        ),
        "category": "format-specific",
        "format": "commander",
    },

    # ── Power Level & Brackets ───────────────────────────────────────────
    {
        "title": "Commander banned list (current)",
        "body": (
            "Cards banned in Commander (EDH) as of the banned & restricted "
            "list current on 2026-07-01. The most recent Commander change "
            "was February 9, 2026 (Biorhythm was UNBANNED — it is now a Game "
            "Changer, not banned; Lutri, the Spellchaser is legal in the "
            "99/command zone and banned ONLY as a companion). Never propose "
            "or endorse a banned card for a Commander deck; if a player "
            "already runs one, flag it. The banned cards are:\n\n"
            + ", ".join(sorted(_BANNED_COMMANDER))
            + ".\n\nAlso banned as whole categories: cards that reference "
            "ante, Conspiracy-type cards, sticker/Attraction cards, and "
            "cards depicting racially or culturally offensive content. "
            "This list changes over time — if a player cites a very recent "
            "ban/unban you don't have, say so and verify rather than "
            "guessing."
        ),
        "category": "power-level",
        "format": "commander",
    },
    {
        "title": "Commander bracket system overview",
        "body": (
            "Wizards of the Coast introduced a 5-tier bracket system for "
            "Commander in late 2024 to replace the informal 1-10 power "
            "scale. Brackets focus on card quality, speed, and intent "
            "rather than just win rate. Game Changers are the primary "
            "dial; TUTOR LIMITS WERE REMOVED FROM EVERY BRACKET in the "
            "October 2025 update, so the number of tutors in a deck does "
            "NOT set its bracket. A tutor only matters if it is itself on "
            "the Game Changers list (Demonic Tutor, Vampiric Tutor, "
            "Imperial Seal, Crop Rotation), and then it counts as a Game "
            "Changer. Never tell a player their tutor count moved them up "
            "a bracket. The five brackets are: "
            "Bracket 1 (Exhibition) — ultra-casual, theme/flavor first, "
            "no game changers, no mass land denial, no extra turns, no "
            "2-card infinite combos. Games go 12+ turns. "
            "Bracket 2 (Core) — average modern precon level, still zero "
            "game changers, extra turns limited, no 2-card infinite "
            "combos. Games go 9-12 turns. Bracket 3 (Upgraded) — tuned "
            "precon or custom deck, UP TO 3 GAME CHANGERS, extra turns "
            "limited, 2-card combos only as a late-game (turn 7+) plan, "
            "no mass land denial. Games go 7-9 turns. "
            "Bracket 4 (Optimized) — high power, no limit on game "
            "changers (4+ in practice), mass land denial allowed, extra "
            "turns allowed, 2-card infinite combos allowed, heavy "
            "interaction, but stops short of the cEDH meta. Games go 5-7 "
            "turns. Bracket 5 (cEDH) — competitive meta, no restrictions, "
            "fastest mana, compact win cons, Ad Nauseam and Underworld "
            "Breach lines. Games can end turn 2-4."
        ),
        "category": "power-level",
        "format": "commander",
    },
    {
        "title": "Game Changer cards list (as scored by this tool)",
        "body": (
            "Game Changers are high-impact cards that define bracket "
            "boundaries: 0 expected in Bracket 1-2, up to 3 in Bracket 3, "
            "unlimited in Brackets 4-5. This tool's bracket estimator "
            "matches deck cards against the EXACT list below (53 cards, "
            "matching the official February 2026 Game Changers update). "
            "Tutors that are not on this list do not affect the bracket at "
            "all — tutor limits were removed in October 2025. Only these "
            "names "
            "count as Game Changers in the computed bracket, so when "
            "comparing a deck to 'the Game Changers list' use this set:\n\n"
            + ", ".join(sorted(_GAME_CHANGERS))
            + ".\n\nNote WotC's official list changes over time and may "
            "differ from this snapshot — if a player cites a card as a Game "
            "Changer that isn't listed here, the tool's bracket score will "
            "not have counted it. The fast-mana cards the estimator tracks "
            "(separate from Game Changers, used for the cEDH/fast-combo "
            "profile) are: "
            + ", ".join(sorted(_FAST_MANA))
            + "."
        ),
        "category": "power-level",
        "format": "commander",
    },
    {
        "title": "Exact bracket scoring thresholds this tool uses",
        "body": (
            "deck_get_stats computes the bracket (1-5) with these exact "
            "rules, evaluated top-down (first match wins). GC = count of "
            "Game Changer cards, MV = average mana value excluding lands, "
            "fast = count of tracked fast-mana cards, tutors = count of "
            "tracked tutors, MLD = any tracked mass-land-denial/stax card. "
            "BRACKET 5 if GC >= 6, OR (GC >= 4 AND MV <= 1.8 AND fast >= 4). "
            "BRACKET 4 if MLD present, OR GC >= 4, OR (GC >= 2 AND MV <= 2.2 "
            "AND fast >= 2). BRACKET 3 if GC >= 1, OR tutors >= 3, OR "
            "(MV <= 2.5 AND ramp >= 10 AND interaction >= 10). BRACKET 2 if "
            "tutors >= 1 (i.e. 1-2 tutors and nothing higher), OR "
            "(lands >= 35 AND ramp >= 8 AND MV <= 3.5). BRACKET 1 otherwise "
            "(no Game Changers, no tutors). Interaction = removal + "
            "counterspells. The per-deck bracket_factors field returned by "
            "deck_get_stats lists exactly which cards were detected and "
            "which rule fired — cite it when explaining a bracket."
        ),
        "category": "power-level",
        "format": "commander",
    },
    {
        "title": "Exact power-level (1-10) scoring formula this tool uses",
        "body": (
            "deck_get_stats computes the traditional 1-10 power level by "
            "summing five dimensions (each 0-2 points) and adding 1, then "
            "clamping to 1-10: level = clamp(round(raw + 1), 1, 10). "
            "LANDS: >=36 -> +0.5, 33-35 -> +1.0, 28-32 -> +2.0, <28 -> +1.5 "
            "(low land counts score high because they imply a fast, "
            "tuned mana base). RAMP: >=14 -> +2.0, 10-13 -> +1.5, 8-9 -> "
            "+1.0, 6-7 -> +0.5, <6 -> +0. DRAW: >=15 -> +2.0, 12-14 -> "
            "+1.5, 8-11 -> +1.0, 5-7 -> +0.5, <5 -> +0. INTERACTION "
            "(removal + counterspells): >=15 -> +2.0, 13-14 -> +1.5, 10-12 "
            "-> +1.0, 5-9 -> +0.5, <5 -> +0. AVG MV: <=2.0 -> +2.0, <=2.5 "
            "-> +1.5, <=3.0 -> +1.0, <=3.5 -> +0.5, >3.5 -> +0. The "
            "per-deck power_factors field shows each dimension's actual "
            "contribution — cite it when explaining a power level. This is "
            "a fundamentals-based heuristic: it does not detect combos or "
            "card quality beyond the counts above, so treat it as a floor "
            "and adjust up for decks with tutors, Game Changers, or combo "
            "lines the bracket score catches."
        ),
        "category": "power-level",
        "format": "commander",
    },
    {
        "title": "Mass land denial and bracket restrictions",
        "body": (
            "Mass land denial (MLD) — Armageddon, Winter Orb, Stasis, "
            "Blood Moon effects that lock players out — is not allowed in "
            "Brackets 1-3. It is allowed in Bracket 4-5 but should be "
            "discussed in pregame (Rule 0). Targeted land destruction "
            "(Strip Mine, Wasteland, Ghost Quarter) and single-player land "
            "hate does not count as MLD and is legal at all brackets, "
            "though repeated Strip Mine locks may be considered MLD by "
            "playgroups. Land-based stax (Winter Orb, Static Orb) that "
            "symmetrically denies mana production is firmly bracket 4+."
        ),
        "category": "power-level",
        "format": "commander",
    },
    {
        "title": "Traditional 1-10 power level scale",
        "body": (
            "Before brackets, the community used a 1-10 scale. Levels 1-2: "
            "joke/meme decks, no coherent plan. Levels 3-4: precon-level, "
            "casual, slow, few tutors. Levels 5-6: focused casual, clear "
            "gameplan, some tutors, budget-optimised. Levels 7-8: "
            "high-power, efficient mana, strong interaction, fast combos "
            "possible but not the primary plan. Levels 9-10: cEDH — "
            "fully optimised, fastest mana, compact win conditions, "
            "meta-tuned. The scale suffered from severe compression at 7 "
            "('every deck is a 7') because nobody wanted to admit their "
            "deck was an 8+ or a 5-. The bracket system addresses this "
            "by categorising intent alongside card quality."
        ),
        "category": "power-level",
        "format": "commander",
    },
    {
        "title": "Bracket to power level mapping",
        "body": (
            "Rough mapping between the two systems: Bracket 1 ≈ PL 1-3 "
            "(pure jank/precon), Bracket 2 ≈ PL 3-5 (stock precon, "
            "lightly upgraded), Bracket 3 ≈ PL 5-7 (tuned casual, the "
            "old '7'), Bracket 4 ≈ PL 7-9 (optimised but not cEDH meta), "
            "Bracket 5 ≈ PL 9-10 (cEDH, meta-tuned). These overlap "
            "because brackets consider intent and card choices while PL "
            "only considered win rate. A Bracket 3 deck with efficient "
            "interaction and a focused plan can easily be stronger than "
            "a poorly-built Bracket 4 deck. When assessing a deck, "
            "mention both the likely bracket and the traditional PL for "
            "players more familiar with the older system."
        ),
        "category": "power-level",
        "format": "commander",
    },
    {
        "title": "How to assess a deck's bracket",
        "body": (
            "To determine a deck's bracket, check in order: (1) Does it "
            "run mass land denial? If yes, Bracket 4+. (2) Count game "
            "changer cards. 0 = Bracket 1-2, 1-3 = Bracket 3, 4+ = "
            "Bracket 4+. (3) Can it win before turn 7? Consistently "
            "winning turn 2-4 = Bracket 5, turn 4-6 = Bracket 4. "
            "(4) Does it run 2-card infinite combos? Early-game combos "
            "(Thoracle/Consultation, Breach/LED) = Bracket 4-5. "
            "Late-game combos (turn 7+ setup) = Bracket 3. No combos = "
            "Bracket 1-2. (5) Intent: is the deck built to win at all "
            "costs (Bracket 5) or to express a theme first (Bracket 1)? "
            "The player's stated intent matters as much as the card "
            "choices. When in doubt between two brackets, round down "
            "and note the ambiguity."
        ),
        "category": "power-level",
        "format": "commander",
    },
    {
        "title": "Rule 0 conversation guide",
        "body": (
            "A good pregame (Rule 0) conversation covers: (1) Bracket or "
            "power level — what are we all playing? (2) Expected game "
            "length — 5 turns or 15? (3) Combo tolerance — are 2-card "
            "infinites okay? Turn 3 or turn 10? (4) Stax and MLD "
            "comfort — is Winter Orb acceptable? Armageddon? (5) Proxy "
            "policy — are proxies welcome? (6) Take-backs and "
            "missed-trigger policy — casual or strict? A 2-minute "
            "conversation prevents 90% of bad play experiences. "
            "As a deckbuilding assistant, help players place their "
            "deck honestly — most accidental pubstomping comes from "
            "players who think their deck is a 7 when it's a high 8."
        ),
        "category": "power-level",
        "format": "commander",
    },
]


def seed_knowledge_base() -> None:
    """Insert seed entries, re-seeding whenever the content changed.

    Compares the stored (title, body) pairs against ENTRIES rather than just
    the row count — some entry bodies are generated from the bracket-scoring
    card sets (Game Changers, fast mana), so they can change without the
    count changing, and a stale copy would misrepresent what the tool scores.
    """

    from app.db.session import get_engine
    from app.knowledge.models import SOURCE_SEED, SOURCE_USER

    desired = sorted((e["title"], e["body"]) for e in ENTRIES)

    with Session(get_engine()) as session:
        # Rows from before the source column existed carry NULL; they are all
        # seed rows, since nothing else wrote to the table then.
        session.exec(text(
            f"UPDATE knowledge_entries SET source = '{SOURCE_SEED}' WHERE source IS NULL"
        ))
        session.commit()

        seeded = session.exec(
            select(KnowledgeEntry).where(KnowledgeEntry.source != SOURCE_USER)
        ).all()
        stored = sorted((e.title, e.body) for e in seeded)
        if stored == desired:
            return  # Already up to date

        # Only the seeded rows are replaced. The player's own entries are
        # theirs, whatever the seed does.
        for entry in seeded:
            session.delete(entry)
        session.commit()

        for entry in ENTRIES:
            session.add(KnowledgeEntry(**entry, source=SOURCE_SEED))
        session.commit()
