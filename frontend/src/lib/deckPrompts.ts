import type { Deck, DeckStats } from '../types/api'

const COLOR_NAMES: Record<string, string> = {
  W: 'White', U: 'Blue', B: 'Black', R: 'Red', G: 'Green',
}

// What "Done reviewing" sends. The decisions themselves reach the model from
// the server (app.chat.context.since_last_turn), read from the database, so
// the message no longer has to carry them.
export const REVIEW_DONE_MESSAGE = "I've reviewed the proposals. Let's continue."

export const FORMAT_OPTIONS: { value: string; label: string }[] = [
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

export function buildContextPrompt(deck: Deck, stats: DeckStats | null, focus: string): string {
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


