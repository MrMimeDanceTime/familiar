/**
 * The "why?" panels behind the power level and bracket numbers.
 *
 * The backend hands over pre-formatted human strings (see
 * _estimate_power_level / _estimate_bracket in deck_tools.py). Rather than
 * surface that raw text, the few known shapes are parsed back into rows the
 * UI can style.
 */

// ── "why?" breakdown parsing ────────────────────────────────────────────────
// The backend hands us pre-formatted human strings (see _estimate_power_level /
// _estimate_bracket in deck_tools.py). Rather than surface that raw text, we
// parse the few known shapes back into structured rows the UI can style.

interface PowerRow {
  label: string
  detail: string
  points: number
}

// e.g. "Lands: 34 (focused sweet spot) +1.0" → {label, detail, points}
function parsePowerRow(line: string): PowerRow | null {
  const m = line.match(/^([^:]+):\s*(.*?)\s*([+-][\d.]+)\s*$/)
  if (!m) return null
  return { label: m[1].trim(), detail: m[2].trim(), points: parseFloat(m[3]) }
}

interface PowerBreakdown {
  raw: number
  base: number
  level: number
  rows: PowerRow[]
}

function parsePowerFactors(factors: string[]): PowerBreakdown | null {
  // First line is the summary of the deterministic base: "Raw: 6.5 + 1 = 7/10".
  // (This is the BASE level; the nuanced final level comes from stats, not here.)
  const head = factors[0]?.match(/Raw:\s*([\d.]+).*?=\s*(\d+)\/10/)
  // The appended "LLM nuance: +x (reason)" line has its value mid-string, so
  // parsePowerRow (which expects a trailing +/-n) correctly skips it — nuance is
  // rendered separately from the structured stats fields, not from this text.
  const rows = factors.slice(1).map(parsePowerRow).filter((r): r is PowerRow => r !== null)
  if (!head) return null
  const base = parseInt(head[2], 10)
  return { raw: parseFloat(head[1]), base, level: base, rows }
}

interface BracketRow {
  label: string
  cards: string[]
  note: string
}

// e.g. "Game Changers (1): Rhystic Study" or "Game Changers: none"
function parseBracketRow(line: string): BracketRow {
  const m = line.match(/^([^:]+):\s*(.*)$/)
  if (!m) return { label: line.trim(), cards: [], note: '' }
  const label = m[1].replace(/\s*\(\d+\)\s*$/, '').trim()
  const rest = m[2].trim()
  if (!rest || rest.toLowerCase() === 'none') {
    return { label, cards: [], note: 'none' }
  }
  return { label, cards: rest.split(',').map((c) => c.trim()).filter(Boolean), note: '' }
}

interface BracketBreakdown {
  bracket: number
  verdict: string
  rows: BracketRow[]
}

function parseBracketFactors(factors: string[]): BracketBreakdown | null {
  // First line is the verdict: "Bracket 3: 1 Game Changer(s) present"
  const head = factors[0]?.match(/^Bracket\s*(\d+):\s*(.*)$/)
  if (!head) return null
  const rows = factors.slice(1).map(parseBracketRow)
  return { bracket: parseInt(head[1], 10), verdict: head[2].trim(), rows }
}

export function PowerWhy({
  factors,
  level,
  base,
  nuanceAdj,
  nuanceReason,
}: {
  factors: string[]
  level: number
  base: number
  nuanceAdj: number
  nuanceReason: string
}) {
  const data = parsePowerFactors(factors)
  if (!data) {
    return <div className="why-panel why-panel--raw">{factors.join('\n')}</div>
  }
  // Show the nuance row whenever the LLM produced a judgment (a reason), even
  // when it chose NOT to move the score (adj 0). A zero with a reason is
  // informative — "the model looked and the fundamentals already capture it" —
  // and hiding it makes a fully-evaluated deck look un-evaluated.
  const hasNuanceJudgment = nuanceReason.trim().length > 0
  const nuanceMovedScore = nuanceAdj !== 0
  const maxPts = Math.max(...data.rows.map((r) => Math.abs(r.points)), Math.abs(nuanceAdj), 2)
  const fmtAdj = (a: number) => (a > 0 ? `+${a.toFixed(1)}` : a < 0 ? a.toFixed(1) : '0')
  return (
    <div className="why-panel">
      <div className="why-panel__head">
        <div className="why-panel__title">
          {nuanceMovedScore ? (
            <>Power level {base} → {level}/10</>
          ) : (
            <>Power level {level}/10</>
          )}
        </div>
        <div className="why-panel__sub">
          {data.rows.length} fundamentals scored · base 1 + {data.raw.toFixed(1)} earned
          {hasNuanceJudgment
            ? nuanceMovedScore ? ' · LLM nuance applied' : ' · LLM nuance: no change'
            : ''}
        </div>
      </div>
      <ul className="why-rows">
        {data.rows.map((r) => (
          <li className="why-row" key={r.label}>
            <span className="why-row__label">{r.label}</span>
            <span className="why-row__detail">{r.detail}</span>
            <span className="why-row__bar" aria-hidden>
              <span
                className={`why-row__bar-fill ${r.points <= 0 ? 'why-row__bar-fill--zero' : ''}`}
                style={{ width: `${(Math.abs(r.points) / maxPts) * 100}%` }}
              />
            </span>
            <span className={`why-row__pts ${r.points <= 0 ? 'why-row__pts--zero' : ''}`}>
              {r.points > 0 ? `+${r.points.toFixed(1)}` : r.points.toFixed(1)}
            </span>
          </li>
        ))}
        {hasNuanceJudgment && (
          <li className="why-row why-row--nuance" key="__nuance">
            <span className="why-row__label">✦ LLM nuance</span>
            <span className="why-row__detail">{nuanceReason}</span>
            <span className="why-row__bar" aria-hidden>
              <span
                className={`why-row__bar-fill why-row__bar-fill--nuance ${nuanceAdj <= 0 ? 'why-row__bar-fill--zero' : ''}`}
                style={{ width: `${(Math.abs(nuanceAdj) / maxPts) * 100}%` }}
              />
            </span>
            <span className={`why-row__pts ${nuanceAdj <= 0 ? 'why-row__pts--zero' : ''}`}>
              {fmtAdj(nuanceAdj)}
            </span>
          </li>
        )}
      </ul>
    </div>
  )
}

export function BracketWhy({ factors }: { factors: string[] }) {
  const data = parseBracketFactors(factors)
  if (!data) {
    return <div className="why-panel why-panel--raw">{factors.join('\n')}</div>
  }
  return (
    <div className="why-panel">
      <div className="why-panel__head">
        <div className="why-panel__title why-panel__title--bracket">
          Bracket {data.bracket}
          <span className="why-panel__of"> / 5</span>
        </div>
        <div className="why-panel__sub">{data.verdict}</div>
      </div>
      <ul className="why-rows why-rows--bracket">
        {data.rows.map((r) => (
          <li className="why-row why-row--bracket" key={r.label}>
            <span className="why-row__label">{r.label}</span>
            {r.note === 'none' ? (
              <span className="why-chip why-chip--none">none</span>
            ) : (
              <span className="why-chips">
                {r.cards.map((c) => (
                  <span className="why-chip" key={c}>{c}</span>
                ))}
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}


