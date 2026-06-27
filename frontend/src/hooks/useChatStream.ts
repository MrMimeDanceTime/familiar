import { useCallback, useRef, useState } from 'react'
import { streamChat } from '../api/sse'
import type { Deck } from '../types/api'

export interface DisplayMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
}

interface UseChatStreamOptions {
  onDeckUpdated?: (deck: Deck) => void
  onConversationCreated?: (conversationId: number) => void
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
          } else if (evt.event === 'deck_updated') {
            options.onDeckUpdated?.(evt.data)
          } else if (evt.event === 'done') {
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
