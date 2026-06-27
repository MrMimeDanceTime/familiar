import { useEffect, useRef, useState } from 'react'
import type { DisplayMessage } from '../hooks/useChatStream'
import { MessageBubble } from './MessageBubble'
import { ToolActivityIndicator } from './ToolActivityIndicator'

interface ChatViewProps {
  messages: DisplayMessage[]
  activeTool: string | null
  isStreaming: boolean
  error: string | null
  onSend: (text: string) => void
}

export function ChatView({ messages, activeTool, isStreaming, error, onSend }: ChatViewProps) {
  const [draft, setDraft] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [messages, activeTool])

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const text = draft.trim()
    if (!text || isStreaming) return
    setDraft('')
    onSend(text)
  }

  return (
    <div className="chat-view">
      <div className="chat-view__messages" ref={scrollRef}>
        {messages.length === 0 && (
          <div className="chat-view__empty">
            Tell Familiar about a commander idea — even a weird one.
          </div>
        )}
        {messages.map((m) => (
          <MessageBubble key={m.id} message={m} />
        ))}
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
