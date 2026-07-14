# Scryfall Query Cheatsheet — Few-Shot Block

**Purpose:** paste into the **stage-1 (query-spec) prompt** for the Flash model. Its whole job is to emit good Scryfall query strings; concrete examples matter more than prose for a weak model.

**Two audiences for this file:**
1. The **coding agent** — read §D and §E for how the harness should wrap/enforce queries.
2. The **model at runtime** — §A–§C are the content to embed in its prompt.

> Assumption baked into examples: a Rakdos (B/R) commander, shown as `id<=rakdos`. At runtime, **substitute the actual deck's color identity.** The harness should also *enforce* this (§D) so the model can't get it wrong.

---

## A. Operator reference (deckbuilding subset)

### Color identity — the one that matters most in Commander
- `id<=rakdos` — cards **legal in a Rakdos deck** (identity ⊆ B/R). **This is the legality filter.**
- `id:rakdos` — **same thing.** Identity search is now *coverage*-based (subset), so bare `id:` == `id<=`.
- `id=2` — cards whose identity is exactly 2 colors. `id:c` — colorless identity.
- Nicknames accepted: guilds (`rakdos`, `azorius`…), shards (`grixis`, `bant`…), wedges (`abzan`, `jeskai`…), colleges (`quandrix`…), 4-color (`artifice`, `chaos`, `aggression`, `altruism`, `growth`).
- **Do not confuse with `c:` (color).** `c:` is the card's own colors and the colon means *at least* (`c:rg` = is red **and** green). For deck legality you almost always want `id<=`, not `c:`.

### Function tags (Scryfall Tagger — high value)
- `otag:` / `function:` — Oracle "function" tags describing what a card *does*: e.g. `otag:ramp`, `otag:removal`, `otag:card-advantage`, `otag:tutor`, `otag:counterspell`.
- `atag:` — art tags (rarely useful here).
- ⚠️ **Exact tag names come from the Tagger project and must be verified empirically** — don't trust a hardcoded list. Have the harness enumerate which tags actually return results and cache the valid vocabulary. Treat `otag:` as a bonus filter layered on top of text/type filters, not the sole filter.

### Text, type, stats
- `o:"draw a card"` / `oracle:` — oracle text; quote phrases; `~` = the card's own name. `fo:` = full oracle incl. reminder text.
- `o:/regex/` — regex on oracle text (supports `.*?`, `(a|b)`, `[ab]`, `\d`, `\b`, `^`, `$`; escape `/` as `\/`).
- `t:` / `type:` — type line; partial words OK; combine/negate: `t:goblin -t:creature`.
- `mv<=3` / `mv=2` / `mv>=6` — mana value (`cmc`, `manavalue` are aliases).
- `pow>=4`, `tou<=2`, `pow>tou` — power/toughness (comparisons and cross-compares).
- `keyword:flying` / `kw:menace` — keyword abilities.
- `produces:c`, `produces:rg` — mana a card can produce (mana base).
- `r:rare`, `r>=rare` — rarity.

### Lands / fixing (`is:` land subtypes)
- `is:dual`, `is:fetchland`, `is:shockland`, `is:painland`, `is:fastland`, `is:checkland`, `is:triland`, `is:bounceland`, `is:creatureland` (a.k.a. manland).

### Legality, misc
- `f:commander` — legal in Commander. (`banned:commander`, `restricted:` also exist.)
- `is:permanent`, `is:spell`, `is:commander` (can be a commander), `is:vanilla`.
- `usd<5` — approximate paper price ceiling (budget builds).
- `-` negates any term; `OR` unions; `( )` groups. Terms without `OR` are AND by default.

---

## B. Few-shot examples (intent → query)

*(substitute `id<=rakdos` with the deck's identity at runtime)*

```
# Spot removal (text-based)
id<=rakdos (t:instant or t:sorcery) o:"destroy target" f:commander

# Spot removal (tag-based, preferred if the otag validates)
id<=rakdos otag:removal (t:instant or t:sorcery) f:commander

# Board wipe
id<=rakdos o:"destroy all creatures" f:commander

# Ramp under 3 mana
id<=rakdos otag:ramp mv<=3 f:commander

# Card advantage
id<=rakdos otag:card-advantage f:commander

# Tutors
id<=rakdos o:"search your library" f:commander

# Treasure synergy payoff
id<=rakdos o:"treasure" -t:land f:commander

# Sacrifice outlet (regex: activated "sacrifice a creature:" ability)
id<=rakdos o:/sacrifice a creature:/ f:commander

# Aristocrats death trigger
id<=rakdos o:"whenever a creature you control dies" f:commander

# Recursion from graveyard
id<=rakdos o:"return target creature card from your graveyard" f:commander

# Evasive beaters, mid-cost
id<=rakdos t:creature keyword:menace mv<=4 f:commander

# Cheap interaction on a budget
id<=rakdos otag:removal mv<=2 usd<5 f:commander

# Finisher
id<=rakdos t:creature mv>=6 (keyword:trample or keyword:flying) f:commander

# Mana fixing lands
id<=rakdos t:land (is:dual or is:fetchland or is:painland) f:commander

# Counterspell (example for a blue-inclusive identity)
id<=grixis o:"counter target spell" t:instant f:commander
```

---

## C. Rules the model must follow when emitting queries

1. **Every query includes `id<=<deck identity>` and `f:commander`.** No exceptions. (Harness enforces this too — §D — but emit it anyway.)
2. **Prefer several tight queries over one broad query.** One query per role/intent. A giant OR-soup returns shallow results for everything.
3. **Layer `otag:` on top of text/type filters, never alone** — until the harness confirms the tag returns results. If unsure a tag exists, fall back to `o:`/`t:`.
4. **Quote multi-word oracle phrases**; use `~` for self-references; reach for `o:/regex/` for activated-ability patterns (`sacrifice a creature:`).
5. **Do not try to exclude the commander or already-owned cards in the query** — the harness does that after retrieval.
6. **Don't over-constrain.** If a query would plausibly return < ~5 cards, loosen it (drop an `mv` bound or a keyword) rather than returning an empty pool.
7. Output queries as an array of strings in the stage-1 JSON schema — nothing else in that field.

---

## D. Harness responsibilities (coding agent)

- **Enforce identity + format:** append `id<=<deck>` and `f:commander` to every query server-side, even if the model already included them. Belt and suspenders — legality must not depend on the model.
- **Sort for playability:** set the API `order=edhrec` param so the top of each result pool is the most-played (best candidates first). Set `unique=cards` to avoid duplicate printings flooding the pool.
- **Post-filter in code:** drop the commander itself, cards already in the decklist, and anything failing hard legality (banned list, singleton). See spec §5.4.
- **Validate the otag vocabulary once:** on startup or nightly, probe the `otag:` values the model is allowed to use, cache the valid set, and strip/repair any invalid `otag:` the model emits before running the query.
- **Cap the merged pool** by EDHREC rank before it reaches stage-4 selection (spec §4) — never hand the reasoning model hundreds of cards.

---

## E. Common query mistakes (reject/repair these)

- ❌ `c:rakdos` where `id<=rakdos` was meant — `c:` is card color, not deck legality, and uses "at least" semantics. **This silently returns wrong cards.**
- ❌ `otag:<something-unverified>` as the only filter → empty pool. Layer it or drop it.
- ❌ One mega-query with five `OR` groups → shallow, unfocused results.
- ❌ Missing `f:commander` → returns Commander-illegal cards (e.g. banlist, non-legal sets).
- ❌ Excluding owned cards via `-name:` inside the query → do it in code instead.
- ❌ Over-tight `mv`/`keyword` stacks that return 0–2 cards → loosen and re-run.
