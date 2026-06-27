import type { Conversation } from '../types/api'

interface ConversationSidebarProps {
  conversations: Conversation[]
  activeId: number | null
  onSelect: (id: number) => void
  onNew: () => void
}

export function ConversationSidebar({
  conversations,
  activeId,
  onSelect,
  onNew,
}: ConversationSidebarProps) {
  return (
    <div className="conversation-sidebar">
      <button className="conversation-sidebar__new" onClick={onNew}>
        + New conversation
      </button>
      <div className="conversation-sidebar__list">
        {conversations.map((c) => (
          <button
            key={c.id}
            className={`conversation-sidebar__item ${c.id === activeId ? 'conversation-sidebar__item--active' : ''}`}
            onClick={() => onSelect(c.id)}
          >
            {c.title}
          </button>
        ))}
      </div>
    </div>
  )
}
