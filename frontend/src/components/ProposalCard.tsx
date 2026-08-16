import { useState } from 'react'
import { DENIAL_REASONS, type DeckProposal, type ProposalScores } from '../types/api'

interface ProposalCardProps {
  proposal: DeckProposal
  onApply: (id: number) => Promise<void>
  onDeny: (id: number, reason?: string) => Promise<void>
}

/** How the brain map scored this card, as a readable line rather than a number.
 *
 * The SHAPE of the score carries the useful information. A card the crowd loves
 * and a card that uniquely fits this commander can reach the same total by very
 * different routes, and a player deciding whether to keep it wants to know
 * which one they are looking at. */
function ScoreBars({ scores }: { scores: ProposalScores }) {
  const layers = [
    { key: 'consensus', label: 'Played', value: scores.consensus, hint: 'How often decks with this commander run it' },
    { key: 'mechanical', label: 'Fits', value: scores.mechanical, hint: "How well it serves the deck's mechanics" },
    { key: 'personal', label: 'You', value: scores.personal, hint: 'Your own history with this card' },
  ].filter((l) => l.value > 0)

  if (layers.length === 0) return null

  return (
    <div className="proposal-scores">
      {layers.map((layer) => (
        <div key={layer.key} className="proposal-score" title={layer.hint}>
          <span className="proposal-score__label">{layer.label}</span>
          <span className="proposal-score__track">
            <span
              className={`proposal-score__fill proposal-score__fill--${layer.key}`}
              style={{ width: `${Math.round(layer.value * 100)}%` }}
            />
          </span>
        </div>
      ))}
    </div>
  )
}

export function ProposalCard({ proposal, onApply, onDeny }: ProposalCardProps) {
  const [loading, setLoading] = useState(false)
  const [denying, setDenying] = useState(false)

  const handleApply = async () => {
    setLoading(true)
    try { await onApply(proposal.id) } finally { setLoading(false) }
  }

  const handleDeny = async (reason?: string) => {
    setLoading(true)
    try {
      await onDeny(proposal.id, reason)
    } finally {
      setLoading(false)
      setDenying(false)
    }
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
        <span className="proposal-swap__note">
          {approved ? 'Applied' : proposal.denial_reason ? `Denied — ${proposal.denial_reason}` : 'Denied'}
        </span>
      </div>
    )
  }

  return (
    <div className={`proposal-card ${isRemove ? 'proposal-card--remove' : ''}`}>
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
      {proposal.scores ? (
        <>
          <ScoreBars scores={proposal.scores} />
          {proposal.scores.explain && (
            <div className="proposal-card__explain">{proposal.scores.explain}</div>
          )}
        </>
      ) : (
        // No score is a real state, not a rendering gap: the card had no
        // EDHREC entry for this commander, matched no mechanical theme, and
        // has no history. Saying so is more honest than an empty space that
        // reads as a bug — and it tells the player the pick rests on the
        // model's judgement alone.
        proposal.action === 'add' && (
          <div className="proposal-card__explain proposal-card__explain--none">
            No scoring data — picked on rules text alone
          </div>
        )
      )}

      {denying ? (
        // Asking WHY costs one extra click and is the only place the app can
        // learn anything. "No" is not a signal; "wrong slot" is.
        <div className="proposal-card__deny-reasons">
          <span className="proposal-card__deny-prompt">Why pass on it?</span>
          <div className="proposal-card__deny-options">
            {DENIAL_REASONS.map((reason) => (
              <button
                key={reason.key}
                className="btn btn--chip"
                onClick={() => handleDeny(reason.label)}
                disabled={loading}
              >
                {reason.label}
              </button>
            ))}
            <button
              className="btn btn--chip btn--chip-muted"
              onClick={() => handleDeny()}
              disabled={loading}
            >
              Skip
            </button>
          </div>
        </div>
      ) : (
        <div className="proposal-card__actions">
          <button className="btn btn--primary" onClick={handleApply} disabled={loading}>
            {loading ? '…' : 'Approve'}
          </button>
          <button
            className="btn btn--secondary"
            onClick={() => setDenying(true)}
            disabled={loading}
          >
            {loading ? '…' : 'Deny'}
          </button>
        </div>
      )}
    </div>
  )
}
