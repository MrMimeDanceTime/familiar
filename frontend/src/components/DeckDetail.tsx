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

// ── "why?" breakdown parsing ────────────────────────────────────────────────
// The backend hands us pre-formatted human strings (see _estimate_power_level /
// _estimate_bracket in deck_tools.py). Rather than surface that raw text, we
// parse the few known shapes back into structured rows the UI can style.

interface PowerRow {
  label: string
  detail: string
  points: number
}

// e.g. "Lands: 34 (focused sweet spot) +1.0" → {label, detail, points}
function parsePowerRow(line: string): PowerRow | null {
  const m = line.match(/^([^:]+):\s*(.*?)\s*([+-][\d.]+)\s*$/)
  if (!m) return null
  return { label: m[1].trim(), detail: m[2].trim(), points: parseFloat(m[3]) }
}

interface PowerBreakdown {
  raw: number
  level: number
  rows: PowerRow[]
}

function parsePowerFactors(factors: string[]): PowerBreakdown | null {
  // First line is the summary: "Raw: 6.5 + 1 = 8/10"
  const head = factors[0]?.match(/Raw:\s*([\d.]+).*?=\s*(\d+)\/10/)
  const rows = factors.slice(1).map(parsePowerRow).filter((r): r is PowerRow => r !== null)
  if (!head) return null
  return { raw: parseFloat(head[1]), level: parseInt(head[2], 10), rows }
}

interface BracketRow {
  label: string
  cards: string[]
  note: string
}

// e.g. "Game Changers (1): Rhystic Study" or "Game Changers: none"
function parseBracketRow(line: string): BracketRow {
  const m = line.match(/^([^:]+):\s*(.*)$/)
  if (!m) return { label: line.trim(), cards: [], note: '' }
  const label = m[1].replace(/\s*\(\d+\)\s*$/, '').trim()
  const rest = m[2].trim()
  if (!rest || rest.toLowerCase() === 'none') {
    return { label, cards: [], note: 'none' }
  }
  return { label, cards: rest.split(',').map((c) => c.trim()).filter(Boolean), note: '' }
}

interface BracketBreakdown {
  bracket: number
  verdict: string
  rows: BracketRow[]
}

function parseBracketFactors(factors: string[]): BracketBreakdown | null {
  // First line is the verdict: "Bracket 3: 1 Game Changer(s) present"
  const head = factors[0]?.match(/^Bracket\s*(\d+):\s*(.*)$/)
  if (!head) return null
  const rows = factors.slice(1).map(parseBracketRow)
  return { bracket: parseInt(head[1], 10), verdict: head[2].trim(), rows }
}

function PowerWhy({ factors }: { factors: string[] }) {
  const data = parsePowerFactors(factors)
  if (!data) {
    return <div className="why-panel why-panel--raw">{factors.join('\n')}</div>
  }
  const maxPts = Math.max(...data.rows.map((r) => Math.abs(r.points)), 2)
  return (
    <div className="why-panel">
      <div className="why-panel__head">
        <div className="why-panel__title">Power level {data.level}/10</div>
        <div className="why-panel__sub">
          {data.rows.length} fundamentals scored · base 1 + {data.raw.toFixed(1)} earned
        </div>
      </div>
      <ul className="why-rows">
        {data.rows.map((r) => (
          <li className="why-row" key={r.label}>
            <span className="why-row__label">{r.label}</span>
            <span className="why-row__detail">{r.detail}</span>
            <span className="why-row__bar" aria-hidden>
              <span
                className={`why-row__bar-fill ${r.points <= 0 ? 'why-row__bar-fill--zero' : ''}`}
                style={{ width: `${(Math.abs(r.points) / maxPts) * 100}%` }}
              />
            </span>
            <span className={`why-row__pts ${r.points <= 0 ? 'why-row__pts--zero' : ''}`}>
              {r.points > 0 ? `+${r.points.toFixed(1)}` : r.points.toFixed(1)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function BracketWhy({ factors }: { factors: string[] }) {
  const data = parseBracketFactors(factors)
  if (!data) {
    return <div className="why-panel why-panel--raw">{factors.join('\n')}</div>
  }
  return (
    <div className="why-panel">
      <div className="why-panel__head">
        <div className="why-panel__title why-panel__title--bracket">
          Bracket {data.bracket}
          <span className="why-panel__of"> / 5</span>
        </div>
        <div className="why-panel__sub">{data.verdict}</div>
      </div>
      <ul className="why-rows why-rows--bracket">
        {data.rows.map((r) => (
          <li className="why-row why-row--bracket" key={r.label}>
            <span className="why-row__label">{r.label}</span>
            {r.note === 'none' ? (
              <span className="why-chip why-chip--none">none</span>
            ) : (
              <span className="why-chips">
                {r.cards.map((c) => (
                  <span className="why-chip" key={c}>{c}</span>
                ))}
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
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
  const [draftNotes, setDraftNotes] = useState(deck.notes ?? '')
  const [savingNotes, setSavingNotes] = useState(false)
  const [notesSaved, setNotesSaved] = useState(false)

  useEffect(() => {
    api.listDeckConversations(deck.id).then(setDeckConversations).catch(() => {})
  }, [deck.id])

  // Re-sync the notes draft when switching decks or when the assistant edits
  // notes via deck_update_notes — but not on every keystroke (that would fight
  // the user's typing), so key it on the persisted value.
  useEffect(() => {
    setDraftNotes(deck.notes ?? '')
  }, [deck.id, deck.notes])

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

  const saveNotes = useCallback(async () => {
    setSavingNotes(true)
    try {
      // Send "" (not null) to clear: update_deck treats null as "leave
      // unchanged", so null would make emptied notes un-clearable.
      const updated = await api.updateDeck(deck.id, { notes: draftNotes.trim() })
      onDeckUpdated(updated)
      setNotesSaved(true)
      setTimeout(() => setNotesSaved(false), 1500)
    } finally {
      setSavingNotes(false)
    }
  }, [deck.id, draftNotes, onDeckUpdated])

  const notesDirty = draftNotes.trim() !== (deck.notes ?? '').trim()

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

      {expandedBreakdown === 'power' && stats && (
        <PowerWhy factors={stats.power_factors} />
      )}
      {expandedBreakdown === 'bracket' && stats && (
        <BracketWhy factors={stats.bracket_factors} />
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

      {/* ── Notes ── */}
      <div className="deck-detail__section">
        <div className="deck-detail__notes-head">
          <div className="micro-label">Notes</div>
          {notesDirty && !savingNotes && (
            <button className="btn btn--ghost btn--chip" onClick={saveNotes}>
              Save
            </button>
          )}
          {savingNotes && <span className="deck-detail__notes-status">Saving…</span>}
          {notesSaved && !notesDirty && (
            <span className="deck-detail__notes-status deck-detail__notes-status--ok">Saved ✓</span>
          )}
        </div>
        <textarea
          className="deck-detail__notes"
          placeholder="Strategy notes, combos, cards to try, playgroup meta… The assistant can read and update these too."
          value={draftNotes}
          onChange={(e) => setDraftNotes(e.target.value)}
          onBlur={() => { if (notesDirty) saveNotes() }}
          rows={4}
        />
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
