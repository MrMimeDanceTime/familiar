import { useState } from 'react'
import type { DeckProposal } from '../types/api'

interface ProposalCardProps {
  proposal: DeckProposal
  onApply: (id: number) => Promise<void>
  onDeny: (id: number) => Promise<void>
}

export function ProposalCard({ proposal, onApply, onDeny }: ProposalCardProps) {
  const [loading, setLoading] = useState(false)

  const handleApply = async () => {
    setLoading(true)
    try { await onApply(proposal.id) } finally { setLoading(false) }
  }

  const handleDeny = async () => {
    setLoading(true)
    try { await onDeny(proposal.id) } finally { setLoading(false) }
  }

  const name = proposal.card_name ?? proposal.commander_name
  const isRemove = proposal.action === 'remove'

  if (proposal.status !== 'pending') {
    const approved = proposal.status === 'approved'
    return (
      <div className="proposal-swap proposal-swap--resolved">
        <span className={`proposal-chip ${approved ? 'proposal-chip--add' : 'proposal-chip--deny'}`}>
          {approved ? '✓' : '✗'}
        </span>
        <span className="proposal-swap__name">{name}</span>
        <span className="proposal-swap__note">{approved ? 'Applied' : 'Denied'}</span>
      </div>
    )
  }

  return (
    <div className="proposal-card">
      <div className="proposal-card__head">
        <span className="proposal-card__label">Proposed change</span>
        <span className="proposal-card__meta">
          {proposal.action === 'set_commander'
            ? 'commander'
            : `${proposal.quantity} card${proposal.quantity === 1 ? '' : 's'}`}
        </span>
      </div>
      <div className="proposal-swap">
        <span className={`proposal-chip ${isRemove ? 'proposal-chip--remove' : 'proposal-chip--add'}`}>
          {isRemove ? '−' : '+'}
        </span>
        <span className="proposal-swap__name">{name}</span>
        {proposal.category && <span className="proposal-swap__note">{proposal.category}</span>}
      </div>
      {proposal.reasoning && <div className="proposal-card__rationale">{proposal.reasoning}</div>}
      <div className="proposal-card__actions">
        <button className="btn btn--primary" onClick={handleApply} disabled={loading}>
          {loading ? '…' : 'Approve'}
        </button>
        <button className="btn btn--secondary" onClick={handleDeny} disabled={loading}>
          {loading ? '…' : 'Deny'}
        </button>
      </div>
    </div>
  )
}
