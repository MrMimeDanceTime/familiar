import { describe, expect, it } from 'vitest'
import type { Deck } from '../types/api'
import { exportDecklist } from './exportDecklist'

const deck: Deck = {
  id: 1, name: 'Test', commander: 'Korvold, Fae-Cursed King', partner_commander: null,
  notes: null, power_level: null, format: 'commander', conversation_id: null,
  cards: [
    { name: 'Sol Ring', quantity: 1, category: 'Ramp', mana_value: 1, color_identity: '', type_line: 'Artifact', tags: [], notes: null },
    { name: 'Korvold, Fae-Cursed King', quantity: 1, category: 'Commander', mana_value: 5, color_identity: 'BRG', type_line: 'Legendary Creature', tags: [], notes: null },
    { name: 'Swamp', quantity: 10, category: 'Land', mana_value: null, color_identity: '', type_line: 'Basic Land', tags: [], notes: null },
  ],
}

describe('exportDecklist', () => {
  it('plain: commander first, no blank line, then the rest', () => {
    expect(exportDecklist(deck, 'plain')).toBe(
      '1 Korvold, Fae-Cursed King\n1 Sol Ring\n10 Swamp',
    )
  })

  it('arena: Commander and Deck sections', () => {
    expect(exportDecklist(deck, 'arena')).toBe(
      'Commander\n1 Korvold, Fae-Cursed King\n\nDeck\n1 Sol Ring\n10 Swamp',
    )
  })

  it('categories: // headers per group, commander first', () => {
    expect(exportDecklist(deck, 'categories')).toBe(
      '//Commander\n1 Korvold, Fae-Cursed King\n//Ramp\n1 Sol Ring\n//Land\n10 Swamp',
    )
  })

  it('arena without a commander has only a Deck section', () => {
    const noCommander = { ...deck, cards: deck.cards.filter((c) => c.category !== 'Commander') }
    expect(exportDecklist(noCommander, 'arena')).toBe('Deck\n1 Sol Ring\n10 Swamp')
  })
})
