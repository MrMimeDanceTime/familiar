import { useTheme } from '../hooks/useTheme'
import type { Conversation, Deck } from '../types/api'
import { GearIcon, MoonIcon, SunIcon } from './icons'

export type SidebarTab = 'conversations' | 'decks'

interface SidebarProps {
  tab: SidebarTab
  onTabChange: (tab: SidebarTab) => void
  conversations: Conversation[]
  activeId: number | null
  onSelect: (id: number) => void
  onNew: () => void
  onDelete: (id: number) => void
  decks: Deck[]
  activeDeckId: number | null
  onSelectDeck: (deck: Deck) => void
  onNewDeck: () => void
  onDeleteDeck: (id: number) => void
  onOpenPreferences: () => void
}

export function Sidebar({
  tab,
  onTabChange,
  conversations,
  activeId,
  onSelect,
  onNew,
  onDelete,
  decks,
  activeDeckId,
  onSelectDeck,
  onNewDeck,
  onDeleteDeck,
  onOpenPreferences,
}: SidebarProps) {
  const { isDark, toggle } = useTheme()
  return (
    <div className="conversation-sidebar">
      <div className="sidebar-brand">
        <span className="sidebar-brand__name">Familiar</span>
        <button
          className="sidebar-icon-btn"
          onClick={toggle}
          title={isDark ? 'Switch to light theme' : 'Switch to dark theme'}
          aria-label="Toggle theme"
        >
          {isDark ? <SunIcon /> : <MoonIcon />}
        </button>
        <button className="sidebar-icon-btn" onClick={onOpenPreferences} title="Preferences" aria-label="Preferences">
          <GearIcon />
        </button>
      </div>
      <div className="segmented">
        <button
          className={`segmented__seg ${tab === 'conversations' ? 'segmented__seg--active' : ''}`}
          onClick={() => onTabChange('conversations')}
        >
          Conversations
        </button>
        <button
          className={`segmented__seg ${tab === 'decks' ? 'segmented__seg--active' : ''}`}
          onClick={() => onTabChange('decks')}
        >
          Decks
        </button>
      </div>

      {tab === 'conversations' ? (
        <>
          <button className="btn btn--ghost conversation-sidebar__new" onClick={onNew}>
            + New conversation
          </button>
          <div className="micro-label conversation-sidebar__recent">Recent</div>
          <div className="conversation-sidebar__list">
            {conversations.map((c) => (
              <div
                key={c.id}
                className={`conversation-sidebar__item ${c.id === activeId ? 'conversation-sidebar__item--active' : ''}`}
              >
                <button className="conversation-sidebar__item-title" onClick={() => onSelect(c.id)}>
                  {c.title}
                </button>
                <button
                  className="conversation-sidebar__item-delete"
                  title="Delete conversation"
                  onClick={(e) => {
                    e.stopPropagation()
                    if (window.confirm(`Delete "${c.title}"? This can't be undone.`)) {
                      onDelete(c.id)
                    }
                  }}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        </>
      ) : (
        <>
          <button className="btn btn--ghost conversation-sidebar__new" onClick={onNewDeck}>
            + New deck
          </button>
          <div className="micro-label conversation-sidebar__recent">Decks</div>
          <div className="conversation-sidebar__list">
            {decks.map((d) => (
              <div
                key={d.id}
                className={`conversation-sidebar__item ${d.id === activeDeckId ? 'conversation-sidebar__item--active' : ''}`}
              >
                <button
                  className="conversation-sidebar__item-title"
                  onClick={() => onSelectDeck(d)}
                >
                  <span className="sidebar-deck-name">{d.name}</span>
                  {d.commander && (
                    <span className="sidebar-deck-commander">{d.commander}</span>
                  )}
                </button>
                <button
                  className="conversation-sidebar__item-delete"
                  title="Delete deck"
                  onClick={(e) => {
                    e.stopPropagation()
                    if (window.confirm(`Delete "${d.name}"? This can't be undone.`)) {
                      onDeleteDeck(d.id)
                    }
                  }}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
