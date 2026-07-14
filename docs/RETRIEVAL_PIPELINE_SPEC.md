# MtG Deckbuilder — RAG & Retrieval Overhaul Spec

**Audience:** coding agent performing the refactor.
**Pilot model:** DeepSeek (V4). The tool must work well *on DeepSeek*; if it needs a stronger model to hide sloppy context, the context design is wrong, not the model.
**Format:** Commander (EDH). Assume ~99-card singleton decks, color-identity legality, casual/interactive pod.

> **⚠️ Annotation pass (2026-07-10):** This spec was written before an audit of
> the actual `familiar` codebase. The **direction is sound and kept intact**, but
> several concrete assumptions were wrong or already handled. Correction callouts
> are inline below as `> **[CODE CHECK]**` blocks. Read those before implementing
> the stage they attach to. Summary of what changed:
> - Model IDs are **real** (verified against DeepSeek docs) — but the migration is
>   *urgent*, not optional (see §5.1).
> - "Forced tool-calling" is **not used today** — the crusade against it is moot;
>   the real change is removing tool-mediated *control flow* (see §5.2).
> - The `roles` taxonomy must **fall through to the existing 4 stats-critical roles**
>   (ramp/draw/removal/land), not replace them (see §3.3).
> - EDHREC per-card data has a **narrower real shape** than §3.5 assumes (see §3.4).
> - The pipeline is **provider-neutral** (Anthropic path must survive) and outputs
>   into an existing **proposal/approval workflow**, not free JSON (see §6, new §11).

---

## 0. The core change (read this first)

The current tool lets the DeepSeek reasoning model **drive** — it decides when to call Scryfall/EDHREC tools and orchestrates the flow. The overhaul **inverts control**:

> **Deterministic Python owns the pipeline. The LLM is a bounded component that (a) emits a structured query spec and (b) reasons/selects over a pool the code has already retrieved, cleaned, and tagged.**

The model never "decides to go get data" as a first-class action. Code gets the data. The model proposes *what to look for* (as parsed output) and *what to keep* (as parsed output). This removes dependence on DeepSeek's least reliable behavior (forced/agentic tool-calling) and leans on its most reliable ones (thinking-mode reasoning + JSON Output).

**Why:** DeepSeek V4 Pro/Flash are always in thinking mode and *reject* `tool_choice="required"` and forced-function `tool_choice`. Auto tool-choice fires only ~60% of the time in complex flows. That's disqualifying for a reliability bar, and it's a *protocol quirk, not a context-quality problem* — do not try to fix it with better prompting; route around it.

> **[CODE CHECK] The current code never forces tool_choice.** `deepseek_provider.py`
> calls `chat.completions.create(...)` with **no `tool_choice` argument at all**
> (defaults to auto). So there is nothing to "stop forcing." The real problem this
> overhaul fixes is **tool-mediated control flow**: `engine.py:run_chat_turn` is a
> 12-iteration agentic loop (`MAX_TOOL_ITERATIONS = 12`) in which the model *elects*
> to call `scryfall_search` / `edhrec_commander_recs` and drives when data gets
> pulled. Inverting control means deterministic Python calls those clients (which
> already exist as plain functions — see §8 [CODE CHECK]) and the model only emits
> query-spec JSON (stage 1) and selection JSON (stage 4). Frame the work as
> "remove the model from the driver's seat," not "change a `tool_choice` param."

---

## 1. Guiding principles (durable — survive model changes)

1. **Signal-to-noise beats formatting.** The right 15 cards in scrappy formatting beat the wrong 40 in perfect formatting. Strip aggressively before you polish.
2. **Precompute, don't re-derive.** Any fact the code can compute (color identity, mana value, legality-in-this-deck) is handed to the model as ground truth. Never make the model infer it from oracle text — that's a silent-error factory.
3. **Tag functionally, retrieve the tags.** Oracle text is literal; deckbuilding is functional. "role: board-wipe" reasons better than re-parsing "destroy all creatures" every call.
4. **Query-generation > embedding retrieval for this domain.** Scryfall's DSL is a better filter than any vector store. Reserve semantic search for genuinely fuzzy prose lookups, if at all.
5. **Code is the forgiving reader, not the model.** Validate and repair every model output in code. Assume malformed output is normal and handle it.
6. **Consistency > cleverness in representation.** Uniform card blocks help a weak model more than any specific syntax does.
7. **Every token in context earns its place.** Same philosophy as a good card — latent detail, nothing gaudy or wasted.

---

## 2. Target architecture (pipeline stages)

```
User intent (commander, theme, constraints)
        │
        ▼
[1] QUERY-SPEC generation      ← LLM (Flash, JSON Output)
        │   model emits Scryfall query strings + intent metadata
        ▼
[2] CANDIDATE generation       ← Python, deterministic
        │   run Scryfall queries; pull EDHREC synergy/theme data
        ▼
[3] CONTEXT shaping            ← Python, deterministic
        │   strip → precompute → functional-tag → dedupe → cap
        ▼
[4] SELECTION / synergy        ← LLM (Pro, thinking + JSON Output)
        │   reason over clean tagged pool; pick + justify + suggest cuts
        ▼
[5] VALIDATION                 ← Python, deterministic
        │   legality, color identity, singleton, count; repair loop
        ▼
Deck delta → (existing Moxfield validation flow)
```

Stages 2, 3, 5 are pure code. Stages 1 and 4 are the only LLM calls. Keep it that way.

---

## 3. Data representation spec

### 3.1 Strip the Scryfall object

A raw Scryfall card is 40+ fields, ~90% noise for deckbuilding. **Drop:** image URIs, oracle/print/illustration IDs, multi-currency prices, per-format legalities (keep only what you compute in 3.2), artist, set SVGs, rulings URIs, related URIs, purchase URIs, frame/border/finish data, collector metadata.

**Keep only:** `name`, `mana_cost`, `type_line`, `oracle_text`, `power`/`toughness` (or `loyalty`), `keywords`, `rarity`.

> **[CODE CHECK] Stripping is already half-done, but not fully.** `scryfall_client.py::_normalize_card`
> already reduces the raw object to: `name`, `oracle_id`, `mana_cost`, `cmc`,
> `type_line`, `oracle_text`, `color_identity`, `image_url`, `legal_commander`,
> `scryfall_uri`. To hit this spec, drop `image_url` and `scryfall_uri` from the
> stage-4 view (keep `oracle_id` internally — it's the join key into the oracle-tag
> lookup) and *add* `keywords`, `power`/`toughness`/`loyalty`, and `rarity`, which
> `_normalize_card` currently discards. Do not remove fields from `_normalize_card`
> globally — other call paths (card preview images, deck import) rely on `image_url`.
> Build the trimmed stage-4 block as a *separate projection*, not by mutating the
> shared normalizer.

### 3.2 Precompute (code adds these as ground truth)

- `mv` (mana value) — numeric, don't make the model count pips
- `color_identity` — **explicit**, this is the Commander-legality field, not the same as cost
- `legal_in_deck` — boolean, computed against *this commander's* color identity + format legality + singleton status
- `produces_mana` / `mana_produced` — parsed from oracle where determinable (helps mana-base reasoning)

> **[CODE CHECK] `mv` and `color_identity` already exist; `legal_in_deck` does not.**
> `_normalize_card` already carries `cmc` (rename/alias to `mv` for the block) and
> `color_identity` verbatim from Scryfall — both are ground-truth, no model inference
> needed. `legal_commander` (format legality only) also exists. But the spec's
> `legal_in_deck` is *stricter*: it's `legal_commander` **AND** color-identity ⊆ the
> commander's identity **AND** not already in the deck (singleton). That per-deck
> computation is **new code** — it needs the active deck's commander color identity
> and current card list, which live in the DB (`DeckCard`, `Deck.commander`), so this
> precompute step must run inside a stage that has DB/session access, not in the
> stateless Scryfall client. `produces_mana` parsing does not exist and is optional
> for v1 — the existing oracle-tag lookup already yields a `ramp`/mana signal (see
> §3.3 [CODE CHECK]), which covers most mana-base reasoning without a bespoke parser.

### 3.3 Functional tags (the high-leverage enrichment)

Attach a `roles: []` list from a controlled taxonomy. Derive heuristically from oracle text (regex/keyword rules in code) where possible; use EDHREC theme data to fill gaps. Suggested Commander taxonomy — keep it **closed** so the model reasons over a stable vocabulary:

```
ramp, mana-rock, mana-dork, land-ramp,
card-draw, card-advantage, tutor, recursion,
removal-spot, removal-board, removal-targeted-noncreature,
counterspell, protection, evasion,
wincon, combo-piece, payoff,
stax, hatebear, taxes, pillowfort,
sac-outlet, token-maker, aristocrats-payoff,
graveyard-enabler, reanimation,
cost-reducer, extra-turns, extra-combat,
utility-land, fixing
```

A card can hold multiple roles. `roles` is what the selection step filters and reasons on.

> **[CODE CHECK] Build ON TOP of the existing 4-role system, with a fallthrough —
> do not replace it.** `knowledge/tag_lookup.py` already maps Scryfall's
> community-vetted Oracle Tags down to **four** internal roles: `ramp`, `draw`,
> `removal`, `land`. Those four are **load-bearing** — `deck_get_stats` computes
> bracket/power and the `deficiencies` breakdown from them, and `engine.py::_deck_grounding`
> keys off them. Breaking them breaks the stats system.
>
> **Decided approach (2026-07-10):** the richer ~30-role taxonomy is a *superset* that
> **falls through** to the existing 4. Each fine-grained role declares which of the 4
> coarse buckets (if any) it rolls up into, so stats keep working unchanged while
> stage 4 gets the richer vocabulary. Example mapping:
> - `mana-rock, mana-dork, land-ramp, cost-reducer` → **ramp**
> - `card-draw, card-advantage` → **draw**
> - `removal-spot, removal-board, removal-targeted-noncreature, counterspell` → **removal**
> - `utility-land, fixing` → **land** (where the card is a land)
> - `wincon, combo-piece, stax, token-maker, tutor, recursion, …` → no coarse bucket
>   (stage-4-only signal; invisible to the 4-role stats, which is fine)
>
> The raw material is already there: `tag_lookup.py::_tag_to_role` shows Scryfall's
> oracle tags are *far* richer than 4 slugs (dozens of `mana-*`, `removal-*`,
> `wheel`, `ritual`, `tutor-land-*` slugs already flow through). The extra roles are
> mostly a **re-bucketing of tags the code already downloads**, not new data.
> Prefer deriving roles from those existing oracle-tag slugs first; use EDHREC theme
> data (§3.4) only to fill genuine gaps. "More robust tagging can never hurt" — but
> ship it as an additive layer with the fallthrough, and keep the closed vocabulary.

### 3.4 EDHREC layer (synergy — what card text can't give you)

For each candidate, attach where available: `synergy_score` (or salience within the requested theme), `theme_tags: []`, and a short `plays_well_with: []` (top co-occurring cards). This is the statistical/relationship layer that pure oracle text can't produce — it's why EDHREC stays in the pipeline.

> **[CODE CHECK] The real EDHREC shape is narrower than this.** `edhrec_client.py`
> hits the unofficial `json.edhrec.com` endpoint and `_normalize_cardview` yields only:
> `name`, `synergy` (float), `inclusion`, `num_decks`, `potential_decks`. So:
> - `synergy_score` → **available** as `synergy`. Use it directly (and as the sort
>   key for the §4 pool cap).
> - `theme_tags` per card → **not available** from the card view. What *is* available
>   is the commander page's `categories` dict (`commander_recs` groups cards by
>   category/tag). You can attach the category a card appeared under as a coarse
>   theme signal, but there's no per-card theme-tag list.
> - `plays_well_with` → **not available** and **not cheaply derivable**. EDHREC's JSON
>   has no per-card co-occurrence list here. Treat it as out of scope for v1 rather
>   than inventing it — an invented `plays_well_with` violates guiding principle #2
>   (never make the model reason over facts the code can't actually ground). Drop it
>   from the card block, or leave it empty.
>
> Also note: EDHREC data is **commander-scoped and disk-cached** (`edhrec_cache_ttl_hours`,
> default 24h) and its parsing is deliberately defensive because the endpoint is
> undocumented and can change shape. Any new consumer must degrade gracefully on
> missing keys, same as the existing client.

### 3.5 The canonical card block (what stage 4 actually sees)

Delimit each record with tags so a weak model keeps them separate and can reference one precisely. Uniform every time:

```xml
<card>
name: Dockside Extortionist
mv: 2 | cost: {1}{R} | color_identity: R
type: Creature — Goblin Pirate
oracle: ETB — create X Treasures, X = artifacts+enchantments opponents control.
pt: 1/2 | keywords: none | rarity: rare
roles: [ramp, token-maker, value-etb]
edhrec: synergy 0.42 | themes: [treasure, artifacts-matter]
plays_with: [reset effects, sac outlets, ritual payoffs]
legal_in_deck: true
</card>
```

Oracle text may be lightly abbreviated for token economy **only if** meaning is preserved — err toward faithful. Never abbreviate names or numbers.

> **[CODE CHECK] Adjust the example block to real data.** Given the EDHREC shape
> above, the `edhrec:` line realistically carries `synergy <float>` and the EDHREC
> *category* the card appeared under (not a free `themes` list), and the `plays_with:`
> line is dropped for v1. `roles:` comes from the additive tag layer (§3.3) — note the
> example's `value-etb` isn't in the closed taxonomy; either add it or map it to an
> existing role. Keep everything else (uniform `<card>` delimiting, precomputed `mv`/
> `cost`/`color_identity`, `legal_in_deck`) — that part is exactly right and is the
> highest-leverage idea in this section.

---

## 4. Retrieval strategy

- **Candidate generation is deterministic filtering, not embedding search.** Scryfall DSL does the heavy lifting: `id<=rakdos o:"draw a card" t:instant f:commander`. The LLM's retrieval job is to *write good queries*, executed by code.
- Give the query-generation call a **few-shot Scryfall cheatsheet** in its prompt: color identity (`id<=`, `id:`), oracle (`o:`), type (`t:`), format legality (`f:commander`), mv (`mv<=`, `mv=`), and combinations. Concrete examples matter more than prose explanation for a weak model.

> **[CODE CHECK] The cheatsheet is a separate doc — `scryfall-query-cheatsheet.md`
> is canonical for query syntax.** It supersedes this bullet's rough sketch. Key
> decisions it locks in, and the code gaps it exposes:
> - **Idiom decided: `f:commander` and `id<=<identity>`** on every query. Note the
>   *existing* `scryfall_search` tool prompt (`tools/schemas.py`) uses `legal:commander`
>   instead — equivalent on Scryfall, but the new pipeline follows the cheatsheet's
>   `f:commander` consistently. (Leave the old tool prompt alone; it serves the legacy
>   conversational path.)
> - **Carry over the `game:paper` warning** from the existing tool prompt — it silently
>   excludes many legal cards. (The cheatsheet doesn't mention it; add it there.)
> - **`otag:` maps straight onto our roles** (`otag:ramp`/`removal`/`card-advantage`/
>   `tutor`/`counterspell`). Cheatsheet §D rightly says validate the `otag:` vocabulary
>   before trusting it — `tag_lookup.py` already downloads/caches the oracle-tags bulk
>   file, so the valid-tag set is derivable from data already on disk.
> - **Two concrete `ScryfallClient.search` gaps** the cheatsheet §D assumes but the code
>   lacks: it hardcodes `params={"q": query}` (`scryfall_client.py:98`) and passes
>   **no `order=edhrec`** (playability sort) and **no `unique=cards`** (dedupe printings).
>   Both need adding to `search()` for the retrieval layer — additive, low-risk.
> - **Legality is belt-and-suspenders in code** (cheatsheet §C.1 + §D): the harness
>   appends `id<=`/`f:commander` server-side regardless of what the model emits, and
>   does commander/owned-card exclusion *after* retrieval (§C.5) — never via `-name:`
>   in the query. This is the spec's "code owns legality" made concrete.
> - Empty-pool handling (§E, §C.6): code auto-broadens or re-asks stage 1; the model
>   never re-drives.
> Otherwise `ScryfallClient.search` already takes a raw query + limit, so stage 2 is a
> thin loop over the stage-1 `scryfall_queries` calling it (once the two params above
> are added).
- Pool hygiene in code: dedupe, drop the commander itself and cards already in the deck, cap the pool to a token-sane size (e.g. top N by EDHREC synergy for the theme) **before** it reaches stage 4. The model should never see 400 candidates.
- Embedding retrieval is **out of scope** unless a genuine fuzzy-prose need appears (e.g. "cards that feel like X"). Default: no vector store.

---

## 5. DeepSeek integration specifics

### 5.1 Model IDs & routing

- Use `deepseek-v4-flash` and `deepseek-v4-pro`. **Do not** use `deepseek-chat` / `deepseek-reasoner` — legacy aliases retiring after 2026-07-24.
- **Flash** (cheap, high-volume): stage 1 query-spec generation, bulk card tagging (3.3), any classification/extraction.
- **Pro** (harder reasoning): stage 4 selection/synergy only. Don't pay Pro rates to strip JSON.

> **[CODE CHECK] DECIDED (2026-07-12): default to Pro everywhere, not Flash.**
> This overrides the Flash-primary split above. Rationale: the Flash/Pro *cost* gap on
> DeepSeek is negligible for this app's volume, and the split's whole justification was
> cost. With cost off the table, use the stronger model unless Flash is *genuinely*
> better for the task. Consequences:
> - **Stage 1 (query-spec) → Pro.** Better queries directly improve the candidate pool
>   — the highest-leverage upgrade in the pipeline. No reason to handicap it.
> - **Stage 4 (selection) → Pro.** Unchanged; it was always Pro.
> - **Flash stays available** via a per-call model override for any future genuine
>   high-throughput extraction step. But note: our functional-role tagging (§3.3) is
>   **code-derived from Scryfall oracle tags, not an LLM call**, so the "bulk card
>   tagging on Flash" bullet likely has no LLM step to route at all.
> - **Config:** default `deepseek_model` = `deepseek-v4-pro`; add a separate
>   `deepseek_model_fast` (= `deepseek-v4-flash`) for the rare Flash case, rather than
>   making Flash the base. Provider `send()` still needs the per-call override so a
>   stage can opt into either.

> **[CODE CHECK] VERIFIED against DeepSeek docs (2026-07-10) — and it's now urgent.**
> The model IDs are **real and current**: `deepseek-v4-flash` and `deepseek-v4-pro`
> exist. The deprecation is confirmed: `deepseek-chat`/`deepseek-reasoner` **hard-error
> after 2026-07-24 15:59 UTC — no grace period, no silent fallback.** That is **~2
> weeks out** and this repo's default is `deepseek-reasoner` (`config.py:21`, plus
> `.env`/`.env.example`). **This one line is a time bomb independent of the whole
> overhaul** — the model-ID swap should land *first* (it's a one-line config +
> `.env.example` change) so the app doesn't break on 07-24, even if the rest of the
> pipeline work takes longer.
>
> There is **no `deepseek-v4-reasoning`/`-reasoner` ID** — V4 folded reasoning from a
> *model choice* into a *per-call mode toggle*. There are exactly two V4 IDs:
> `deepseek-v4-flash` and `deepseek-v4-pro`. The respec uses **both** per the
> Flash/Pro split — see below.
>
> Verified migration mapping (DeepSeek docs + WaveSpeed migration guide):
>
> | Old alias | New ID | Thinking mode |
> |---|---|---|
> | `deepseek-reasoner` (current default) | `deepseek-v4-flash` | **must opt in** via `extra_body` |
> | `deepseek-chat` | `deepseek-v4-flash` | off (non-thinking) |
> | upgraded reasoning | `deepseek-v4-pro` | **must opt in** via `extra_body` |
>
> **⚠️ Reasoning is now opt-in per call, not implied by the ID:**
> ```python
> extra_body={"thinking": {"type": "enabled"}}   # + optional reasoning_effort: "high"|"max"
> ```
> **The direct replacement for `deepseek-reasoner` is `deepseek-v4-flash` — NOT Pro.**
> WaveSpeed: *"if you were on `deepseek-reasoner`, you're already running on Flash,
> not Pro."* The single gotcha that makes this more than a rename: **swapping to any
> V4 ID without the `extra_body` thinking toggle silently disables reasoning** and
> quietly degrades quality. The current chat loop depends on `deepseek-reasoner`'s
> default-on thinking, so the urgent pre-deadline swap is **two coordinated changes**,
> not one line: (a) ID → `deepseek-v4-flash`, **and** (b) add the `extra_body` thinking
> toggle in `deepseek_provider.py::send`. Ship them together or chat quality regresses.
>
> Two more nuances:
> 1. After that swap the app is on `deepseek-v4-flash` *with thinking on* — a true
>    capability no-op vs today, just the non-deprecated name. (Only a no-op **if** the
>    thinking toggle is added; without it, it's a regression.)
> 2. **Pro is a genuinely different, larger model** (~1.6T total / 49B active vs
>    Flash's 284B/13B), not a rename. Routing stage 4 to Pro is a real capability
>    upgrade and a real cost increase — worth it per the Flash/Pro split, but budget
>    for it, and remember Pro *also* needs the `extra_body` toggle to actually reason.
>
> **Both models are used in the respec** (this answers "aren't we using both anyway?"
> — yes): stage 1 + bulk tagging on **Flash**, stage 4 selection/synergy on **Pro**
> (thinking enabled). The current provider (`deepseek_provider.py`) is single-model;
> supporting the split means either a per-call model override on `ChatProvider.send`
> or two provider instances — a small but real change to the provider abstraction
> (keep it provider-neutral, see §6). The same `send` change should carry the
> `extra_body` thinking flag so both stages can request reasoning explicitly.

### 5.2 Structured output: JSON Output, NOT forced tool-calling

- Set `response_format={"type":"json_object"}`, include the literal word "json" in the prompt, provide an **example of the exact shape**, and set `max_tokens` high enough to avoid truncation.
- Do **not** rely on `tool_choice="required"` or forced-function tool_choice — rejected by V4 thinking models (HTTP 400).
- If tools are used at all, only in auto mode with a code fallback for the ~40% no-call case. Preference: don't use tool-calling for control flow at all (see §0).

> **[CODE CHECK] `response_format` / JSON Output is not wired up yet.** `deepseek_provider.py::send`
> passes `messages` + `tools` only — no `response_format`, no `max_tokens`. Stages 1
> and 4 need a JSON-mode call path. Cleanest fit for the provider-neutral abstraction:
> add an optional structured-output flag (and optional `max_tokens`, and for stage 4
> the `deepseek-v4-pro` model override) to `ChatProvider.send`, implemented per
> provider (DeepSeek `response_format={"type":"json_object"}`; Anthropic uses a
> different mechanism — tool-based or prefill — so don't assume the DeepSeek shape
> works for both). As the spec says, still include the literal word "json" in the
> prompt and give an exact-shape example. Reminder that the current code already does
> defensive arg parsing (`json.loads` with a `_parse_error` fallback in
> `deepseek_provider.py` + the `_parse_error` branch in `dispatch.py`) — reuse that
> resilience pattern for the new JSON responses.

### 5.3 Thinking mode

- V4 is thinking-mode by default. This is a **feature** for stage 4 (synergy reasoning comes cheap) and only a problem for forced tool-calls (which we've removed). Keep it on for stage 4.
- In any multi-turn loop, **preserve the full assistant message including `reasoning_content`** and pass it back. Dropping it silently breaks the next turn. (Do not surface `reasoning_content` to the end user.)

> **[CODE CHECK] History replay already preserves the raw message — verify it covers
> `reasoning_content`.** The engine persists `raw_assistant_message` (from
> `message.model_dump(exclude_none=True)`) into `Message.provider_native` and replays
> it verbatim (`engine.py::_load_history`). If the DeepSeek SDK surfaces
> `reasoning_content` as a field on the message object, `model_dump` captures it and
> replay is already correct. **Confirm this against a live thinking-mode turn** — this
> is exactly the class of provider-shape assumption that `PROVIDER_SHAPES.md` warns
> caused a real 400 bug before. The two-call pipeline mostly *sidesteps* multi-turn
> tool loops anyway (stages 1 and 4 are single-shot JSON calls), which reduces the
> surface where dropped `reasoning_content` could bite — but any repair-retry loop
> (§5.4) is multi-turn and must carry it.

### 5.4 Validation & repair (mandatory)

- Parse every JSON response defensively: strip ```` ```json ```` fences, catch parse errors, retry with a repair prompt that echoes the malformed output and the schema.
- After parse, **hard-validate against game rules in code**, not via the model: color-identity legality, singleton, 99-card count, banned list. Any picks that fail are dropped or sent back for re-pick. The model is never the source of truth for legality.

---

## 6. The contract: model's job vs. code's job

| Concern | Owner |
|---|---|
| Deciding *what to search for* | **LLM** (stage 1, as JSON) |
| Constructing & running Scryfall queries | Code |
| Pulling EDHREC data | Code |
| Stripping / precomputing / tagging cards | Code |
| Capping & deduping the pool | Code |
| *Selecting* cards & explaining synergy | **LLM** (stage 4, as JSON) |
| Legality / color-identity / singleton checks | Code |
| Repairing malformed model output | Code |

If a responsibility isn't in the LLM rows, the model must not own it.

> **[CODE CHECK] Two owners the table omits, both Code:**
> - **Turning stage-4 picks into player-approvable proposals.** The model **cannot
>   mutate the deck** in this app — by design it emits proposals the player approves or
>   denies (`propose_deck_changes` tool → `DeckProposal` rows → `apply_proposal`/
>   `deny_proposal`; see `engine.py:141-145` and the data model in ARCHITECTURE.md).
>   Stage 4's `picks`/`suggested_cut` JSON must be translated by code into that
>   proposal batch, not written to the deck. This is a real integration point the spec
>   doesn't mention (see new §11).
> - **Staying provider-neutral.** `PROVIDERS.md` makes the Anthropic path a live goal,
>   not vestigial. Both LLM stages must go through the `ChatProvider` protocol
>   (`llm/base.py`), or at minimum not break the Anthropic implementation. The spec is
>   written DeepSeek-only; keep the seams provider-agnostic even while piloting on
>   DeepSeek.

---

## 7. Suggested JSON schemas

**Stage 1 — query spec (Flash):**
```json
{
  "reasoning": "brief: why these queries serve the intent",
  "scryfall_queries": [
    "id<=rakdos o:\"draw a card\" t:instant f:commander mv<=3",
    "id<=rakdos o:\"create a treasure\" f:commander"
  ],
  "target_roles": ["card-draw", "ramp"],
  "theme": "treasure/artifacts sacrifice"
}
```

**Stage 4 — selection (Pro):**
```json
{
  "picks": [
    {
      "name": "Dockside Extortionist",
      "role": "ramp",
      "why": "explosive treasure burst enables the ritual payoffs already in the pool",
      "suggested_cut": "Commander's Sphere",
      "cut_reason": "strictly slower ramp, no synergy payoff"
    }
  ],
  "notes": "pool was light on interaction; consider a second removal query"
}
```

Both consumed by code, validated, never trusted raw.

---

## 8. Migration path (don't rebuild from scratch)

1. **Freeze behavior with a golden test:** pick 2–3 decks you already run; capture current tool output as a baseline to diff against.
2. **Extract the retrieval into code.** Move Scryfall/EDHREC calls out of tool-call handlers into plain deterministic functions. This alone kills most fragility.
3. **Insert the shaping layer (§3).** Strip → precompute → tag → cap. Verify card blocks look like §3.5.
4. **Split the two LLM calls.** Replace the single reasoning-model-drives-everything call with stage 1 (Flash, JSON) and stage 4 (Pro, JSON).
5. **Add validation/repair (§5.4).** Only after this is the pilot bar meaningful.
6. **Swap model IDs** to `v4-flash`/`v4-pro`.
7. Diff against the golden baseline; iterate on tags and query cheatsheet, not on prompting the model harder.

> **[CODE CHECK] Reordered/annotated for this codebase:**
> - **Do step 6 FIRST, standalone, now.** The `deepseek-reasoner` alias dies 2026-07-24
>   (~2 weeks out). Swapping `config.py` default + `.env.example` to `deepseek-v4-flash`
>   is a one-liner that de-risks the deadline and is independent of everything else.
>   Ship it before the deadline regardless of pipeline progress.
> - **Step 2 is largely already done.** The Scryfall/EDHREC clients (`scryfall_client.py`,
>   `edhrec_client.py`) are *already* plain deterministic functions; they're just
>   currently reached through `dispatch.py` tool handlers. "Extraction" here mostly
>   means building a new deterministic pipeline module that calls them directly, not
>   untangling them from the loop. The existing tool path can stay for the Anthropic/
>   conversational flow.
> - **Step 3's tagging is partly built.** `tag_lookup.py` already yields the 4 coarse
>   roles; the shaping layer extends it with the additive fallthrough taxonomy (§3.3
>   [CODE CHECK]) and the `legal_in_deck` per-deck precompute (§3.2 [CODE CHECK]).
> - **Step 4 needs the provider to grow JSON-mode + a model override** (§5.2, §5.1
>   [CODE CHECK]s) before the two calls can be split cleanly.
> - **Add a step: wire stage-4 output into the proposal workflow** (new §11) — the
>   golden diff isn't meaningful until picks actually surface as proposals the way the
>   current tool does.
> - **Golden test harness:** `backend` uses pytest (`tests/`, run via
>   `.venv\Scripts\python.exe -m pytest -q`). Capture the baseline as a test fixture
>   there; `FakeProvider` in `tests/test_chat_engine.py` is the existing pattern for
>   deterministic LLM-free engine tests.

---

## 9. Eval / done criteria

- **The DeepSeek bar:** the full pipeline produces legal, sensible, on-theme picks on `v4-flash`+`v4-pro` with **zero** reliance on forced tool-calling.
- **Legality:** 100% of surfaced picks pass code validation (color identity, singleton, format). A single illegal pick reaching the user is a bug, not a model limitation.
- **Signal check:** spot-audit stage-4 input — if you see raw Scryfall noise or untagged cards, shaping is incomplete.
- **Regression:** picks on your golden decks are as good or better than the pre-overhaul baseline, judged against decks you actually pilot.

---

## 10. Anti-patterns (do not do)

- ❌ Letting the model call tools to control flow (use auto+fallback at most; prefer none).
- ❌ Forcing `tool_choice` on V4 — it 400s.
- ❌ Feeding raw Scryfall JSON into any prompt.
- ❌ Asking the model to determine color identity / legality.
- ❌ Trusting model JSON without parse-and-validate.
- ❌ Uncapped candidate pools dumped into stage 4.
- ❌ Dropping `reasoning_content` in multi-turn loops.
- ❌ "Fixing" a protocol quirk with more prompt engineering instead of routing around it in code.
- ❌ **[CODE CHECK]** Writing stage-4 picks straight to the deck. The model never
  mutates the deck in this app — picks become **proposals the player approves** (§11).
- ❌ **[CODE CHECK]** Mutating `scryfall_client.py::_normalize_card` to strip fields
  other call paths (card images, import) depend on — build a separate stage-4
  projection instead.
- ❌ **[CODE CHECK]** Adding a DeepSeek-only JSON/model-override path that bypasses or
  breaks the `ChatProvider` protocol and the Anthropic implementation.

---

## 11. [CODE CHECK — new] Stage-4 output → the proposal/approval workflow

The spec treats stage 4's job as "emit picks JSON," which is correct but incomplete for
*this* app. Familiar's core product invariant (ARCHITECTURE.md, PRODUCT.md) is that
**the model proposes; the player disposes.** The model cannot edit a deck directly.

Existing mechanism to reuse (don't reinvent):

- The current conversational flow has the model call the **`propose_deck_changes`** tool
  with a `summary` + a batch of `changes` (`action: add|remove|set_commander`,
  `card_name`, `reasoning`). That creates **`DeckProposal`** rows (status
  `pending`), emits a `deck_proposal`/`deck_updated` SSE event, and the frontend
  renders an approve/deny card. The player applies via
  `/api/decks/proposals/{id}/apply|deny`.
- In the inverted pipeline, **stage 4 does not call a tool** (that's the whole point).
  Instead, **code** takes the validated (§5) `picks` JSON and constructs the same
  `DeckProposal` batch programmatically:
  - each `pick.name` + `pick.role` → an `add` proposal with `reasoning = pick.why`
  - each `pick.suggested_cut` → a `remove` proposal with `reasoning = pick.cut_reason`
  - anchor them to the assistant message the player sees, exactly as
    `engine.py::anchor_proposals_to_message` does today.
- **Validation gates proposal creation, not deck mutation.** A pick that fails §5
  (illegal color identity, already in deck, etc.) must never become a proposal — drop
  it or send it back to stage 4 for a re-pick. This is the "single illegal pick
  reaching the user is a bug" bar from §9, enforced at the proposal boundary.

Net: the pipeline's *terminal* action is "create a pending proposal batch," and the
approve/deny/apply machinery downstream is **already built and unchanged**. Wire into
it; don't build a parallel path.
