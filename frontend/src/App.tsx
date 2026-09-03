import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api/client'
import { CardPinProvider } from './components/CardPinContext'
import { ChatView } from './components/ChatView'
import { Sidebar } from './components/ConversationSidebar'
import type { SidebarTab } from './components/ConversationSidebar'
import { DeckDetail } from './components/DeckDetail'
import { DeckPanel } from './components/DeckPanel'
import { GearIcon, MoonIcon, SunIcon } from './components/icons'
import { MobileHeader } from './components/MobileHeader'
import type { MobileTab } from './components/MobileNav'
import { MobileNav } from './components/MobileNav'
import { MobileConversationList, MobileDeckList, MobilePinsView } from './components/MobileViews'
import { PinnedTray } from './components/PinnedTray'
import { PreferencesPanel } from './components/PreferencesPanel'
import { useChatStream } from './hooks/useChatStream'
import { useDeck } from './hooks/useDeck'
import { useDeckStats } from './hooks/useDeckStats'
import { groupProposalsIntoBatches, useProposals } from './hooks/useProposals'
import { useTheme } from './hooks/useTheme'
import type { Conversation, Deck, DeckSummary } from './types/api'
import './styles/global.css'

type MobileView = 'chats-list' | 'chat' | 'decks-list' | 'deck-detail' | 'pins'

function App() {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [decks, setDecks] = useState<DeckSummary[]>([])
  const [sidebarTab, setSidebarTab] = useState<SidebarTab>('conversations')
  const [activeConversationId, setActiveConversationId] = useState<number | null>(null)
  const [prefsOpen, setPrefsOpen] = useState(false)
  const { isDark, toggle: toggleTheme } = useTheme()
  const { deck, setDeck, loadDeck, clearDeck } = useDeck()
  const { stats: deckStats, nuanceLoading, loadStats } = useDeckStats(deck)

  const refreshConversations = useCallback(() => {
    api.listConversations().then(setConversations).catch(() => {})
  }, [])

  const refreshDecks = useCallback(() => {
    api.listDecks().then(setDecks).catch(() => {})
  }, [])

  // Deck auto-naming runs server-side in the background (it's a slow LLM
  // call), so a freshly-committed commander leaves the deck "Untitled Deck"
  // in the approve response. Poll the deck a few times to pick up the
  // generated name once it lands, then refresh the sidebar list.
  const pollForName = useCallback((deckId: number) => {
    let tries = 0
    const poll = async () => {
      tries += 1
      const fresh = await api.getDeck(deckId).catch(() => null)
      if (fresh && fresh.name !== 'Untitled Deck') {
        // Only replace the panel deck if the user is still viewing this one.
        setDeck((cur) => (cur && cur.id === fresh.id ? fresh : cur))
        refreshDecks()
      } else if (tries < 6) {
        setTimeout(poll, 2000)
      }
    }
    setTimeout(poll, 2000)
  }, [setDeck, refreshDecks])

  const onProposalDeckChanged = useCallback((updated: Deck) => {
    setDeck(updated)
    loadStats(updated.id)
    if (updated.name === 'Untitled Deck' && updated.commander) pollForName(updated.id)
  }, [setDeck, loadStats, pollForName])

  const proposals = useProposals({ onDeckChanged: onProposalDeckChanged })
  const { batches: proposalBatches, setBatches: setProposalBatches, pendingCount } = proposals

  const { messages, sendMessage, reset, activeTool, isStreaming, error } = useChatStream({
    onDeckUpdated: setDeck,
    onDeckProposal: proposals.addBatch,
    onTurnComplete: proposals.anchorPending,
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

  useEffect(() => {
    refreshConversations()
    refreshDecks()
  }, [refreshConversations, refreshDecks])

  const handleSelectConversation = useCallback(
    async (id: number) => {
      setActiveConversationId(id)
      setSidebarTab('conversations')
      const detail = await api.getConversation(id)
      // Text the model wrote alongside a tool call is stored as its own
      // message; live, it streamed into the same bubble as the reply that
      // followed. Merge consecutive assistant messages so a reload reads the
      // way the turn did, keeping the final message's id for anchoring.
      const merged: { id: string; role: 'user' | 'assistant'; text: string; serverId: number }[] = []
      for (const m of detail.messages) {
        if (!(m.role === 'user' || (m.role === 'assistant' && m.text_content))) continue
        const last = merged[merged.length - 1]
        if (m.role === 'assistant' && last?.role === 'assistant') {
          last.text = `${last.text}\n\n${m.text_content ?? ''}`
          last.serverId = m.id
          last.id = `server-${m.id}`
          continue
        }
        merged.push({
          id: `server-${m.id}`,
          role: m.role as 'user' | 'assistant',
          text: m.text_content ?? '',
          serverId: m.id,
        })
      }
      reset(merged)
      if (detail.conversation.deck_id) {
        loadDeck(detail.conversation.deck_id)
      } else {
        clearDeck()
      }
      setProposalBatches(groupProposalsIntoBatches(detail.proposals))
    },
    [reset, loadDeck, clearDeck, setProposalBatches],
  )

  const handleNewConversation = useCallback(() => {
    setActiveConversationId(null)
    reset([])
    clearDeck()
    setProposalBatches([])
  }, [reset, clearDeck, setProposalBatches])

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
    async (d: DeckSummary) => {
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

  const handleApplyProposal = proposals.apply
  const handleDenyProposal = proposals.deny
  const handleRevertProposal = proposals.revert

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
    async (d: DeckSummary) => {
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

  // ── Derived state ──────────────────────────────────────────────────────

  const showDeckDetail = sidebarTab === 'decks' && deck !== null
  // The mobile list views have header room to spare; chat and deck-detail spend
  // theirs on the back button and title.
  const isMobileListView =
    mobileView === 'chats-list' || mobileView === 'decks-list' || mobileView === 'pins'
  // Card names to make hoverable/pinnable in chat: everything in the deck PLUS
  // every card named in a proposal this conversation. Cards Familiar suggests
  // live in proposals before (and whether or not) they're added to the deck, so
  // without the proposal names its in-chat suggestions were never pinnable.
  const cardNames = useMemo(() => {
    const names = new Set<string>()
    for (const c of deck?.cards ?? []) names.add(c.name)
    for (const b of proposalBatches) {
      for (const p of b.proposals) {
        if (p.card_name) names.add(p.card_name)
        if (p.commander_name) names.add(p.commander_name)
      }
    }
    return [...names]
  }, [deck, proposalBatches])

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
            nuanceLoading={nuanceLoading}
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
              onRevertProposal={handleRevertProposal}
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
            {/* Theme and preferences otherwise live only in the desktop
                sidebar, which is display:none under the mobile breakpoint —
                so on a phone they were unreachable rather than merely hidden.
                Shown on list views only; the chat and deck-detail views need
                their header room for the back button and title. */}
            {isMobileListView && (
              <>
                <button
                  className="mobile-header__icon-btn"
                  onClick={toggleTheme}
                  title={isDark ? 'Switch to light theme' : 'Switch to dark theme'}
                  aria-label={isDark ? 'Switch to light theme' : 'Switch to dark theme'}
                >
                  {isDark ? <SunIcon /> : <MoonIcon />}
                </button>
                <button
                  className="mobile-header__icon-btn"
                  onClick={() => setPrefsOpen(true)}
                  title="Preferences"
                  aria-label="Preferences"
                >
                  <GearIcon />
                </button>
              </>
            )}
          </MobileHeader>

          <div className="mobile-content">
            {mobileView === 'chats-list' && (
              <MobileConversationList
                conversations={conversations}
                activeId={activeConversationId}
                onSelect={handleMobileSelectConv}
              />
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
                onRevertProposal={handleRevertProposal}
                cardNames={cardNames}
              />
            )}

            {mobileView === 'decks-list' && (
              <MobileDeckList decks={decks} activeId={deck?.id ?? null} onSelect={handleMobileSelectDeck} />
            )}

            {mobileView === 'deck-detail' && deck && (
              <DeckDetail
                deck={deck}
                stats={deckStats}
                nuanceLoading={nuanceLoading}
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

export default App
