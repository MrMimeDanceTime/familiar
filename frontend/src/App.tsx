import { useCallback, useEffect, useState } from 'react'
import { api } from './api/client'
import { ChatView } from './components/ChatView'
import { ConversationSidebar } from './components/ConversationSidebar'
import { DeckPanel } from './components/DeckPanel'
import { useChatStream } from './hooks/useChatStream'
import { useDeck } from './hooks/useDeck'
import type { Conversation } from './types/api'
import './styles/global.css'

function App() {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeConversationId, setActiveConversationId] = useState<number | null>(null)
  const { deck, setDeck, loadDeck, clearDeck } = useDeck()
  const { messages, sendMessage, reset, activeTool, isStreaming, error } = useChatStream({
    onDeckUpdated: setDeck,
    onConversationCreated: (id) => {
      setActiveConversationId(id)
      refreshConversations()
    },
  })

  const refreshConversations = useCallback(() => {
    api.listConversations().then(setConversations).catch(() => {})
  }, [])

  useEffect(() => {
    refreshConversations()
  }, [refreshConversations])

  const handleSelectConversation = useCallback(
    async (id: number) => {
      setActiveConversationId(id)
      const detail = await api.getConversation(id)
      reset(
        detail.messages
          .filter((m) => m.role === 'user' || (m.role === 'assistant' && m.text_content))
          .map((m) => ({
            id: `server-${m.id}`,
            role: m.role as 'user' | 'assistant',
            text: m.text_content ?? '',
          })),
      )
      if (detail.conversation.deck_id) {
        loadDeck(detail.conversation.deck_id)
      } else {
        clearDeck()
      }
    },
    [reset, loadDeck, clearDeck],
  )

  const handleNewConversation = useCallback(() => {
    setActiveConversationId(null)
    reset([])
    clearDeck()
  }, [reset, clearDeck])

  const handleSend = useCallback(
    (text: string) => {
      sendMessage(activeConversationId ?? 'new', text)
    },
    [activeConversationId, sendMessage],
  )

  return (
    <div className="app-layout">
      <ConversationSidebar
        conversations={conversations}
        activeId={activeConversationId}
        onSelect={handleSelectConversation}
        onNew={handleNewConversation}
      />
      <ChatView
        messages={messages}
        activeTool={activeTool}
        isStreaming={isStreaming}
        error={error}
        onSend={handleSend}
      />
      <DeckPanel deck={deck} />
    </div>
  )
}

export default App
