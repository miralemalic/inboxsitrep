// Everything the app has done, newest first: checks, changes, alerts and
// the reports it sent, with what failed picked out.
import { useEffect, useState } from 'react'
import { get, R } from './api'

const KINDS = [['', 'Everything'], ['change', 'Changes'], ['check', 'Checks'], ['alert', 'Alerts'],
  ['report', 'Reports sent'], ['problem', 'Problems']]
const LABEL = { change: 'change', check: 'check', alert: 'alert', report: 'report', problem: 'problem' }

export default function Logs({ app }) {
  const [rows, setRows] = useState(null)
  const [kind, setKind] = useState('')
  const [find, setFind] = useState('')

  useEffect(() => {
    if (!app || !app.app) return
    const load = () => get('/api/app/log').then(r => Array.isArray(r) && setRows(r))
    load()
    const t = setInterval(load, 10000)
    return () => clearInterval(t)
  }, [app])

  if (!app || !app.app) return <div className="empty"><h2>Logs live in the desktop app.</h2><p>Start it with <code>inboxsitrep app</code>.</p></div>
  if (!rows) return <p className="dim">Reading.</p>
  const q = find.trim().toLowerCase()
  const shown = rows.filter(r => (!kind || r.kind === kind)
    && (!q || (r.who + ' ' + r.text).toLowerCase().includes(q)))
  let last = ''
  return (
    <>
      <div className="toolbar">
        <input placeholder="Find in the log" value={find} onChange={e => setFind(e.target.value)} />
        <div className="chips">{KINDS.map(([k, label]) => (
          <button key={k} className={kind === k ? 'on' : ''} onClick={() => setKind(k)}>
            {label}{k === 'problem' && rows.some(r => r.kind === 'problem') ? ' (' + rows.filter(r => r.kind === 'problem').length + ')' : ''}
          </button>))}</div>
        <span className="dim small" style={{ marginLeft: 'auto' }}>{shown.length} lines</span>
      </div>
      <ol className="logbook">
        {shown.slice(0, 600).map((r, i) => {
          const day = r.at.slice(0, 10)
          const head = day !== last ? <li key={'d' + i} className="day">{day}</li> : null
          last = day
          return [head,
            <li key={i} className={'k-' + r.kind}>
              <span className="t mono">{r.at.slice(11)}</span>
              <span className="tag">{LABEL[r.kind]}</span>
              <span className="w">{R(r.who)}</span>
              <span className="x">{R(r.text)}</span>
            </li>]
        })}
      </ol>
      {!shown.length && <p className="dim">Nothing matches.</p>}
    </>
  )
}
