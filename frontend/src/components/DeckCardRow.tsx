import type { DeckCard } from '../types/api'

export function DeckCardRow({ card }: { card: DeckCard }) {
  return (
    <div className="deck-card-row">
      <span className="deck-card-row__qty">{card.quantity}×</span>
      <span className="deck-card-row__name">{card.name}</span>
      {card.mana_value !== null && <span className="deck-card-row__mv">{card.mana_value}</span>}
      {card.category && <span className="deck-card-row__category">{card.category}</span>}
    </div>
  )
}
