import { useEffect, useState } from 'react'
import { THINKING } from '../lib/activity'

const TOOL_LABELS: Record<string, string> = {
  scryfall_search: 'Searching Scryfall…',
  scryfall_card_by_name: 'Looking up a card…',
  scryfall_card_collection: 'Fetching cards…',
  edhrec_commander_recs: 'Checking EDHREC recommendations…',
  edhrec_card_synergy: 'Checking card synergy…',
  search_deckbuilding_knowledge: 'Consulting deckbuilding knowledge…',
  deck_get_current: 'Reading the deck…',
  deck_get_stats: 'Crunching the deck stats…',
  deck_add_card: 'Adding a card to the deck…',
  deck_remove_card: 'Removing a card from the deck…',
  deck_set_commander: 'Setting the commander…',
  deck_update_notes: 'Updating deck notes…',
  propose_deck_changes: 'Putting together a proposal…',
  withdraw_pending_proposals: 'Clearing the pending batch…',
  suggest_cards: 'Hunting for cards that fit…',
}

// Shown while the model reasons before writing (the `thinking` stream event),
// with its own elapsed counter so a long think reads as work in progress
// rather than a stall.

function ThinkingIndicator() {
  const [seconds, setSeconds] = useState(0)
  useEffect(() => {
    const started = Date.now()
    const timer = window.setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 1000)
    return () => window.clearInterval(timer)
  }, [])
  return (
    <div className="tool-activity">
      <span className="tool-activity__dot" />
      Thinking it through…{seconds >= 2 ? ` ${seconds}s` : ''}
    </div>
  )
}

export function ToolActivityIndicator({ toolName }: { toolName: string }) {
  if (toolName === THINKING) return <ThinkingIndicator />
  return (
    <div className="tool-activity">
      <span className="tool-activity__dot" />
      {TOOL_LABELS[toolName] ?? `Running ${toolName}…`}
    </div>
  )
}
