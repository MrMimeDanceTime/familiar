import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { DisplayMessage } from '../hooks/useChatStream'

export function MessageBubble({ message }: { message: DisplayMessage }) {
  return (
    <div className={`message-bubble message-bubble--${message.role}`}>
      <div className="message-bubble__role">{message.role === 'user' ? 'You' : 'Familiar'}</div>
      <div className="message-bubble__text">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.text}</ReactMarkdown>
      </div>
    </div>
  )
}
