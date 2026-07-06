# Familiar — UX Design Brief

**Familiar** is a personal, local-only AI assistant for Magic: The Gathering
Commander/EDH deckbuilding. Single-user desktop web app, served locally. The
user chats with an AI deckbuilding partner; the AI proposes deck changes the
user approves/denies, and a live deck panel + stats keep pace with the build.

We're looking for a UX/visual design package: refined component styling, a
tightened visual system, and any layout/interaction improvements — while
staying within the existing tech constraints below.

---

## Tech stack (hard constraints)

- **React 19** + **TypeScript 6** (strict).
- **Vite 8** build, **oxlint** lint.
- **No component/UI library** — every component is hand-rolled.
- **No CSS framework** (no Tailwind/MUI/etc.). Plain CSS in two files:
  - `theme-tokens.css` (included here — this is the real `src/index.css`):
    design tokens + base reset.
  - one global stylesheet (`src/styles/global.css`, ~1,200 lines) with
    **BEM-ish** class names (`.deck-detail__header`, `.import-pop__chip`).
- Only runtime UI deps: **react-markdown** + **remark-gfm** (assistant
  messages render markdown, incl. tables — see screenshot 05).
- **Desktop-first.** Runs in a browser window on desktop; mobile is NOT a
  target.
- Please **keep new dependencies to a minimum** — the app is intentionally
  dependency-light. If a design needs a new lib, call it out explicitly.

## Theme system (please reuse — see theme-tokens.css)

CSS custom properties with a light default and an automatic
`@media (prefers-color-scheme: dark)` override. Both light and dark must keep
working, so **design against the tokens, not hardcoded colors.**

Core tokens: `--text`, `--text-h`, `--bg`, `--bg-soft`, `--border`,
`--code-bg`, `--accent`, `--accent-bg`, `--accent-border`, `--user-bubble-bg`,
`--sans`, `--mono`.

Brand accent: **purple** — `#aa3bff` (light) / `#c084fc` (dark). The logo &
wordmark use a purple→blue gradient (`#863bff` → `#47bfff`). Favicon is a
geometric purple/blue mark. Screenshots below are dark mode (the app's default
on this machine).

## Layout

Three-column CSS grid: **`240px | 1fr | 320px`**
- **Left (240px):** sidebar — brand header, Conversations/Decks tabs, list,
  settings gear.
- **Center (1fr):** chat view (message stream + composer) OR the full-screen
  **deck manager** when the Decks tab + a deck are active.
- **Right (320px):** deck side-panel — live deck contents while chatting.

## Key surfaces / components

- **Chat** — streaming AI responses, markdown (incl. tables), a tool-activity
  indicator, and inline **proposal cards** (approve/deny AI deck-change
  suggestions).
- **Deck manager** (`DeckDetail`) — the stats grid is the signature surface:
  mana curve, avg MV, **power level (1–10)**, **commander bracket (1–5)**,
  card count, type/color breakdown, role counts (ramp/draw/removal/lands);
  plus commander setter, quick-action prompt buttons, category-grouped card
  list.
- **Deck side-panel** (`DeckPanel`) — narrower live deck view during chat.
- **Import popover** (`ImportPanel`) — paste a decklist or fetch from a remote
  source (Archidekt); portaled, anchored under the Import button.
- **Preferences** (`PreferencesPanel`) — bracket / power / budget / rule-0
  notes.

Component files: `ConversationSidebar`, `ChatView`, `MessageBubble`,
`ToolActivityIndicator`, `ProposalCard`, `DeckPanel`, `DeckDetail`,
`DeckCardRow`, `ImportPanel`, `PreferencesPanel`.

## Screenshots (dark mode, 1440-wide)

| File | Surface |
|------|---------|
| `01-app-main.png` | Empty state — three-column layout, sidebar, empty chat + deck panel |
| `02-deck-manager.png` | Deck manager with full stats grid, quick actions, card list |
| `03-import-popover.png` | Import popover (Text / Archidekt), anchored to Import button |
| `04-preferences.png` | Preferences modal |
| `05-chat-conversation.png` | Active chat: message bubbles + markdown table + live deck panel |

## What we'd value most from the package

- A cohesive visual refresh of the stats grid and card list (the deck manager
  is where users spend the most time).
- Better hierarchy/rhythm in chat (message bubbles, proposal cards).
- Consistent component primitives (buttons, chips, inputs, cards, modals) as
  reusable CSS classes on the existing tokens.
- Any layout improvements to the three-column grid for desktop.
