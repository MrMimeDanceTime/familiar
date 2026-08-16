import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api/client'
import { CardPinProvider, useCardPins } from './components/CardPinContext'
import { ChatView } from './components/ChatView'
import { Sidebar } from './components/ConversationSidebar'
import type { SidebarTab } from './components/ConversationSidebar'
import { DeckDetail } from './components/DeckDetail'
import { DeckPanel } from './components/DeckPanel'
import { GearIcon, MoonIcon, SunIcon } from './components/icons'
import { MobileHeader } from './components/MobileHeader'
import type { MobileTab } from './components/MobileNav'
import { MobileNav } from './components/MobileNav'
import { PinnedTray } from './components/PinnedTray'
import { PreferencesPanel } from './components/PreferencesPanel'
import { useChatStream } from './hooks/useChatStream'
import type { ProposalBatch } from './hooks/useChatStream'
import { useDeck } from './hooks/useDeck'
import { useTheme } from './hooks/useTheme'
import type { Conversation, Deck, DeckProposal, DeckStats } from './types/api'
import './styles/global.css'

type MobileView = 'chats-list' | 'chat' | 'decks-list' | 'deck-detail' | 'pins'

// Summarize a batch for its inline header — e.g. "3 additions, 1 commander".
function summarizeBatch(proposals: DeckProposal[]): string {
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
function groupProposalsIntoBatches(proposals: DeckProposal[]): ProposalBatch[] {
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

function App() {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [decks, setDecks] = useState<Deck[]>([])
  const [sidebarTab, setSidebarTab] = useState<SidebarTab>('conversations')
  const [activeConversationId, setActiveConversationId] = useState<number | null>(null)
  const [deckStats, setDeckStats] = useState<DeckStats | null>(null)
  const [nuanceLoading, setNuanceLoading] = useState(false)
  const [proposalBatches, setProposalBatches] = useState<ProposalBatch[]>([])
  const [prefsOpen, setPrefsOpen] = useState(false)
  const { isDark, toggle: toggleTheme } = useTheme()
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
    onTurnComplete: (finalMessageId) => {
      // Pin this turn's freshly-streamed batches (still anchor-less) to the
      // assistant bubble that just landed, so they render inline immediately
      // rather than floating at the bottom until the next reload.
      setProposalBatches((prev) =>
        prev.map((b) =>
          b.anchorMessageId == null ? { ...b, anchorMessageId: finalMessageId } : b,
        ),
      )
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

  // The deck whose stats are currently wanted. Every stats/nuance response is
  // checked against this before it's applied, so a slow response for a deck the
  // user has already navigated away from is dropped instead of overwriting the
  // current deck's panel (the "click deck A, see deck B's numbers" race).
  const statsDeckRef = useRef<number | null>(null)

  const loadStats = useCallback((deckId: number) => {
    statsDeckRef.current = deckId
    setNuanceLoading(true)
    // Deterministic stats: fast, no LLM. Paints the panel immediately.
    api.getDeckStats(deckId)
      .then((s) => { if (statsDeckRef.current === deckId) setDeckStats(s) })
      .catch(() => { if (statsDeckRef.current === deckId) setDeckStats(null) })
    // LLM-refined power level: separate request, covered by its own spinner.
    // Merged into the existing stats so only the power fields swap when it lands.
    api.getDeckStatsNuance(deckId)
      .then((n) => {
        if (statsDeckRef.current !== deckId) return
        setDeckStats((prev) => (prev ? { ...prev, ...n } : prev))
      })
      .catch(() => {})
      .finally(() => { if (statsDeckRef.current === deckId) setNuanceLoading(false) })
  }, [])

  useEffect(() => {
    refreshConversations()
    refreshDecks()
  }, [refreshConversations, refreshDecks])

  useEffect(() => {
    if (deck) {
      loadStats(deck.id)
    } else {
      statsDeckRef.current = null
      setDeckStats(null)
      setNuanceLoading(false)
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
            serverId: m.id,
          })),
      )
      if (detail.conversation.deck_id) {
        loadDeck(detail.conversation.deck_id)
      } else {
        clearDeck()
      }
      setProposalBatches(groupProposalsIntoBatches(detail.proposals))
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
      // Deck auto-naming now runs server-side in the background (it's a slow LLM
      // call), so a freshly-committed commander leaves the deck "Untitled Deck"
      // in this response. Poll the deck a few times to pick up the generated
      // name once it lands, then refresh the sidebar list.
      if (updatedDeck.name === 'Untitled Deck' && updatedDeck.commander) {
        let tries = 0
        const poll = async () => {
          tries += 1
          const fresh = await api.getDeck(updatedDeck.id).catch(() => null)
          if (fresh && fresh.name !== 'Untitled Deck') {
            // Only replace the panel deck if the user is still viewing this one.
            setDeck((cur) => (cur && cur.id === fresh.id ? fresh : cur))
            refreshDecks()
          } else if (tries < 6) {
            setTimeout(poll, 2000)
          }
        }
        setTimeout(poll, 2000)
      }
    },
    [setDeck, loadStats, refreshDecks],
  )

  const handleDenyProposal = useCallback(
    async (proposalId: number, reason?: string) => {
      await api.denyProposal(proposalId, reason)
      setProposalBatches((prev) =>
        prev.map((batch) => ({
          ...batch,
          proposals: batch.proposals.map((p) =>
            p.id === proposalId
              ? { ...p, status: 'denied' as const, denial_reason: reason ?? null }
              : p,
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
