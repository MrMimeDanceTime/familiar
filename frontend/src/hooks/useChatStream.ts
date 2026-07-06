import { useCallback, useRef, useState } from 'react'
import { streamChat } from '../api/sse'
import type { Deck, DeckProposal } from '../types/api'

export interface DisplayMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  serverId?: number
}

export interface ProposalBatch {
  summary: string
  proposals: DeckProposal[]
  // The server id of the assistant message this batch belongs to, so it can
  // render inline where it happened. null for a live batch not yet anchored
  // (its assistant message id isn't known until the turn's `done` event).
  anchorMessageId: number | null
}

interface UseChatStreamOptions {
  onDeckUpdated?: (deck: Deck) => void
  onDeckProposal?: (batch: ProposalBatch) => void
  onConversationCreated?: (conversationId: number) => void
  // Fires when a turn finishes, with the persisted id of its final assistant
  // message — used to anchor this turn's proposal batches to that bubble.
  onTurnComplete?: (finalMessageId: number) => void
}

export function useChatStream(options: UseChatStreamOptions = {}) {
  const [messages, setMessages] = useState<DisplayMessage[]>([])
  const [activeTool, setActiveTool] = useState<string | null>(null)
  const [isStreaming, setIsStreaming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const nextId = useRef(0)

  const reset = useCallback((initial: DisplayMessage[] = []) => {
    setMessages(initial)
    nextId.current = initial.length
  }, [])

  const sendMessage = useCallback(
    async (conversationId: number | 'new', text: string) => {
      setError(null)
      setMessages((prev) => [...prev, { id: `local-${nextId.current++}`, role: 'user', text }])
      setIsStreaming(true)
      setActiveTool(null)

      const assistantId = `local-${nextId.current++}`
      let assistantText = ''

      try {
        await streamChat(conversationId, text, (evt) => {
          if (evt.event === 'tool_call') {
            setActiveTool(evt.data.name)
          } else if (evt.event === 'token') {
            assistantText += evt.data.text
            setActiveTool(null)
            setMessages((prev) => {
              const existing = prev.find((m) => m.id === assistantId)
              if (existing) {
                return prev.map((m) => (m.id === assistantId ? { ...m, text: assistantText } : m))
              }
              return [...prev, { id: assistantId, role: 'assistant', text: assistantText }]
            })
          } else if (evt.event === 'deck_proposal') {
            if (evt.data.ok) {
              options.onDeckProposal?.({
                summary: evt.data.summary,
                proposals: evt.data.proposals,
                anchorMessageId: null,
              })
            }
          } else if (evt.event === 'deck_updated') {
            options.onDeckUpdated?.(evt.data)
          } else if (evt.event === 'done') {
            // Stamp the just-streamed assistant bubble with its persisted id so
            // this turn's proposal batches (created with a null anchor) can be
            // pinned under it without waiting for a reload.
            const finalMessageId = evt.data.message_id
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId ? { ...m, serverId: finalMessageId } : m,
              ),
            )
            options.onTurnComplete?.(finalMessageId)
            options.onConversationCreated?.(evt.data.conversation_id)
          } else if (evt.event === 'error') {
            setError(evt.data.message)
          }
        })
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        setIsStreaming(false)
        setActiveTool(null)
      }
    },
    [options],
  )

  return { messages, sendMessage, reset, activeTool, isStreaming, error }
}
