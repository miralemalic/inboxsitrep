// The fleet, day by day.  One row per family of look-alike domains: each
// day a column split by how many of its domains were fine, partly refused,
// refused or listed.  Open a family for one strip per domain.
import { useState } from 'react'
import { STATE, day, stateClass, R } from './api'

const W = 1100, LABEL = 200, ROW = 24, STRIP = 11, GAP = 2
const ORDER = ['l', 'x', 'w', 'o']

function trouble(rows) {
  let n = 0
  for (const r of rows) for (const c of r.s.slice(-7)) if (c === 'x' || c === 'l') n++
  return n
}

export default function Map({ map, go, first = 14 }) {
  const [open, setOpen] = useState({})
  const [all, setAll] = useState(false)
  const [tip, setTip] = useState(null)
  if (!map || !map.days.length) return <p className="dim">No sending data yet.</p>
  const days = map.days
  const col = (W - LABEL) / days.length
  const fams = map.families.map(f => ({ ...f, bad: trouble(f.rows) }))
    .sort((a, b) => b.bad - a.bad || b.rows.length - a.rows.length)
  const shown = all ? fams : fams.slice(0, first)
  const show = (e, text) => setTip({ x: e.clientX, y: e.clientY, text })

  let y = 0
  const parts = []
  for (const f of shown) {
    const name = f.family ? f.family + '*' : 'no family'
    const top = y
    parts.push(
      <g key={'f' + f.family} className="fam" onClick={() => setOpen({ ...open, [f.family]: !open[f.family] })}>
        <text x={0} y={top + 16} className="label">{(open[f.family] ? '▾ ' : '▸ ') + R(name)}</text>
        <text x={LABEL - 12} y={top + 16} textAnchor="end">{f.rows.length}</text>
        {days.map((d, i) => {
          const tally = { o: 0, w: 0, x: 0, l: 0, '.': 0 }
          for (const r of f.rows) tally[r.s[i]]++
          const x = LABEL + i * col
          let h = 0
          const bits = []
          for (const s of ORDER) {
            if (!tally[s]) continue
            const hh = (ROW - 4) * tally[s] / f.rows.length
            bits.push(<rect key={s} x={x} y={top + ROW - 2 - h - hh} width={col - GAP} height={hh} className={stateClass(s)} />)
            h += hh
          }
          if (h < ROW - 4) bits.unshift(<rect key="n" x={x} y={top + 2} width={col - GAP} height={ROW - 4 - h} className="s-n" />)
          return <g key={d} onMouseMove={e => show(e, famTip(R(name), d, tally))} onMouseLeave={() => setTip(null)}>{bits}</g>
        })}
      </g>
    )
    y += ROW + 4
    if (open[f.family]) {
      for (const r of f.rows) {
        const ry = y
        parts.push(
          <g key={'d' + r.domain} className="dom" onClick={() => go('#/domains/' + r.domain)}>
            <text x={16} y={ry + 9.5} style={{ fontSize: 11 }}>{R(r.domain)}</text>
            {days.map((d, i) => (
              <rect key={d} x={LABEL + i * col} y={ry} width={col - GAP} height={STRIP - 1} rx={1.5}
                    className={stateClass(r.s[i])}
                    onMouseMove={e => show(e, R(r.domain) + ', ' + day(d) + ': ' + STATE[r.s[i]])}
                    onMouseLeave={() => setTip(null)} />
            ))}
          </g>
        )
        y += STRIP + 1
      }
      y += 8
    }
  }
  const height = y + 16
  return (
    <div className="mapwrap">
      <svg className="map" viewBox={`0 0 ${W} ${height}`} role="img" aria-label="Every family, day by day">
        {parts}
        <text x={LABEL} y={height - 2}>{day(days[0])}</text>
        <text x={W - GAP} y={height - 2} textAnchor="end">{day(days[days.length - 1])}</text>
      </svg>
      {fams.length > first && (
        <button className="ghost small" style={{ marginTop: 8 }} onClick={() => setAll(!all)}>
          {all ? 'Show the ' + first + ' with the most trouble' : 'Show all ' + fams.length + ' families'}
        </button>
      )}
      {tip && <div className="tip" style={{ left: tip.x + 14, top: tip.y + 12 }}>{tip.text}</div>}
    </div>
  )
}

function famTip(name, d, t) {
  const bits = []
  if (t.l) bits.push(t.l + ' listed')
  if (t.x) bits.push(t.x + ' refused')
  if (t.w) bits.push(t.w + ' partly refused')
  if (t.o) bits.push(t.o + ' fine')
  if (t['.']) bits.push(t['.'] + ' sent nothing')
  return name + ', ' + day(d) + ': ' + bits.join(', ')
}

export function Strip({ s, days, size = 'small' }) {
  return (
    <div className={'strip ' + size}>
      {[...s].map((c, i) => <span key={i} className={stateClass(c)} title={(days ? day(days[i]) + ': ' : '') + STATE[c]} />)}
    </div>
  )
}

export function Legend() {
  return (
    <div className="legend">
      <span><i className="s-o" />fine</span>
      <span><i className="s-w" />some refused</span>
      <span><i className="s-x" />most refused</span>
      <span><i className="s-l" />on a blocklist</span>
      <span><i className="s-n" />nothing sent</span>
    </div>
  )
}
