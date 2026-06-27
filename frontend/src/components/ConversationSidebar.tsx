import type { Conversation } from '../types/api'

interface ConversationSidebarProps {
  conversations: Conversation[]
  activeId: number | null
  onSelect: (id: number) => void
  onNew: () => void
  onDelete: (id: number) => void
}

export function ConversationSidebar({
  conversations,
  activeId,
  onSelect,
  onNew,
  onDelete,
}: ConversationSidebarProps) {
  return (
    <div className="conversation-sidebar">
      <button className="conversation-sidebar__new" onClick={onNew}>
        + New conversation
      </button>
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
    </div>
  )
}
