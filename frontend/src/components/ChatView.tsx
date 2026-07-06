import { useCallback, useEffect, useRef, useState } from 'react'
import type { DisplayMessage, ProposalBatch } from '../hooks/useChatStream'
import { FamiliarMark } from './icons'
import { MessageBubble } from './MessageBubble'
import { ProposalCard } from './ProposalCard'
import { ToolActivityIndicator } from './ToolActivityIndicator'

interface ProposalBatchBlockProps {
  batch: ProposalBatch
  isLastBatch: boolean
  isStreaming: boolean
  isDismissed: boolean
  onApply: (id: number) => Promise<void>
  onDeny: (id: number) => Promise<void>
  onContinue: () => void
}

function ProposalBatchBlock({
  batch, isLastBatch, isStreaming, isDismissed, onApply, onDeny, onContinue,
}: ProposalBatchBlockProps) {
  const pending = batch.proposals.filter((p) => p.status === 'pending')
  const resolved = batch.proposals.filter((p) => p.status !== 'pending')
  const allResolved = pending.length === 0
  const approvedCount = resolved.filter((p) => p.status === 'approved').length
  const deniedCount = resolved.filter((p) => p.status === 'denied').length

  // A fully-resolved batch from earlier in the conversation collapses to a
  // one-line marker so the transcript stays readable; click to expand it.
  const [expanded, setExpanded] = useState(false)
  const collapsible = allResolved

  return (
    <div className={`proposal-batch ${allResolved ? 'proposal-batch--resolved' : ''}`}>
      {collapsible ? (
        <button
          className="proposal-batch__marker"
          onClick={() => setExpanded((e) => !e)}
          aria-expanded={expanded}
        >
          <span className="proposal-batch__marker-caret">{expanded ? '▾' : '▸'}</span>
          <span className="proposal-batch__marker-summary">{batch.summary}</span>
          {approvedCount > 0 && (
            <span className="proposal-batch__tally proposal-batch__tally--approved">
              ✓ {approvedCount}
            </span>
          )}
          {deniedCount > 0 && (
            <span className="proposal-batch__tally proposal-batch__tally--denied">
              ✗ {deniedCount}
            </span>
          )}
        </button>
      ) : (
        <div className="proposal-batch__summary">{batch.summary}</div>
      )}

      {(!collapsible || expanded) && resolved.map((p) => (
        <ProposalCard key={p.id} proposal={p} onApply={onApply} onDeny={onDeny} />
      ))}

      {pending.map((p) => (
        <ProposalCard key={p.id} proposal={p} onApply={onApply} onDeny={onDeny} />
      ))}

      {allResolved && isLastBatch && !isStreaming && !isDismissed && (
        <button className="proposal-batch__continue" onClick={onContinue}>
          Done reviewing →
        </button>
      )}
    </div>
  )
}

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

  // Living-mascot state for the latest assistant message: glimmer while a
  // reply is streaming, otherwise a one-shot "curious" flourish every 5–10s
  // while idle. Bumping curiousKey remounts the tilt wrapper so the animation
  // replays; the flourish self-clears after it finishes.
  const [curiousKey, setCuriousKey] = useState(0)
  const [curious, setCurious] = useState(false)

  useEffect(() => {
    if (isStreaming) {
      setCurious(false)
      return
    }
    let flourish: ReturnType<typeof setTimeout>
    let settle: ReturnType<typeof setTimeout>
    const schedule = () => {
      flourish = setTimeout(() => {
        setCurious(true)
        setCuriousKey((k) => k + 1)
        settle = setTimeout(() => {
          setCurious(false)
          schedule()
        }, 2600)
      }, 5000 + Math.random() * 5000)
    }
    schedule()
    return () => { clearTimeout(flourish); clearTimeout(settle) }
  }, [isStreaming])

  const mascotState = isStreaming ? 'glimmer' : curious ? 'curious' : 'breathing'
  const lastAssistantId = [...messages].reverse().find((m) => m.role === 'assistant')?.id ?? null

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

  // Anchor each batch under the assistant message that produced it. Batches
  // with no anchor (live batches this turn, or legacy proposals from before
  // anchoring existed) fall through to a trailing group after all messages.
  const anchoredMessageIds = new Set(
    messages.map((m) => m.serverId).filter((id): id is number => id != null),
  )
  const batchesByMessage = new Map<number, { batch: ProposalBatch; index: number }[]>()
  const trailingBatches: { batch: ProposalBatch; index: number }[] = []
  proposalBatches.forEach((batch, index) => {
    const anchor = batch.anchorMessageId
    if (anchor != null && anchoredMessageIds.has(anchor)) {
      const list = batchesByMessage.get(anchor) ?? []
      list.push({ batch, index })
      batchesByMessage.set(anchor, list)
    } else {
      trailingBatches.push({ batch, index })
    }
  })

  const lastBatchIndex = proposalBatches.length - 1

  const renderBatch = ({ batch, index }: { batch: ProposalBatch; index: number }) => (
    <ProposalBatchBlock
      key={`batch-${index}`}
      batch={batch}
      isLastBatch={index === lastBatchIndex}
      isStreaming={isStreaming}
      isDismissed={dismissedBatches.has(index)}
      onApply={onApplyProposal}
      onDeny={onDenyProposal}
      onContinue={() => handleContinue(index)}
    />
  )

  return (
    <div className="chat-view">
      <div className="chat-view__messages" ref={scrollRef}>
        {messages.length === 0 && (
          <div className="chat-view__empty">
            <FamiliarMark size={56} outline />
            <p>Tell Familiar about a commander idea — even a weird one.</p>
          </div>
        )}
        {messages.map((m) => (
          <div key={m.id}>
            <MessageBubble
              message={m}
              cardNames={cardNames}
              mascotState={m.id === lastAssistantId ? mascotState : 'breathing'}
              mascotFlourishKey={curiousKey}
            />
            {m.serverId != null && batchesByMessage.get(m.serverId)?.map(renderBatch)}
          </div>
        ))}
        {trailingBatches.map(renderBatch)}
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
