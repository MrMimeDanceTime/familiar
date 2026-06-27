import type { SseEvent } from '../types/api'

export async function streamChat(
  conversationId: number | 'new',
  message: string,
  onEvent: (event: SseEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ conversation_id: conversationId, message }),
    signal,
  })

  if (!resp.ok || !resp.body) {
    throw new Error(`Chat request failed: ${resp.status}`)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    const chunks = buffer.split('\n\n')
    buffer = chunks.pop() ?? ''

    for (const chunk of chunks) {
      const lines = chunk.split('\n')
      let eventName = ''
      let dataLine = ''
      for (const line of lines) {
        if (line.startsWith('event: ')) eventName = line.slice('event: '.length)
        else if (line.startsWith('data: ')) dataLine = line.slice('data: '.length)
      }
      if (!eventName || !dataLine) continue
      onEvent({ event: eventName, data: JSON.parse(dataLine) } as SseEvent)
    }
  }
}
