import { describe, expect, it } from 'vitest'
import type { DeckProposal } from '../types/api'
import { reviewSummary } from './reviewSummary'

function p(over: Partial<DeckProposal>): DeckProposal {
  return {
    id: 1, deck_id: 1, message_id: null, status: 'pending', action: 'add',
    card_name: 'X', quantity: 1, category: null, commander_name: null, reasoning: '',
    ...over,
  }
}

describe('reviewSummary', () => {
  it('names approvals and denials with the reason the player picked', () => {
    const text = reviewSummary([
      p({ id: 1, card_name: 'Sol Ring', status: 'approved' }),
      p({ id: 2, card_name: 'Cultivate', status: 'denied', denial_reason: 'Too expensive' }),
      p({ id: 3, card_name: 'Farseek', status: 'denied' }),
    ])
    expect(text).toBe(
      "I've reviewed the proposals. Approved: Sol Ring. Denied: Cultivate (Too expensive), Farseek. Let's continue.",
    )
  })

  it('leaves out withdrawals and superseded rows, which were not the player', () => {
    const text = reviewSummary([
      p({ id: 1, card_name: 'Sol Ring', status: 'denied', denial_reason: 'withdrawn' }),
      p({ id: 2, card_name: 'Old', status: 'denied', denial_reason: 'superseded' }),
    ])
    expect(text).toBe("I've reviewed the proposals. Let's continue.")
  })

  it('uses the commander name for a commander proposal', () => {
    const text = reviewSummary([
      p({ id: 1, action: 'set_commander', card_name: null, commander_name: 'Korvold', status: 'approved' }),
    ])
    expect(text).toContain('Approved: Korvold.')
  })
})
