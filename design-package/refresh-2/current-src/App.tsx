import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api/client'
import { CardPinProvider, useCardPins } from './components/CardPinContext'
import { ChatView } from './components/ChatView'
import { Sidebar } from './components/ConversationSidebar'
import type { SidebarTab } from './components/ConversationSidebar'
import { DeckDetail } from './components/DeckDetail'
import { DeckPanel } from './components/DeckPanel'
import { MobileHeader } from './components/MobileHeader'
import type { MobileTab } from './components/MobileNav'
import { MobileNav } from './components/MobileNav'
import { PinnedTray } from './components/PinnedTray'
import { PreferencesPanel } from './components/PreferencesPanel'
import { useChatStream } from './hooks/useChatStream'
import type { ProposalBatch } from './hooks/useChatStream'
import { useDeck } from './hooks/useDeck'
import type { Conversation, Deck, DeckStats } from './types/api'
import './styles/global.css'

type MobileView = 'chats-list' | 'chat' | 'decks-list' | 'deck-detail' | 'pins'

function App() {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [decks, setDecks] = useState<Deck[]>([])
  const [sidebarTab, setSidebarTab] = useState<SidebarTab>('conversations')
  const [activeConversationId, setActiveConversationId] = useState<number | null>(null)
  const [deckStats, setDeckStats] = useState<DeckStats | null>(null)
  const [proposalBatches, setProposalBatches] = useState<ProposalBatch[]>([])
  const [prefsOpen, setPrefsOpen] = useState(false)
  const { deck, setDeck, loadDeck, clearDeck } = useDeck()
  const { messages, sendMessage, reset, activeTool, isStreaming, error } = useChatStream({
    onDeckUpdated: setDeck,
    onDeckProposal: (batch) => {
      setProposalBatches((prev) => {
        const cleaned = prev.map((b) => ({
          ...b,
          proposals: b.proposals.map((p) =>
            p.status === 'pending' ? { ...p, status: 'denied' as const } : p,
          ),
        }))
        return [...cleaned, batch]
      })
    },
    onConversationCreated: (id) => {
      setActiveConversationId(id)
      refreshConversations()
      refreshDecks()
    },
  })

  // ── Mobile view state ──────────────────────────────────────────────────
  const [mobileTab, setMobileTab] = useState<MobileTab>('chats')
  const [mobileView, setMobileView] = useState<MobileView>('chats-list')
  const tabLastView = useRef<Record<MobileTab, MobileView>>({
    chats: 'chats-list',
    decks: 'decks-list',
    pins: 'pins',
  })

  // Remember the last sub-view for each tab so switching tabs restores it.
  useEffect(() => {
    if (mobileView === 'chat' || mobileView === 'chats-list') {
      tabLastView.current.chats = mobileView
    } else if (mobileView === 'deck-detail' || mobileView === 'decks-list') {
      tabLastView.current.decks = mobileView
    } else {
      tabLastView.current.pins = mobileView
    }
  }, [mobileView])

  const refreshConversations = useCallback(() => {
    api.listConversations().then(setConversations).catch(() => {})
  }, [])

  const refreshDecks = useCallback(() => {
    api.listDecks().then(setDecks).catch(() => {})
  }, [])

  const loadStats = useCallback((deckId: number) => {
    api.getDeckStats(deckId).then(setDeckStats).catch(() => setDeckStats(null))
  }, [])

  useEffect(() => {
    refreshConversations()
    refreshDecks()
  }, [refreshConversations, refreshDecks])

  useEffect(() => {
    if (deck) {
      loadStats(deck.id)
    } else {
      setDeckStats(null)
    }
  }, [deck, loadStats])

  const handleSelectConversation = useCallback(
    async (id: number) => {
      setActiveConversationId(id)
      setSidebarTab('conversations')
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
      const pending = detail.proposals.filter((p) => p.status === 'pending')
      if (pending.length > 0) {
        setProposalBatches([{ summary: 'Pending proposals', proposals: pending }])
      } else {
        setProposalBatches([])
      }
    },
    [reset, loadDeck, clearDeck],
  )

  const handleNewConversation = useCallback(() => {
    setActiveConversationId(null)
    reset([])
    clearDeck()
    setProposalBatches([])
  }, [reset, clearDeck])

  const handleDeleteConversation = useCallback(
    async (id: number) => {
      await api.deleteConversation(id).catch(() => {})
      if (id === activeConversationId) {
        handleNewConversation()
      }
      refreshConversations()
      refreshDecks()
    },
    [activeConversationId, handleNewConversation, refreshConversations, refreshDecks],
  )

  const navigateToDeckConversation = useCallback(
    async (deckId: number) => {
      const conv = await api.startDeckConversation(deckId)
      refreshDecks()
      setActiveConversationId(conv.id)
      setSidebarTab('conversations')
      reset([])
      loadDeck(deckId)
    },
    [refreshDecks, reset, loadDeck],
  )

  const handleSelectDeck = useCallback(
    async (d: Deck) => {
      loadDeck(d.id)
    },
    [loadDeck],
  )

  const handleDeckUpdated = useCallback(
    (updated: Deck) => {
      setDeck(updated)
      refreshDecks()
    },
    [setDeck, refreshDecks],
  )

  const handleNewDeck = useCallback(async () => {
    const d = await api.createDeck()
    refreshDecks()
    loadDeck(d.id)
  }, [refreshDecks, loadDeck])

  const handleDeleteDeck = useCallback(
    async (id: number) => {
      await api.deleteDeck(id).catch(() => {})
      if (deck?.id === id) {
        clearDeck()
      }
      refreshDecks()
    },
    [deck, clearDeck, refreshDecks],
  )

  const handleQuickStart = useCallback(
    async (deckId: number, prompt: string) => {
      const d = decks.find((d) => d.id === deckId)
      let convId: number
      if (d?.conversation_id) {
        convId = d.conversation_id
      } else {
        const conv = await api.startDeckConversation(deckId)
        refreshDecks()
        convId = conv.id
      }
      setActiveConversationId(convId)
      setSidebarTab('conversations')
      const detail = await api.getConversation(convId)
      if (detail.messages.length === 0) {
        loadDeck(deckId)
        reset([{ id: 'quickstart', role: 'user' as const, text: prompt }])
        sendMessage(convId, prompt)
      } else {
        handleSelectConversation(convId)
      }
    },
    [decks, refreshDecks, reset, sendMessage, handleSelectConversation, loadDeck],
  )

  const handleApplyProposal = useCallback(
    async (proposalId: number) => {
      const updatedDeck = await api.applyProposal(proposalId)
      setDeck(updatedDeck)
      setProposalBatches((prev) =>
        prev.map((batch) => ({
          ...batch,
          proposals: batch.proposals.map((p) =>
            p.id === proposalId ? { ...p, status: 'approved' as const } : p,
          ),
        })),
      )
      loadStats(updatedDeck.id)
    },
    [setDeck, loadStats],
  )

  const handleDenyProposal = useCallback(
    async (proposalId: number) => {
      await api.denyProposal(proposalId)
      setProposalBatches((prev) =>
        prev.map((batch) => ({
          ...batch,
          proposals: batch.proposals.map((p) =>
            p.id === proposalId ? { ...p, status: 'denied' as const } : p,
          ),
        })),
      )
    },
    [],
  )

  const handleSend = useCallback(
    (text: string) => {
      sendMessage(activeConversationId ?? 'new', text)
    },
    [activeConversationId, sendMessage],
  )

  // ── Mobile handlers ────────────────────────────────────────────────────

  const handleMobileSelectConv = useCallback(
    async (id: number) => {
      await handleSelectConversation(id)
      setMobileView('chat')
    },
    [handleSelectConversation],
  )

  const handleMobileSelectDeck = useCallback(
    async (d: Deck) => {
      loadDeck(d.id)
      setMobileView('deck-detail')
    },
    [loadDeck],
  )

  const handleMobileNewConversation = useCallback(() => {
    handleNewConversation()
    setMobileView('chat')
  }, [handleNewConversation])

  const handleMobileTabChange = useCallback((tab: MobileTab) => {
    setMobileTab(tab)
    setMobileView(tabLastView.current[tab])
  }, [])

  const pendingCount =
    proposalBatches.reduce((sum, b) => sum + b.proposals.filter((p) => p.status === 'pending').length, 0)

  // ── Derived state ──────────────────────────────────────────────────────

  const showDeckDetail = sidebarTab === 'decks' && deck !== null
  const cardNames = deck?.cards.map((c) => c.name) ?? []

  return (
    <CardPinProvider>
      <div className={`app-layout ${showDeckDetail ? 'app-layout--deck-detail' : ''}`}>
        {/* ── Desktop layout ────────────────────────────────────────────── */}
        <Sidebar
          tab={sidebarTab}
          onTabChange={setSidebarTab}
          conversations={conversations}
          activeId={activeConversationId}
          onSelect={handleSelectConversation}
          onNew={handleNewConversation}
          onDelete={handleDeleteConversation}
          decks={decks}
          activeDeckId={deck?.id ?? null}
          onSelectDeck={handleSelectDeck}
          onNewDeck={handleNewDeck}
          onDeleteDeck={handleDeleteDeck}
          onOpenPreferences={() => setPrefsOpen(true)}
        />
        {showDeckDetail ? (
          <DeckDetail
            deck={deck}
            stats={deckStats}
            onDeckUpdated={handleDeckUpdated}
            onStartConversation={navigateToDeckConversation}
            onSelectConversation={handleSelectConversation}
            onQuickStart={handleQuickStart}
          />
        ) : (
          <>
            <ChatView
              messages={messages}
              activeTool={activeTool}
              isStreaming={isStreaming}
              error={error}
              onSend={handleSend}
              proposalBatches={proposalBatches}
              onApplyProposal={handleApplyProposal}
              onDenyProposal={handleDenyProposal}
              cardNames={cardNames}
            />
            <DeckPanel deck={deck} stats={deckStats} onDeckUpdated={handleDeckUpdated} onStartConversation={navigateToDeckConversation} onSelectConversation={handleSelectConversation} />
          </>
        )}

        {/* ── Mobile layout (hidden on desktop via CSS) ─────────────────── */}
        <div className="mobile-layout">
          <MobileHeader
            showBack={mobileView === 'chat' || mobileView === 'deck-detail'}
            onBack={() => setMobileView(mobileTab === 'chats' ? 'chats-list' : 'decks-list')}
            title={
              mobileView === 'chats-list' ? 'Chats' :
              mobileView === 'decks-list' ? 'Decks' :
              mobileView === 'pins' ? 'Pinned' :
              undefined
            }
          >
            {mobileView === 'chats-list' && (
              <button className="btn btn--ghost btn--mono" onClick={handleMobileNewConversation}>
                + New
              </button>
            )}
            {mobileView === 'decks-list' && (
              <button className="btn btn--ghost btn--mono" onClick={handleNewDeck}>
                + New
              </button>
            )}
          </MobileHeader>

          <div className="mobile-content">
            {mobileView === 'chats-list' && (
              <div className="mobile-list">
                {conversations.length === 0 && (
                  <p className="mobile-list__empty">No conversations yet. Start a new one.</p>
                )}
                {conversations.map((c) => (
                  <button
                    key={c.id}
                    className={`mobile-list__item ${c.id === activeConversationId ? 'mobile-list__item--active' : ''}`}
                    onClick={() => handleMobileSelectConv(c.id)}
                  >
                    <span className="mobile-list__item-title">{c.title || 'Untitled'}</span>
                    <span className="mobile-list__item-date">
                      {new Date(c.updated_at).toLocaleDateString()}
                    </span>
                  </button>
                ))}
              </div>
            )}

            {mobileView === 'chat' && (
              <ChatView
                messages={messages}
                activeTool={activeTool}
                isStreaming={isStreaming}
                error={error}
                onSend={handleSend}
                proposalBatches={proposalBatches}
                onApplyProposal={handleApplyProposal}
                onDenyProposal={handleDenyProposal}
                cardNames={cardNames}
              />
            )}

            {mobileView === 'decks-list' && (
              <div className="mobile-list">
                {decks.length === 0 && (
                  <p className="mobile-list__empty">No decks yet. Create one to get started.</p>
                )}
                {decks.map((d) => (
                  <button
                    key={d.id}
                    className={`mobile-list__item ${d.id === deck?.id ? 'mobile-list__item--active' : ''}`}
                    onClick={() => handleMobileSelectDeck(d)}
                  >
                    <span className="mobile-list__item-title">{d.name}</span>
                    {d.commander && <span className="mobile-list__item-deck">{d.commander}</span>}
                  </button>
                ))}
              </div>
            )}

            {mobileView === 'deck-detail' && deck && (
              <DeckDetail
                deck={deck}
                stats={deckStats}
                onDeckUpdated={handleDeckUpdated}
                onStartConversation={navigateToDeckConversation}
                onSelectConversation={handleSelectConversation}
                onQuickStart={handleQuickStart}
              />
            )}

            {mobileView === 'pins' && <MobilePinsView />}
          </div>

          <MobileNav
            activeTab={mobileTab}
            onTabChange={handleMobileTabChange}
            proposalCount={pendingCount}
          />
        </div>

        <PreferencesPanel open={prefsOpen} onClose={() => setPrefsOpen(false)} />
      </div>
      <PinnedTray />
    </CardPinProvider>
  )
}

/** Full-screen pinned-card view for the mobile Pins tab. */
function MobilePinsView() {
  const { pinned, unpin, clear } = useCardPins()

  if (pinned.length === 0) {
    return (
      <div className="mobile-list">
        <p className="mobile-list__empty">
          No cards pinned yet. Tap a card name in a message or deck list to pin it for comparison.
        </p>
      </div>
    )
  }

  return (
    <div className="mobile-pins">
      <div className="mobile-pins__head">
        <span className="micro-label">Pinned · {pinned.length}</span>
        <button className="pinned-tray__clear" onClick={clear}>Clear all</button>
      </div>
      <div className="mobile-pins__grid">
        {pinned.map((name) => (
          <div key={name} className="mobile-pins__card">
            <img
              src={`https://api.scryfall.com/cards/named?exact=${encodeURIComponent(name)}&format=image`}
              alt={name}
              loading="lazy"
            />
            <button
              className="pinned-card__close"
              onClick={() => unpin(name)}
              title={`Unpin ${name}`}
              aria-label={`Unpin ${name}`}
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}

export default App
