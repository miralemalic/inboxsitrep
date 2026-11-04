// Which days the numbers cover: today (live from the sequencer), the last 7,
// 14 or 30 days, or two dates of your own.
import { useState } from 'react'
import { day } from './api'

export function rangeQuery(v) {
  return v.from ? 'from=' + v.from + '&to=' + v.to : 'range=' + v.range
}

export function rangeName(v) {
  if (v.range === 'today') return 'today'
  return v.from ? (v.from === v.to ? day(v.from) : day(v.from) + ' to ' + day(v.to)) : 'last ' + v.range + ' days'
}

// first, last: the days collected.  Dates outside them are pulled in, and
// the range applies as soon as both dates are set.
export default function Range({ value, onChange, today = true, note, first, last }) {
  const [custom, setCustom] = useState(value.from ? value : null)
  const opts = [...(today ? [['today', 'Today']] : []), ['7', '7 days'], ['14', '14 days'], ['30', '30 days']]
  const clamp = d => (first && d < first ? first : last && d > last ? last : d)
  function pick(next) {
    setCustom(next)
    if (/^\d{4}-\d{2}-\d{2}$/.test(next.from) && /^\d{4}-\d{2}-\d{2}$/.test(next.to) && next.from <= next.to)
      onChange({ from: clamp(next.from), to: clamp(next.to) })
  }
  const outside = custom && first && ((custom.from && custom.from < first) || (custom.to && last && custom.to > last))
  return (
    <div className="range">
      <div className="chips">
        {opts.map(([k, label]) => (
          <button key={k} className={!custom && value.range === k ? 'on' : ''}
                  onClick={() => { setCustom(null); onChange({ range: k }) }}>{label}</button>))}
        <button className={custom ? 'on' : ''}
                onClick={() => setCustom(custom || { from: value.from || first || '', to: value.to || last || '' })}>Custom</button>
      </div>
      {custom && <span className="dates">
        <input type="date" value={custom.from} min={first} max={last} onChange={e => pick({ ...custom, from: e.target.value })} />
        <span className="dim">to</span>
        <input type="date" value={custom.to} min={first} max={last} onChange={e => pick({ ...custom, to: e.target.value })} />
      </span>}
      {custom && first && <span className={'small ' + (outside ? 'p2' : 'faint')}>
        {outside ? 'Data available from ' + day(first) + ' to ' + day(last) + '; showing that.'
          : 'Data available from ' + day(first) + ' to ' + day(last) + '.'}</span>}
      {note && <span className="faint small">{note}</span>}
    </div>
  )
}
