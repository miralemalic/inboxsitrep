// A small line chart in plain SVG: one value a day per recipient provider,
// each line named at its end instead of in a legend.
import { PROVIDER, day } from './api'

const W = 560, H = 150, L = 34, R = 74, T = 10, B = 22

export default function Chart({ series, field, title, lines = ['google', 'microsoft'] }) {
  const used = lines.filter(p => series.some(d => d[p] && d[p].sent))
  const points = series.filter(d => used.some(p => d[p].sent))
  if (points.length < 2 || !used.length) return null
  const values = points.flatMap(d => used.map(p => d[p][field])).filter(v => v != null)
  const top = niceTop(Math.max(1, ...values))
  const x = i => L + i * (W - L - R) / (points.length - 1)
  const y = v => T + (H - T - B) * (1 - v / top)
  const path = p => points
    .map((d, i) => d[p][field] == null ? null : x(i).toFixed(1) + ',' + y(d[p][field]).toFixed(1))
    .filter(Boolean).join(' ')
  const ends = used.map(p => {
    const i = points.map(d => d[p][field]).findLastIndex(v => v != null)
    return { p, i, v: i < 0 ? null : points[i][p][field] }
  }).filter(e => e.v != null)
  spread(ends, y)

  return (
    <figure className="chart">
      <figcaption>{title}</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title}>
        {[0, top / 2, top].map(v => (
          <g key={v}>
            <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} className="axis" />
            <text x={L - 6} y={y(v) + 4} textAnchor="end">{v}{field === 'score' ? '' : '%'}</text>
          </g>
        ))}
        <text x={L} y={H - 4}>{day(points[0].day)}</text>
        <text x={W - R} y={H - 4} textAnchor="end">{day(points[points.length - 1].day)}</text>
        {used.map(p => <polyline key={p} points={path(p)} className={p} />)}
        {ends.map(e => (
          <text key={e.p} x={W - R + 8} y={e.ly + 4} className="end">
            {field === 'score' ? e.v : PROVIDER[e.p]}
          </text>
        ))}
      </svg>
      <div className="key">{used.map(p => <span key={p}><i className={'sw ' + p} />{PROVIDER[p] === 'Others' ? 'Other providers' : PROVIDER[p]}</span>)}</div>
    </figure>
  )
}

function niceTop(v) {
  for (const t of [1, 2, 3, 5, 10, 20, 30, 50, 100]) if (v <= t) return t
  return Math.ceil(v / 10) * 10
}

// Keep end labels from sitting on top of each other.
function spread(ends, y) {
  ends.forEach(e => { e.ly = y(e.v) })
  ends.sort((a, b) => a.ly - b.ly)
  for (let i = 1; i < ends.length; i++) {
    if (ends[i].ly - ends[i - 1].ly < 13) ends[i].ly = ends[i - 1].ly + 13
  }
}
