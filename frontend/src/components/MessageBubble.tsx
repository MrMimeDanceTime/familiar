import { useMemo, type ComponentProps } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { DisplayMessage } from '../hooks/useChatStream'
import { CardPreview } from './CardPreview'
import { FamiliarMark } from './icons'
import type { FamiliarState } from './icons'

// Regex-escape special characters for building a card-name pattern.
function escapeRx(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

// Minimal HAST node shapes we touch. react-markdown builds a HAST tree from the
// parsed markdown, and this rehype plugin runs on that tree — after parsing —
// so nothing we do here can corrupt markdown syntax.
interface HastText { type: 'text'; value: string }
interface HastElement {
  type: 'element'
  tagName: string
  properties?: Record<string, unknown>
  children: HastNode[]
}
interface HastRoot { type: 'root'; children: HastNode[] }
type HastNode = HastText | HastElement | HastRoot | { type: string; children?: HastNode[] }

// A rehype plugin that turns every occurrence of a known card name into a
// `<a href="#pin">` element, which the `a` component below renders as a
// CardPreview (hover image + click-to-pin).
//
// This works on the HAST tree rather than by rewriting the markdown source
// string, which fixes two bugs the old string-injection approach had:
//   1. Injecting `[Name](#pin)` into text that was already inside a markdown
//      link produced a broken nested link (`[[Name](#pin)](url)`) that leaked a
//      literal "(#pin)" into the rendered message.
//   2. `\b` word boundaries and markdown emphasis markers (`_x_`) caused misses.
// Operating post-parse, we simply skip any subtree already inside an <a>
// (never nest links) and split every text node everywhere else.
// Card names reach us two ways:
//   1. Explicit `[[Card Name]]` markers the model writes around every card it
//      names (see the CARD-NAME MARKUP prompt rule) — the reliable source,
//      covering cards merely discussed, not just those in the deck/proposals.
//   2. `cardNames` (deck + proposal names) — a fallback so known cards still
//      linkify if the model forgets to bracket one.
// The `[[...]]` markers win and are stripped from the visible text.
function rehypeCardLinks(cardNames: string[]) {
  const unique = [...new Set(cardNames)].filter(Boolean)
  // Longer names first so "Sol Ring Alpha" wins over "Sol Ring" within one pass.
  unique.sort((a, b) => b.length - a.length)
  const namePattern = unique.length
    ? new RegExp(`\\b(${unique.map(escapeRx).join('|')})\\b`, 'gi')
    : null
  // [[...]] markers first (non-greedy, no nested brackets), then known names.
  const markerPattern = /\[\[([^\][]+)\]\]/g

  const pinLink = (name: string): HastNode => ({
    type: 'element',
    tagName: 'a',
    properties: { href: '#pin' },
    children: [{ type: 'text', value: name }],
  })

  // Split a plain string on known card names (used only for the text between
  // [[...]] markers, so a marked name is never re-scanned).
  const splitOnNames = (value: string): HastNode[] => {
    if (!namePattern) return value ? [{ type: 'text', value }] : []
    const out: HastNode[] = []
    let last = 0
    namePattern.lastIndex = 0
    let m: RegExpExecArray | null
    while ((m = namePattern.exec(value)) !== null) {
      if (m.index > last) out.push({ type: 'text', value: value.slice(last, m.index) })
      out.push(pinLink(m[0]))
      last = m.index + m[0].length
    }
    if (last < value.length) out.push({ type: 'text', value: value.slice(last) })
    return out
  }

  const splitText = (value: string): HastNode[] => {
    const out: HastNode[] = []
    let last = 0
    markerPattern.lastIndex = 0
    let m: RegExpExecArray | null
    while ((m = markerPattern.exec(value)) !== null) {
      // Text before this marker: still scan it for known names.
      if (m.index > last) out.push(...splitOnNames(value.slice(last, m.index)))
      out.push(pinLink(m[1].trim()))
      last = m.index + m[0].length
    }
    // Trailing text after the last marker (or the whole string if none).
    if (last < value.length) out.push(...splitOnNames(value.slice(last)))
    return out.length ? out : [{ type: 'text', value }]
  }

  const walk = (node: HastNode, insideLink: boolean): void => {
    const children = (node as HastElement).children
    if (!children) return
    const nextInside = insideLink || (node as HastElement).tagName === 'a'
    const rebuilt: HastNode[] = []
    for (const child of children) {
      if (child.type === 'text') {
        if (nextInside) {
          // Already inside a link — don't nest another. Still strip any stray
          // [[ ]] markers so the brackets never render as literal text.
          const value = (child as HastText).value.replace(markerPattern, (_all, name) => name.trim())
          rebuilt.push({ type: 'text', value })
        } else {
          rebuilt.push(...splitText((child as HastText).value))
        }
      } else {
        walk(child, nextInside)
        rebuilt.push(child)
      }
    }
    ;(node as HastElement).children = rebuilt
  }

  return () => (tree: HastNode) => {
    walk(tree, false)
    return tree
  }
}

interface MessageBubbleProps {
  message: DisplayMessage
  cardNames: string[]
  mascotState?: FamiliarState
  mascotFlourishKey?: number
}

export function MessageBubble({ message, cardNames, mascotState = 'breathing', mascotFlourishKey }: MessageBubbleProps) {
  // Rebuild the rehype plugin only when the set of card names changes, not on
  // every keystroke of a streaming reply.
  const rehypePlugins = useMemo(() => [rehypeCardLinks(cardNames)], [cardNames])

  if (message.role === 'user') {
    return (
      <div className="message message--user">
        <div className="message__text">
          <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={rehypePlugins} components={{ a: CardLink }}>
            {message.text}
          </ReactMarkdown>
        </div>
      </div>
    )
  }
  return (
    <div className="message message--assistant">
      <FamiliarMark size={28} state={mascotState} flourishKey={mascotFlourishKey} />
      <div className="message__body">
        <div className="message__author">Familiar</div>
        <div className="message__text">
          <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={rehypePlugins} components={{ a: CardLink }}>
            {message.text}
          </ReactMarkdown>
        </div>
      </div>
    </div>
  )
}

// Renders links whose href is "#pin" (injected by rehypeCardLinks) as
// CardPreview; all other links pass through as normal <a> tags.
function CardLink(props: ComponentProps<'a'>) {
  if (props.href === '#pin') {
    const name = typeof props.children === 'string' ? props.children : String(props.children ?? '')
    return <CardPreview name={name} />
  }
  return <a {...props} />
}
