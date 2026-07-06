import { useCallback, useEffect, useRef, useState } from 'react'
import type { DisplayMessage, ProposalBatch } from '../hooks/useChatStream'
import { MessageBubble } from './MessageBubble'
import { ProposalCard } from './ProposalCard'
import { ToolActivityIndicator } from './ToolActivityIndicator'

interface ChatViewProps {
  messages: DisplayMessage[]
  activeTool: string | null
  isStreaming: boolean
  error: string | null
  onSend: (text: string) => void
  proposalBatches: ProposalBatch[]
  onApplyProposal: (id: number) => Promise<void>
  onDenyProposal: (id: number) => Promise<void>
  cardNames: string[]
}

export function ChatView({ messages, activeTool, isStreaming, error, onSend, proposalBatches, onApplyProposal, onDenyProposal, cardNames }: ChatViewProps) {
  const [draft, setDraft] = useState('')
  const [dismissedBatches, setDismissedBatches] = useState<Set<number>>(new Set())
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [messages, activeTool, proposalBatches])

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const text = draft.trim()
    if (!text || isStreaming) return
    setDraft('')
    onSend(text)
  }

  const handleContinue = useCallback((batchIndex: number) => {
    setDismissedBatches((prev) => new Set(prev).add(batchIndex))
    onSend("I've reviewed the proposals. Let's continue.")
  }, [onSend])

  return (
    <div className="chat-view">
      <div className="chat-view__messages" ref={scrollRef}>
        {messages.length === 0 && (
          <div className="chat-view__empty">
            Tell Familiar about a commander idea — even a weird one.
          </div>
        )}
        {messages.map((m) => (
          <MessageBubble key={m.id} message={m} cardNames={cardNames} />
        ))}
        {proposalBatches.map((batch, bi) => {
          const pending = batch.proposals.filter((p) => p.status === 'pending')
          const resolved = batch.proposals.filter((p) => p.status !== 'pending')
          const allResolved = pending.length === 0
          const isDismissed = dismissedBatches.has(bi)
          const isLastBatch = bi === proposalBatches.length - 1

          return (
            <div key={`batch-${bi}`} className={`proposal-batch ${allResolved ? 'proposal-batch--resolved' : ''}`}>
              <div className="proposal-batch__summary">{batch.summary}</div>

              {resolved.length > 0 && (
                <div className="proposal-batch__resolved-summary">
                  {resolved.filter((p) => p.status === 'approved').length > 0 && (
                    <span className="proposal-batch__tally proposal-batch__tally--approved">
                      ✓ {resolved.filter((p) => p.status === 'approved').length} approved
                    </span>
                  )}
                  {resolved.filter((p) => p.status === 'denied').length > 0 && (
                    <span className="proposal-batch__tally proposal-batch__tally--denied">
                      ✗ {resolved.filter((p) => p.status === 'denied').length} denied
                    </span>
                  )}
                </div>
              )}

              {pending.map((p) => (
                <ProposalCard
                  key={p.id}
                  proposal={p}
                  onApply={onApplyProposal}
                  onDeny={onDenyProposal}
                />
              ))}

              {allResolved && isLastBatch && !isStreaming && !isDismissed && (
                <button
                  className="proposal-batch__continue"
                  onClick={() => handleContinue(bi)}
                >
                  Done reviewing →
                </button>
              )}
            </div>
          )
        })}
        {activeTool && <ToolActivityIndicator toolName={activeTool} />}
        {error && <div className="chat-view__error">{error}</div>}
      </div>
      <form className="chat-view__input" onSubmit={handleSubmit}>
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              handleSubmit(e)
            }
          }}
          placeholder="Brainstorm a deck idea…"
          disabled={isStreaming}
        />
        <button type="submit" disabled={isStreaming || !draft.trim()}>
          Send
        </button>
      </form>
    </div>
  )
}
