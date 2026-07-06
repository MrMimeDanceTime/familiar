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
