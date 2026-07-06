# Handoff: Familiar — UI Visual Refresh ("Console" direction)

## Overview

Familiar is a local-only AI deckbuilding assistant for MTG Commander/EDH. This
package is the approved **visual system** for the app — a refined, restrained
"deck-console" language applied across every surface: the three-column app
shell, chat (bubbles, proposal cards, results tables, tool-activity), the live
deck side-panel, the deck manager, the import popover, and the preferences
modal.

The approved direction is labeled **3A / "Console"** in the reference file.
It was chosen over three earlier explorations (Refined Grid, Grimoire, Analyst)
and two console variants; the losing options are kept in the file only for
reference and are **not** part of this handoff.

## About the design files

The file in this bundle (`Deck Manager Directions.dc.html`) is a **design
reference created in HTML** — a prototype showing the intended look and
behavior. It is **not production code to copy**. It runs on a small custom
prototype runtime (`support.js`, also included) purely so the reference renders
in a browser; that runtime is **not** part of the design and must not be ported.

Your task is to **recreate these designs in the existing Familiar codebase**
using its established patterns:

- **React 19 + TypeScript (strict), Vite 8.** No component/UI library — every
  component stays hand-rolled.
- **No CSS framework.** Plain CSS in the two existing files: `src/index.css`
  (tokens + reset) and `src/styles/global.css` (BEM-ish class names like
  `.deck-detail__stat`, `.proposal-card__row`).
- **react-markdown + remark-gfm** already render assistant messages incl.
  tables — style GFM tables to match the "Results table" spec below.
- **Design against the tokens, not hardcoded colors.** Both the light default
  and the `prefers-color-scheme: dark` override must keep working. The
  reference is shown in dark mode; every hex below is the **dark-mode** value.
  Add the new tokens (see Design Tokens) to both `:root` and the dark block.
- Desktop-first. Mobile is not a target.

## Fidelity

**High-fidelity.** Colors, typography, spacing, radii, and component structure
are final. Recreate pixel-for-pixel with your CSS + tokens. The one deliberate
liberty: the reference hardcodes dark hex values inline (prototype constraint) —
in the codebase these must become token references so light mode works.

---

## Design tokens

### Existing tokens (already in `src/index.css`) — dark values shown
```
--text:        #c4c2cb      body / secondary text
--text-h:      #f3f4f6      headings, primary values, card names
--bg:          #16171d      app base, chat, modal, primary buttons' contrast
--bg-soft:     #1c1d24      cards, sidebar, panel, inputs, popover
--border:      #2e303a      all hairlines / card borders
--code-bg:     #1f2028
--accent:      #c084fc      brand purple — primary actions, active states, dial
--accent-bg:   rgba(192,132,252,.15)   ghost-button + active-pill fill
--accent-border: rgba(192,132,252,.5)
--user-bubble-bg: #2a2535   user chat bubble
--sans:  system-ui, 'Segoe UI', Roboto, sans-serif
--mono:  ui-monospace, 'SF Mono', Consolas, monospace
```

### New tokens to add (recommend adding light + dark values)
```
--bg-soft-2:   #22232c      inset track behind role-balance bars (dark)
--label:       #7a7c88      mono micro-labels (UPPERCASE section headers)
--text-dim:    #8b8d99      tertiary / helper / caption text
--bracket:     #ffb454      commander-bracket gold (numerals + diamonds)
--bracket-2:   #ff8f3f      gold gradient end (diamond fill)
--ok:          #3fbf7f      "100 ✓ legal"
--ok-2:        #58c98d      proposal "+" chip, positive table scores
--danger:      #d0402e      proposal "−" chip base
--danger-fg:   #e0685a      proposal "−" glyph
--brand-a:     #863bff      logo/wordmark gradient start
--brand-b:     #47bfff      logo/wordmark gradient end
--on-accent:   #17121d      text/icon color on solid --accent buttons
```
Brand gradient (logo mark, top hairlines, primary "Open →" in dark panels):
`linear-gradient(135deg, var(--brand-a), var(--brand-b))`.

### WUBRG mana palette (mode-agnostic; tuned for dark, verify on light)
```
W #e7e2bf   U #2f86e0   B #6d5c80   R #d0402e   G #2f9e5f   Colorless #6f7280
```
Expose as `--mana-w … --mana-c`. Role-balance bar colors reuse these:
Lands = neutral `#7c7f8c`, Ramp = G, Draw = U, Removal = R.
Venn "overlap lens" (two-color commander pip) = `#8a5cc0`.

### Typography scale
- Base: `16px / 1.45 var(--sans)`.
- **Micro-label** (every section header, e.g. `MANA CURVE`, `POWER LEVEL`):
  `var(--mono)`, 10–11px, weight 600, `letter-spacing:.12–.16em`,
  `text-transform:uppercase`, color `--label`.
- **Numerals / data** (curve counts, MV, table cells, qty): `var(--mono)`,
  weight 600–700, `font-variant-numeric: tabular-nums`. Sizes: 18px (panel
  stat), 26px (metric-strip), 30px (analyst), 40–46px (hero dials elsewhere).
- **Headings:** deck title h2 22px/700 (`letter-spacing:-.01em`); modal/panel
  h3 16–18px/700.
- **Body / chat:** 13–13.5px, line-height 1.5–1.55.
- **Chat "FAMILIAR" author label:** mono, 10px, weight 600,
  `letter-spacing:.16em`, uppercase, color `--accent`.

### Spacing, radius, shadow
- Radius: cards/panels 14–16px; window 16px; buttons 6–11px; pills/chips 20px;
  mana pips & role tracks fully round; bracket diamonds 3–7px (on the rotated
  square).
- Card/section padding 14–22px; grid/flex gaps 6–16px.
- Elevation: floating window `0 24–30px 60–70px -30px rgba(0,0,0,.75)`;
  popover `0 24px 50px -20px rgba(0,0,0,.75)`; modal
  `0 30px 60px -24px rgba(0,0,0,.8)`.
- Hairline dividers are always `1px solid var(--border)`. Metric-strip and
  analyst tables use borders (not shadows) to separate cells.

---

## Screens / views

### 1 · App shell (three-column grid)
- **Layout:** `display:grid; grid-template-columns:240px 1fr 320px; height:100vh;`
  Columns from left: **Sidebar**, **Center** (chat OR deck manager), **Deck
  side-panel**. Each column manages its own vertical overflow.
- Column dividers: sidebar `border-right`, panel `border-left`, both
  `1px solid var(--border)`.

### 2 · Sidebar (240px)
- `background:var(--bg-soft)`, flex column, padding 16px 14px, gap ~15px.
- **Brand row:** 20px gradient-mark square (radius 6) + "Familiar" 16px/700;
  right-aligned 28px settings button (radius 7, `border`, `bg var(--bg)`,
  gear glyph, color `--text-dim`).
- **Tabs** (Conversations / Decks): segmented control — container
  `bg var(--bg)`, `border`, radius 9, padding 3; each button flex:1, 12.5px;
  active = `bg var(--accent-bg)`, `color var(--accent)`, weight 600.
- **"+ New conversation":** ghost-accent button (see Buttons).
- **List:** `RECENT` micro-label, then item rows: padding 9px 11px, radius 8,
  12.5px, single-line ellipsis. Active item: `bg var(--accent-bg)` +
  `border-left:2px solid var(--accent)`, `color var(--text-h)`. Inactive:
  `color var(--text)`, transparent.

### 3 · Chat (center, 1fr)
- Flex column. Scroll area `flex:1; overflow:auto; padding:26px 32px;` with
  `display:flex; flex-direction:column; gap:20px`. Composer pinned below with
  `border-top`.
- **User bubble:** `align-self:flex-end; max-width:72%;`
  `bg var(--user-bubble-bg); border; border-radius:14px 14px 4px 14px;`
  padding 12px 15px; 13.5px; `color var(--text-h)`.
- **Familiar message:** `max-width:~90%`; `FAMILIAR` author label (accent mono)
  then markdown body (13.5px/1.55, `color var(--text)`; `<strong>` →
  `var(--text-h)`).
- **Proposal card** (inline in a Familiar message): see Components.
- **Tool-activity indicator:** row, mono 11.5px, `color var(--label)`; leading
  7px accent dot animated with `@keyframes fam-pulse { 0%,100%{opacity:.35} 50%{opacity:1} }`,
  `animation:fam-pulse 1.2s ease-in-out infinite`. Copy e.g. "Checked 100 cards
  against Scryfall · 2 flagged".
- **Results table** (rendered from a GFM markdown table): see Components.
- **Composer:** `border-top`; padding 15px 20px; flex row gap 10, align center.
  Input = `flex:1; bg var(--bg-soft); border; radius 11; padding 12px 14px;`
  13.5px; placeholder color `--label`. Send = solid-accent button.

### 4 · Deck side-panel (320px)
- `background:var(--bg-soft)`, `border-left`, flex column, overflow hidden.
- **Header** (padding 16px 18px, `border-bottom`): commander color pip (13px
  circle in mana color, `box-shadow:0 0 0 1px rgba(255,255,255,.16)`) + deck
  name 16px/700; sub-line mono 11px `--text-dim` ("Korlash, Heir to Blackblade
  · mono-black"); button row Export / Import (secondary mono) + "Open →"
  (ghost-accent, flex-grown).
- **Mini stat strip:** `grid-template-columns:1fr 1fr 1fr`, cells split by
  `border-right`, `border-bottom` under the strip. Each: micro-label (9px) +
  18px/700 mono numeral. POWER value `--accent`, BRACKET value `--bracket`,
  CARDS value `--text-h`.
- **Card list:** `flex:1; overflow:auto; padding:14px 16px;` grouped by
  category. Group = micro-label + count (mono, right). Row = qty `1×` (mono,
  14px wide, `--label`) + 9px mana pip + name (`--text-h`, ellipsis) + MV
  (mono, `--label`, right).

### 5 · Deck manager (center, when Decks tab + a deck active) — "3A"
- **Compact top bar** (`bg var(--bg-soft)`, single row, padding 14px 22px):
  **Commander Venn pip** (see Components) + deck title 15px/700 + mono tag
  `UR · Commander/EDH` (`--label`) + commander names (mono, `--text-dim`,
  `flex:1`, ellipsis) · right: Export / Import (secondary mono) + "Open →"
  (ghost-accent).
- **Dense metric strip** (edge-to-edge, `grid-template-columns:1.35fr 1fr 1fr 1fr 1fr`,
  cells split by `border-right`, `bg var(--bg-soft)`, `border-top`+`border-bottom`):
  1) **POWER LEVEL** — 56px conic power dial (see Components) + "High-power ·
  7/10". 2) **BRACKET** — 26px/700 gold numeral `3 / 5` + row of 5 bracket
  diamonds. 3) **CARDS** 100 + "legal ✓" (`--ok`). 4) **AVG MV** 2.6 +
  "nonland". 5) **LANDS** 34 + "34%".
- **Stat grid** (below, padding 22px 26px): two rows of bordered stat cards
  (`bg var(--bg-soft)`, radius 14, padding 18px) — MANA CURVE + ROLE BALANCE,
  then COLORS + TYPES. Then QUICK ACTIONS chips, then a 2-column DECKLIST
  grouped by type (group header with bottom border + count; rows: qty · pip ·
  name · MV).

### 6 · Import popover — "4B"
- Portaled, anchored under the Import button. Panel: `width:296px;`
  `bg var(--bg-soft); border; border-radius:12px;` popover shadow; padding 16px.
- `IMPORT FROM…` micro-label; segmented control **Text / Archidekt** (Archidekt
  active); URL input (`bg var(--bg); border`; when focused use
  `border:1px solid var(--accent-border)`; radius 9; placeholder `--label`);
  ghost-accent "Fetch from Archidekt" full-width; helper text 11.5px
  `--text-dim` "Merges into this deck; maybeboard is skipped."

### 7 · Preferences modal — "4C"
- Centered modal: `width:400px; bg var(--bg); border; border-radius:14px;`
  modal shadow; padding 22px. (Over a dimmed page scrim in the real app.)
- Header: "Preferences" h3 18px/700 + 26px close button (radius 7, `border`,
  `bg var(--bg-soft)`, ✕).
- Fields (gap 15px): each = label (12px/600, `--text-h`) + control. Selects
  render as `bg var(--bg-soft); border; radius 9; padding 10px 12px;` 13px
  `--text-h` with a `▾`. Rule-0 = textarea, min-height 70px, 12.5px/1.5.
- **Save** = solid-accent button, left-aligned.

---

## Components (reusable primitives)

- **Buttons.**
  - *Secondary:* `border:1px solid var(--border); bg var(--bg-soft)` (or
    `var(--bg)` on soft surfaces); `color var(--text)`; radius 7–8; 12–12.5px.
    Mono variant for compact toolbars (panel/manager top bars).
  - *Ghost-accent:* `border:1px solid var(--accent-border); bg var(--accent-bg);
    color var(--accent);` weight 600. Used for New conversation, Open →, Fetch,
    quick-action chips (radius up to 9–20).
  - *Solid-accent (primary):* `bg var(--accent); color var(--on-accent);`
    weight 700; radius 9–11. Used for Send, Approve, Save. No glow/shadow.
- **Segmented control:** container `bg var(--bg); border; radius 9; padding 3`;
  active segment `bg var(--accent-bg); color var(--accent); weight 600`;
  inactive `color var(--text-dim)`.
- **Mana pip:** solid circle in the card's mana color (WUBRG palette); 9px in
  lists, 11–13px in headers. Colorless → `#6f7280`.
- **Commander Venn pip** (dual-color identity): 26×16 box, two 16px circles —
  color-A at `left:0`, color-B at `left:10px` — plus a small lens
  `left:10px; top:2px; width:6px; height:12px; border-radius:50%;` in the
  overlap filled with the blend color (UR → `#8a5cc0`). Reads as a 2-circle
  Venn, not a pie. (Earlier `mix-blend-mode`/`<svg clipPath>` attempts were
  abandoned — build it from three positioned circles as above, or as a small
  inline SVG with an explicit intersection path.)
- **Power dial:** round element, `background: conic-gradient(var(--accent) 0turn
  <p/10>turn, var(--border) <p/10>turn 1turn)`; inner circle (inset ~6–9px)
  filled with the surface bg, centered mono numeral. 56px in strips.
- **Bracket diamonds:** 14px square, `transform:rotate(45deg)`, radius 3.
  Filled (≤ bracket): `linear-gradient(135deg,var(--bracket),var(--bracket-2))`.
  Empty: `bg rgba(255,255,255,.04); border:1px solid var(--border)`.
- **Mana-curve bars:** flex row, `align-items:flex-end`; each bar height ∝
  count/max, `linear-gradient(180deg, var(--accent), rgba(192,132,252,.45))`,
  radius `6px 6px 2px 2px`; count above (mono), bucket label below.
- **Role-balance bars:** label + count row, then track
  (`bg var(--bg-soft-2); radius 4; overflow hidden`) with fill width ∝
  count/max, colored per role (Lands neutral, Ramp G, Draw U, Removal R).
- **Card row:** `qty (mono) · pip · name (ellipsis) · MV (mono)`; padding 3–4px
  vertical; 12.5–13px.
- **Proposal card:** `border:1px solid var(--border); border-left:3px solid
  var(--accent); bg var(--bg-soft); radius 12; padding 15px 16px`. Header:
  `PROPOSED CHANGE` accent micro-label + count/net (mono `--label`). Swap rows:
  17px rounded chip — remove = `bg rgba(208,64,46,.16); color var(--danger-fg)`
  with `−`; add = `bg rgba(63,191,127,.16); color var(--ok-2)` with `+` — then
  card name (`--text-h`) + short mono note. Rationale 12.5px `--text-dim`.
  Actions: solid-accent **Approve** + secondary **Deny**, each `flex:1`.
- **Results table** (also the target style for GFM tables in chat): outer
  `border; radius 10; overflow hidden`. Header row `bg var(--bg-soft);
  border-bottom`; cells are micro-labels. Body rows: zebra
  (`background:rgba(255,255,255,.014)` on odd), `border-bottom:1px solid
  rgba(46,48,58,.6)`, mono data. First column = metric (`--text-h`, 12.5px,
  weight 500); score column = mono, weight 600, `color var(--ok-2)`.
- **Popover / Modal surfaces:** popover `bg var(--bg-soft)`; modal `bg var(--bg)`;
  both `border; radius 12–14` with the elevation shadows above.

---

## Interactions & behavior

- **Tabs (Conversations/Decks):** switch the center column between chat stream
  and the deck manager.
- **Conversation / deck list:** click selects (active styling); center + panel
  reflect the selection.
- **Proposal card:** Approve applies the swap(s) to the deck and updates the
  side-panel + all stats live; Deny dismisses. Multi-swap proposals show one
  row per add/remove and act atomically ("Approve both").
- **Tool-activity indicator:** visible while the assistant is computing / calling
  tools; the pulsing dot is the only motion in the UI. Remove when the message
  resolves.
- **Streaming assistant text + markdown** via react-markdown/remark-gfm; GFM
  tables adopt the Results-table style.
- **Import popover:** Text vs Archidekt toggles the body; Fetch is disabled
  until the field is non-empty; success merges into the current deck.
- **Preferences:** selects + textarea persist locally on Save; close via ✕ or
  scrim click.
- **Power level / bracket** are derived from deck contents (not user-set here;
  the preferences hold *preferred* targets).
- No decorative animation, no gradient washes, no glow. Motion is limited to the
  tool-activity pulse and native control transitions.

## State management (unchanged from current component set)

`ConversationSidebar`, `ChatView`, `MessageBubble`, `ToolActivityIndicator`,
`ProposalCard`, `DeckPanel`, `DeckDetail`, `DeckCardRow`, `ImportPanel`,
`PreferencesPanel`. State needed: active tab; selected conversation & deck;
message list (role, markdown, optional proposal/tool-activity); pending
proposals; deck model (commander(s), cards w/ type+MV+color, derived
curve/roles/types/colors/power/bracket/counts); preferences
(bracket/power/budget/rule-0); import form (source, value, loading).

## Assets

**Logo — "Watcher".** The Familiar mark is a geometric cat head (the
"familiar") with arcane almond eyes, in the brand gradient
`#863bff → #47bfff`. Real SVGs are included in `assets/`:

- `assets/familiar-mark.svg` — the standalone mark: gradient cat with
  **transparent** (knocked-out) eyes, transparent background. Use anywhere on
  any background (sidebar brand, docs, etc.). Recolor by editing the two
  `linearGradient` stops.
- `assets/favicon.svg` — a rounded brand-gradient tile (rx 24) with a white
  cat; eyes show the tile gradient through. Reads at 16px. Use for the browser
  favicon / app icon. (Export PNGs at 16/32/180/512 from this if you need
  raster favicons.)

Mark geometry (viewBox 0 0 100 100), if you need to rebuild it: head polygon
`12,34 24,4 42,30 58,30 76,4 88,34 82,62 64,88 50,96 36,88 18,62`; eyes are two
ellipses `rx 5.5 ry 8.5` at `(36,55)` rotated +20° and `(64,55)` rotated −20°.
The wordmark is the mark + "Familiar" in `var(--sans)`, 700, `letter-spacing:-.02em`.

No other raster assets. Mana pips, power dial, bracket diamonds, and role bars
are all pure CSS — no icon font or image files required. If you already have a
WUBRG mana-symbol set, you may substitute it for the solid pips.

## Files

- `Deck Manager Directions.dc.html` — the design reference. Open in a browser
  (needs the sibling `support.js`). Relevant sections, top to bottom:
  - **4A** — full three-column app (sidebar + chat + deck side-panel).
  - **4B** — import popover. **4C** — preferences modal.
  - **3A** — the deck manager (the canonical stat-grid surface).
  - (1A / 1B / 1C below are earlier explorations — ignore.)
- `assets/familiar-mark.svg` — logo mark (transparent eyes/bg).
- `assets/favicon.svg` — favicon / app-icon tile.
- `support.js` — prototype runtime only. **Do not port.**
