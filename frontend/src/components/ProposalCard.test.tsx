import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ProposalCard } from './ProposalCard'
import type { DeckProposal } from '../types/api'

function makeProposal(over: Partial<DeckProposal> = {}): DeckProposal {
  return {
    id: 1,
    deck_id: 1,
    message_id: null,
    status: 'pending',
    action: 'add',
    card_name: 'Mayhem Devil',
    quantity: 1,
    category: 'removal',
    commander_name: null,
    reasoning: 'Pings on every sacrifice.',
    ...over,
  }
}

const noop = async () => {}

describe('ProposalCard', () => {
  it('shows the card and its reasoning', () => {
    render(<ProposalCard proposal={makeProposal()} onApply={noop} onDeny={noop} />)
    expect(screen.getByText('Mayhem Devil')).toBeInTheDocument()
    expect(screen.getByText('Pings on every sacrifice.')).toBeInTheDocument()
  })

  it('renders a bar per layer that scored the card', () => {
    render(
      <ProposalCard
        proposal={makeProposal({
          scores: {
            total: 0.78, consensus: 0.85, mechanical: 0.75, personal: 0,
            explain: 'consensus 0.85, mechanical 0.75 — payoff for sacrifice',
          },
        })}
        onApply={noop}
        onDeny={noop}
      />,
    )
    // Layers with no opinion are omitted rather than drawn empty: a silent
    // layer means "no data", not "scored zero".
    expect(screen.getByText('Played')).toBeInTheDocument()
    expect(screen.getByText('Fits')).toBeInTheDocument()
    expect(screen.queryByText('You')).not.toBeInTheDocument()
  })

  it('shows the explanation so the score is inspectable', () => {
    render(
      <ProposalCard
        proposal={makeProposal({
          scores: {
            total: 0.5, consensus: 0.2, mechanical: 0.9, personal: 0,
            explain: 'mechanical 0.90 — engine piece for sacrifice',
          },
        })}
        onApply={noop}
        onDeny={noop}
      />,
    )
    expect(screen.getByText(/engine piece for sacrifice/)).toBeInTheDocument()
  })

  it('says so when the map had no opinion, rather than showing a gap', () => {
    // Verified live: 35 of 60 pool cards score nothing because they have no
    // EDHREC entry for this commander, match no mechanical theme, and have no
    // history. An empty space there reads as a bug; saying it plainly tells the
    // player the pick rests on the model's judgement alone.
    render(<ProposalCard proposal={makeProposal()} onApply={noop} onDeny={noop} />)
    expect(screen.queryByText('Played')).not.toBeInTheDocument()
    expect(screen.getByText(/No scoring data/)).toBeInTheDocument()
  })

  it('does not claim missing scores on a removal', () => {
    render(
      <ProposalCard
        proposal={makeProposal({ action: 'remove' })}
        onApply={noop}
        onDeny={noop}
      />,
    )
    expect(screen.queryByText(/No scoring data/)).not.toBeInTheDocument()
  })

  it('asks why before denying', () => {
    const onDeny = vi.fn(async () => {})
    render(<ProposalCard proposal={makeProposal()} onApply={noop} onDeny={onDeny} />)

    fireEvent.click(screen.getByRole('button', { name: 'Deny' }))

    expect(screen.getByText('Why pass on it?')).toBeInTheDocument()
    expect(onDeny).not.toHaveBeenCalled()
  })

  it('sends the chosen reason with the denial', async () => {
    const onDeny = vi.fn(async () => {})
    render(<ProposalCard proposal={makeProposal()} onApply={noop} onDeny={onDeny} />)

    fireEvent.click(screen.getByRole('button', { name: 'Deny' }))
    fireEvent.click(screen.getByRole('button', { name: 'Too generic' }))

    expect(onDeny).toHaveBeenCalledWith(1, 'Too generic')
  })

  it('allows denying without a reason', async () => {
    const onDeny = vi.fn(async () => {})
    render(<ProposalCard proposal={makeProposal()} onApply={noop} onDeny={onDeny} />)

    fireEvent.click(screen.getByRole('button', { name: 'Deny' }))
    fireEvent.click(screen.getByRole('button', { name: 'Skip' }))

    expect(onDeny).toHaveBeenCalledWith(1, undefined)
  })

  it('approves without asking anything', () => {
    const onApply = vi.fn(async () => {})
    render(<ProposalCard proposal={makeProposal()} onApply={onApply} onDeny={noop} />)

    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))

    expect(onApply).toHaveBeenCalledWith(1)
  })

  it('shows the recorded reason on a resolved denial', () => {
    render(
      <ProposalCard
        proposal={makeProposal({ status: 'denied', denial_reason: 'Off-theme' })}
        onApply={noop}
        onDeny={noop}
      />,
    )
    expect(screen.getByText(/Denied — Off-theme/)).toBeInTheDocument()
  })

  it('falls back to a plain denied label without a reason', () => {
    render(
      <ProposalCard
        proposal={makeProposal({ status: 'denied' })}
        onApply={noop}
        onDeny={noop}
      />,
    )
    expect(screen.getByText('Denied')).toBeInTheDocument()
  })
})
