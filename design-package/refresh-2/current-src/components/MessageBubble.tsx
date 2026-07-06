import type { ComponentProps } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { DisplayMessage } from '../hooks/useChatStream'
import { CardPreview } from './CardPreview'

// Regex-escape special characters for building a card-name pattern.
function escapeRx(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

// Wrap every occurrence of a known card name in the text as a special markdown
// link so ReactMarkdown renders it; we then intercept those links with a
// custom `a` component that renders CardPreview instead.
function injectCardLinks(text: string, cardNames: string[]): string {
  const unique = [...new Set(cardNames)]
  // Sort descending by length so longer names match before shorter ones
  // (e.g. "Jace, the Mind Sculptor" before "Jace").
  unique.sort((a, b) => b.length - a.length)
  const escaped = unique.map(escapeRx)
  const pattern = new RegExp(`\\b(${escaped.join('|')})\\b`, 'gi')
  return text.replace(pattern, (_match, captured: string) => {
    // Use the actual captured casing for display, but a stable fragment id.
    return `[${captured}](#pin)`
  })
}

interface MessageBubbleProps {
  message: DisplayMessage
  cardNames: string[]
}

export function MessageBubble({ message, cardNames }: MessageBubbleProps) {
  if (message.role === 'user') {
    const text = cardNames.length > 0 ? injectCardLinks(message.text, cardNames) : message.text
    return (
      <div className="message message--user">
        <div className="message__text">
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ a: CardLink }}>
            {text}
          </ReactMarkdown>
        </div>
      </div>
    )
  }
  const text = cardNames.length > 0 ? injectCardLinks(message.text, cardNames) : message.text
  return (
    <div className="message message--assistant">
      <div className="message__author">Familiar</div>
      <div className="message__text">
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ a: CardLink }}>
          {text}
        </ReactMarkdown>
      </div>
    </div>
  )
}

// Renders markdown links whose href is "#pin" as CardPreview; all other links
// pass through as normal <a> tags.
function CardLink(props: ComponentProps<'a'>) {
  if (props.href === '#pin') {
    const name = typeof props.children === 'string' ? props.children : String(props.children ?? '')
    return <CardPreview name={name} />
  }
  return <a {...props} />
}
