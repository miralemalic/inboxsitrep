// Every workspace side by side, worst first.  Open one for its own pages.
import { useEffect, useState } from 'react'
import { get, num, pct, words, when, R } from './api'

const RANK = { RED: 0, AMBER: 1, GREEN: 2 }
const TONE = { RED: 'p1', AMBER: 'p2', GREEN: 'ok' }

export default function Overview({ pick, app, check, stop }) {
  const [rows, setRows] = useState(null)
  // Read again as each workspace's check ends.
  const current = app && app.state && app.state.busy ? app.state.fleet : null
  useEffect(() => { get('/api/overview').then(setRows) }, [current])
  if (!rows) return <p className="dim">Reading every workspace.</p>
  const list = [...rows].sort((a, b) => (b.quiet > 3) - (a.quiet > 3) || RANK[a.status] - RANK[b.status]
    || b.decisions - a.decisions)
  const st = (app && app.state) || {}
  const can = app && app.app
  return (
    <section>
      {can && <div className="toolbar">
        <button onClick={() => check('*')} disabled={st.busy}>{st.busy ? 'A check is running' : 'Check all workspaces'}</button>
        {st.busy && <button className="line" onClick={stop}>Stop</button>}
        <span className="faint small" style={{ marginLeft: 'auto' }}>Workspaces are checked one at a time; progress shows in the sidebar.
          {st.queued ? ' One more check is waiting.' : ''}</span>
      </div>}
      <div style={{ overflowX: 'auto' }}><table className="overview">
        <thead><tr>
          <th>Workspace</th><th>Status</th><th>Decide</th><th>Leads left</th>
          <th title="Warmed spare domains, and how many weeks they last at the rate domains were lost in the last 30 days">Spare domains</th><th>Sent, 7 days</th>
          <th>Positives /1,000</th><th>Refused</th><th>Blocklisted</th><th>Disconnected</th>
          <th>Checked</th>
        </tr></thead>
        <tbody>{list.map(r => (
          <tr key={r.name} className="click" onClick={() => pick(r.name)}>
            <td className="dom"><b>{R(r.name)}</b>
              <div className="small dim top">{r.quiet > 3 ? <span className="p1">nothing sent for {r.quiet} days</span>
                : r.top ? words(r.top) : 'nothing needs you'}</div></td>
            <td><span className={'prio ' + TONE[r.status]}>{r.status}</span></td>
            <td className={r.decisions ? 'p1' : ''}>{num(r.decisions)}</td>
            <td className={r.runway != null && r.runway < 3 ? 'p1' : ''}>{r.runway == null ? '–' : r.runway < 1.5 ? '~1 day' : Math.round(r.runway) + ' days'}</td>
            <td className={r.reserve && r.reserve.buy ? 'p2' : ''}>{!r.reserve ? '–' : num(r.reserve.spare)
              + (r.reserve.weeks != null ? ' · ' + Math.round(r.reserve.weeks) + ' wk' : '')}</td>
            <td>{num(r.sent)}</td>
            <td>{r.per_1000 == null ? '–' : r.per_1000}</td>
            <td className={r.sender_bounces >= 2 ? 'p2' : ''}>{pct(r.sender_bounces)}</td>
            <td className={r.listed ? 'p2' : ''}>{num(r.listed)}</td>
            <td className={r.down ? 'p2' : ''}>{num(r.down)}</td>
            <td className="small" onClick={e => e.stopPropagation()}>
              {st.busy && st.fleet === r.name ? <span className="p2">checking now</span>
                : <><span className="dim">{r.collected ? when(r.collected) : 'never'}</span>
                  {can && <button className="line tiny" disabled={st.busy && st.fleet === r.name} onClick={() => check(r.name)}>Check</button>}</>}
            </td>
          </tr>))}</tbody>
      </table></div>
      <p className="faint small" style={{ marginTop: 12 }}>Slack and Telegram get one message covering every workspace.</p>
    </section>
  )
}
