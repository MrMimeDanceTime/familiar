# Familiar — UIX Overhaul Bundle (current state)

This is a **self-contained snapshot of the Familiar frontend as it exists
today**, assembled to hand to a designer (Claude Design or otherwise) as raw
material for a UI/UX overhaul. Everything here is the *real, shipping* code —
not a prototype and not the older design cycle in `../brief` / `../handoff`
(that one is historical; its `theme-tokens.css` is stale).

**Start here:** [`FAMILIAR-DESIGN-BUNDLE.md`](FAMILIAR-DESIGN-BUNDLE.md) — a
single file that inlines every stylesheet and component below, so it can be
pasted whole into a design chat. The folders exist if you'd rather browse real
files.

---

## What Familiar is

A personal, **local-only** AI assistant for **Magic: The Gathering
Commander/EDH** deckbuilding. Single-user desktop web app, served locally. The
user chats with an AI deckbuilding partner; the AI proposes deck changes the
user approves/denies via inline **proposal cards**, and a live **deck panel +
stats** keep pace with the build.

## Hard tech constraints (please design within these)

- **React 19 + TypeScript 6 (strict)**, **Vite 8** build, **oxlint** lint.
- **No component/UI library** — every component is hand-rolled.
- **No CSS framework** (no Tailwind/MUI). Plain CSS in exactly two files:
  - `current-src/index.css` — **design tokens** (light default +
    `@media (prefers-color-scheme: dark)` override) and the base reset.
  - `current-src/styles/global.css` — one global stylesheet, **BEM-ish** class
    names (`.deck-detail__header`, `.import-pop__chip`). ~2,000 lines.
- Only runtime UI deps: **react-markdown** + **remark-gfm** (assistant messages
  render markdown incl. GFM tables — style those, don't restructure them).
- **Keep new dependencies to a minimum.** If a design needs a new lib, call it
  out explicitly.
- Both **light and dark** must keep working → **design against the tokens, not
  hardcoded colors.** New colors should become new tokens in *both* blocks.

## Layout

Three-column CSS grid **`240px | 1fr | 320px`**, with a separate single-column
mobile layout (`≤768px`) that swaps to bottom-tab navigation:

- **Left (240px):** sidebar — brand header, Conversations/Decks segmented tabs,
  list, settings gear.
- **Center (1fr):** chat view (message stream + composer) **OR** the
  full-screen **deck manager** (`DeckDetail`) when the Decks tab + a deck are
  active.
- **Right (320px):** deck side-panel (`DeckPanel`) — live deck contents while
  chatting.

## Key surfaces / components

| Component | Surface |
|-----------|---------|
| `ConversationSidebar` | Brand, Conversations/Decks tabs, list, gear |
| `ChatView` + `MessageBubble` | Streaming AI chat, markdown incl. tables, user bubbles |
| `ToolActivityIndicator` | Pulsing "tool running" row (only motion in the UI) |
| `ProposalCard` | Inline approve/deny AI deck-change suggestions |
| `DeckPanel` | 320px live deck view during chat (mini stat strip + card list) |
| `DeckDetail` | Full deck manager — **the signature surface** (metric strip, stat grid, quick actions, decklist) |
| `deckViz` | Pure-CSS/SVG primitives: mana pip, commander Venn pip, power dial, bracket diamonds, mana curve, role balance |
| `DeckCardRow` | qty · pip · name · MV row (shared) |
| `ImportPanel` | Portaled import popover (paste text / fetch Archidekt) |
| `PreferencesPanel` | Modal: bracket / power / budget / rule-0 notes |
| `CardPreview` + `PinnedTray` + `CardPinContext` | Hover card image; click to pin into a compare tray |
| `MobileHeader` / `MobileNav` | Mobile-only chrome |

## The "Console" visual language (current)

The app currently uses a restrained **"deck-console"** system: monospace
micro-labels for section headers, tabular-nums for all data, hairline `1px
var(--border)` dividers instead of shadows between cells, purple accent
(`#aa3bff` light / `#c084fc` dark), a purple→blue brand gradient
(`#863bff → #47bfff`), and pure-CSS data viz (power dial = conic-gradient,
bracket diamonds = rotated squares, mana pips = color-banded circles). Motion is
limited to one pulsing dot. This is the baseline to evolve — keep, refine, or
reimagine as the overhaul calls for.

## Where the design effort is most valuable

- The **deck manager** (`DeckDetail`) stat grid + decklist — users spend the
  most time here.
- **Hierarchy / rhythm in chat** — message bubbles, proposal cards, results
  tables.
- **Consistent, reusable component primitives** (buttons, chips, inputs, cards,
  modals) expressed as CSS classes on the tokens.
- Any **layout improvements** to the three-column desktop grid.

## Files in this bundle

```
refresh-2/
├── README.md                      ← you are here
├── FAMILIAR-DESIGN-BUNDLE.md      ← everything inlined into one pasteable file
├── assets/
│   ├── favicon.svg                ← brand tile (browser icon + sidebar mark)
│   └── familiar-mark.svg          ← standalone transparent mark
└── current-src/                   ← verbatim copies of the live frontend
    ├── index.html
    ├── index.css                  ← tokens + reset
    ├── styles/global.css          ← the whole stylesheet
    ├── App.tsx
    ├── components/*.tsx
    ├── types/api.ts               ← data shapes the UI renders
    └── api/cardImage.ts
```

> The `hooks/` directory in the tree is intentionally empty here — the chat
> streaming hook (`useChatStream`) and `useDeck` are behavioral, not visual, and
> were left out to keep the bundle focused on UI. The component prop types they
> feed (`DisplayMessage`, `ProposalBatch`) are described where used.

---

# Inlined source

Every file below is a verbatim copy from `current-src/`. Fenced by type.

## `index.css`

```css
:root {
  --text: #6b6375;
  --text-h: #08060d;
  --bg: #fff;
  --bg-soft: #f7f6fa;
  --bg-soft-2: #efeef4;
  --border: #e5e4e7;
  --code-bg: #f4f3ec;
  --accent: #aa3bff;
  --accent-bg: rgba(170, 59, 255, 0.1);
  --accent-border: rgba(170, 59, 255, 0.5);
  --user-bubble-bg: #ece9f5;

  --label: #8a8894;
  --text-dim: #6f6d79;
  --bracket: #d98a1f;
  --bracket-2: #c9701f;
  --ok: #17945a;
  --ok-2: #17945a;
  --danger: #c53a2a;
  --danger-fg: #c53a2a;
  --brand-a: #863bff;
  --brand-b: #1f8fd6;
  --on-accent: #fff;

  --mana-w: #b8ad74;
  --mana-u: #3f9bf0;
  --mana-b: #8b6fae;
  --mana-r: #e0503c;
  --mana-g: #35b06a;
  --mana-c: #7f828c;
  --venn-lens: #9a6cd0;
  /* Contrast ring around mana pips — dark on light surfaces, white on dark. */
  --pip-ring: rgba(0, 0, 0, 0.3);

  --sans: system-ui, 'Segoe UI', Roboto, sans-serif;
  --mono: ui-monospace, Consolas, monospace;

  font: 16px/145% var(--sans);
  color-scheme: light dark;
  color: var(--text);
  background: var(--bg);
  font-synthesis: none;
  text-rendering: optimizeLegibility;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

@media (prefers-color-scheme: dark) {
  :root {
    --text: #c4c2cb;
    --text-h: #f3f4f6;
    --bg: #16171d;
    --bg-soft: #1c1d24;
    --bg-soft-2: #22232c;
    --border: #2e303a;
    --code-bg: #1f2028;
    --accent: #c084fc;
    --accent-bg: rgba(192, 132, 252, 0.15);
    --accent-border: rgba(192, 132, 252, 0.5);
    --user-bubble-bg: #2a2535;

    --label: #7a7c88;
    --text-dim: #8b8d99;
    --bracket: #ffb454;
    --bracket-2: #ff8f3f;
    --ok: #3fbf7f;
    --ok-2: #58c98d;
    --danger: #d0402e;
    --danger-fg: #e0685a;
    --brand-a: #863bff;
    --brand-b: #47bfff;
    --on-accent: #17121d;

    --mana-w: #e7e2bf;
    --mana-u: #4aa4f5;
    --mana-b: #a17fc4;
    --mana-r: #e85a44;
    --mana-g: #3cbf76;
    --mana-c: #a9adb8;
    --venn-lens: #a074d8;
    --pip-ring: rgba(255, 255, 255, 0.5);
  }
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  color: var(--text);
  background: var(--bg);
}

h1, h2, h3 {
  font-weight: 600;
  color: var(--text-h);
  margin: 0;
}

button {
  font: inherit;
  cursor: pointer;
}

#root {
  height: 100vh;
  height: 100dvh;
}
```

## `styles/global.css`

```css
/* =========================================================================
   Familiar — "Console" visual system.
   Design against tokens (index.css); every hardcoded color here is a token
   so light + dark both work.
   ========================================================================= */

.app-layout {
  display: grid;
  grid-template-columns: 240px 1fr 320px;
  height: 100vh;
  height: 100dvh;
}

/* ── Shared primitives ─────────────────────────────────────────────────── */

.micro-label {
  font-family: var(--mono);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--label);
}

/* Buttons */
.btn {
  font: inherit;
  font-size: 12.5px;
  cursor: pointer;
  border-radius: 8px;
  padding: 7px 12px;
  border: 1px solid var(--border);
  background: var(--bg-soft);
  color: var(--text);
  white-space: nowrap;
}

.btn--mono {
  font-family: var(--mono);
  font-size: 11px;
}

.btn--secondary:hover {
  background: var(--accent-bg);
  color: var(--text-h);
}

.btn--ghost {
  border-color: var(--accent-border);
  background: var(--accent-bg);
  color: var(--accent);
  font-weight: 600;
}

.btn--ghost:hover {
  background: var(--accent);
  color: var(--on-accent);
  border-color: var(--accent);
}

.btn--primary {
  border-color: transparent;
  background: var(--accent);
  color: var(--on-accent);
  font-weight: 700;
}

.btn--primary:hover {
  opacity: 0.9;
}

.btn--chip {
  border-radius: 20px;
  padding: 9px 15px;
  font-size: 13px;
}

.btn--active {
  background: var(--accent-bg);
  border-color: var(--accent-border);
  color: var(--accent);
}

.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* Segmented control */
.segmented {
  display: flex;
  gap: 3px;
  padding: 3px;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: var(--bg);
}

.segmented__seg {
  flex: 1;
  border: none;
  background: transparent;
  color: var(--text-dim);
  font: inherit;
  font-size: 12.5px;
  padding: 6px;
  border-radius: 6px;
}

.segmented__seg--active {
  background: var(--accent-bg);
  color: var(--accent);
  font-weight: 600;
}

/* Mana pip */
.mana-pip {
  display: inline-block;
  border-radius: 50%;
  flex: 0 0 auto;
  box-shadow: 0 0 0 1px var(--pip-ring);
}

/* Commander Venn pip — two circles + overlap lens. Sized up vs the small
   list pips so the two color pies (one per commander) stay readable next to
   the deck name. */
.venn-pip {
  position: relative;
  display: inline-block;
  width: 36px;
  height: 22px;
  flex: 0 0 auto;
}
.venn-pip__a,
.venn-pip__b {
  position: absolute;
  top: 0;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  box-shadow: 0 0 0 1px var(--pip-ring);
}
.venn-pip__a { left: 0; }
.venn-pip__b { left: 14px; }
.venn-pip__lens {
  position: absolute;
  left: 14px;
  top: 3px;
  width: 8px;
  height: 16px;
  border-radius: 50%;
  background: var(--venn-lens);
}

/* Power dial */
.power-dial {
  position: relative;
  border-radius: 50%;
  flex: 0 0 auto;
}
.power-dial__hole {
  position: absolute;
  inset: 6px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
}
.power-dial__num {
  font-family: var(--mono);
  font-weight: 700;
  font-size: 19px;
  line-height: 1;
  color: var(--text-h);
}

/* Bracket diamonds */
.bracket-diamonds {
  display: flex;
  gap: 8px;
}
.bracket-diamond {
  transform: rotate(45deg);
  border-radius: 3px;
  flex: 0 0 auto;
  background: rgba(127, 127, 140, 0.08);
  border: 1px solid var(--border);
}
.bracket-diamond--on {
  background: linear-gradient(135deg, var(--bracket), var(--bracket-2));
  border-color: transparent;
}

/* Mana curve */
.mana-curve {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  height: 118px;
}
.mana-curve__bar-wrap {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  justify-content: flex-end;
  height: 100%;
}
.mana-curve__count {
  font-family: var(--mono);
  font-weight: 600;
  font-size: 12px;
  color: var(--text-h);
}
.mana-curve__bar {
  width: 100%;
  min-height: 4px;
  border-radius: 6px 6px 2px 2px;
  background: linear-gradient(180deg, var(--accent), rgba(192, 132, 252, 0.45));
}
.mana-curve__label {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--label);
}

/* Role balance */
.role-balance {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.role-balance__head {
  display: flex;
  justify-content: space-between;
  font-size: 12.5px;
  margin-bottom: 5px;
}
.role-balance__label { color: var(--text-h); }
.role-balance__count {
  font-family: var(--mono);
  font-weight: 600;
  font-size: 12px;
  color: var(--text-dim);
}
.role-balance__track {
  height: 8px;
  border-radius: 4px;
  background: var(--bg-soft-2);
  overflow: hidden;
}
.role-balance__fill {
  height: 100%;
  border-radius: 4px;
}

/* ── Sidebar (240px) ───────────────────────────────────────────────────── */

.conversation-sidebar {
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--border);
  background: var(--bg-soft);
  padding: 16px 14px;
  gap: 15px;
  overflow-y: auto;
}

.sidebar-brand {
  display: flex;
  align-items: center;
  gap: 8px;
}

.sidebar-brand__logo {
  flex-shrink: 0;
}

.sidebar-brand__name {
  font-size: 16px;
  font-weight: 700;
  letter-spacing: -0.01em;
  background: linear-gradient(135deg, var(--brand-a), var(--brand-b));
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}

.sidebar-prefs-btn {
  margin-left: auto;
  width: 28px;
  height: 28px;
  border: 1px solid var(--border);
  background: var(--bg);
  border-radius: 7px;
  font-size: 13px;
  line-height: 1;
  color: var(--text-dim);
  display: flex;
  align-items: center;
  justify-content: center;
}

.sidebar-prefs-btn:hover {
  background: var(--accent-bg);
  color: var(--text-h);
}

.conversation-sidebar__new {
  width: 100%;
  border-radius: 9px;
  padding: 10px;
  font-size: 13px;
}

.conversation-sidebar__recent {
  margin-top: 4px;
}

.conversation-sidebar__list {
  display: flex;
  flex-direction: column;
  gap: 3px;
  overflow: hidden;
}

.conversation-sidebar__item {
  display: flex;
  align-items: center;
  gap: 4px;
  border-radius: 8px;
  border-left: 2px solid transparent;
}

.conversation-sidebar__item:hover {
  background: var(--bg);
}

.conversation-sidebar__item--active {
  background: var(--accent-bg);
  border-left-color: var(--accent);
  color: var(--text-h);
}

.conversation-sidebar__item-title {
  flex: 1;
  min-width: 0;
  text-align: left;
  border: none;
  background: transparent;
  color: inherit;
  font: inherit;
  font-size: 12.5px;
  line-height: 1.35;
  padding: 9px 11px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.sidebar-deck-name {
  display: block;
}

.sidebar-deck-commander {
  display: block;
  font-size: 11px;
  color: var(--text-dim);
  margin-top: 1px;
}

.conversation-sidebar__item-delete {
  border: none;
  background: transparent;
  color: var(--label);
  opacity: 0;
  width: 22px;
  height: 22px;
  border-radius: 4px;
  font-size: 16px;
  line-height: 1;
  flex-shrink: 0;
  margin-right: 6px;
}

.conversation-sidebar__item:hover .conversation-sidebar__item-delete {
  opacity: 1;
}

.conversation-sidebar__item-delete:hover {
  background: var(--border);
  color: var(--text-h);
}

/* On touch devices there is no hover — keep delete buttons visible. */
@media (hover: none) {
  .conversation-sidebar__item-delete {
    opacity: 0.55;
  }
}

/* ── Chat (center) ─────────────────────────────────────────────────────── */

.chat-view {
  display: flex;
  flex-direction: column;
  height: 100vh;
  height: 100dvh;
  border-right: 1px solid var(--border);
  background: var(--bg);
  min-width: 0;
}

.chat-view__messages {
  flex: 1;
  overflow-y: auto;
  padding: 26px 32px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

body.has-pinned-tray .chat-view__messages {
  padding-bottom: 354px;
}

.chat-view__empty {
  color: var(--text-dim);
  margin: auto;
  text-align: center;
}

.chat-view__error {
  background: rgba(208, 64, 46, 0.1);
  border: 1px solid rgba(208, 64, 46, 0.4);
  color: var(--danger-fg);
  padding: 10px 12px;
  border-radius: 8px;
  font-size: 13px;
}

.chat-view__input {
  display: flex;
  gap: 10px;
  padding: 15px 20px;
  border-top: 1px solid var(--border);
  align-items: center;
}

.chat-view__input textarea {
  flex: 1;
  resize: none;
  border: 1px solid var(--border);
  border-radius: 11px;
  padding: 12px 14px;
  font: inherit;
  font-size: 13.5px;
  background: var(--bg-soft);
  color: var(--text-h);
  min-height: 44px;
  max-height: 160px;
}

.chat-view__input textarea::placeholder {
  color: var(--label);
}

.chat-view__input button {
  border: 1px solid transparent;
  background: var(--accent);
  color: var(--on-accent);
  border-radius: 11px;
  padding: 12px 20px;
  font-weight: 700;
  font-size: 13.5px;
}

.chat-view__input button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* Messages */
.message--user {
  align-self: flex-end;
  max-width: 72%;
  background: var(--user-bubble-bg);
  border: 1px solid var(--border);
  border-radius: 14px 14px 4px 14px;
  padding: 12px 15px;
}

.message--assistant {
  align-self: flex-start;
  max-width: 90%;
}

.message__author {
  font-family: var(--mono);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--accent);
  margin-bottom: 9px;
}

.message__text {
  font-size: 13.5px;
  line-height: 1.55;
  color: var(--text);
}

.message--user .message__text {
  color: var(--text-h);
}

.message__text strong {
  color: var(--text-h);
}

.message__text p {
  margin: 0 0 9px;
}

.message__text p:last-child {
  margin-bottom: 0;
}

.message__text ul, .message__text ol {
  margin: 0 0 9px;
  padding-left: 22px;
}

/* GFM tables → Results-table style */
.message__text table {
  border-collapse: separate;
  border-spacing: 0;
  width: 100%;
  margin: 4px 0 11px;
  font-size: 12.5px;
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
}

.message__text thead th {
  background: var(--bg-soft);
  border-bottom: 1px solid var(--border);
  font-family: var(--mono);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--label);
  padding: 9px 10px;
  text-align: left;
}

.message__text tbody td {
  border-bottom: 1px solid rgba(46, 48, 58, 0.6);
  padding: 8px 10px;
  font-family: var(--mono);
  color: var(--text);
}

.message__text tbody tr:nth-child(odd) td {
  background: rgba(127, 127, 140, 0.03);
}

.message__text tbody tr:last-child td {
  border-bottom: none;
}

.message__text tbody td:first-child {
  font-family: var(--sans);
  color: var(--text-h);
  font-weight: 500;
}

/* Tool activity */
.tool-activity {
  align-self: flex-start;
  display: flex;
  align-items: center;
  gap: 9px;
  font-family: var(--mono);
  font-size: 11.5px;
  color: var(--label);
}

.tool-activity__dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--accent);
  flex: 0 0 auto;
  animation: fam-pulse 1.2s ease-in-out infinite;
}

@keyframes fam-pulse {
  0%, 100% { opacity: 0.35; }
  50% { opacity: 1; }
}

/* ── Proposal card ─────────────────────────────────────────────────────── */

.proposal-batch {
  align-self: stretch;
  max-width: 90%;
}

.proposal-batch--resolved {
  opacity: 0.7;
}

.proposal-batch__summary {
  font-size: 12.5px;
  color: var(--text-dim);
  margin-bottom: 8px;
}

.proposal-batch__resolved-summary {
  display: flex;
  gap: 12px;
  margin-bottom: 8px;
  font-family: var(--mono);
  font-size: 11px;
}

.proposal-batch__tally--approved { color: var(--ok); }
.proposal-batch__tally--denied { color: var(--danger); }

.proposal-card {
  border: 1px solid var(--border);
  border-left: 3px solid var(--accent);
  border-radius: 12px;
  background: var(--bg-soft);
  padding: 15px 16px;
  margin-bottom: 8px;
}

.proposal-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.proposal-card__label {
  font-family: var(--mono);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--accent);
}

.proposal-card__meta {
  font-family: var(--mono);
  font-size: 10px;
  color: var(--label);
}

.proposal-swap {
  display: flex;
  align-items: center;
  gap: 9px;
  font-size: 13px;
  padding: 3px 0;
}

.proposal-swap--resolved {
  padding: 2px 0;
}

.proposal-chip {
  width: 17px;
  height: 17px;
  border-radius: 5px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: var(--mono);
  font-weight: 700;
  font-size: 13px;
  flex: 0 0 auto;
}

.proposal-chip--add {
  background: rgba(63, 191, 127, 0.16);
  color: var(--ok-2);
}

.proposal-chip--remove,
.proposal-chip--deny {
  background: rgba(208, 64, 46, 0.16);
  color: var(--danger-fg);
}

.proposal-swap__name {
  color: var(--text-h);
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.proposal-swap__note {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--label);
  flex: 0 0 auto;
}

.proposal-card__rationale {
  font-size: 12.5px;
  color: var(--text-dim);
  line-height: 1.45;
  margin: 6px 0 14px;
}

.proposal-card__actions {
  display: flex;
  gap: 9px;
}

.proposal-card__actions .btn {
  flex: 1;
  padding: 9px;
  border-radius: 9px;
  font-size: 13px;
  text-align: center;
  justify-content: center;
}

.proposal-batch__continue {
  display: block;
  margin-top: 10px;
  width: 100%;
  border: 1px solid var(--accent-border);
  background: var(--accent-bg);
  color: var(--accent);
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  padding: 9px 20px;
  border-radius: 9px;
}

.proposal-batch__continue:hover {
  background: var(--accent);
  color: var(--on-accent);
}

/* ── Deck side-panel (320px) ───────────────────────────────────────────── */

.deck-panel {
  background: var(--bg-soft);
  border-left: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.deck-panel--empty {
  align-items: center;
  justify-content: center;
  text-align: center;
  color: var(--text-dim);
  padding: 24px;
}

.deck-panel__header {
  padding: 16px 18px;
  border-bottom: 1px solid var(--border);
}

.deck-panel__title-row {
  display: flex;
  align-items: center;
  gap: 9px;
  margin-bottom: 5px;
}

.deck-panel__title {
  font-size: 16px;
  font-weight: 700;
  letter-spacing: -0.01em;
  cursor: pointer;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.deck-panel__title:hover {
  color: var(--accent);
}

.deck-panel__title-input {
  font-size: 16px;
  font-weight: 700;
  border: 1px solid var(--accent-border);
  border-radius: 7px;
  padding: 4px 8px;
  background: var(--bg);
  color: var(--text-h);
  font-family: inherit;
  width: 100%;
  box-sizing: border-box;
  margin-bottom: 5px;
}

.deck-panel__subline {
  font-family: var(--mono);
  font-size: 11px;
  line-height: 1.4;
  color: var(--text-dim);
}

.deck-panel__actions {
  display: flex;
  gap: 6px;
  margin-top: 12px;
}

.deck-panel__actions .btn {
  flex: 1;
  padding: 7px;
  text-align: center;
  justify-content: center;
}

.deck-panel__open {
  flex: 1.4 !important;
}

.deck-panel__strip {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  border-bottom: 1px solid var(--border);
}

.deck-panel__strip-cell {
  padding: 12px 14px;
  border-right: 1px solid var(--border);
}

.deck-panel__strip-cell:last-child {
  border-right: none;
}

.deck-panel__strip-cell .micro-label {
  font-size: 9px;
  letter-spacing: 0.1em;
  margin-bottom: 7px;
}

.deck-panel__strip-val {
  font-family: var(--mono);
  font-weight: 700;
  font-size: 18px;
  line-height: 1;
}

.deck-panel__strip-val--power { color: var(--accent); }
.deck-panel__strip-val--bracket { color: var(--bracket); }
.deck-panel__strip-val--cards { color: var(--text-h); }

.deck-panel__strip-unit {
  font-family: var(--mono);
  font-size: 10px;
  color: var(--label);
  margin-left: 2px;
}

.deck-panel__cards {
  flex: 1;
  overflow-y: auto;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 13px;
}

/* When the pinned tray is visible, make room so the last few cards aren't
   hidden behind it.  ~340 px covers the tray header + a row of card images. */
body.has-pinned-tray .deck-panel__cards {
  padding-bottom: 354px;
}

.deck-panel__group-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}

.deck-panel__group-head .micro-label {
  font-size: 10px;
  letter-spacing: 0.12em;
}

.deck-panel__group-count {
  font-family: var(--mono);
  font-size: 10px;
  color: var(--label);
}

.deck-panel__notes {
  margin: 0;
  font-size: 12.5px;
  color: var(--text);
  line-height: 1.5;
}

/* Card row (shared) */
.deck-card-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
  padding: 3px 0;
}

.deck-card-row__qty {
  font-family: var(--mono);
  font-weight: 600;
  font-size: 10px;
  color: var(--label);
  width: 16px;
  flex: 0 0 auto;
}

.deck-card-row__name {
  flex: 1;
  color: var(--text-h);
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.deck-card-row__mv {
  font-family: var(--mono);
  font-size: 10px;
  color: var(--label);
  flex: 0 0 auto;
  /* Push MV to the row's right edge now that the name sizes to its text. */
  margin-left: auto;
}

/* Card image preview (hover + click-to-pin) */
.card-preview-anchor {
  cursor: pointer;
}

/* In a card row the name both truncates AND sizes to its text, so the hover
   target is the name itself, not the row's empty stretch. */
.deck-card-row .card-preview-anchor.deck-card-row__name {
  flex: 0 1 auto;
  max-width: 100%;
}

.card-preview-anchor:hover {
  color: var(--accent);
}

.card-preview-anchor--pinned {
  color: var(--accent);
  text-decoration: underline;
  text-decoration-color: var(--accent-border);
  text-underline-offset: 2px;
}

.card-preview {
  position: fixed;
  z-index: 1200;
  border-radius: 5%;
  overflow: hidden;
  box-shadow: 0 24px 50px -20px rgba(0, 0, 0, 0.75);
  pointer-events: none;
  background: var(--bg-soft);
}

.card-preview__img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

/* ── Pinned tray (compare multiple cards) ──────────────────────────────── */
.pinned-tray {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 1100;
  background: var(--bg-soft);
  background: color-mix(in srgb, var(--bg-soft) 92%, transparent);
  backdrop-filter: blur(8px);
  border-top: 1px solid var(--border);
  box-shadow: 0 -12px 30px -18px rgba(0, 0, 0, 0.6);
  padding: 10px 16px 14px;
}

.pinned-tray__head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}

.pinned-tray__clear {
  border: 1px solid var(--border);
  background: var(--bg);
  color: var(--text-dim);
  font: inherit;
  font-family: var(--mono);
  font-size: 10px;
  padding: 3px 9px;
  border-radius: 6px;
  cursor: pointer;
}

.pinned-tray__clear:hover {
  color: var(--text-h);
  border-color: var(--accent-border);
}

.pinned-tray__cards {
  display: flex;
  gap: 12px;
  overflow-x: auto;
  padding-bottom: 4px;
}

.pinned-card {
  position: relative;
  flex: 0 0 auto;
  width: 200px;
  border-radius: 5%;
  overflow: hidden;
  box-shadow: 0 12px 28px -16px rgba(0, 0, 0, 0.7);
}

.pinned-card__img {
  display: block;
  width: 100%;
  height: auto;
}

.pinned-card__close {
  position: absolute;
  top: 6px;
  right: 6px;
  width: 22px;
  height: 22px;
  border: none;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.62);
  color: #fff;
  font-size: 15px;
  line-height: 1;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}

.pinned-card__close:hover {
  background: var(--danger);
}

/* ── Deck manager (center, "3A") ───────────────────────────────────────── */

.app-layout--deck-detail {
  grid-template-columns: 240px 1fr;
}

.app-layout--deck-detail .chat-view,
.app-layout--deck-detail .deck-panel {
  display: none;
}

.deck-detail {
  overflow-y: auto;
  background: var(--bg);
}

body.has-pinned-tray .deck-detail {
  padding-bottom: 354px;
}

.deck-detail__topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 22px;
  background: var(--bg-soft);
}

.deck-detail__topbar-left {
  display: flex;
  align-items: center;
  gap: 11px;
  min-width: 0;
  flex: 1;
}

.deck-detail__title {
  font-size: 15px;
  font-weight: 700;
  letter-spacing: -0.01em;
  flex: 0 0 auto;
  cursor: pointer;
}

.deck-detail__title:hover {
  color: var(--accent);
}

.deck-detail__title-input {
  font-size: 15px;
  font-weight: 700;
  border: 1px solid var(--accent-border);
  border-radius: 7px;
  padding: 3px 8px;
  background: var(--bg);
  color: var(--text-h);
  font-family: inherit;
}

.deck-detail__identity-tag {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--label);
  flex: 0 0 auto;
}

.deck-detail__format-tag {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--text-dim);
  cursor: pointer;
}

.deck-detail__format-tag:hover {
  color: var(--text-h);
}

.deck-detail__commander-names {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--text-dim);
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: pointer;
}

.deck-detail__commander-names:hover {
  color: var(--text-h);
}

.deck-detail__header-actions {
  display: flex;
  gap: 6px;
  flex: 0 0 auto;
}

.deck-detail__editors {
  padding: 10px 22px;
  border-bottom: 1px solid var(--border);
}

.deck-detail__commander-edit,
.deck-detail__format-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

/* Styled to match the mono toolbar buttons (Export/Import) rather than a
   native OS dropdown: strip the platform chrome and draw our own caret. */
.deck-detail__select {
  appearance: none;
  -webkit-appearance: none;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 7px 30px 7px 12px;
  font: inherit;
  font-family: var(--mono);
  font-size: 11px;
  background-color: var(--bg-soft);
  color: var(--text);
  cursor: pointer;
  /* Chevron caret in --label color; encoded so it follows the token. */
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath d='M1 1l4 4 4-4' fill='none' stroke='%237a7c88' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 11px center;
}

.deck-detail__select:hover {
  background-color: var(--accent-bg);
  color: var(--text-h);
}

.deck-detail__select:focus {
  outline: none;
  border-color: var(--accent-border);
}

/* The dropdown list itself is OS-rendered; keep its rows legible in dark mode. */
.deck-detail__select option {
  font-family: var(--sans);
  color: var(--text-h);
  background: var(--bg-soft);
}

/* Dense metric strip */
.metric-strip {
  display: grid;
  grid-template-columns: 1.35fr 1fr 1fr 1fr 1fr;
  border-top: 1px solid var(--border);
  border-bottom: 1px solid var(--border);
  background: var(--bg-soft);
}

.metric-strip__cell {
  padding: 15px 18px;
  border-right: 1px solid var(--border);
}

.metric-strip__cell:last-child {
  border-right: none;
}

.metric-strip__cell .micro-label {
  font-size: 10px;
  letter-spacing: 0.12em;
  margin-bottom: 9px;
}

.metric-strip__cell--power {
  display: flex;
  align-items: center;
  gap: 13px;
}

.metric-strip__cell--power .micro-label {
  margin-bottom: 6px;
}

.metric-strip__power-tier {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-h);
}

.metric-strip__num,
.metric-strip__gold {
  font-family: var(--mono);
  font-weight: 700;
  font-size: 26px;
  line-height: 1;
}

.metric-strip__num { color: var(--text-h); }
.metric-strip__gold { color: var(--bracket); }

.metric-strip__bracket-num {
  margin-bottom: 12px;
}

.metric-strip__muted,
.metric-strip__muted-line {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--label);
  font-weight: 400;
}

.metric-strip__muted-line {
  margin-top: 8px;
}

.metric-strip__ok {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--ok);
  margin-top: 8px;
}

.metric-strip__why {
  border: none;
  background: transparent;
  color: var(--accent);
  font-family: var(--mono);
  font-size: 10px;
  padding: 0;
  margin-left: 8px;
  cursor: pointer;
}

.deck-detail__factors {
  margin: 12px 22px 0;
  padding: 12px 14px;
  background: var(--bg-soft);
  border: 1px solid var(--border);
  border-radius: 12px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--text);
  white-space: pre-line;
}

/* Stat grid */
.deck-detail__grid {
  display: grid;
  grid-template-columns: 1.15fr 1fr;
  gap: 14px;
  padding: 22px 26px 0;
}

.stat-card {
  background: var(--bg-soft);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 18px;
}

.stat-card > .micro-label {
  display: block;
  letter-spacing: 0.14em;
  margin-bottom: 16px;
}

.stat-card__rows {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.stat-card__row {
  display: flex;
  align-items: center;
  gap: 11px;
  font-size: 13px;
}

.stat-card__row-label {
  color: var(--text-h);
  flex: 1;
}

.stat-card__row-val {
  font-family: var(--mono);
  font-weight: 600;
  font-size: 12px;
  color: var(--text-dim);
}

.stat-card__types {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 9px 22px;
}

/* Sections below the grid */
.deck-detail__section {
  padding: 24px 26px 0;
}

.deck-detail__section > .micro-label {
  display: block;
  letter-spacing: 0.14em;
  margin-bottom: 14px;
}

.deck-detail__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.deck-detail__convo-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 12px;
}

.deck-detail__convo-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  border: 1px solid var(--border);
  background: var(--bg-soft);
  color: var(--text);
  font: inherit;
  font-size: 13px;
  padding: 9px 12px;
  border-radius: 9px;
  cursor: pointer;
  text-align: left;
}

.deck-detail__convo-item:hover {
  background: var(--accent-bg);
  border-color: var(--accent-border);
  color: var(--text-h);
}

.deck-detail__convo-title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.deck-detail__convo-date {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--label);
  flex-shrink: 0;
  margin-left: 12px;
}

.deck-detail__decklist {
  columns: 2;
  column-gap: 26px;
  padding-bottom: 26px;
}

.deck-detail__group {
  break-inside: avoid;
  margin-bottom: 16px;
}

.deck-detail__group-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 7px;
  margin-bottom: 6px;
  border-bottom: 1px solid var(--border);
}

.deck-detail__group-name {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.02em;
  color: var(--text-h);
}

.deck-detail__group-count {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--label);
}

.deck-detail__empty-stats {
  text-align: center;
  color: var(--text-dim);
  padding: 40px 0;
}

/* ── Import popover ("4B") ─────────────────────────────────────────────── */

.import-pop {
  position: fixed;
  z-index: 1000;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
  background: var(--bg-soft);
  border: 1px solid var(--border);
  border-radius: 12px;
  box-shadow: 0 24px 50px -20px rgba(0, 0, 0, 0.55);
  text-align: left;
}

.import-pop__head {
  font-family: var(--mono);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--label);
}

.import-pop__chips {
  display: flex;
  gap: 3px;
  padding: 3px;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: var(--bg);
}

.import-pop__chip {
  flex: 1;
  padding: 7px;
  font: inherit;
  font-size: 12.5px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-dim);
  cursor: pointer;
}

.import-pop__chip--active {
  background: var(--accent-bg);
  color: var(--accent);
  font-weight: 600;
}

.import-pop__body {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.import-pop__textarea,
.import-pop__ref {
  border: 1px solid var(--border);
  border-radius: 9px;
  padding: 10px 12px;
  font: inherit;
  font-size: 13px;
  width: 100%;
  box-sizing: border-box;
  background: var(--bg);
  color: var(--text-h);
}

.import-pop__textarea::placeholder,
.import-pop__ref::placeholder {
  color: var(--label);
}

.import-pop__ref:focus,
.import-pop__textarea:focus {
  outline: none;
  border-color: var(--accent-border);
}

.import-pop__textarea {
  resize: vertical;
  min-height: 90px;
}

.import-pop__go {
  width: 100%;
  padding: 10px;
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  border: 1px solid var(--accent-border);
  border-radius: 9px;
  background: var(--accent-bg);
  color: var(--accent);
  cursor: pointer;
}

.import-pop__go:hover {
  background: var(--accent);
  color: var(--on-accent);
}

.import-pop__go:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.import-pop__hint {
  font-size: 11.5px;
  color: var(--text-dim);
  line-height: 1.4;
}

.import-pop__error {
  font-size: 12px;
  color: var(--danger-fg);
  background: rgba(208, 64, 46, 0.08);
  border-radius: 6px;
  padding: 6px 8px;
}

.import-pop__error p {
  margin: 0;
}

/* ── Preferences modal ("4C") ──────────────────────────────────────────── */

.preferences-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}

.preferences-panel {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 22px;
  width: 400px;
  max-width: 90vw;
  box-shadow: 0 30px 60px -24px rgba(0, 0, 0, 0.6);
}

.preferences-panel__header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 18px;
}

.preferences-panel__header h3 {
  margin: 0;
  font-size: 18px;
  font-weight: 700;
}

.preferences-panel__close {
  width: 26px;
  height: 26px;
  border: 1px solid var(--border);
  background: var(--bg-soft);
  border-radius: 7px;
  font-size: 14px;
  cursor: pointer;
  color: var(--text-dim);
  line-height: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.preferences-panel__close:hover {
  color: var(--text-h);
}

.preferences-panel__field {
  display: block;
  margin-bottom: 15px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-h);
}

.preferences-panel__field select {
  display: block;
  width: 100%;
  margin-top: 7px;
  border: 1px solid var(--border);
  border-radius: 9px;
  padding: 10px 12px;
  font: inherit;
  font-size: 13px;
  background: var(--bg-soft);
  color: var(--text-h);
  cursor: pointer;
}

.preferences-panel__textarea {
  display: block;
  width: 100%;
  margin-top: 7px;
  border: 1px solid var(--border);
  border-radius: 9px;
  padding: 10px 12px;
  font: inherit;
  font-size: 12.5px;
  line-height: 1.5;
  background: var(--bg-soft);
  color: var(--text-h);
  resize: vertical;
  min-height: 70px;
  box-sizing: border-box;
}

.preferences-panel__save {
  border: 1px solid transparent;
  background: var(--accent);
  color: var(--on-accent);
  font: inherit;
  font-size: 13px;
  font-weight: 700;
  padding: 10px 24px;
  border-radius: 9px;
  cursor: pointer;
  margin-top: 4px;
}

.preferences-panel__save:hover {
  opacity: 0.9;
}

/* =========================================================================
   Mobile (≤ 768px)
   Single-column with bottom tab navigation.
   ========================================================================= */

@media (max-width: 768px) {
  /* ── Layout ─────────────────────────────────────────────────────────── */
  .app-layout {
    display: flex;
    flex-direction: column;
    height: 100dvh;
  }

  /* Hide desktop panels */
  .conversation-sidebar,
  .deck-panel {
    display: none;
  }

  .app-layout > .chat-view,
  .app-layout--deck-detail > .deck-detail {
    display: none;
  }

  /* Reveal mobile layout */
  .mobile-layout {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;
    /* Prevent iOS Safari from zooming when the address bar hides */
    height: 100%;
  }

  .mobile-content {
    flex: 1;
    overflow-y: auto;
    -webkit-overflow-scrolling: touch;
  }

  /* ── Mobile header ──────────────────────────────────────────────────── */
  .mobile-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    padding: 10px 14px;
    background: var(--bg-soft);
    border-bottom: 1px solid var(--border);
    flex: 0 0 auto;
  }

  .mobile-header__left {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
  }

  .mobile-header__back {
    border: none;
    background: transparent;
    color: var(--text-dim);
    font-size: 20px;
    padding: 4px 8px;
    cursor: pointer;
    flex: 0 0 auto;
  }

  .mobile-header__title {
    font-size: 14px;
    font-weight: 600;
    color: var(--text-h);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .mobile-header__actions {
    display: flex;
    gap: 6px;
    flex: 0 0 auto;
  }

  /* ── Bottom nav ─────────────────────────────────────────────────────── */
  .mobile-nav {
    display: flex;
    background: var(--bg-soft);
    border-top: 1px solid var(--border);
    padding: 6px 8px calc(6px + env(safe-area-inset-bottom, 0px));
    flex: 0 0 auto;
  }

  .mobile-nav__tab {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 2px;
    padding: 6px 4px;
    border: none;
    background: transparent;
    color: var(--label);
    font: inherit;
    font-size: 10px;
    font-family: var(--mono);
    cursor: pointer;
    position: relative;
  }

  .mobile-nav__tab--active {
    color: var(--accent);
  }

  .mobile-nav__icon {
    font-size: 18px;
  }

  .mobile-nav__label {
    font-size: 10px;
  }

  .mobile-nav__badge {
    position: absolute;
    top: 2px;
    right: calc(50% - 24px);
    background: var(--danger);
    color: #fff;
    font-size: 10px;
    font-family: var(--mono);
    min-width: 16px;
    height: 16px;
    line-height: 16px;
    text-align: center;
    border-radius: 8px;
    padding: 0 4px;
  }

  /* ── Mobile list views ──────────────────────────────────────────────── */
  .mobile-list {
    padding: 8px 0 calc(8px + env(safe-area-inset-bottom, 0px));
  }

  .mobile-list__empty {
    color: var(--text-dim);
    text-align: center;
    padding: 32px 16px;
    font-size: 13px;
  }

  .mobile-list__item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    width: 100%;
    padding: 14px 16px;
    border: none;
    border-bottom: 1px solid var(--border);
    background: transparent;
    color: var(--text);
    font: inherit;
    text-align: left;
    cursor: pointer;
  }

  .mobile-list__item--active {
    background: var(--bg-soft);
  }

  .mobile-list__item-title {
    font-size: 14px;
    color: var(--text-h);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .mobile-list__item-meta {
    display: flex;
    gap: 8px;
    font-family: var(--mono);
    font-size: 10px;
    color: var(--label);
    flex: 0 0 auto;
  }

  .mobile-list__item-deck {
    color: var(--text-dim);
  }

  .mobile-list__item-date {
    white-space: nowrap;
  }

  /* ── Mobile pins view ───────────────────────────────────────────────── */
  .mobile-pins {
    padding: 12px 12px calc(12px + env(safe-area-inset-bottom, 0px));
  }

  .mobile-pins__head {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 12px;
  }

  .mobile-pins__grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
  }

  .mobile-pins__card {
    position: relative;
    border-radius: 5%;
    overflow: hidden;
    box-shadow: 0 8px 20px -12px rgba(0, 0, 0, 0.6);
  }

  .mobile-pins__card img {
    display: block;
    width: 100%;
    height: auto;
  }

  /* ── ChatView ───────────────────────────────────────────────────────── */
  .mobile-content .chat-view {
    height: 100%;
  }

  .chat-view__messages {
    padding: 12px 12px;
    padding-bottom: calc(56px + env(safe-area-inset-bottom, 0px) + 12px);
  }

  .chat-view__input {
    padding: 10px 12px;
    padding-bottom: calc(10px + env(safe-area-inset-bottom, 0px));
  }

  /* ── DeckDetail ─────────────────────────────────────────────────────── */
  .deck-detail {
    padding-bottom: calc(56px + env(safe-area-inset-bottom, 0px));
  }

  .deck-detail__topbar {
    flex-wrap: wrap;
    gap: 8px;
    padding: 10px 14px;
  }

  .deck-detail__header-actions {
    width: 100%;
  }

  .deck-detail__editors {
    padding: 10px 14px;
  }

  .metric-strip {
    grid-template-columns: 1fr 1fr;
  }

  .deck-detail__grid {
    grid-template-columns: 1fr;
    padding: 16px 14px 0;
  }

  .deck-detail__decklist {
    columns: 1;
  }

  .deck-detail__section {
    padding: 16px 14px 0;
  }

  /* ── PreferencesPanel ───────────────────────────────────────────────── */
  .preferences-panel {
    width: 100%;
    max-width: 100%;
    border-radius: 0;
  }

  /* ── Pinned tray on mobile (desktop tray is hidden, pins live in tab) ── */
  .pinned-tray {
    display: none;
  }

  /* Smaller tray padding overrides (tray is hidden on mobile, but keep
     the overrides for any future use) */
  body.has-pinned-tray .deck-panel__cards,
  body.has-pinned-tray .deck-detail,
  body.has-pinned-tray .chat-view__messages {
    padding-bottom: 200px;
  }
}

/* Desktop only: hide mobile layout. Must come AFTER the mobile media query
   so it doesn't get overridden by the cascade (same specificity, later wins). */
@media (min-width: 769px) {
  .mobile-layout {
    display: none;
  }

  .mobile-header {
    display: none;
  }

  .mobile-nav {
    display: none;
  }
}

```

## `index.html`

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Familiar — MTG Deckbuilding</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

## `types/api.ts`

```ts
export interface DeckCard {
  name: string
  quantity: number
  category: string | null
  mana_value: number | null
  color_identity: string | null
  type_line: string | null
  tags: string[]
  notes: string | null
}

export interface DeckStats {
  mana_curve: { mv: string; count: number }[]
  avg_mv: number
  color_distribution: { color: string; count: number; pct: number }[]
  type_breakdown: { type: string; count: number; pct: number }[]
  land_count: number
  land_pct: number
  ramp_count: number
  draw_count: number
  removal_count: number
  total_cards: number
  power_level: number
  power_factors: string[]
  bracket: number
  bracket_factors: string[]
  untagged: string[]
  deficiencies: {
    category: string
    count: number
    target_low: number
    target_high: number
    status: 'LOW' | 'OK' | 'HIGH'
  }[]
}

export interface Deck {
  id: number
  name: string
  commander: string | null
  partner_commander: string | null
  notes: string | null
  power_level: string | null
  format: string
  conversation_id: number | null
  cards: DeckCard[]
}

export interface ImportResult extends Deck {
  _import?: { imported: number; errors: string[] }
  _source?: {
    provider: string
    url: string | null
    name: string
    commanders: string[]
  }
}

export interface DeckProvider {
  name: string
  display_name: string
  supports_fetch: boolean
  supports_push: boolean
}

export interface Conversation {
  id: number
  title: string
  deck_id: number | null
  created_at: string
  updated_at: string
}

export interface ChatMessage {
  id: number
  role: string
  text_content: string | null
  tool_calls: { id: string; name: string; arguments: Record<string, unknown> }[] | null
  tool_results: { call_id: string; content: string }[] | null
  sequence: number
  created_at: string
}

export interface DeckProposal {
  id: number
  deck_id: number
  status: 'pending' | 'approved' | 'denied'
  action: 'add' | 'remove' | 'set_commander'
  card_name: string | null
  quantity: number
  category: string | null
  commander_name: string | null
  reasoning: string
}

export interface ConversationDetail {
  conversation: Conversation
  messages: ChatMessage[]
  proposals: DeckProposal[]
}

export interface UserPreferences {
  preferred_bracket: string | null
  preferred_power: string | null
  budget: string | null
  rule0_notes: string | null
}

export type SseEvent =
  | { event: 'token'; data: { text: string } }
  | { event: 'tool_call'; data: { name: string; arguments: Record<string, unknown> } }
  | { event: 'deck_proposal'; data: { ok: boolean; summary: string; proposals: DeckProposal[] } }
  | { event: 'deck_updated'; data: Deck }
  | { event: 'done'; data: { message_id: number; conversation_id: number } }
  | { event: 'error'; data: { message: string } }
```

## `api/cardImage.ts`

```ts
// Scryfall's image-redirect endpoint serves a card image directly from a card
// name — no JSON round-trip, and Scryfall's CDN handles caching. Our decklist
// names are canonical (resolved via Scryfall on import), so `exact` is safe;
// double-faced cards return their front face at this endpoint.
//
// https://scryfall.com/docs/api/cards/named
export function cardImageUrl(name: string, version: 'normal' | 'large' = 'normal'): string {
  const q = encodeURIComponent(name)
  return `https://api.scryfall.com/cards/named?exact=${q}&format=image&version=${version}`
}
```

## `App.tsx`

```tsx
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api/client'
import { CardPinProvider, useCardPins } from './components/CardPinContext'
import { ChatView } from './components/ChatView'
import { Sidebar } from './components/ConversationSidebar'
import type { SidebarTab } from './components/ConversationSidebar'
import { DeckDetail } from './components/DeckDetail'
import { DeckPanel } from './components/DeckPanel'
import { MobileHeader } from './components/MobileHeader'
import type { MobileTab } from './components/MobileNav'
import { MobileNav } from './components/MobileNav'
import { PinnedTray } from './components/PinnedTray'
import { PreferencesPanel } from './components/PreferencesPanel'
import { useChatStream } from './hooks/useChatStream'
import type { ProposalBatch } from './hooks/useChatStream'
import { useDeck } from './hooks/useDeck'
import type { Conversation, Deck, DeckStats } from './types/api'
import './styles/global.css'

type MobileView = 'chats-list' | 'chat' | 'decks-list' | 'deck-detail' | 'pins'

function App() {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [decks, setDecks] = useState<Deck[]>([])
  const [sidebarTab, setSidebarTab] = useState<SidebarTab>('conversations')
  const [activeConversationId, setActiveConversationId] = useState<number | null>(null)
  const [deckStats, setDeckStats] = useState<DeckStats | null>(null)
  const [proposalBatches, setProposalBatches] = useState<ProposalBatch[]>([])
  const [prefsOpen, setPrefsOpen] = useState(false)
  const { deck, setDeck, loadDeck, clearDeck } = useDeck()
  const { messages, sendMessage, reset, activeTool, isStreaming, error } = useChatStream({
    onDeckUpdated: setDeck,
    onDeckProposal: (batch) => {
      setProposalBatches((prev) => {
        const cleaned = prev.map((b) => ({
          ...b,
          proposals: b.proposals.map((p) =>
            p.status === 'pending' ? { ...p, status: 'denied' as const } : p,
          ),
        }))
        return [...cleaned, batch]
      })
    },
    onConversationCreated: (id) => {
      setActiveConversationId(id)
      refreshConversations()
      refreshDecks()
    },
  })

  // ── Mobile view state ──────────────────────────────────────────────────
  const [mobileTab, setMobileTab] = useState<MobileTab>('chats')
  const [mobileView, setMobileView] = useState<MobileView>('chats-list')
  const tabLastView = useRef<Record<MobileTab, MobileView>>({
    chats: 'chats-list',
    decks: 'decks-list',
    pins: 'pins',
  })

  // Remember the last sub-view for each tab so switching tabs restores it.
  useEffect(() => {
    if (mobileView === 'chat' || mobileView === 'chats-list') {
      tabLastView.current.chats = mobileView
    } else if (mobileView === 'deck-detail' || mobileView === 'decks-list') {
      tabLastView.current.decks = mobileView
    } else {
      tabLastView.current.pins = mobileView
    }
  }, [mobileView])

  const refreshConversations = useCallback(() => {
    api.listConversations().then(setConversations).catch(() => {})
  }, [])

  const refreshDecks = useCallback(() => {
    api.listDecks().then(setDecks).catch(() => {})
  }, [])

  const loadStats = useCallback((deckId: number) => {
    api.getDeckStats(deckId).then(setDeckStats).catch(() => setDeckStats(null))
  }, [])

  useEffect(() => {
    refreshConversations()
    refreshDecks()
  }, [refreshConversations, refreshDecks])

  useEffect(() => {
    if (deck) {
      loadStats(deck.id)
    } else {
      setDeckStats(null)
    }
  }, [deck, loadStats])

  const handleSelectConversation = useCallback(
    async (id: number) => {
      setActiveConversationId(id)
      setSidebarTab('conversations')
      const detail = await api.getConversation(id)
      reset(
        detail.messages
          .filter((m) => m.role === 'user' || (m.role === 'assistant' && m.text_content))
          .map((m) => ({
            id: `server-${m.id}`,
            role: m.role as 'user' | 'assistant',
            text: m.text_content ?? '',
          })),
      )
      if (detail.conversation.deck_id) {
        loadDeck(detail.conversation.deck_id)
      } else {
        clearDeck()
      }
      const pending = detail.proposals.filter((p) => p.status === 'pending')
      if (pending.length > 0) {
        setProposalBatches([{ summary: 'Pending proposals', proposals: pending }])
      } else {
        setProposalBatches([])
      }
    },
    [reset, loadDeck, clearDeck],
  )

  const handleNewConversation = useCallback(() => {
    setActiveConversationId(null)
    reset([])
    clearDeck()
    setProposalBatches([])
  }, [reset, clearDeck])

  const handleDeleteConversation = useCallback(
    async (id: number) => {
      await api.deleteConversation(id).catch(() => {})
      if (id === activeConversationId) {
        handleNewConversation()
      }
      refreshConversations()
      refreshDecks()
    },
    [activeConversationId, handleNewConversation, refreshConversations, refreshDecks],
  )

  const navigateToDeckConversation = useCallback(
    async (deckId: number) => {
      const conv = await api.startDeckConversation(deckId)
      refreshDecks()
      setActiveConversationId(conv.id)
      setSidebarTab('conversations')
      reset([])
      loadDeck(deckId)
    },
    [refreshDecks, reset, loadDeck],
  )

  const handleSelectDeck = useCallback(
    async (d: Deck) => {
      loadDeck(d.id)
    },
    [loadDeck],
  )

  const handleDeckUpdated = useCallback(
    (updated: Deck) => {
      setDeck(updated)
      refreshDecks()
    },
    [setDeck, refreshDecks],
  )

  const handleNewDeck = useCallback(async () => {
    const d = await api.createDeck()
    refreshDecks()
    loadDeck(d.id)
  }, [refreshDecks, loadDeck])

  const handleDeleteDeck = useCallback(
    async (id: number) => {
      await api.deleteDeck(id).catch(() => {})
      if (deck?.id === id) {
        clearDeck()
      }
      refreshDecks()
    },
    [deck, clearDeck, refreshDecks],
  )

  const handleQuickStart = useCallback(
    async (deckId: number, prompt: string) => {
      const d = decks.find((d) => d.id === deckId)
      let convId: number
      if (d?.conversation_id) {
        convId = d.conversation_id
      } else {
        const conv = await api.startDeckConversation(deckId)
        refreshDecks()
        convId = conv.id
      }
      setActiveConversationId(convId)
      setSidebarTab('conversations')
      const detail = await api.getConversation(convId)
      if (detail.messages.length === 0) {
        loadDeck(deckId)
        reset([{ id: 'quickstart', role: 'user' as const, text: prompt }])
        sendMessage(convId, prompt)
      } else {
        handleSelectConversation(convId)
      }
    },
    [decks, refreshDecks, reset, sendMessage, handleSelectConversation, loadDeck],
  )

  const handleApplyProposal = useCallback(
    async (proposalId: number) => {
      const updatedDeck = await api.applyProposal(proposalId)
      setDeck(updatedDeck)
      setProposalBatches((prev) =>
        prev.map((batch) => ({
          ...batch,
          proposals: batch.proposals.map((p) =>
            p.id === proposalId ? { ...p, status: 'approved' as const } : p,
          ),
        })),
      )
      loadStats(updatedDeck.id)
    },
    [setDeck, loadStats],
  )

  const handleDenyProposal = useCallback(
    async (proposalId: number) => {
      await api.denyProposal(proposalId)
      setProposalBatches((prev) =>
        prev.map((batch) => ({
          ...batch,
          proposals: batch.proposals.map((p) =>
            p.id === proposalId ? { ...p, status: 'denied' as const } : p,
          ),
        })),
      )
    },
    [],
  )

  const handleSend = useCallback(
    (text: string) => {
      sendMessage(activeConversationId ?? 'new', text)
    },
    [activeConversationId, sendMessage],
  )

  // ── Mobile handlers ────────────────────────────────────────────────────

  const handleMobileSelectConv = useCallback(
    async (id: number) => {
      await handleSelectConversation(id)
      setMobileView('chat')
    },
    [handleSelectConversation],
  )

  const handleMobileSelectDeck = useCallback(
    async (d: Deck) => {
      loadDeck(d.id)
      setMobileView('deck-detail')
    },
    [loadDeck],
  )

  const handleMobileNewConversation = useCallback(() => {
    handleNewConversation()
    setMobileView('chat')
  }, [handleNewConversation])

  const handleMobileTabChange = useCallback((tab: MobileTab) => {
    setMobileTab(tab)
    setMobileView(tabLastView.current[tab])
  }, [])

  const pendingCount =
    proposalBatches.reduce((sum, b) => sum + b.proposals.filter((p) => p.status === 'pending').length, 0)

  // ── Derived state ──────────────────────────────────────────────────────

  const showDeckDetail = sidebarTab === 'decks' && deck !== null
  const cardNames = deck?.cards.map((c) => c.name) ?? []

  return (
    <CardPinProvider>
      <div className={`app-layout ${showDeckDetail ? 'app-layout--deck-detail' : ''}`}>
        {/* ── Desktop layout ────────────────────────────────────────────── */}
        <Sidebar
          tab={sidebarTab}
          onTabChange={setSidebarTab}
          conversations={conversations}
          activeId={activeConversationId}
          onSelect={handleSelectConversation}
          onNew={handleNewConversation}
          onDelete={handleDeleteConversation}
          decks={decks}
          activeDeckId={deck?.id ?? null}
          onSelectDeck={handleSelectDeck}
          onNewDeck={handleNewDeck}
          onDeleteDeck={handleDeleteDeck}
          onOpenPreferences={() => setPrefsOpen(true)}
        />
        {showDeckDetail ? (
          <DeckDetail
            deck={deck}
            stats={deckStats}
            onDeckUpdated={handleDeckUpdated}
            onStartConversation={navigateToDeckConversation}
            onSelectConversation={handleSelectConversation}
            onQuickStart={handleQuickStart}
          />
        ) : (
          <>
            <ChatView
              messages={messages}
              activeTool={activeTool}
              isStreaming={isStreaming}
              error={error}
              onSend={handleSend}
              proposalBatches={proposalBatches}
              onApplyProposal={handleApplyProposal}
              onDenyProposal={handleDenyProposal}
              cardNames={cardNames}
            />
            <DeckPanel deck={deck} stats={deckStats} onDeckUpdated={handleDeckUpdated} onStartConversation={navigateToDeckConversation} onSelectConversation={handleSelectConversation} />
          </>
        )}

        {/* ── Mobile layout (hidden on desktop via CSS) ─────────────────── */}
        <div className="mobile-layout">
          <MobileHeader
            showBack={mobileView === 'chat' || mobileView === 'deck-detail'}
            onBack={() => setMobileView(mobileTab === 'chats' ? 'chats-list' : 'decks-list')}
            title={
              mobileView === 'chats-list' ? 'Chats' :
              mobileView === 'decks-list' ? 'Decks' :
              mobileView === 'pins' ? 'Pinned' :
              undefined
            }
          >
            {mobileView === 'chats-list' && (
              <button className="btn btn--ghost btn--mono" onClick={handleMobileNewConversation}>
                + New
              </button>
            )}
            {mobileView === 'decks-list' && (
              <button className="btn btn--ghost btn--mono" onClick={handleNewDeck}>
                + New
              </button>
            )}
          </MobileHeader>

          <div className="mobile-content">
            {mobileView === 'chats-list' && (
              <div className="mobile-list">
                {conversations.length === 0 && (
                  <p className="mobile-list__empty">No conversations yet. Start a new one.</p>
                )}
                {conversations.map((c) => (
                  <button
                    key={c.id}
                    className={`mobile-list__item ${c.id === activeConversationId ? 'mobile-list__item--active' : ''}`}
                    onClick={() => handleMobileSelectConv(c.id)}
                  >
                    <span className="mobile-list__item-title">{c.title || 'Untitled'}</span>
                    <span className="mobile-list__item-date">
                      {new Date(c.updated_at).toLocaleDateString()}
                    </span>
                  </button>
                ))}
              </div>
            )}

            {mobileView === 'chat' && (
              <ChatView
                messages={messages}
                activeTool={activeTool}
                isStreaming={isStreaming}
                error={error}
                onSend={handleSend}
                proposalBatches={proposalBatches}
                onApplyProposal={handleApplyProposal}
                onDenyProposal={handleDenyProposal}
                cardNames={cardNames}
              />
            )}

            {mobileView === 'decks-list' && (
              <div className="mobile-list">
                {decks.length === 0 && (
                  <p className="mobile-list__empty">No decks yet. Create one to get started.</p>
                )}
                {decks.map((d) => (
                  <button
                    key={d.id}
                    className={`mobile-list__item ${d.id === deck?.id ? 'mobile-list__item--active' : ''}`}
                    onClick={() => handleMobileSelectDeck(d)}
                  >
                    <span className="mobile-list__item-title">{d.name}</span>
                    {d.commander && <span className="mobile-list__item-deck">{d.commander}</span>}
                  </button>
                ))}
              </div>
            )}

            {mobileView === 'deck-detail' && deck && (
              <DeckDetail
                deck={deck}
                stats={deckStats}
                onDeckUpdated={handleDeckUpdated}
                onStartConversation={navigateToDeckConversation}
                onSelectConversation={handleSelectConversation}
                onQuickStart={handleQuickStart}
              />
            )}

            {mobileView === 'pins' && <MobilePinsView />}
          </div>

          <MobileNav
            activeTab={mobileTab}
            onTabChange={handleMobileTabChange}
            proposalCount={pendingCount}
          />
        </div>

        <PreferencesPanel open={prefsOpen} onClose={() => setPrefsOpen(false)} />
      </div>
      <PinnedTray />
    </CardPinProvider>
  )
}

/** Full-screen pinned-card view for the mobile Pins tab. */
function MobilePinsView() {
  const { pinned, unpin, clear } = useCardPins()

  if (pinned.length === 0) {
    return (
      <div className="mobile-list">
        <p className="mobile-list__empty">
          No cards pinned yet. Tap a card name in a message or deck list to pin it for comparison.
        </p>
      </div>
    )
  }

  return (
    <div className="mobile-pins">
      <div className="mobile-pins__head">
        <span className="micro-label">Pinned · {pinned.length}</span>
        <button className="pinned-tray__clear" onClick={clear}>Clear all</button>
      </div>
      <div className="mobile-pins__grid">
        {pinned.map((name) => (
          <div key={name} className="mobile-pins__card">
            <img
              src={`https://api.scryfall.com/cards/named?exact=${encodeURIComponent(name)}&format=image`}
              alt={name}
              loading="lazy"
            />
            <button
              className="pinned-card__close"
              onClick={() => unpin(name)}
              title={`Unpin ${name}`}
              aria-label={`Unpin ${name}`}
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}

export default App
```

## `components/ConversationSidebar.tsx`

```tsx
import type { Conversation, Deck } from '../types/api'

export type SidebarTab = 'conversations' | 'decks'

interface SidebarProps {
  tab: SidebarTab
  onTabChange: (tab: SidebarTab) => void
  conversations: Conversation[]
  activeId: number | null
  onSelect: (id: number) => void
  onNew: () => void
  onDelete: (id: number) => void
  decks: Deck[]
  activeDeckId: number | null
  onSelectDeck: (deck: Deck) => void
  onNewDeck: () => void
  onDeleteDeck: (id: number) => void
  onOpenPreferences: () => void
}

export function Sidebar({
  tab,
  onTabChange,
  conversations,
  activeId,
  onSelect,
  onNew,
  onDelete,
  decks,
  activeDeckId,
  onSelectDeck,
  onNewDeck,
  onDeleteDeck,
  onOpenPreferences,
}: SidebarProps) {
  return (
    <div className="conversation-sidebar">
      <div className="sidebar-brand">
        <img className="sidebar-brand__logo" src="/favicon.svg" alt="" width={20} height={20} />
        <span className="sidebar-brand__name">Familiar</span>
        <button className="sidebar-prefs-btn" onClick={onOpenPreferences} title="Preferences">
          ⚙
        </button>
      </div>
      <div className="segmented">
        <button
          className={`segmented__seg ${tab === 'conversations' ? 'segmented__seg--active' : ''}`}
          onClick={() => onTabChange('conversations')}
        >
          Conversations
        </button>
        <button
          className={`segmented__seg ${tab === 'decks' ? 'segmented__seg--active' : ''}`}
          onClick={() => onTabChange('decks')}
        >
          Decks
        </button>
      </div>

      {tab === 'conversations' ? (
        <>
          <button className="btn btn--ghost conversation-sidebar__new" onClick={onNew}>
            + New conversation
          </button>
          <div className="micro-label conversation-sidebar__recent">Recent</div>
          <div className="conversation-sidebar__list">
            {conversations.map((c) => (
              <div
                key={c.id}
                className={`conversation-sidebar__item ${c.id === activeId ? 'conversation-sidebar__item--active' : ''}`}
              >
                <button className="conversation-sidebar__item-title" onClick={() => onSelect(c.id)}>
                  {c.title}
                </button>
                <button
                  className="conversation-sidebar__item-delete"
                  title="Delete conversation"
                  onClick={(e) => {
                    e.stopPropagation()
                    if (window.confirm(`Delete "${c.title}"? This can't be undone.`)) {
                      onDelete(c.id)
                    }
                  }}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        </>
      ) : (
        <>
          <button className="btn btn--ghost conversation-sidebar__new" onClick={onNewDeck}>
            + New deck
          </button>
          <div className="micro-label conversation-sidebar__recent">Decks</div>
          <div className="conversation-sidebar__list">
            {decks.map((d) => (
              <div
                key={d.id}
                className={`conversation-sidebar__item ${d.id === activeDeckId ? 'conversation-sidebar__item--active' : ''}`}
              >
                <button
                  className="conversation-sidebar__item-title"
                  onClick={() => onSelectDeck(d)}
                >
                  <span className="sidebar-deck-name">{d.name}</span>
                  {d.commander && (
                    <span className="sidebar-deck-commander">{d.commander}</span>
                  )}
                </button>
                <button
                  className="conversation-sidebar__item-delete"
                  title="Delete deck"
                  onClick={(e) => {
                    e.stopPropagation()
                    if (window.confirm(`Delete "${d.name}"? This can't be undone.`)) {
                      onDeleteDeck(d.id)
                    }
                  }}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
```

## `components/ChatView.tsx`

```tsx
import { useCallback, useEffect, useRef, useState } from 'react'
import type { DisplayMessage, ProposalBatch } from '../hooks/useChatStream'
import { MessageBubble } from './MessageBubble'
import { ProposalCard } from './ProposalCard'
import { ToolActivityIndicator } from './ToolActivityIndicator'

interface ChatViewProps {
  messages: DisplayMessage[]
  activeTool: string | null
  isStreaming: boolean
  error: string | null
  onSend: (text: string) => void
  proposalBatches: ProposalBatch[]
  onApplyProposal: (id: number) => Promise<void>
  onDenyProposal: (id: number) => Promise<void>
  cardNames: string[]
}

export function ChatView({ messages, activeTool, isStreaming, error, onSend, proposalBatches, onApplyProposal, onDenyProposal, cardNames }: ChatViewProps) {
  const [draft, setDraft] = useState('')
  const [dismissedBatches, setDismissedBatches] = useState<Set<number>>(new Set())
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [messages, activeTool, proposalBatches])

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const text = draft.trim()
    if (!text || isStreaming) return
    setDraft('')
    onSend(text)
  }

  const handleContinue = useCallback((batchIndex: number) => {
    setDismissedBatches((prev) => new Set(prev).add(batchIndex))
    onSend("I've reviewed the proposals. Let's continue.")
  }, [onSend])

  return (
    <div className="chat-view">
      <div className="chat-view__messages" ref={scrollRef}>
        {messages.length === 0 && (
          <div className="chat-view__empty">
            Tell Familiar about a commander idea — even a weird one.
          </div>
        )}
        {messages.map((m) => (
          <MessageBubble key={m.id} message={m} cardNames={cardNames} />
        ))}
        {proposalBatches.map((batch, bi) => {
          const pending = batch.proposals.filter((p) => p.status === 'pending')
          const resolved = batch.proposals.filter((p) => p.status !== 'pending')
          const allResolved = pending.length === 0
          const isDismissed = dismissedBatches.has(bi)
          const isLastBatch = bi === proposalBatches.length - 1

          return (
            <div key={`batch-${bi}`} className={`proposal-batch ${allResolved ? 'proposal-batch--resolved' : ''}`}>
              <div className="proposal-batch__summary">{batch.summary}</div>

              {resolved.length > 0 && (
                <div className="proposal-batch__resolved-summary">
                  {resolved.filter((p) => p.status === 'approved').length > 0 && (
                    <span className="proposal-batch__tally proposal-batch__tally--approved">
                      ✓ {resolved.filter((p) => p.status === 'approved').length} approved
                    </span>
                  )}
                  {resolved.filter((p) => p.status === 'denied').length > 0 && (
                    <span className="proposal-batch__tally proposal-batch__tally--denied">
                      ✗ {resolved.filter((p) => p.status === 'denied').length} denied
                    </span>
                  )}
                </div>
              )}

              {pending.map((p) => (
                <ProposalCard
                  key={p.id}
                  proposal={p}
                  onApply={onApplyProposal}
                  onDeny={onDenyProposal}
                />
              ))}

              {allResolved && isLastBatch && !isStreaming && !isDismissed && (
                <button
                  className="proposal-batch__continue"
                  onClick={() => handleContinue(bi)}
                >
                  Done reviewing →
                </button>
              )}
            </div>
          )
        })}
        {activeTool && <ToolActivityIndicator toolName={activeTool} />}
        {error && <div className="chat-view__error">{error}</div>}
      </div>
      <form className="chat-view__input" onSubmit={handleSubmit}>
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              handleSubmit(e)
            }
          }}
          placeholder="Brainstorm a deck idea…"
          disabled={isStreaming}
        />
        <button type="submit" disabled={isStreaming || !draft.trim()}>
          Send
        </button>
      </form>
    </div>
  )
}
```

## `components/MessageBubble.tsx`

```tsx
import type { ComponentProps } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { DisplayMessage } from '../hooks/useChatStream'
import { CardPreview } from './CardPreview'

// Regex-escape special characters for building a card-name pattern.
function escapeRx(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

// Wrap every occurrence of a known card name in the text as a special markdown
// link so ReactMarkdown renders it; we then intercept those links with a
// custom `a` component that renders CardPreview instead.
function injectCardLinks(text: string, cardNames: string[]): string {
  const unique = [...new Set(cardNames)]
  // Sort descending by length so longer names match before shorter ones
  // (e.g. "Jace, the Mind Sculptor" before "Jace").
  unique.sort((a, b) => b.length - a.length)
  const escaped = unique.map(escapeRx)
  const pattern = new RegExp(`\\b(${escaped.join('|')})\\b`, 'gi')
  return text.replace(pattern, (_match, captured: string) => {
    // Use the actual captured casing for display, but a stable fragment id.
    return `[${captured}](#pin)`
  })
}

interface MessageBubbleProps {
  message: DisplayMessage
  cardNames: string[]
}

export function MessageBubble({ message, cardNames }: MessageBubbleProps) {
  if (message.role === 'user') {
    const text = cardNames.length > 0 ? injectCardLinks(message.text, cardNames) : message.text
    return (
      <div className="message message--user">
        <div className="message__text">
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ a: CardLink }}>
            {text}
          </ReactMarkdown>
        </div>
      </div>
    )
  }
  const text = cardNames.length > 0 ? injectCardLinks(message.text, cardNames) : message.text
  return (
    <div className="message message--assistant">
      <div className="message__author">Familiar</div>
      <div className="message__text">
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ a: CardLink }}>
          {text}
        </ReactMarkdown>
      </div>
    </div>
  )
}

// Renders markdown links whose href is "#pin" as CardPreview; all other links
// pass through as normal <a> tags.
function CardLink(props: ComponentProps<'a'>) {
  if (props.href === '#pin') {
    const name = typeof props.children === 'string' ? props.children : String(props.children ?? '')
    return <CardPreview name={name} />
  }
  return <a {...props} />
}
```

## `components/ToolActivityIndicator.tsx`

```tsx
const TOOL_LABELS: Record<string, string> = {
  scryfall_search: 'Searching Scryfall…',
  scryfall_card_by_name: 'Looking up a card…',
  scryfall_card_collection: 'Fetching cards…',
  edhrec_commander_recs: 'Checking EDHREC recommendations…',
  edhrec_card_synergy: 'Checking card synergy…',
  deck_get_current: 'Reading the deck…',
  deck_add_card: 'Adding a card to the deck…',
  deck_remove_card: 'Removing a card from the deck…',
  deck_set_commander: 'Setting the commander…',
  deck_update_notes: 'Updating deck notes…',
}

export function ToolActivityIndicator({ toolName }: { toolName: string }) {
  return (
    <div className="tool-activity">
      <span className="tool-activity__dot" />
      {TOOL_LABELS[toolName] ?? `Running ${toolName}…`}
    </div>
  )
}
```

## `components/ProposalCard.tsx`

```tsx
import { useState } from 'react'
import type { DeckProposal } from '../types/api'

interface ProposalCardProps {
  proposal: DeckProposal
  onApply: (id: number) => Promise<void>
  onDeny: (id: number) => Promise<void>
}

export function ProposalCard({ proposal, onApply, onDeny }: ProposalCardProps) {
  const [loading, setLoading] = useState(false)

  const handleApply = async () => {
    setLoading(true)
    try { await onApply(proposal.id) } finally { setLoading(false) }
  }

  const handleDeny = async () => {
    setLoading(true)
    try { await onDeny(proposal.id) } finally { setLoading(false) }
  }

  const name = proposal.card_name ?? proposal.commander_name
  const isRemove = proposal.action === 'remove'

  if (proposal.status !== 'pending') {
    const approved = proposal.status === 'approved'
    return (
      <div className="proposal-swap proposal-swap--resolved">
        <span className={`proposal-chip ${approved ? 'proposal-chip--add' : 'proposal-chip--deny'}`}>
          {approved ? '✓' : '✗'}
        </span>
        <span className="proposal-swap__name">{name}</span>
        <span className="proposal-swap__note">{approved ? 'Applied' : 'Denied'}</span>
      </div>
    )
  }

  return (
    <div className="proposal-card">
      <div className="proposal-card__head">
        <span className="proposal-card__label">Proposed change</span>
        <span className="proposal-card__meta">
          {proposal.action === 'set_commander'
            ? 'commander'
            : `${proposal.quantity} card${proposal.quantity === 1 ? '' : 's'}`}
        </span>
      </div>
      <div className="proposal-swap">
        <span className={`proposal-chip ${isRemove ? 'proposal-chip--remove' : 'proposal-chip--add'}`}>
          {isRemove ? '−' : '+'}
        </span>
        <span className="proposal-swap__name">{name}</span>
        {proposal.category && <span className="proposal-swap__note">{proposal.category}</span>}
      </div>
      {proposal.reasoning && <div className="proposal-card__rationale">{proposal.reasoning}</div>}
      <div className="proposal-card__actions">
        <button className="btn btn--primary" onClick={handleApply} disabled={loading}>
          {loading ? '…' : 'Approve'}
        </button>
        <button className="btn btn--secondary" onClick={handleDeny} disabled={loading}>
          {loading ? '…' : 'Deny'}
        </button>
      </div>
    </div>
  )
}
```

## `components/DeckPanel.tsx`

```tsx
import { useCallback, useRef, useState } from 'react'
import { api } from '../api/client'
import type { Deck, DeckStats } from '../types/api'
import { DeckCardRow } from './DeckCardRow'
import { commanderColorIdentities, CommanderPip } from './deckViz'
import { ImportPanel } from './ImportPanel'

const COLOR_NAMES: Record<string, string> = {
  W: 'white', U: 'blue', B: 'black', R: 'red', G: 'green',
}

function identityLabel(colors: string): string {
  const c = colors.replace(/[^WUBRG]/g, '')
  if (c.length === 0) return 'colorless'
  if (c.length === 1) return `mono-${COLOR_NAMES[c] ?? c.toLowerCase()}`
  return c.split('').join('')
}

interface DeckPanelProps {
  deck: Deck | null
  stats: DeckStats | null
  onDeckUpdated: (deck: Deck) => void
  onStartConversation: (deckId: number) => void
  onSelectConversation: (conversationId: number) => void
}

export function DeckPanel({ deck, stats, onDeckUpdated, onStartConversation, onSelectConversation }: DeckPanelProps) {
  const [renaming, setRenaming] = useState(false)
  const [draftName, setDraftName] = useState('')
  const [showImport, setShowImport] = useState(false)
  const [copied, setCopied] = useState(false)
  const importBtnRef = useRef<HTMLButtonElement>(null)

  const startRename = useCallback(() => {
    if (!deck) return
    setDraftName(deck.name)
    setRenaming(true)
  }, [deck])

  const commitRename = useCallback(async () => {
    if (!deck || !draftName.trim()) {
      setRenaming(false)
      return
    }
    const updated = await api.updateDeck(deck.id, { name: draftName.trim() })
    onDeckUpdated(updated)
    setRenaming(false)
  }, [deck, draftName, onDeckUpdated])

  const handleExport = useCallback(() => {
    if (!deck) return
    const grouped = new Map<string, typeof deck.cards>()
    for (const card of deck.cards) {
      const key = card.category ?? 'Uncategorized'
      grouped.set(key, [...(grouped.get(key) ?? []), card])
    }
    const lines: string[] = []
    // Commander(s) first — no blank line after: a blank line reads as a
    // sideboard/maybeboard separator to Archidekt and Cockatrice, which
    // would dump the entire 99 into the sideboard.
    for (const c of deck.cards) {
      if (c.category === 'Commander') {
        lines.push(`${c.quantity} ${c.name}`)
      }
    }
    // Rest of the deck
    for (const [category, cards] of grouped.entries()) {
      if (category === 'Commander') continue
      for (const c of cards) {
        lines.push(`${c.quantity} ${c.name}`)
      }
    }
    navigator.clipboard.writeText(lines.join('\n').trim())
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }, [deck])

  const handleOpenConversation = useCallback(() => {
    if (!deck) return
    onStartConversation(deck.id)
  }, [deck, onStartConversation])

  if (!deck) {
    return (
      <div className="deck-panel deck-panel--empty">
        <p>No deck yet. Start a conversation to begin building one.</p>
      </div>
    )
  }

  const grouped = new Map<string, typeof deck.cards>()
  for (const card of deck.cards) {
    const raw = card.category || 'Uncategorized'
    const key = raw.charAt(0).toUpperCase() + raw.slice(1).toLowerCase()
    const existing = grouped.get(key)
    if (existing) {
      existing.push(card)
    } else {
      grouped.set(key, [card])
    }
  }

  const commanderIdentities = commanderColorIdentities(deck)
  const combinedIdentity =
    commanderIdentities.map((id) => id ?? '').join('').replace(/[^WUBRG]/g, '') || null
  const total = deck.cards.reduce((s, c) => s + c.quantity, 0)
  const subline = [deck.commander, deck.partner_commander].filter(Boolean).join(' / ')

  return (
    <div className="deck-panel">
      <div className="deck-panel__header">
        {renaming ? (
          <input
            className="deck-panel__title-input"
            value={draftName}
            onChange={(e) => setDraftName(e.target.value)}
            onBlur={commitRename}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commitRename()
              if (e.key === 'Escape') setRenaming(false)
            }}
            autoFocus
          />
        ) : (
          <div className="deck-panel__title-row">
            <CommanderPip identities={commanderIdentities} />
            <h2 className="deck-panel__title" onClick={startRename} title="Click to rename">
              {deck.name}
            </h2>
          </div>
        )}
        {subline && (
          <div className="deck-panel__subline">
            {subline}
            {combinedIdentity && ` · ${identityLabel(combinedIdentity)}`}
          </div>
        )}

        <div className="deck-panel__actions">
          <button className="btn btn--secondary btn--mono" onClick={handleExport}>
            {copied ? 'Copied!' : 'Export'}
          </button>
          <button
            ref={importBtnRef}
            className={`btn btn--secondary btn--mono ${showImport ? 'btn--active' : ''}`}
            onClick={() => setShowImport((v) => !v)}
          >
            Import
          </button>
          {showImport && (
            <ImportPanel
              deckId={deck.id}
              anchorRef={importBtnRef}
              onImported={onDeckUpdated}
              onClose={() => setShowImport(false)}
            />
          )}
          {deck.conversation_id ? (
            <button className="btn btn--ghost btn--mono deck-panel__open" onClick={() => onSelectConversation(deck.conversation_id!)}>
              Open →
            </button>
          ) : (
            <button className="btn btn--ghost btn--mono deck-panel__open" onClick={handleOpenConversation}>
              Open →
            </button>
          )}
        </div>
      </div>

      <div className="deck-panel__strip">
        <div className="deck-panel__strip-cell">
          <div className="micro-label">Power</div>
          <span className="deck-panel__strip-val deck-panel__strip-val--power">
            {stats?.power_level ?? '—'}
          </span>
          <span className="deck-panel__strip-unit">/10</span>
        </div>
        <div className="deck-panel__strip-cell">
          <div className="micro-label">Bracket</div>
          <span className="deck-panel__strip-val deck-panel__strip-val--bracket">
            {stats?.bracket ?? '—'}
          </span>
          <span className="deck-panel__strip-unit">/5</span>
        </div>
        <div className="deck-panel__strip-cell">
          <div className="micro-label">Cards</div>
          <span className="deck-panel__strip-val deck-panel__strip-val--cards">{total}</span>
        </div>
      </div>

      <div className="deck-panel__cards">
        {[...grouped.entries()].map(([category, cards]) => (
          <div key={category} className="deck-panel__group">
            <div className="deck-panel__group-head">
              <span className="micro-label">{category}</span>
              <span className="deck-panel__group-count">
                {cards.reduce((s, c) => s + c.quantity, 0)}
              </span>
            </div>
            {cards.map((c) => (
              <DeckCardRow key={c.name} card={c} />
            ))}
          </div>
        ))}
        {deck.notes && (
          <div className="deck-panel__group">
            <div className="deck-panel__group-head">
              <span className="micro-label">Notes</span>
            </div>
            <p className="deck-panel__notes">{deck.notes}</p>
          </div>
        )}
      </div>
    </div>
  )
}
```

## `components/DeckDetail.tsx`

```tsx
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { Conversation, Deck, DeckStats } from '../types/api'
import { DeckCardRow } from './DeckCardRow'
import {
  BracketDiamonds,
  commanderColorIdentities,
  CommanderPip,
  ManaCurve,
  ManaPip,
  PowerDial,
  RoleBalance,
} from './deckViz'
import { ImportPanel } from './ImportPanel'

const COLOR_NAMES: Record<string, string> = {
  W: 'White', U: 'Blue', B: 'Black', R: 'Red', G: 'Green',
}

const POWER_TIER = (pl: number): string => {
  if (pl >= 8) return 'cEDH-adjacent'
  if (pl >= 7) return 'High-power'
  if (pl >= 5) return 'Focused'
  if (pl >= 3) return 'Casual'
  return 'Precon-level'
}

const FORMAT_OPTIONS: { value: string; label: string }[] = [
  { value: 'commander', label: 'Commander/EDH' },
  { value: 'brawl', label: 'Brawl' },
  { value: 'oathbreaker', label: 'Oathbreaker' },
  { value: 'standard', label: 'Standard' },
  { value: 'modern', label: 'Modern' },
  { value: 'pioneer', label: 'Pioneer' },
  { value: 'pauper', label: 'Pauper' },
  { value: 'legacy', label: 'Legacy' },
  { value: 'vintage', label: 'Vintage' },
  { value: 'premodern', label: 'Premodern' },
]

interface DeckDetailProps {
  deck: Deck
  stats: DeckStats | null
  onDeckUpdated: (deck: Deck) => void
  onStartConversation: (deckId: number) => void
  onSelectConversation: (conversationId: number) => void
  onQuickStart: (deckId: number, prompt: string) => void
}

function buildContextPrompt(deck: Deck, stats: DeckStats | null, focus: string): string {
  const lines: string[] = []
  lines.push(`I'm working on my deck "${deck.name}".`)

  if (deck.commander) {
    lines.push(`Commander: ${deck.commander}${deck.partner_commander ? ' / ' + deck.partner_commander : ''}.`)
  }
  const formatLabel = FORMAT_OPTIONS.find((f) => f.value === deck.format)?.label ?? deck.format
  lines.push(`Format: ${formatLabel}.`)

  const byCategory = new Map<string, typeof deck.cards>()
  for (const c of deck.cards) {
    const key = c.category ?? 'Uncategorized'
    byCategory.set(key, [...(byCategory.get(key) ?? []), c])
  }
  lines.push('')
  lines.push('## Current decklist')
  for (const [cat, cards] of byCategory.entries()) {
    lines.push(`//${cat}`)
    for (const c of cards) {
      lines.push(`${c.quantity} ${c.name}`)
    }
    lines.push('')
  }

  if (stats && stats.total_cards > 0) {
    lines.push('## Stats')
    lines.push(`- Cards: ${stats.total_cards}`)
    lines.push(`- Avg mana value (nonland): ${stats.avg_mv}`)
    lines.push(`- Lands: ${stats.land_count} (${stats.land_pct}%)`)
    if (stats.ramp_count) lines.push(`- Ramp: ${stats.ramp_count}`)
    if (stats.draw_count) lines.push(`- Draw: ${stats.draw_count}`)
    if (stats.removal_count) lines.push(`- Removal: ${stats.removal_count}`)
    if (stats.color_distribution.length > 0) {
      const colorSummary = stats.color_distribution
        .map((c) => `${COLOR_NAMES[c.color] ?? c.color} (${c.pct}%)`)
        .join(', ')
      lines.push(`- Color distribution: ${colorSummary}`)
    }
    if (stats.type_breakdown.length > 0) {
      const typeSummary = stats.type_breakdown
        .filter((t) => t.count > 0)
        .map((t) => `${t.type}: ${t.count}`)
        .join(', ')
      lines.push(`- Type breakdown: ${typeSummary}`)
    }
    lines.push(`- Power level: ${stats.power_level}/10`)
    lines.push(`- Commander bracket: ${stats.bracket}/5`)
  }

  lines.push('')
  lines.push(focus)

  return lines.join('\n')
}

export function DeckDetail({ deck, stats, onDeckUpdated, onStartConversation, onSelectConversation, onQuickStart }: DeckDetailProps) {
  const [renaming, setRenaming] = useState(false)
  const [draftName, setDraftName] = useState('')
  const [showImport, setShowImport] = useState(false)
  const [copied, setCopied] = useState(false)
  const importBtnRef = useRef<HTMLButtonElement>(null)
  const [expandedBreakdown, setExpandedBreakdown] = useState<'power' | 'bracket' | null>(null)
  const [deckConversations, setDeckConversations] = useState<Conversation[]>([])
  const [editingCommanders, setEditingCommanders] = useState(false)
  const [editingFormat, setEditingFormat] = useState(false)
  const [draftCommander, setDraftCommander] = useState<string>('')
  const [draftPartner, setDraftPartner] = useState<string>('')
  const [draftFormat, setDraftFormat] = useState<string>('')
  const [savingCommanders, setSavingCommanders] = useState(false)
  const [savingFormat, setSavingFormat] = useState(false)

  useEffect(() => {
    api.listDeckConversations(deck.id).then(setDeckConversations).catch(() => {})
  }, [deck.id])

  // Cards eligible to be a commander: legendary creatures/planeswalkers and
  // Backgrounds. Fall back to all cards if the type filter matches nothing
  // (e.g. an import that didn't populate type lines).
  const commanderCandidates = (() => {
    const eligible = deck.cards.filter((c) => {
      const t = (c.type_line ?? '').toLowerCase()
      return (t.includes('legendary') && (t.includes('creature') || t.includes('planeswalker'))) || t.includes('background')
    })
    return (eligible.length > 0 ? eligible : deck.cards)
      .map((c) => c.name)
      .sort((a, b) => a.localeCompare(b))
  })()

  const startEditCommanders = useCallback(() => {
    setDraftCommander(deck.commander ?? '')
    setDraftPartner(deck.partner_commander ?? '')
    setEditingFormat(false)
    setEditingCommanders(true)
  }, [deck.commander, deck.partner_commander])

  const saveCommanders = useCallback(async () => {
    setSavingCommanders(true)
    try {
      const updated = await api.setCommanders(deck.id, {
        commander: draftCommander || null,
        partner_commander: draftPartner || null,
      })
      onDeckUpdated(updated)
      setEditingCommanders(false)
    } finally {
      setSavingCommanders(false)
    }
  }, [deck.id, draftCommander, draftPartner, onDeckUpdated])

  const startRename = useCallback(() => {
    setDraftName(deck.name)
    setRenaming(true)
  }, [deck])

  const commitRename = useCallback(async () => {
    if (!draftName.trim()) { setRenaming(false); return }
    const updated = await api.updateDeck(deck.id, { name: draftName.trim() })
    onDeckUpdated(updated)
    setRenaming(false)
  }, [deck.id, draftName, onDeckUpdated])

  const saveFormat = useCallback(async () => {
    setSavingFormat(true)
    try {
      const updated = await api.updateDeck(deck.id, { format: draftFormat })
      onDeckUpdated(updated)
      setEditingFormat(false)
    } finally {
      setSavingFormat(false)
    }
  }, [deck.id, draftFormat, onDeckUpdated])

  const handleExport = useCallback(() => {
    const lines: string[] = []
    // Commander(s) first — no blank line after: a blank line reads as a
    // sideboard/maybeboard separator to Archidekt and Cockatrice, which
    // would dump the entire 99 into the sideboard.
    for (const c of deck.cards) {
      if (c.category === 'Commander') {
        lines.push(`${c.quantity} ${c.name}`)
      }
    }
    // Rest of the deck
    for (const c of deck.cards) {
      if (c.category !== 'Commander') {
        lines.push(`${c.quantity} ${c.name}`)
      }
    }
    navigator.clipboard.writeText(lines.join('\n').trim())
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }, [deck])

  const grouped = new Map<string, typeof deck.cards>()
  for (const card of deck.cards) {
    const raw = card.category || 'Uncategorized'
    const key = raw.charAt(0).toUpperCase() + raw.slice(1).toLowerCase()
    const existing = grouped.get(key)
    if (existing) { existing.push(card) } else { grouped.set(key, [card]) }
  }

  const commanderIdentities = commanderColorIdentities(deck)
  const identityTag =
    commanderIdentities
      .map((id) => id ?? '')
      .join('')
      .replace(/[^WUBRG]/g, '')
      .split('')
      .filter((c, i, a) => a.indexOf(c) === i)
      .join('') || 'C'
  const formatLabel = FORMAT_OPTIONS.find((f) => f.value === deck.format)?.label ?? deck.format
  const total = deck.cards.reduce((s, c) => s + c.quantity, 0)

  return (
    <div className="deck-detail">
      {/* ── Compact top bar ── */}
      <div className="deck-detail__topbar">
        <div className="deck-detail__topbar-left">
          <CommanderPip identities={commanderIdentities} />
          {renaming ? (
            <input
              className="deck-detail__title-input"
              value={draftName}
              onChange={(e) => setDraftName(e.target.value)}
              onBlur={commitRename}
              onKeyDown={(e) => {
                if (e.key === 'Enter') commitRename()
                if (e.key === 'Escape') setRenaming(false)
              }}
              autoFocus
            />
          ) : (
            <h2 className="deck-detail__title" onClick={startRename} title="Click to rename">
              {deck.name}
            </h2>
          )}
          <span className="deck-detail__format-group">
            <span className="deck-detail__identity-tag">{identityTag} · </span>
            <span className="deck-detail__format-tag" onClick={() => { setEditingCommanders(false); setDraftFormat(deck.format); setEditingFormat(true); }} title="Click to change format">
              {formatLabel}
            </span>
          </span>
          <span className="deck-detail__commander-names" onClick={startEditCommanders} title="Click to set commander(s)">
            {deck.commander
              ? `${deck.commander}${deck.partner_commander ? ` / ${deck.partner_commander}` : ''}`
              : 'Set commander(s)…'}
          </span>
        </div>
        <div className="deck-detail__header-actions">
          <button className="btn btn--secondary btn--mono" onClick={handleExport}>
            {copied ? 'Copied!' : 'Export'}
          </button>
          <button
            ref={importBtnRef}
            className={`btn btn--secondary btn--mono ${showImport ? 'btn--active' : ''}`}
            onClick={() => setShowImport((v) => !v)}
          >
            Import
          </button>
          {showImport && (
            <ImportPanel
              deckId={deck.id}
              anchorRef={importBtnRef}
              onImported={onDeckUpdated}
              onClose={() => setShowImport(false)}
            />
          )}
          <button
            className="btn btn--ghost btn--mono"
            onClick={() => deck.conversation_id ? onSelectConversation(deck.conversation_id) : onStartConversation(deck.id)}
          >
            {deck.conversation_id ? 'Open →' : 'Start →'}
          </button>
        </div>
      </div>

      {/* ── Commander / format editors (revealed on click) ── */}
      {(editingCommanders || editingFormat) && (
        <div className="deck-detail__editors">
          {editingCommanders && (
            <div className="deck-detail__commander-edit">
              <label className="micro-label">Commander</label>
              <select
                className="deck-detail__select"
                value={draftCommander}
                onChange={(e) => setDraftCommander(e.target.value)}
              >
                <option value="">— none —</option>
                {commanderCandidates.map((name) => (
                  <option key={name} value={name}>{name}</option>
                ))}
              </select>
              <label className="micro-label">Partner</label>
              <select
                className="deck-detail__select"
                value={draftPartner}
                onChange={(e) => setDraftPartner(e.target.value)}
              >
                <option value="">— none —</option>
                {commanderCandidates.map((name) => (
                  <option key={name} value={name}>{name}</option>
                ))}
              </select>
              <button className="btn btn--primary" onClick={saveCommanders} disabled={savingCommanders}>
                {savingCommanders ? 'Saving…' : 'Save'}
              </button>
              <button className="btn btn--secondary" onClick={() => setEditingCommanders(false)} disabled={savingCommanders}>
                Cancel
              </button>
            </div>
          )}
          {editingFormat && (
            <div className="deck-detail__format-row">
              <label className="micro-label">Format</label>
              <select
                className="deck-detail__select"
                value={draftFormat}
                onChange={(e) => setDraftFormat(e.target.value)}
              >
                {FORMAT_OPTIONS.map((f) => (
                  <option key={f.value} value={f.value}>{f.label}</option>
                ))}
              </select>
              <button className="btn btn--primary" onClick={saveFormat} disabled={savingFormat}>
                {savingFormat ? 'Saving…' : 'Save'}
              </button>
              <button className="btn btn--secondary" onClick={() => setEditingFormat(false)} disabled={savingFormat}>
                Cancel
              </button>
            </div>
          )}
        </div>
      )}

      {/* ── Dense metric strip ── */}
      {stats && stats.total_cards > 0 && (
        <div className="metric-strip">
          <div className="metric-strip__cell metric-strip__cell--power">
            <PowerDial value={stats.power_level} surface="var(--bg-soft)" />
            <div>
              <div className="micro-label">Power level</div>
              <div className="metric-strip__power-tier">
                {POWER_TIER(stats.power_level)} <span className="metric-strip__muted">· {stats.power_level}/10</span>
              </div>
              {stats.power_factors?.length > 0 && (
                <button
                  className="metric-strip__why"
                  onClick={() => setExpandedBreakdown(expandedBreakdown === 'power' ? null : 'power')}
                >
                  why?
                </button>
              )}
            </div>
          </div>
          <div className="metric-strip__cell">
            <div className="micro-label">Bracket</div>
            <div className="metric-strip__bracket-num">
              <span className="metric-strip__gold">{stats.bracket}</span>
              <span className="metric-strip__muted"> / 5</span>
              {stats.bracket_factors?.length > 0 && (
                <button
                  className="metric-strip__why"
                  onClick={() => setExpandedBreakdown(expandedBreakdown === 'bracket' ? null : 'bracket')}
                >
                  why?
                </button>
              )}
            </div>
            <BracketDiamonds filled={stats.bracket} />
          </div>
          <div className="metric-strip__cell">
            <div className="micro-label">Cards</div>
            <span className="metric-strip__num">{stats.total_cards}</span>
            <div className="metric-strip__ok">legal ✓</div>
          </div>
          <div className="metric-strip__cell">
            <div className="micro-label">Avg MV</div>
            <span className="metric-strip__num">{stats.avg_mv}</span>
            <div className="metric-strip__muted-line">nonland</div>
          </div>
          <div className="metric-strip__cell">
            <div className="micro-label">Lands</div>
            <span className="metric-strip__num">{stats.land_count}</span>
            <div className="metric-strip__muted-line">{stats.land_pct}%</div>
          </div>
        </div>
      )}

      {(expandedBreakdown === 'power' || expandedBreakdown === 'bracket') && stats && (
        <div className="deck-detail__factors">
          {(expandedBreakdown === 'power' ? stats.power_factors : stats.bracket_factors).join('\n')}
        </div>
      )}

      {/* ── Stat grid ── */}
      {stats && stats.total_cards > 0 && (
        <div className="deck-detail__grid">
          <div className="stat-card">
            <div className="micro-label">Mana Curve</div>
            <ManaCurve buckets={stats.mana_curve} />
          </div>
          <div className="stat-card">
            <div className="micro-label">Role Balance</div>
            <RoleBalance
              roles={[
                { label: 'Lands', count: stats.land_count },
                { label: 'Ramp', count: stats.ramp_count },
                { label: 'Draw', count: stats.draw_count },
                { label: 'Removal', count: stats.removal_count },
              ]}
            />
          </div>
          <div className="stat-card">
            <div className="micro-label">Colors</div>
            <div className="stat-card__rows">
              {stats.color_distribution.map((c) => (
                <div key={c.color} className="stat-card__row">
                  <ManaPip colorIdentity={c.color} size={13} />
                  <span className="stat-card__row-label">{COLOR_NAMES[c.color] ?? c.color}</span>
                  <span className="stat-card__row-val">{c.count} · {c.pct}%</span>
                </div>
              ))}
            </div>
          </div>
          <div className="stat-card">
            <div className="micro-label">Types</div>
            <div className="stat-card__types">
              {stats.type_breakdown.filter((t) => t.count > 0).map((t) => (
                <div key={t.type} className="stat-card__row">
                  <span className="stat-card__row-label">{t.type}</span>
                  <span className="stat-card__row-val">{t.count} · {t.pct}%</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {stats && stats.total_cards === 0 && (
        <div className="deck-detail__empty-stats">
          <p>No cards yet. Import a decklist or start a conversation to build one.</p>
        </div>
      )}

      {/* ── Quick-start prompts ── */}
      <div className="deck-detail__section">
        <div className="micro-label">Quick actions</div>
        <div className="deck-detail__chips">
          <button
            className="btn btn--ghost btn--chip"
            onClick={() => onQuickStart(deck.id, buildContextPrompt(deck, stats, 'Please help me tune and optimize this deck. Look for synergy gaps, suggest cuts and additions, and evaluate the overall gameplan.'))}
          >
            Tune this deck
          </button>
          <button
            className="btn btn--ghost btn--chip"
            onClick={() => onQuickStart(deck.id, buildContextPrompt(deck, stats, 'Please analyze my mana curve and ramp package. Is my curve too high? Do I have enough ramp for my average mana value? Suggest adjustments to smooth things out.'))}
          >
            Fix my mana curve
          </button>
          <button
            className="btn btn--ghost btn--chip"
            onClick={() => onQuickStart(deck.id, buildContextPrompt(deck, stats, 'Please do a deep-dive on this deck\'s power level. Where does it fall on the 1-10 scale, and what bracket would it be in? What specific cards or patterns push it higher or lower?'))}
          >
            Assess power level
          </button>
          <button
            className="btn btn--ghost btn--chip"
            onClick={() => onQuickStart(deck.id, buildContextPrompt(deck, stats, 'Please review my removal and interaction suite. Do I have enough? Is my removal versatile enough to handle different threat types (creatures, artifacts, enchantments, graveyards)? Suggest improvements.'))}
          >
            Review my removal
          </button>
        </div>
      </div>

      {/* ── Conversations ── */}
      {deckConversations.length > 0 && (
        <div className="deck-detail__section">
          <div className="micro-label">Conversations · {deckConversations.length}</div>
          <div className="deck-detail__convo-list">
            {deckConversations.map((c) => (
              <button
                key={c.id}
                className="deck-detail__convo-item"
                onClick={() => onSelectConversation(c.id)}
              >
                <span className="deck-detail__convo-title">{c.title}</span>
                <span className="deck-detail__convo-date">
                  {new Date(c.updated_at).toLocaleDateString()}
                </span>
              </button>
            ))}
          </div>
          <button
            className="btn btn--ghost btn--chip"
            onClick={() => onStartConversation(deck.id)}
          >
            + New conversation
          </button>
        </div>
      )}

      {/* ── Card list ── */}
      <div className="deck-detail__section">
        <div className="micro-label">Decklist · {total}</div>
        <div className="deck-detail__decklist">
          {[...grouped.entries()].map(([category, cards]) => (
            <div key={category} className="deck-detail__group">
              <div className="deck-detail__group-head">
                <span className="deck-detail__group-name">{category}</span>
                <span className="deck-detail__group-count">
                  {cards.reduce((s, c) => s + c.quantity, 0)}
                </span>
              </div>
              {cards.map((c) => (
                <DeckCardRow key={c.name} card={c} />
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
```

## `components/deckViz.tsx`

```tsx
// Pure CSS/SVG deck-console primitives shared across the deck side-panel,
// deck manager, and chat. All colors come from tokens (var(--mana-*), etc.)
// so light and dark modes both work.

import type { Deck } from '../types/api'

const MANA_VAR: Record<string, string> = {
  W: 'var(--mana-w)',
  U: 'var(--mana-u)',
  B: 'var(--mana-b)',
  R: 'var(--mana-r)',
  G: 'var(--mana-g)',
  C: 'var(--mana-c)',
}

// Fill for a single circle representing a (possibly multi-color) commander or
// card. One color → solid; two+ → equal vertical bands with hard stops. Bands
// read far better than a conic pie at small pip sizes: each color is a solid
// full-height block, whereas pie wedges converge to a muddy point at the
// center and each color only shows at the rim. A vertical (90deg) split also
// avoids the "muddy diagonal" of the earlier 135deg gradient.
function identityFill(colorIdentity: string | null): string {
  const colors = (colorIdentity ?? '').replace(/[^WUBRG]/g, '')
  if (colors.length === 0) return MANA_VAR.C
  if (colors.length === 1) return MANA_VAR[colors[0]] ?? MANA_VAR.C
  const letters = colors.split('')
  const stops = letters
    .map((c, i) => {
      const from = (i / letters.length) * 100
      const to = ((i + 1) / letters.length) * 100
      const v = MANA_VAR[c] ?? MANA_VAR.C
      return `${v} ${from}% ${to}%`
    })
    .join(', ')
  return `linear-gradient(90deg, ${stops})`
}

// One color-identity string per commander (commander + partner/background),
// matched to the deck card carrying its color identity. Prefers matching by
// name; falls back to Commander-category cards for imports that didn't set
// deck.commander / deck.partner_commander.
export function commanderColorIdentities(deck: Deck): (string | null)[] {
  const names = [deck.commander, deck.partner_commander].filter(
    (n): n is string => !!n,
  )

  if (names.length > 0) {
    return names.map(
      (name) =>
        deck.cards.find((c) => c.name === name)?.color_identity ?? null,
    )
  }

  return deck.cards
    .filter((c) => c.category === 'Commander')
    .map((c) => c.color_identity ?? null)
}

export function ManaPip({ colorIdentity, size = 9 }: { colorIdentity: string | null; size?: number }) {
  // Multicolor cards render as a color pie (same as the commander pip) rather
  // than one flat blend — a GU card shows green + blue, not purple.
  return (
    <span
      className="mana-pip"
      style={{ width: size, height: size, background: identityFill(colorIdentity) }}
    />
  )
}

// Commander identity pip. `identities` holds one color-identity string per
// commander card (1 for a solo commander, 2 for partner/background pairs).
// Two commanders → a 2-circle Venn, one circle per commander's own colors,
// with an overlap lens (README §Commander Venn pip). One commander → a single
// circle filled with its (possibly multi-color) identity.
export function CommanderPip({ identities }: { identities: (string | null)[] }) {
  const present = identities.filter((id) => id !== null && id !== undefined)

  if (present.length >= 2) {
    return (
      <span className="venn-pip">
        <span className="venn-pip__a" style={{ background: identityFill(present[0]) }} />
        <span className="venn-pip__b" style={{ background: identityFill(present[1]) }} />
        <span className="venn-pip__lens" />
      </span>
    )
  }

  return (
    <span
      className="mana-pip"
      style={{ width: 18, height: 18, background: identityFill(present[0] ?? null) }}
    />
  )
}

// Conic power dial (README §Power dial). `surface` sets the inner fill so the
// hole matches whatever card it sits on.
export function PowerDial({
  value,
  size = 56,
  surface = 'var(--bg-soft)',
}: {
  value: number
  size?: number
  surface?: string
}) {
  const turn = Math.max(0, Math.min(10, value)) / 10
  return (
    <div
      className="power-dial"
      style={{
        width: size,
        height: size,
        background: `conic-gradient(var(--accent) 0turn ${turn}turn, var(--border) ${turn}turn 1turn)`,
      }}
    >
      <div className="power-dial__hole" style={{ background: surface }}>
        <span className="power-dial__num">{value}</span>
      </div>
    </div>
  )
}

// Row of 5 bracket diamonds, `filled` = current bracket (README §Bracket diamonds).
export function BracketDiamonds({ filled, size = 14 }: { filled: number; size?: number }) {
  return (
    <div className="bracket-diamonds">
      {[1, 2, 3, 4, 5].map((i) => (
        <span
          key={i}
          className={`bracket-diamond ${i <= filled ? 'bracket-diamond--on' : ''}`}
          style={{ width: size, height: size }}
        />
      ))}
    </div>
  )
}

// Vertical mana-curve bars (README §Mana-curve bars).
export function ManaCurve({ buckets }: { buckets: { mv: string; count: number }[] }) {
  const max = Math.max(1, ...buckets.map((b) => b.count))
  return (
    <div className="mana-curve">
      {buckets.map((b) => (
        <div key={b.mv} className="mana-curve__bar-wrap">
          <span className="mana-curve__count">{b.count}</span>
          <div
            className="mana-curve__bar"
            style={{ height: `${Math.max(4, (b.count / max) * 100)}%` }}
          />
          <span className="mana-curve__label">{b.mv}</span>
        </div>
      ))}
    </div>
  )
}

const ROLE_COLOR: Record<string, string> = {
  Lands: '#7c7f8c',
  Ramp: 'var(--mana-g)',
  Draw: 'var(--mana-u)',
  Removal: 'var(--mana-r)',
}

// Horizontal role-balance bars (README §Role-balance bars).
export function RoleBalance({ roles }: { roles: { label: string; count: number }[] }) {
  const max = Math.max(1, ...roles.map((r) => r.count))
  return (
    <div className="role-balance">
      {roles.map((r) => (
        <div key={r.label} className="role-balance__row">
          <div className="role-balance__head">
            <span className="role-balance__label">{r.label}</span>
            <span className="role-balance__count">{r.count}</span>
          </div>
          <div className="role-balance__track">
            <div
              className="role-balance__fill"
              style={{
                width: `${(r.count / max) * 100}%`,
                background: ROLE_COLOR[r.label] ?? 'var(--mana-c)',
              }}
            />
          </div>
        </div>
      ))}
    </div>
  )
}
```

## `components/DeckCardRow.tsx`

```tsx
import type { DeckCard } from '../types/api'
import { CardPreview } from './CardPreview'
import { ManaPip } from './deckViz'

export function DeckCardRow({ card }: { card: DeckCard }) {
  return (
    <div className="deck-card-row">
      <span className="deck-card-row__qty">{card.quantity}×</span>
      <ManaPip colorIdentity={card.color_identity} />
      <CardPreview name={card.name} className="deck-card-row__name" />
      {card.mana_value !== null && <span className="deck-card-row__mv">{card.mana_value}</span>}
    </div>
  )
}
```

## `components/ImportPanel.tsx`

```tsx
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../api/client'
import type { DeckProvider, ImportResult } from '../types/api'

interface ImportPanelProps {
  deckId: number
  anchorRef: React.RefObject<HTMLElement | null>
  onImported: (result: ImportResult) => void
  onClose: () => void
}

// `source` drives which input is shown: null = just the source chips,
// "text" = paste box, otherwise a provider name = that provider's URL box.
type Source = null | 'text' | string

const POP_WIDTH = 300

export function ImportPanel({ deckId, anchorRef, onImported, onClose }: ImportPanelProps) {
  const [providers, setProviders] = useState<DeckProvider[]>([])
  const [source, setSource] = useState<Source>(null)
  const [text, setText] = useState('')
  const [ref, setRef] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [rowErrors, setRowErrors] = useState<string[]>([])
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)
  const popoverRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api.listProviders()
      .then((r) => setProviders(r.providers.filter((p) => p.supports_fetch)))
      .catch(() => setProviders([]))
  }, [])

  // Position the fixed popover under the anchor button, right-aligned, and
  // clamped to the viewport. Recomputed on open, scroll, and resize so it
  // tracks the button even though it's portaled to <body>.
  useLayoutEffect(() => {
    const place = () => {
      const el = anchorRef.current
      if (!el) return
      const r = el.getBoundingClientRect()
      const width = Math.min(POP_WIDTH, window.innerWidth - 16)
      let left = r.right - width           // right-align to the button
      left = Math.max(8, Math.min(left, window.innerWidth - width - 8))
      setPos({ top: r.bottom + 6, left })
    }
    place()
    window.addEventListener('scroll', place, true)
    window.addEventListener('resize', place)
    return () => {
      window.removeEventListener('scroll', place, true)
      window.removeEventListener('resize', place)
    }
  }, [anchorRef])

  // Close on outside click (ignoring the anchor) or Escape.
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node
      if (popoverRef.current?.contains(t)) return
      if (anchorRef.current?.contains(t)) return
      onClose()
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [onClose, anchorRef])

  const clearErrors = useCallback(() => { setError(null); setRowErrors([]) }, [])

  const finish = useCallback((result: ImportResult) => {
    onImported(result)
    const errs = result._import?.errors ?? []
    if (errs.length > 0) setRowErrors(errs)
    else onClose()
  }, [onImported, onClose])

  const run = useCallback(async (fn: () => Promise<ImportResult>) => {
    setBusy(true); clearErrors()
    try {
      finish(await fn())
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }, [finish, clearErrors])

  const providerLabel = source && source !== 'text'
    ? providers.find((p) => p.name === source)?.display_name ?? source
    : ''

  const popover = (
    <div
      className="import-pop"
      ref={popoverRef}
      style={pos ? { top: pos.top, left: pos.left, width: Math.min(POP_WIDTH, window.innerWidth - 16) } : { visibility: 'hidden' }}
    >
      <div className="import-pop__head">Import from…</div>

      <div className="import-pop__chips">
        <button
          className={`import-pop__chip ${source === 'text' ? 'import-pop__chip--active' : ''}`}
          onClick={() => { setSource('text'); clearErrors() }}
        >
          Text
        </button>
        {providers.map((p) => (
          <button
            key={p.name}
            className={`import-pop__chip ${source === p.name ? 'import-pop__chip--active' : ''}`}
            onClick={() => { setSource(p.name); clearErrors() }}
          >
            {p.display_name}
          </button>
        ))}
      </div>

      {source === 'text' && (
        <div className="import-pop__body">
          <textarea
            className="import-pop__textarea"
            placeholder="Paste a decklist…"
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={6}
            autoFocus
          />
          <button
            className="import-pop__go"
            onClick={() => run(() => api.importDecklist(deckId, text))}
            disabled={busy || !text.trim()}
          >
            {busy ? 'Importing…' : 'Import cards'}
          </button>
        </div>
      )}

      {source && source !== 'text' && (
        <div className="import-pop__body">
          <input
            className="import-pop__ref"
            placeholder={`${providerLabel} deck URL or id`}
            value={ref}
            onChange={(e) => setRef(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && ref.trim() && !busy) run(() => api.fetchDeckFrom(deckId, source, ref.trim())) }}
            autoFocus
          />
          <button
            className="import-pop__go"
            onClick={() => run(() => api.fetchDeckFrom(deckId, source, ref.trim()))}
            disabled={busy || !ref.trim()}
          >
            {busy ? 'Fetching…' : `Fetch from ${providerLabel}`}
          </button>
          <div className="import-pop__hint">Merges into this deck; maybeboard is skipped.</div>
        </div>
      )}

      {error && <div className="import-pop__error">{error}</div>}
      {rowErrors.length > 0 && (
        <div className="import-pop__error">
          {rowErrors.map((e, i) => <p key={i}>{e}</p>)}
        </div>
      )}
    </div>
  )

  return createPortal(popover, document.body)
}
```

## `components/PreferencesPanel.tsx`

```tsx
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import type { UserPreferences } from '../types/api'

interface PreferencesPanelProps {
  open: boolean
  onClose: () => void
}

export function PreferencesPanel({ open, onClose }: PreferencesPanelProps) {
  const [draft, setDraft] = useState<UserPreferences | null>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (open) {
      api.getPreferences().then(setDraft).catch(() => {})
    }
  }, [open])

  const save = useCallback(async () => {
    if (!draft) return
    await api.updatePreferences(draft)
    setSaved(true)
    setTimeout(() => setSaved(false), 1500)
  }, [draft])

  if (!open) return null

  return (
    <div className="preferences-overlay" onClick={onClose}>
      <div className="preferences-panel" onClick={(e) => e.stopPropagation()}>
        <div className="preferences-panel__header">
          <h3>Preferences</h3>
          <button className="preferences-panel__close" onClick={onClose}>×</button>
        </div>

        <label className="preferences-panel__field">
          Preferred bracket
          <select
            value={draft?.preferred_bracket ?? ''}
            onChange={(e) => setDraft((d) => d ? { ...d, preferred_bracket: e.target.value || null } : d)}
          >
            <option value="">Any</option>
            <option value="1">Bracket 1 — Exhibition</option>
            <option value="2">Bracket 2 — Core</option>
            <option value="3">Bracket 3 — Upgraded</option>
            <option value="4">Bracket 4 — Optimized</option>
            <option value="5">Bracket 5 — cEDH</option>
          </select>
        </label>

        <label className="preferences-panel__field">
          Preferred power level
          <select
            value={draft?.preferred_power ?? ''}
            onChange={(e) => setDraft((d) => d ? { ...d, preferred_power: e.target.value || null } : d)}
          >
            <option value="">Any</option>
            {Array.from({ length: 10 }, (_, i) => (
              <option key={i + 1} value={String(i + 1)}>{i + 1}</option>
            ))}
          </select>
        </label>

        <label className="preferences-panel__field">
          Budget
          <select
            value={draft?.budget ?? ''}
            onChange={(e) => setDraft((d) => d ? { ...d, budget: e.target.value || null } : d)}
          >
            <option value="">Any</option>
            <option value="budget">Budget</option>
            <option value="mid">Mid-range</option>
            <option value="unlimited">Unlimited</option>
          </select>
        </label>

        <label className="preferences-panel__field">
          Rule 0 notes
          <textarea
            className="preferences-panel__textarea"
            placeholder="e.g. proxies welcome, no mass land denial, casual takebacks"
            value={draft?.rule0_notes ?? ''}
            onChange={(e) => setDraft((d) => d ? { ...d, rule0_notes: e.target.value || null } : d)}
            rows={3}
          />
        </label>

        <button className="preferences-panel__save" onClick={save}>
          {saved ? 'Saved ✓' : 'Save'}
        </button>
      </div>
    </div>
  )
}
```

## `components/CardPreview.tsx`

```tsx
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { cardImageUrl } from '../api/cardImage'
import { useCardPins } from './CardPinContext'

// A card name that reveals the card's image on hover and pins it (into the
// shared bottom tray) on click. The hover image is portaled to <body> and
// positioned beside the name, clamped to the viewport. Pinning is app-level
// state (see CardPinContext) so multiple cards can be pinned for comparison.

// Scryfall "normal" images are 488×680 (aspect ~0.717). We render at a fixed
// width and let height follow the aspect so the box is sized before the image
// loads (no layout jump).
const PREVIEW_W = 244
const PREVIEW_H = Math.round(PREVIEW_W / 0.717)
const HOVER_DELAY_MS = 120

interface CardPreviewProps {
  name: string
  className?: string
}

export function CardPreview({ name, className }: CardPreviewProps) {
  const { isPinned, toggle } = useCardPins()
  const pinned = isPinned(name)
  const anchorRef = useRef<HTMLSpanElement>(null)
  const showTimer = useRef<number | undefined>(undefined)
  const [hovering, setHovering] = useState(false)
  const [failed, setFailed] = useState(false)
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)

  const place = useCallback(() => {
    const el = anchorRef.current
    if (!el) return
    const r = el.getBoundingClientRect()
    // Prefer to the right of the name; flip left if it would overflow.
    let left = r.right + 12
    if (left + PREVIEW_W > window.innerWidth - 8) {
      left = r.left - PREVIEW_W - 12
    }
    left = Math.max(8, Math.min(left, window.innerWidth - PREVIEW_W - 8))
    // Vertically center on the name, clamped to the viewport.
    let top = r.top + r.height / 2 - PREVIEW_H / 2
    top = Math.max(8, Math.min(top, window.innerHeight - PREVIEW_H - 8))
    setPos({ top, left })
  }, [])

  useLayoutEffect(() => {
    if (!hovering) return
    place()
    window.addEventListener('scroll', place, true)
    window.addEventListener('resize', place)
    return () => {
      window.removeEventListener('scroll', place, true)
      window.removeEventListener('resize', place)
    }
  }, [hovering, place])

  const show = useCallback(() => {
    window.clearTimeout(showTimer.current)
    showTimer.current = window.setTimeout(() => {
      setFailed(false)
      setHovering(true)
    }, HOVER_DELAY_MS)
  }, [])

  const hide = useCallback(() => {
    window.clearTimeout(showTimer.current)
    setHovering(false)
  }, [])

  const onClick = useCallback(() => {
    window.clearTimeout(showTimer.current)
    setHovering(false) // hand off to the tray; drop the floating hover preview
    toggle(name)
  }, [name, toggle])

  // Clean up a scheduled show on unmount.
  useEffect(() => () => window.clearTimeout(showTimer.current), [])

  // Only float the hover preview when not pinned — pinned cards live in the tray.
  const showImage = hovering && !pinned && !failed

  return (
    <>
      <span
        ref={anchorRef}
        className={`card-preview-anchor ${pinned ? 'card-preview-anchor--pinned' : ''} ${className ?? ''}`}
        onMouseEnter={show}
        onMouseLeave={hide}
        onClick={onClick}
        title={pinned ? 'Unpin' : 'Click to pin for comparison'}
      >
        {name}
      </span>
      {showImage && pos &&
        createPortal(
          <div
            className="card-preview"
            style={{ top: pos.top, left: pos.left, width: PREVIEW_W, height: PREVIEW_H }}
          >
            <img
              className="card-preview__img"
              src={cardImageUrl(name)}
              alt={name}
              width={PREVIEW_W}
              height={PREVIEW_H}
              onError={() => setFailed(true)}
            />
          </div>,
          document.body,
        )}
    </>
  )
}
```

## `components/PinnedTray.tsx`

```tsx
import { useEffect } from 'react'
import { cardImageUrl } from '../api/cardImage'
import { useCardPins } from './CardPinContext'

// A bottom tray that collects all pinned card images side by side for
// comparison. Each card has its own remove button; the whole tray clears on
// Escape or the "Clear all" button.
export function PinnedTray() {
  const { pinned, unpin, clear } = useCardPins()

  // Let CSS know the tray is visible so scrollable deck areas can add
  // bottom padding — otherwise the fixed tray covers the last few cards.
  useEffect(() => {
    document.body.classList.toggle('has-pinned-tray', pinned.length > 0)
    return () => document.body.classList.remove('has-pinned-tray')
  }, [pinned.length])

  useEffect(() => {
    if (pinned.length === 0) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') clear()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [pinned.length, clear])

  if (pinned.length === 0) return null

  return (
    <div className="pinned-tray">
      <div className="pinned-tray__head">
        <span className="micro-label">Pinned · {pinned.length}</span>
        <button className="pinned-tray__clear" onClick={clear}>
          Clear all
        </button>
      </div>
      <div className="pinned-tray__cards">
        {pinned.map((name) => (
          <div key={name} className="pinned-card">
            <img
              className="pinned-card__img"
              src={cardImageUrl(name)}
              alt={name}
              loading="lazy"
            />
            <button
              className="pinned-card__close"
              onClick={() => unpin(name)}
              title={`Unpin ${name}`}
              aria-label={`Unpin ${name}`}
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}
```

## `components/CardPinContext.tsx`

```tsx
import { createContext, useCallback, useContext, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

// Shared state for pinned card previews. Pins live at the app level (not inside
// each CardPreview) so multiple cards can be pinned at once and collected into a
// single tray for side-by-side comparison.

interface CardPinContextValue {
  pinned: string[]
  isPinned: (name: string) => boolean
  toggle: (name: string) => void
  unpin: (name: string) => void
  clear: () => void
}

const CardPinContext = createContext<CardPinContextValue | null>(null)

export function CardPinProvider({ children }: { children: ReactNode }) {
  // Ordered list, newest last. A card is pinned at most once (keyed by name).
  const [pinned, setPinned] = useState<string[]>([])

  const toggle = useCallback((name: string) => {
    setPinned((prev) =>
      prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name],
    )
  }, [])

  const unpin = useCallback((name: string) => {
    setPinned((prev) => prev.filter((n) => n !== name))
  }, [])

  const clear = useCallback(() => setPinned([]), [])

  const value = useMemo<CardPinContextValue>(
    () => ({
      pinned,
      isPinned: (name: string) => pinned.includes(name),
      toggle,
      unpin,
      clear,
    }),
    [pinned, toggle, unpin, clear],
  )

  return <CardPinContext.Provider value={value}>{children}</CardPinContext.Provider>
}

export function useCardPins(): CardPinContextValue {
  const ctx = useContext(CardPinContext)
  if (!ctx) {
    throw new Error('useCardPins must be used within a CardPinProvider')
  }
  return ctx
}
```

## `components/MobileHeader.tsx`

```tsx
import type { ReactNode } from 'react'

interface MobileHeaderProps {
  /** When true, shows a back arrow that calls onBack. */
  showBack?: boolean
  onBack?: () => void
  title?: string
  children?: ReactNode
}

export function MobileHeader({ showBack, onBack, title, children }: MobileHeaderProps) {
  return (
    <header className="mobile-header">
      <div className="mobile-header__left">
        {showBack && (
          <button
            className="mobile-header__back"
            onClick={onBack}
            aria-label="Back"
          >
            ←
          </button>
        )}
        {title && <span className="mobile-header__title">{title}</span>}
      </div>
      {children && <div className="mobile-header__actions">{children}</div>}
    </header>
  )
}
```

## `components/MobileNav.tsx`

```tsx
export type MobileTab = 'chats' | 'decks' | 'pins'

interface MobileNavProps {
  activeTab: MobileTab
  onTabChange: (tab: MobileTab) => void
  /** Number of pending proposals across all conversations, shown as a badge. */
  proposalCount?: number
}

const TABS: { key: MobileTab; label: string; icon: string }[] = [
  { key: 'chats', label: 'Chats', icon: '◉' },
  { key: 'decks', label: 'Decks', icon: '◆' },
  { key: 'pins', label: 'Pins', icon: '◈' },
]

export function MobileNav({ activeTab, onTabChange, proposalCount }: MobileNavProps) {
  return (
    <nav className="mobile-nav">
      {TABS.map((t) => (
        <button
          key={t.key}
          className={`mobile-nav__tab ${activeTab === t.key ? 'mobile-nav__tab--active' : ''}`}
          onClick={() => onTabChange(t.key)}
        >
          <span className="mobile-nav__icon">{t.icon}</span>
          <span className="mobile-nav__label">{t.label}</span>
          {t.key === 'chats' && proposalCount !== undefined && proposalCount > 0 && (
            <span className="mobile-nav__badge">{proposalCount}</span>
          )}
        </button>
      ))}
    </nav>
  )
}
```

## `assets/favicon.svg`

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100" role="img" aria-label="Familiar">
  <defs>
    <linearGradient id="familiarTileGradient" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#863bff"></stop>
      <stop offset="1" stop-color="#47bfff"></stop>
    </linearGradient>
    <clipPath id="familiarTileClip">
      <rect width="100" height="100" rx="24" ry="24"></rect>
    </clipPath>
  </defs>
  
  <g clip-path="url(#familiarTileClip)">
    <rect width="100" height="100" fill="url(#familiarTileGradient)"></rect>
    <g transform="translate(11,11) scale(0.78)">
      <polygon points="12,34 24,4 42,30 58,30 76,4 88,34 82,62 64,88 50,96 36,88 18,62" fill="#ffffff"></polygon>
      <ellipse cx="36" cy="55" rx="5.5" ry="8.5" fill="url(#familiarTileGradient)" transform="rotate(20 36 55)"></ellipse>
      <ellipse cx="64" cy="55" rx="5.5" ry="8.5" fill="url(#familiarTileGradient)" transform="rotate(-20 64 55)"></ellipse>
    </g>
  </g>
</svg>
```

## `assets/familiar-mark.svg`

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100" role="img" aria-label="Familiar">
  <defs>
    <linearGradient id="familiarGradient" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#863bff"></stop>
      <stop offset="1" stop-color="#47bfff"></stop>
    </linearGradient>
    <mask id="familiarEyes">
      <polygon points="12,34 24,4 42,30 58,30 76,4 88,34 82,62 64,88 50,96 36,88 18,62" fill="#fff"></polygon>
      <ellipse cx="36" cy="55" rx="5.5" ry="8.5" fill="#000" transform="rotate(20 36 55)"></ellipse>
      <ellipse cx="64" cy="55" rx="5.5" ry="8.5" fill="#000" transform="rotate(-20 64 55)"></ellipse>
    </mask>
  </defs>
  
  <rect width="100" height="100" fill="url(#familiarGradient)" mask="url(#familiarEyes)"></rect>
</svg>
```

