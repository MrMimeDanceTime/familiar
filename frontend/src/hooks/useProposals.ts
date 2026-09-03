import { useCallback, useMemo, useState } from 'react'
import { api } from '../api/client'
import type { Deck, DeckProposal } from '../types/api'
import type { ProposalBatch } from './useChatStream'

// Summarize a batch for its inline header — e.g. "3 additions, 1 commander".
export function summarizeBatch(proposals: DeckProposal[]): string {
  const adds = proposals.filter((p) => p.action === 'add').length
  const removes = proposals.filter((p) => p.action === 'remove').length
  const cmd = proposals.filter((p) => p.action === 'set_commander').length
  const parts: string[] = []
  if (cmd) parts.push(cmd === 1 ? 'commander' : `${cmd} commanders`)
  if (adds) parts.push(`${adds} ${adds === 1 ? 'addition' : 'additions'}`)
  if (removes) parts.push(`${removes} ${removes === 1 ? 'cut' : 'cuts'}`)
  return parts.length ? `Proposed ${parts.join(', ')}` : 'Proposed changes'
}

// Regroup a conversation's persisted proposals into the batches they were
// created in (proposals from one tool call share a message_id), each anchored
// to that assistant message so the UI can render it inline where it happened.
// Proposals predating the anchoring change have message_id === null and fall
// into a single trailing batch, preserving old behavior for old conversations.
export function groupProposalsIntoBatches(proposals: DeckProposal[]): ProposalBatch[] {
  const byMessage = new Map<number | null, DeckProposal[]>()
  for (const p of proposals) {
    const key = p.message_id ?? null
    const bucket = byMessage.get(key)
    if (bucket) bucket.push(p)
    else byMessage.set(key, [p])
  }
  return [...byMessage.entries()].map(([anchorMessageId, group]) => ({
    summary: summarizeBatch(group),
    proposals: group,
    anchorMessageId,
  }))
}

interface UseProposalsOptions {
  onDeckChanged: (deck: Deck) => void
}

/**
 * The proposal batches on screen and the three things the player can do to
 * one: approve, deny with a reason, or undo an approval.
 *
 * Earlier batches are left exactly as the server has them: when a new batch
 * arrives the server has already withdrawn any stale one, and it says so on
 * the rows, so nothing is faked here.
 */
export function useProposals({ onDeckChanged }: UseProposalsOptions) {
  const [batches, setBatches] = useState<ProposalBatch[]>([])

  const setStatus = useCallback(
    (proposalId: number, patch: Partial<DeckProposal>) => {
      setBatches((prev) =>
        prev.map((batch) => ({
          ...batch,
          proposals: batch.proposals.map((p) => (p.id === proposalId ? { ...p, ...patch } : p)),
        })),
      )
    },
    [],
  )

  const addBatch = useCallback((batch: ProposalBatch) => {
    setBatches((prev) => [...prev, batch])
  }, [])

  // Pin this turn's freshly-streamed batches (still anchor-less) to the
  // assistant bubble that just landed, so they render inline immediately
  // rather than floating at the bottom until the next reload.
  const anchorPending = useCallback((finalMessageId: number) => {
    setBatches((prev) =>
      prev.map((b) => (b.anchorMessageId == null ? { ...b, anchorMessageId: finalMessageId } : b)),
    )
  }, [])

  const apply = useCallback(
    async (proposalId: number) => {
      const updated = await api.applyProposal(proposalId)
      setStatus(proposalId, { status: 'approved' })
      onDeckChanged(updated)
    },
    [onDeckChanged, setStatus],
  )

  const deny = useCallback(
    async (proposalId: number, reason?: string) => {
      await api.denyProposal(proposalId, reason)
      setStatus(proposalId, { status: 'denied', denial_reason: reason ?? null })
    },
    [setStatus],
  )

  const revert = useCallback(
    async (proposalId: number) => {
      const updated = await api.revertProposal(proposalId)
      setStatus(proposalId, { status: 'pending' })
      onDeckChanged(updated)
    },
    [onDeckChanged, setStatus],
  )

  const pendingCount = useMemo(
    () => batches.reduce((sum, b) => sum + b.proposals.filter((p) => p.status === 'pending').length, 0),
    [batches],
  )

  return { batches, setBatches, addBatch, anchorPending, apply, deny, revert, pendingCount }
}
