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
