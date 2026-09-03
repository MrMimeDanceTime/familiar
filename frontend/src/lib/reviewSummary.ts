import type { DeckProposal } from '../types/api'

// Reasons the app writes rather than the player; not decisions to report.
const APP_REASONS = new Set(['withdrawn', 'superseded'])

function label(p: DeckProposal): string {
  return p.card_name ?? p.commander_name ?? 'a proposal'
}

/**
 * The message "Done reviewing" sends.
 *
 * It used to be a fixed "let's continue", so every review cost the model a
 * deck read just to learn what was approved. Naming the decisions, with the
 * reasons the player chose, hands it what it needs to adapt without a call.
 */
export function reviewSummary(proposals: DeckProposal[]): string {
  const approved = proposals.filter((p) => p.status === 'approved').map(label)
  const denied = proposals
    .filter((p) => p.status === 'denied' && !APP_REASONS.has(p.denial_reason ?? ''))
    .map((p) => (p.denial_reason ? `${label(p)} (${p.denial_reason})` : label(p)))

  const parts: string[] = ["I've reviewed the proposals."]
  if (approved.length) parts.push(`Approved: ${approved.join(', ')}.`)
  if (denied.length) parts.push(`Denied: ${denied.join(', ')}.`)
  parts.push("Let's continue.")
  return parts.join(' ')
}
