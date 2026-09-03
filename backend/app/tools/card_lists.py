"""Hand-maintained card lists the bracket rules and the banned-card check
turn on: Game Changers, the Commander banned list, tutors, mass land
destruction, fast mana.

The Game Changers list is a fallback: Scryfall flags every Game Changer and
the local card index stores the flag, so ``game_changer_names`` reads the
live list from there once an import has landed.
"""

from __future__ import annotations

# ── Card-name sets for bracket estimation ──────────────────────────────
# Official Commander Game Changers list as of the February 9, 2026 brackets
# update (the June 29, 2026 B&R made no Commander changes). 53 cards, matching
# Scryfall's `is:gamechanger` query — the canonical machine-readable source.
# When WotC next revises the list, re-run `is:gamechanger` and reconcile.
# https://magic.wizards.com/en/news/announcements/commander-brackets-beta-update-february-9-2026
_GAME_CHANGERS: set[str] = {
    # White
    "Drannith Magistrate", "Enlightened Tutor", "Farewell", "Humility",
    "Serra's Sanctum", "Smothering Tithe", "Teferi's Protection",
    # Blue
    "Consecrated Sphinx", "Cyclonic Rift", "Fierce Guardianship", "Force of Will",
    "Gifts Ungiven", "Intuition", "Mystical Tutor", "Narset, Parter of Veils",
    "Rhystic Study", "Thassa's Oracle",
    # Black
    "Ad Nauseam", "Bolas's Citadel", "Braids, Cabal Minion", "Demonic Tutor",
    "Imperial Seal", "Necropotence", "Opposition Agent", "Orcish Bowmasters",
    "Tergrid, God of Fright", "Vampiric Tutor",
    # Red
    "Gamble", "Jeska's Will", "Underworld Breach",
    # Green
    "Biorhythm", "Crop Rotation", "Gaea's Cradle", "Natural Order",
    "Seedborn Muse", "Survival of the Fittest", "Worldly Tutor",
    # Multicolor
    "Aura Shards", "Coalition Victory", "Grand Arbiter Augustin IV",
    "Notion Thief",
    # Colorless / Artifacts
    "Chrome Mox", "Grim Monolith", "Lion's Eye Diamond", "Mana Vault",
    "Mox Diamond", "Panoptic Mirror", "The One Ring",
    # Lands
    "Ancient Tomb", "Field of the Dead", "Glacial Chasm", "Mishra's Workshop",
    "The Tabernacle at Pendrell Vale",
}

# The list above is the fallback. Scryfall marks every Game Changer with a
# per-card flag that the local index stores, so the live list is read from
# there and the hand-typed copy only serves a cold install whose index has not
# landed yet. A hit is held for an hour; a miss (empty index) for a minute, so
# the switch to real data happens shortly after the first import finishes.
_GC_HIT_TTL = 3600.0
_GC_MISS_TTL = 60.0
_gc_cache: tuple[float, float, frozenset[str]] | None = None


def game_changer_names() -> frozenset[str]:
    """Current Game Changers: from the card index when it has them, else the
    hand-maintained fallback."""
    global _gc_cache
    import time

    now = time.monotonic()
    if _gc_cache is not None:
        stamped, ttl, names = _gc_cache
        if now - stamped < ttl:
            return names

    names: frozenset[str] = frozenset()
    try:
        from app.cards import store as card_store

        names = frozenset(card_store.game_changer_names())
    except Exception:  # noqa: BLE001 - the index is optional, the fallback is not
        names = frozenset()

    if names:
        _gc_cache = (now, _GC_HIT_TTL, names)
    else:
        names = frozenset(_GAME_CHANGERS)
        _gc_cache = (now, _GC_MISS_TTL, names)
    return names


# Cards banned in Commander (EDH) as of the official banned & restricted list
# current on 2026-07-01 (the June 29, 2026 B&R made no Commander changes; the
# most recent Commander change was February 9, 2026, which unbanned Biorhythm
# and left Lutri banned only as a companion). Excludes the broad category bans
# (ante cards, Conspiracies, stickers/Attractions, offensive cards).
# https://magic.wizards.com/en/banned-restricted-list
_BANNED_COMMANDER: set[str] = {
    "Ancestral Recall", "Balance", "Black Lotus", "Chaos Orb", "Channel",
    "Dockside Extortionist", "Emrakul, the Aeons Torn",
    "Erayo, Soratami Ascendant", "Falling Star", "Fastbond", "Flash",
    "Golos, Tireless Pilgrim", "Griselbrand", "Hullbreacher",
    "Iona, Shield of Emeria", "Jeweled Lotus", "Karakas",
    "Leovold, Emissary of Trest", "Library of Alexandria", "Limited Resources",
    "Mana Crypt", "Mox Emerald", "Mox Jet", "Mox Pearl", "Mox Ruby",
    "Mox Sapphire", "Nadu, Winged Wisdom", "Paradox Engine",
    "Primeval Titan", "Prophet of Kruphix", "Recurring Nightmare",
    "Rofellos, Llanowar Emissary", "Shahrazad", "Sundering Titan",
    "Sylvan Primordial", "Time Vault", "Time Walk", "Tinker",
    "Tolarian Academy", "Trade Secrets", "Upheaval", "Yawgmoth's Bargain",
}

_TUTORS: set[str] = {
    "Demonic Tutor", "Vampiric Tutor", "Imperial Seal", "Grim Tutor",
    "Diabolic Intent", "Diabolic Tutor", "Enlightened Tutor", "Mystical Tutor",
    "Worldly Tutor", "Eladamri's Call", "Green Sun's Zenith", "Finale of Devastation",
    "Chord of Calling", "Natural Order", "Tooth and Nail", "Birthing Pod",
    "Eldritch Evolution", "Neoform", "Sylvan Tutor", "Personal Tutor",
    "Merchant Scroll", "Muddle the Mixture", "Fabricate", "Whir of Invention",
    "Buried Alive", "Entomb", "Unmarked Grave", "Gamble", "Goblin Engineer",
    "Recruiter of the Guard", "Ranger-Captain of Eos", "Stoneforge Mystic",
    "Expedition Map", "Knight of the Reliquary", "Crop Rotation", "Scapeshift",
    "Wargate", "Sisay, Weatherlight Captain",
}

_MLD_CARDS: set[str] = {
    "Armageddon", "Ravages of War", "Winter Orb", "Static Orb", "Stasis",
    "Blood Moon", "Magus of the Moon", "Back to Basics", "Ruination",
    "Catastrophe", "Global Ruin", "Sunder", "Rising Waters", "Hokori, Dust Drinker",
    "Tangle Wire", "Smokestack", "Desolation Angel", "Keldon Firebombers",
    "Impending Disaster",
}

_FAST_MANA: set[str] = {
    "Dark Ritual", "Cabal Ritual", "Culling the Weak", "Lotus Petal",
    "Elvish Spirit Guide", "Simian Spirit Guide", "Rite of Flame",
    "Seething Song", "Mana Geyser", "Jeska's Will",
}


