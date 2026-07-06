export type MobileTab = 'chats' | 'decks' | 'pins'

interface MobileNavProps {
  activeTab: MobileTab
  onTabChange: (tab: MobileTab) => void
  /** Number of pending proposals across all conversations, shown as a badge. */
  proposalCount?: number
}

const TABS: { key: MobileTab; label: string; icon: string }[] = [
  { key: 'chats', label: 'Chats', icon: '◉' },
  { key: 'decks', label: 'Decks', icon: '◆' },
  { key: 'pins', label: 'Pins', icon: '◈' },
]

export function MobileNav({ activeTab, onTabChange, proposalCount }: MobileNavProps) {
  return (
    <nav className="mobile-nav">
      {TABS.map((t) => (
        <button
          key={t.key}
          className={`mobile-nav__tab ${activeTab === t.key ? 'mobile-nav__tab--active' : ''}`}
          onClick={() => onTabChange(t.key)}
        >
          <span className="mobile-nav__icon">{t.icon}</span>
          <span className="mobile-nav__label">{t.label}</span>
          {t.key === 'chats' && proposalCount !== undefined && proposalCount > 0 && (
            <span className="mobile-nav__badge">{proposalCount}</span>
          )}
        </button>
      ))}
    </nav>
  )
}
