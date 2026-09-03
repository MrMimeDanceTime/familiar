import type { Deck, DeckCard } from '../types/api'

export type ExportFormat = 'plain' | 'arena' | 'categories'

export const EXPORT_FORMATS: { value: ExportFormat; label: string; hint: string }[] = [
  {
    value: 'plain',
    label: 'Plain list',
    hint: 'Commander first, then one line per card. Moxfield, Archidekt, and Cockatrice all read this; pick the commander in their import dialog.',
  },
  {
    value: 'arena',
    label: 'MTG Arena',
    hint: 'Commander and Deck sections, the layout Arena exports and imports.',
  },
  {
    value: 'categories',
    label: 'With categories',
    hint: 'Grouped under // headers, which Archidekt turns into categories on import.',
  },
]

function line(c: DeckCard): string {
  return `${c.quantity} ${c.name}`
}

function split(deck: Deck): { commanders: DeckCard[]; rest: DeckCard[] } {
  const commanders = deck.cards.filter((c) => c.category === 'Commander')
  const rest = deck.cards.filter((c) => c.category !== 'Commander')
  return { commanders, rest }
}

/**
 * Render a deck as text for another tool to import.
 *
 * Every format keeps the commander before the rest with no blank line between
 * them in the plain list: a blank line reads as a sideboard separator to
 * Archidekt and Cockatrice, which would dump the 99 into the sideboard.
 */
export function exportDecklist(deck: Deck, format: ExportFormat): string {
  const { commanders, rest } = split(deck)
  switch (format) {
    case 'arena': {
      const out: string[] = []
      if (commanders.length) {
        out.push('Commander', ...commanders.map(line), '')
      }
      out.push('Deck', ...rest.map(line))
      return out.join('\n').trim()
    }
    case 'categories': {
      const out: string[] = []
      if (commanders.length) {
        out.push('//Commander', ...commanders.map(line))
      }
      const grouped = new Map<string, DeckCard[]>()
      for (const c of rest) {
        const key = c.category || 'Uncategorized'
        grouped.set(key, [...(grouped.get(key) ?? []), c])
      }
      for (const [category, cards] of grouped.entries()) {
        out.push(`//${category}`, ...cards.map(line))
      }
      return out.join('\n').trim()
    }
    case 'plain':
    default:
      return [...commanders.map(line), ...rest.map(line)].join('\n').trim()
  }
}
