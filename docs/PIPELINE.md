# The retrieval pipeline

How Familiar suggests cards. This documents the system **as it is built**
today. For the original design rationale (and why control was inverted away
from the model), see [RETRIEVAL_PIPELINE_SPEC.md](RETRIEVAL_PIPELINE_SPEC.md) —
that is a pre-implementation spec, not a description of the current code.

Code lives in `backend/app/pipeline/`. The entry point is
`service.build_suggestions()`, reached from the chat loop when the model calls
the `suggest_cards` tool.

## The core idea

Deterministic Python owns retrieval, filtering, legality, and ranking. The LLM
is bounded to two calls: **plan queries** (stage 1) and **pick from a curated
shortlist** (stage 4). Everything the code *can* decide (what's legal, on-color,
already in the deck, how cards rank) it decides, so correctness never depends on
the model getting card facts right.

The one thing Python genuinely can't do — judge whether a card *fits this
specific deck* (combos with the commander, completes a line, is a synergy trap)
— is exactly what stage 4 is for, and why it gets the commander + current deck
as context and runs with thinking on. See "Model & thinking policy" below.

## Stages

The model only decides what to look for (1) and which results to keep (4);
deterministic Python does the searching, filtering, legality, and everything
after. In the diagram, hexagons are LLM calls and rectangles are Python.

```mermaid
flowchart LR
    IN([intent + deck]) --> S1

    subgraph LLM["LLM decides"]
        direction TB
        S1{{"1 · spec<br/>plan queries"}}
        S4{{"4 · select<br/>pick + justify"}}
    end

    subgraph PY["Python does the work"]
        direction TB
        S2["2 · candidates<br/>search + annotate"]
        S3["3 · shape<br/>clean + legality"]
        S5["5 · validate<br/>build proposals"]
    end

    S1 --> S2 --> S3 --> S4 --> S5 --> OUT([proposal batch])

    style S1 fill:#2563eb,stroke:#1e3a8a,color:#ffffff
    style S4 fill:#2563eb,stroke:#1e3a8a,color:#ffffff
    style S2 fill:#e2e8f0,stroke:#94a3b8,color:#0f172a
    style S3 fill:#e2e8f0,stroke:#94a3b8,color:#0f172a
    style S5 fill:#e2e8f0,stroke:#94a3b8,color:#0f172a
    style IN fill:#fef9c3,stroke:#ca8a04,color:#0f172a
    style OUT fill:#fef9c3,stroke:#ca8a04,color:#0f172a
```

Read it as a relay: control bounces from the model (plan the search) to Python
(run it, filter it) back to the model (pick from the results) and back to Python
(validate into proposals). Only stages 1 and 4 are LLM calls. The output is the
exact shape `propose_deck_changes` returns.

| Stage | File | What it does |
|---|---|---|
| 1 spec | `pipeline/spec.py` | Turns intent + colour identity into a few Scryfall queries. The harness **enforces** `id<=<identity>` and `f:commander` on every query (`enforce_query`) regardless of what the model wrote, so legality can't leak. Parse is tolerant (`parse_spec`): coerces/repairs, drops blanks/dupes, caps count. |
| 2 candidates | `pipeline/candidates.py` | Runs each query via `scryfall.search_pipeline` (EDHREC-ordered, deduped printings), **broadening any query that underfills** (see below), merges keeping the best-ranked first, annotates each card with EDHREC synergy/inclusion where the commander's page has it, sorts by EDHREC rank, caps the pool. EDHREC failure degrades to an empty synergy map — never sinks retrieval. |
| 3 shape | `pipeline/shaping.py` | Strips the Scryfall object to what stage 4 needs, precomputes `legal_in_deck` (commander-legal, colour identity within the commander's, not banned, not already in the deck) and functional roles, dedupes by `oracle_id`, caps legal-cards-first, and renders the deterministic text block stage 4 reads (`render_pool`), including each card's EDHREC play rate and synergy. |
| 4 select | `pipeline/selection.py` | The LLM picks from the pool and justifies each pick **against this deck**. Given the commander (with oracle text), current cards by category, strategy notes, the plan's still-needs, and the player's own message beside the distilled intent (so "cheap, nothing green" survives the chat model's summary). Picks not in the pool, or marked ILLEGAL, are dropped in `parse_selection` (hallucination guard). |
| 5 validate | `pipeline/validate.py` | Turns the picks into pending `DeckProposal` rows by reusing `propose_deck_changes` — the same approval gate the conversational path uses. A pick that fails validation never becomes a proposal. |

`build_suggestions` (`pipeline/service.py`) wires them and returns a
`SuggestionResult` whose `proposals` field is the exact shape
`propose_deck_changes` returns — so it plugs into the existing approve/deny SSE
+ REST workflow with no new plumbing.

## Query broadening (stage 2)

The cheatsheet tells the model "if a query would return very few cards, loosen
it", but the model emits queries **blind** — it never sees a result count, so it
cannot act on that instruction. A single over-tight query therefore contributed
nothing, and because stage 2 ran each query exactly once, nothing downstream
could recover. This was the largest single cause of a thin pool.

`pipeline/broaden.py` makes the instruction actionable by the harness: run the
query, count the hits, and if it underfilled (< 8 by default) drop **one**
constraint and re-run, up to 3 rounds. Relaxation order is most-arbitrary-first:
mana value bound, keyword, power/toughness, oracle tag, rarity.

Two invariants:

- **Legality is never relaxed.** `id<=` and `f:commander` survive every round,
  so broadening cannot leak an illegal card into the pool.
- **Intent is never fully relaxed.** A query stripped of every term describing
  what a card *does* (`o:`, `t:`, `otag:`, `is:`, `produces:`, `keyword:`) is no
  longer a search for anything — verified live, `id<=br f:commander` cheerfully
  returns Sol Ring and Command Tower. Broadening stops rather than degrading a
  search into generic staples. Contributing nothing beats contributing
  confident noise.

The `otag:` step matters more than it looks. Tagger's vocabulary is a
**hierarchy, and the bulk export ships only leaf taggings** — `otag:removal`,
`otag:tutor`, and `otag:sacrifice-outlet` all resolve on Scryfall (which expands
ancestors server-side) but have zero rows in the bulk file. Slugs are therefore
not guessable, and a model inventing a plausible tag name is a routine failure
that returns exactly zero cards. Dropping the tag is often the whole fix.

`SuggestionResult.debug["broadened"]` maps each original query to the relaxed
queries actually tried, so a suggestion that quietly widened its search is
inspectable rather than silent.

## EDHREC as a candidate source (stage 2)

EDHREC used to be a garnish. Stage 2 fetched the commander's page only to
annotate cards Scryfall had already returned, so a card EDHREC recommends that
no query happened to match could not enter the pool at all — the best available
signal for "what actually goes in this deck" was structurally unable to
contribute a candidate. Worse, `shaping._STRIP_FIELDS` omitted `edhrec`, so even
the annotation was discarded before stage 4 read it.

`pipeline/edhrec_source.py` makes the page a real source: collect its cardlists,
rank them, hydrate the names through the local card index in one query, and
merge them into the pool ahead of query hits.

### The two numbers

EDHREC gives each card `synergy` and `inclusion`, and only one is what its name
suggests.

`inclusion` is **a raw deck count, not a rate** — verified 2026-08-15, it equals
`num_decks` exactly. The rate is `num_decks / potential_decks`, and the
denominator varies per card because a newer card has had fewer eligible decks
(2,893 for a recent printing vs 20,489 for an established one). Ranking on
`inclusion` therefore sorts by raw popularity *and* quietly buries new cards.
The rate is computed rather than read.

`synergy` is a true differential: inclusion in this commander's decks minus
inclusion in decks of the same colours. Measured on Korvold, the "High Synergy
Cards" list averages ~+0.42 while "Top Cards" averages ~+0.15 at comparable play
rates — same popularity, opposite meaning.

### The off-meta knob

`off_meta` (0.0–1.0, per deck, default 0.25) trades play rate against synergy
when ranking recommendations. This is what stops every deck converging on the
same hundred cards. Measured live on Korvold:

| `off_meta` | Top of pool |
|---|---|
| 0.0 | Forest, Command Tower, Swamp, Sol Ring — every Jund deck's staples |
| 1.0 | Mayhem Devil, Tireless Provisioner, Pitiless Plunderer — the treasure-sacrifice engine specific to Korvold |

EDHREC-sourced cards sort ahead of query hits, preserving the order `rank` put
them in. **Source is tracked explicitly, not inferred from the presence of an
`edhrec` key** — query hits carry one too (from the synergy map), so a
truthiness check puts both groups in the same bucket and lets generic staples
outrank the commander-specific picks. That bug is pinned by a regression test.

## Model & thinking policy

The two LLM stages are **not symmetric**, and the defaults reflect that
(`build_suggestions`):

- **Stage 1 (query planning): fast model, thinking OFF.** Mapping intent to a
  few Scryfall queries is mechanical, and `enforce_query` fixes legality
  regardless — so deep reasoning buys nothing here.
- **Stage 4 (selection): fast model, thinking ON.** This is the only place
  deck-aware *fit* judgment can happen. It receives the commander + current deck
  (`_render_deck_context`) and reasons about combos/synergy, so thinking earns
  its cost. It stays on the fast model to keep latency sane.

**Stage-4 latency is dominated by OUTPUT volume, not prompt size.** Measured over
repeated samples on an identical pool: a thinking selection call emits a median
**9,476 completion tokens** against 489 without, and at ~90 tok/s that generation
*is* the whole wait. Shrinking the prompt does nothing — halving it measured
slightly *slower*.

The only lever that moves it is `reasoning_effort` (`budget_tokens` is silently
ignored by the API). On the same pool:

| Effort | Median latency |
|---|---|
| provider default | 93.2s |
| `medium` | 73.7s |
| `low` | 40.8s |

`low` returned the same theme-aware picks, including both changelings that
trigger the commander twice, so it is the default (`SELECT_REASONING_EFFORT` in
`.env`; set empty to restore the provider default). A full suggestion runs ~62s
end to end.

Both stages default to the provider's fast model (`get_fast_model()` →
`deepseek-v4-flash`). This is a deliberate reversal of the older "Pro
everywhere" default: routing two heavy Pro+thinking calls made a
congested-API `suggest_cards` take ~80s; Flash with the split above brings it
back to ~10–25s with picks that are as good or better (Python already did the
retrieval/ranking). Override per call with `model` / `spec_thinking` /
`select_thinking` (tests pin a fake provider this way).

### Deck-aware selection

`_render_deck_context(snapshot)` builds the compact block stage 4 sees:
commander(s) with oracle text (the key combo signal), current cards grouped by
category (names only — full oracle text for 99 cards would blow up the prompt
and latency), and strategy notes. The selection system prompt tells the model to
judge *fit* against this deck, not just keyword-match the intent, and to name the
commander/card a pick works with in its reason. Without this block the model was
blind to the commander and could only match the intent in a vacuum.

## Timing, timeouts, and diagnosing a stall

`build_suggestions` logs per-stage timings (`pipeline: <stage> done in Ns`) and
emits a `WARNING` for any stage over 20s; the same timings are returned in
`SuggestionResult.debug["timings"]`. Logging is configured in `app/main.py`
(`LOG_LEVEL` in `.env`, default INFO) so these actually reach the console. When a
suggestion "hangs," the logs name the stage — almost always stage 4, dominated
by DeepSeek API latency, which is highly variable (a trivial call is ~1.5s; a
real selection call has ranged 6–50s under load).

LLM calls have a request timeout (`LLM_TIMEOUT_SECONDS`, default 90s, applied in
both providers via the factory) with `max_retries=1`, so a stalled call fails
visibly instead of hanging on the SDK's 600s default. Scryfall (10s) and EDHREC
(15s) have their own httpx timeouts.

The shared Scryfall/EDHREC singletons (`get_scryfall_client`,
`get_edhrec_client`) are used across FastAPI's threadpool, so each guards its
`httpx.Client` with a lock — concurrent requests serialise rather than racing a
non-thread-safe client.

## How the chat loop drives it

`suggest_cards` is one tool among several. The engine gates *entry* (the model
elects to call it) but everything after entry is deterministic. Because
`build_suggestions` returns the standard proposal shape, the engine streams and
anchors its batch exactly like a `propose_deck_changes` batch.

Note the engine's **post-proposal rule** (`chat/engine.py`): once any proposal
batch is emitted in a turn — from `suggest_cards` or `propose_deck_changes` —
the model is offered **only** `withdraw_pending_proposals` on subsequent
iterations, so it can trim a card or two but cannot add another batch or run
more research. The batch is revealed to the UI **once, at turn end**, carrying
only the proposals that survived trimming (`_settled_proposal_batch`), so cards
never flash in and back out. This is what stops the propose→withdraw→propose
churn that used to burn the 12-iteration budget and drop the turn.

## Testing

- `tests/test_pipeline_spec.py` / `test_pipeline_selection.py` — stage parse/repair and hallucination-guard.
- `tests/test_pipeline_candidates.py` / `test_pipeline_shaping.py` — deterministic merge/dedupe/cap and rendering.
- `tests/test_pipeline_golden.py` — a golden render fixture; keep the pool-render output stable.
- `tests/test_pipeline_service.py` — end-to-end with a two-stage fake provider, including that the selection prompt carries the commander context.

Fakes pin `model`/`thinking` so no network is hit. The golden test asserts the
exact stage-4 text block; changing `render_pool` will (intentionally) require
updating the fixture.

## Local first (September 2026)

Before stage 1 runs, `pipeline/local_retrieval.py` builds a pool from the
local card index: the intent's words are matched to the fine roles in
`roles.py` and each role's slug rules become one tag query, and the intent's
remaining content words are searched in name, rules text, and type line.
Colour identity is applied there. When that pool reaches `local_pool_min`
(25) the model's query-planning call and the Scryfall API queries are skipped
entirely; EDHREC's recommendations still lead the pool. A thin local pool
falls back to stage 1 as before. `debug.local_pool` and `debug.stage1_skipped`
say which path a suggestion took.

## Stage 4 on Jev (September 2026)

`SELECT_BACKEND=jev` swaps the thinking selection call for
[TypeSafe's Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev),
a System One model: it takes a state plus typed questions and returns
calibrated answers (scores, yes/no probabilities, choices) in one parallel pass.
Code is in `pipeline/jev.py` (ranking) and `pipeline/explain.py` (prose).

The split:

1. **Jev judges.** Per legal card, one yes/no: "should be one of the cards added
   to this deck in answer to the request", over the card's rules text plus the
   pipeline's evidence (role tags, EDHREC play rate and synergy, brain map
   layer scores), plus one for "answers the request". Three samples are
   averaged, because identical requests swap about one card in ten.
2. **The blend ranks.** A logistic model fitted to the player's own decks
   weighs Jev's two answers with the brain map layers and EDHREC numbers (see
   "Learned blend" below).
3. **Python takes the top 10** and writes each reason from facts it holds:
   the card's roles, any combo it completes, and the brain map's
   plain-language line, e.g. "Ramp; 54% of decks, synergy +0.47; works with
   the commander via synergy-swamp; you have taken this before; Jev 87%."

No language model runs in stage 4. The proposals the chat model reads carry
each card's rules text (`render_proposals`), and the chat model writes the
prose reply after every batch anyway, so a second model writing prose was
duplicate work. A whole suggestion takes 1.2-1.6s when the local index fills
the pool; the remaining slow path is stage 1 query planning, which ran for
"damage to each opponent" and took the total to 14.5s.

Two optional steps, both off by default:

- `JEV_EXPLAIN=true`: DeepSeek (thinking off) rewrites the reasons and adds a
  summary and cuts, without changing the picks. About 6.5s.
- `JEV_CUTS=true`: Jev proposes exactly as many cuts as the batch would push
  the deck past 100, ranking non-land, non-commander deck cards by "cutting
  this costs the deck little", with the batch's adds in the state.

Any Jev failure (no key, a 5xx or 429 that survives three retries, a missing
answer) falls back to the LLM selector; an explainer failure keeps the fact
reasons. `debug.select_backend` records which selector answered. Illegal cards
are never sent to Jev, so they cannot be picked.

Settings (`.env.example`): `JEV_MODE` (default `verdict`), `JEV_SAMPLES` (3),
`JEV_EXPLAIN` and `JEV_CUTS` (false), `JEV_MAX_SIMILAR` and
`JEV_MIN_PROBABILITY` (both 0, off), and `TYPESAFE_MODEL` pinned to
`jev-1.13.0`. `jev-latest` and `jev-preview` both resolved to 1.13.0 on
2026-09-25; the pin keeps a measured baseline from moving silently.

### Cuts

`jev_eval.py --cuts` puts each deck's approved removals back into the deck and
asks every method for the weakest cards. 37 removed cards across three decks
(Korlash, Krark, Azula); chance puts 18% of them in a top k of their size.

| Method | Removed cards in top k | Mean position (0 = cut first) |
|---|---|---|
| Cut what this commander's decks play least (EDHREC) | 14% | 0.42 |
| DeepSeek, thinking off | 11% | |
| DeepSeek, thinking on | 11% | |
| Jev | 27% | 0.34 |
| Jev with EDHREC evidence | 24-27% | 0.33 |

Jev is the best of these and still weak: a removal happens in the context of a
specific swap, which a static "weakest card" ranking cannot see. In a replay on
a full deck, ten cuts a batch (some of them key pieces such as Necropotence)
were withdrawn by the chat model, which is why cuts are off by default.

### How it was measured

`tools/jev_eval.py` scores selectors against ground truth from the player's
own decks. For each deck, its two best-filled roles (ramp, removal, draw) and
its theme cards (those filling no generic role) each become a case: up to six
of those cards are removed, along with this deck's proposal history for them so
the brain map's personal layer cannot leak the answer, and the pipeline is
asked for "more <role>" or for cards that fit the commander and the deck's
plan. 33 cases across 11 decks.

- **End to end**: of every card held out, the share returned in the batch.
  This is the number to trust.
- **Recall@10**: the same, only over held-out cards that reached the pool.
- **Stable**: batch overlap (of 10) after shuffling the pool.

The run is compared against `tools/jev_eval_baseline.json`; `--save` moves it.

### Results, 2026-09-25

Default depth (80 cards per role from the local index, pool cap 60):

| Selector | Role end to end | Role recall@10 | Theme end to end | Theme recall@10 | Stable | Seconds |
|---|---|---|---|---|---|---|
| Brain map order | 26% | 50% | 27% | 55% | | |
| EDHREC order | 31% | 55% | 37% | 69% | | |
| LLM (`low` effort) | 35% | 63% | 38% | 76% | 6.7 | 9.1 |
| **Jev verdict ×3** | **42%** | **73%** | **35%** | **70%** | **9.5** | **0.4** |

Across three runs on this code Jev verdict ×3 held 72-73% role recall@10 and 42% role end to
end; the LLM ranged 63-72%. On theme requests Jev, the LLM, and plain EDHREC
order are within noise of each other.

Earlier rounds, same harness, role cases:

| Mode | Recall@10 | Stable |
|---|---|---|
| blind (0-4 fit, rules text only) | 60% | 9.4 |
| informed (0-4 verdict, with evidence) | 69% | 9.3 |
| verdict (one yes/no, with evidence) | 73% | 9.3 |
| verdict ×3 | 73% | 9.5 |
| choice ×3 (one question across the pool) | 72% | 7.2 |
| ensemble of verdict and choice | 71% | 8.2 |

Evidence helps when it is weighed into one calibrated answer; judging
candidates head to head stayed position-sensitive even averaged over three
orderings.

### What did not help

- **Batch shaping.** Capping interchangeable cards (same type and roles) at 2
  or 3 per batch changed nothing measurable. A 0.5 probability floor halved
  the batch and cut role recall to 54%. Both remain settings, off.
- **A deeper pool.** 200 cards per role and a 150-card cap raised the share of
  held-out cards reaching the pool (58% to 70%) but diluted the top 10: role
  end to end fell from 42% to 36%.
- **Filtering the role query by colour identity before its limit.**
  `cards_matching_slug_rules` takes the top 80 cards across every colour and
  `local_retrieval` filters identity afterwards, so a mono-black deck gets only
  the black share of the 80. Filtering in SQL looks like the obvious fix and
  measured worse: role end to end 38% against 42% (the unfixed code scored 42%
  in two runs). An all-on-colour 80 crowds the capped pool with generic
  staples that the off-colour share used to leave room for. Not applied.

### Where the ceiling was, and the two fixes

About 40% of held-out role cards never reached the pool. Two causes:

- **The EDHREC source ignored the request.** It added the commander's top 40
  cards by play rate and specificity whatever was asked, so for "more ramp"
  the commander's own ramp further down the page never became a candidate, and
  local retrieval's role query ranks by global popularity (a mono-black deck's
  Greed sits 91st of 637 black draw cards, past its 80). Of 84 held-out cards
  that never reached the pool, 54 were on the commander's EDHREC page. Now,
  when the request names roles, every card on the whole page that fills one
  goes in first (`candidates._edhrec_recommendations`, `roles_wanted`).
- **The cap is chosen by the weakest signal.** Shaping keeps the brain map's
  top 60, and the brain map alone recovers fewer of the player's cards than
  plain EDHREC order (27% vs 32% end to end). Retrieving deeper therefore did
  not help: 200 per role with the cap at 60, 100, or 300 per role all scored at
  or below the default.

### Learned blend

`tools/fit_blend.py` fits a logistic model on `jev_eval.py` feature rows
(every legal candidate, its signals, and whether the player ran it) and
evaluates it leave-one-deck-out, so a deck is always scored by weights that
never saw it. `JEV_MODE=blend` ranks with the saved weights
(`app/pipeline/jev_blend.json`); `blend_features` in `jev.py` is the single
definition the eval, the fitter, and ranking all use. The weights are this
player's: refit as decks accumulate, with `--save`, and say why in the commit.

Everything together, 100 cases (79 role, 21 theme) on 11 decks, 2026-09-26:

| End to end | Role | Theme |
|---|---|---|
| Brain map order | 33% | 32% |
| EDHREC order | 39% | 40% |
| Jev verdict ×3 | 52% | 40% |
| **Blend** | **56%** | **44%** |
| Before this round (old EDHREC source, verdict ×3) | 44% | 36% |

Leave-one-deck-out the blend scored 59% role and 40% theme; the theme cases
are 21 and move about four points between runs, so theme is a tie. The fitted
weights lean on Jev's verdict and its "answers the request" answer, then
EDHREC play rate and brain map consensus; brain map mechanical fit gets a
negative weight once the others are in.

### What each signal is worth

Per-case AUC over 100 cases (the chance that one of the player's own cards
outscores another candidate; 0.5 is a coin flip), 2026-09-26:

| Signal | Role | Theme |
|---|---|---|
| Jev "should be added" | 0.85 | 0.85 |
| Jev "answers the request" | 0.72 | 0.83 |
| EDHREC play rate (on the page at all: 52% role, 28% theme) | 0.81 | 0.84 |
| Brain map total | 0.68 | 0.77 |
| Brain map consensus | 0.66 | 0.83 |
| Brain map mechanical | 0.50 | 0.55 |
| Brain map personal | 0.55 | 0.44 |

The mechanical layer is a coin flip on these decks. It scores a card by its
single strongest tag relationship to the commander's tags, and the result is
bimodal: 23% of candidates score exactly 1.0 and 40% exactly 0, so on a full
deck it cannot rank. A replacement scoring each candidate's IDF-weighted tag
overlap with the current deck reached 0.58 role and 0.66 theme, too weak to
build. The personal layer reads cross-deck history only here, because the eval
deletes this deck's proposal history to keep the answer from leaking; this
deck's own denials are its strongest input and this eval cannot credit them.

The blend already gives mechanical a negative weight and personal little, so
the brain map's weak layers do not reach the ranking. They still order the
pool before the cap, which cuts 7% of held-out role cards at 60; a cap of 80
measured no better.

### Theme requests: the whole EDHREC page did not help

A request that names no role draws the commander's top 40 EDHREC cards, and 46
of the 72 held-out theme cards that never reached the pool were on the page
past that cut. Drawing the whole page (`theme_whole_page`, off) raised the share
reaching the pool from 53% to 81% but the batch barely moved:

| Theme, 30-33 cases | Reach | Blend end to end |
|---|---|---|
| Top 40 (current) | 53% | 43% |
| Whole page, cap 60 | 57% | 40% |
| Whole page, cap 120 | 70% | 44% |
| Whole page, cap 250 | 81% | 45% |

Refitting the blend on the whole-page pools scored 43% leave-one-deck-out. With
~180 candidates the rankers cannot pick the player's cards out, and EDHREC
synergy alone gets 42%, so theme requests sit near 43-45% whatever the pool.
The whole page costs about three times the Jev tokens for a gain inside the
noise; it stays off.

### Archetype-conditioned EDHREC (built, measured, off)

EDHREC publishes each commander's themes with their own pages, computed over
only that theme's decks. `pipeline/theme_fit.py` matches the deck to them by
its distinctive cards (mean lift of the deck's cards on each theme page over
the commander page, softmax at temperature 0.02) and reads each candidate's
play rate through the match. The matches are sensible (Massacre Girl Wither to
-1/-1 Counters, Vendrell Rooms to Enchantress and Rooms). Matching on raw play
rates instead spread every deck about evenly across every theme, because the
staples all themes share dominate them.

It adds nothing measurable, leave-one-deck-out over 102 cases:

| | Role | Theme |
|---|---|---|
| Commander-wide EDHREC rate alone | 40% | 37% |
| Theme-matched rate alone | 41% | 35% |
| Blend without theme signals | 59% | 39% |
| Blend with theme signals | 60% | 39% |

The player's cards are mostly the popular ones within their archetype, which
are popular commander-wide too. `prepare_pool(theme_signal=...)` is off.

`jev_eval.py --named-themes` asks each deck's theme cases for its matched
theme by name ("more -1/-1 Counters cards"). Jev and the blend fell to 28% and
32% while EDHREC order held at 39%: given a named theme Jev chases cards that
fit the name, and this eval's theme ground truth (the deck's cards with no
generic role) is not "Combo cards" or "Chaos cards". The theme cases cannot
judge named requests until their ground truth is defined by the theme too.

### Where accuracy stands

Tag-derived signals are weak (mechanical 0.50-0.55 AUC, deck-tag similarity
0.58-0.66) and archetype conditioning adds nothing over commander-wide EDHREC.
The blend of Jev with EDHREC and brain map consensus holds at about 60% of
held-out role cards end to end. The one untested source of better data is the
player's own decisions on real suggestions, which this offline eval cannot
measure.

### Is the eval grading the engine against itself?

Six of the eleven decks were built almost entirely from approved suggestions
(Korlash 64 of 65 non-land cards, Azula 64 of 65, Hei Bai 70 of 71); four were
built outside the app with none (Torbran, Captain N'ghathrod, Rin and Seri,
Marina Vendrell). On an engine-built deck the held-out cards are the engine's
own past picks, and the player approves almost anything that is not a blunder,
so that ground truth partly measures agreement with the engine.

Split out, role requests end to end over the 102-case run:

| | Engine-built (45 cases) | Built outside the app (28 cases) |
|---|---|---|
| Held-out cards reaching the pool | 87% | 73% |
| EDHREC order | 41% | 42% |
| Jev alone | 51% | 55% |
| Blend | 65% | 61% |

Fitted on the engine-built decks only and tested on the four others, the blend
scores 60% against Jev's 55%. The gains hold on ground truth the engine never
touched. `jev_eval.py` now prints this split on every run (a deck is
engine-built when more than half its non-land cards were approved
suggestions); trust the "built outside" row.

### Deck gameplans and deck-aware search

For a request that names no role ("what fits my deck"), search was blind to the
deck: stage 1 saw only the request's words and the colour identity, local
retrieval only the request's words, and EDHREC contributed the commander's top
40 regardless. Jev saw the commander and deck when ranking, but can only rank
what search found. And no deck had a gameplan: `plan_notes` and `themes` were
empty on all eleven.

- `pipeline/gameplan.py` drafts a plan (how the deck wins, its engine, early /
  mid / late) and themes written as rules-text phrases ("-1/-1 counter",
  "sacrifice another creature"), from the commander's text, the cards, and the
  deck notes. `tools/draft_gameplans.py` stores one per deck through
  `deck_set_plan`; all eleven now have one, editable in the app.
- Stage 1 receives a deck brief (commander rules text, plan, themes) and is told
  to plan queries for that deck, not generic staples.
- Local retrieval, for a request naming no role, also searches each theme
  phrase in rules text and pulls cards carrying the commander's own mechanic
  tags. `prepare_pool(deck_aware_search=False)` restores the old search.
- The plan already reached Jev and the chat through `render_plan`.

`jev_eval.py --gameplans` drafts each case's plan from the deck WITHOUT its
held-out cards, so the plan cannot point search at the answer. The eval also
reports novelty: the share of each batch not on the commander's EDHREC page.

Theme cases, 29-32 each, with per-case plans:

| | Old search | Deck-aware, cap 60 | cap 100 | cap 150 |
|---|---|---|---|---|
| Held-out cards never found | 47% | 39% | 38% | 38% |
| Found, then cut by the cap | 1% | 11% | 8% | 5% |
| Blend end to end | 43% | 41% | 41% | 42% |
| Batch off the EDHREC page | 3% | 7% | 6% | 6% |

Search finds more of the player's cards and the batch reaches further past
EDHREC's page, but end to end holds at 41-43% at every cap: the extra
candidates are no easier to rank. On role requests the plans cost nothing
(blend 55%, as before) and Jev alone rose from 44% to 48% with the plan to read.

### Behaviour eval

`tools/behaviour_eval.py replay` over 13 stored turns. Its records were fixed
to score each turn on its own tool calls: they had been collecting the whole
replay conversation, so one refusal in turn 1 counted again in turns 2 and 3.

| Run | Refusals / turn | Role batches via pipeline | Unbracketed-name turns | Ungrounded mentions / turn |
|---|---|---|---|---|
| LLM, run 1 (saved baseline) | 0 | 100% | 6 | 6.1 |
| LLM, run 2 | 0.15 | 0% | 3 | 8.2 |
| Jev + explainer | 0 | 100% | 2 | 7.0 |
| Jev + cuts, no explainer | 0 | none asked | 5 | 7.8 |
| Jev, no explainer, no cuts, run 1 | 0 | 100% | 7 | 9.4 |
| Jev, no explainer, no cuts, run 2 | 0.08 | 0% | 6 | 7.6 |

The two LLM runs differ from each other as much as any Jev run differs from
them. Refusals and the pipeline rate turn on whether the chat model tries to
hand-pick in its own send, before stage 4 runs. At 13 turns this eval cannot
separate the selectors; it shows no harm from Jev, and one real one from cuts
(see above). The first LLM run is saved as `tools/behaviour_baseline.json`.
