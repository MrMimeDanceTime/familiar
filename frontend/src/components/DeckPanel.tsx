import type { Deck } from '../types/api'
import { DeckCardRow } from './DeckCardRow'

export function DeckPanel({ deck }: { deck: Deck | null }) {
  if (!deck) {
    return (
      <div className="deck-panel deck-panel--empty">
        <p>No deck yet. Start a conversation to begin building one.</p>
      </div>
    )
  }

  const grouped = new Map<string, typeof deck.cards>()
  for (const card of deck.cards) {
    const key = card.category ?? 'Uncategorized'
    grouped.set(key, [...(grouped.get(key) ?? []), card])
  }

  return (
    <div className="deck-panel">
      <h2 className="deck-panel__title">{deck.name}</h2>
      {deck.commander && <div className="deck-panel__commander">Commander: {deck.commander}</div>}
      {deck.partner_commander && (
        <div className="deck-panel__commander">Partner: {deck.partner_commander}</div>
      )}
      {deck.power_level && <div className="deck-panel__power">Power level: {deck.power_level}</div>}
      <div className="deck-panel__count">{deck.cards.length} cards</div>
      <div className="deck-panel__cards">
        {[...grouped.entries()].map(([category, cards]) => (
          <div key={category} className="deck-panel__group">
            <div className="deck-panel__group-title">{category}</div>
            {cards.map((c) => (
              <DeckCardRow key={c.name} card={c} />
            ))}
          </div>
        ))}
      </div>
      {deck.notes && (
        <div className="deck-panel__notes">
          <div className="deck-panel__group-title">Notes</div>
          <p>{deck.notes}</p>
        </div>
      )}
    </div>
  )
}
