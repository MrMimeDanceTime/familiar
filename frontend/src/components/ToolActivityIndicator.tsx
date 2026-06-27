const TOOL_LABELS: Record<string, string> = {
  scryfall_search: 'Searching Scryfall…',
  scryfall_card_by_name: 'Looking up a card…',
  scryfall_card_collection: 'Fetching cards…',
  edhrec_commander_recs: 'Checking EDHREC recommendations…',
  edhrec_card_synergy: 'Checking card synergy…',
  deck_get_current: 'Reading the deck…',
  deck_add_card: 'Adding a card to the deck…',
  deck_remove_card: 'Removing a card from the deck…',
  deck_set_commander: 'Setting the commander…',
  deck_update_notes: 'Updating deck notes…',
}

export function ToolActivityIndicator({ toolName }: { toolName: string }) {
  return (
    <div className="tool-activity">
      <span className="tool-activity__spinner" />
      {TOOL_LABELS[toolName] ?? `Running ${toolName}…`}
    </div>
  )
}
