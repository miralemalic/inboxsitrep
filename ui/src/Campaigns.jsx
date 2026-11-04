// Every campaign with its verdict: scale it, keep iterating, or stop it.
// Lifetime totals from the sequencer, judged against the workspace's own
// positive-reply rate.
import { useEffect, useState } from 'react'
import { day, get, num, pct, R } from './api'

const KINDS = [['', 'All'], ['winner', 'Winners'], ['loser', 'Losers'], ['bounces_high', 'Bounces too high'],
  ['bad_list', 'Bad lists'], ['iterate', 'Average'], ['too_early', 'Too early']]
const TONE = { winner: 'ok', loser: 'p1', bounces_high: 'p1', bad_list: 'p2', iterate: '', too_early: 'faint' }

// Broken: sentences some recipients see mangled.  Risky: what spam filters
// hold against cold email.
function copyCell(c) {
  if (!c) return <span className="faint">–</span>
  if (c.broken) return <span className="verdict p1">{c.broken} broken</span>
  if (c.risky) return <span className="verdict p2">{c.risky} risky</span>
  return <span className="verdict ok">clean</span>
}

export default function Campaigns({ fleet }) {
  const [rows, setRows] = useState(null)
  const [kind, setKind] = useState('')
  const [active, setActive] = useState(true)
  const [open, setOpen] = useState(null)
  useEffect(() => { setRows(null); get('/api/campaigns').then(setRows) }, [fleet])
  if (!rows) return <p className="dim">Reading.</p>
  if (!rows.length) return <div className="empty"><h2>No campaigns yet.</h2><p>The next check reads them from the sequencer.</p></div>
  const shown = rows.filter(r => (!kind || r.verdict === kind) && (!active || r.status === 'ACTIVE'))
  const judged = rows.filter(r => r.sent >= 1000)
  const sent = judged.reduce((n, r) => n + r.sent, 0)
  const base = sent ? (100 * judged.reduce((n, r) => n + r.replies, 0) / sent).toFixed(1) : '–'
  return (
    <section>
      <div className="toolbar">
        <div className="chips">{KINDS.map(([k, label]) => (
          <button key={k} className={kind === k ? 'on' : ''} onClick={() => setKind(k)}>
            {label}{k ? ' (' + rows.filter(r => r.verdict === k && (!active || r.status === 'ACTIVE')).length + ')' : ''}</button>))}
        </div>
        <label className="toggle"><input type="checkbox" checked={active} onChange={e => setActive(e.target.checked)} /> Active only</label>
        <span className="faint small" style={{ marginLeft: 'auto' }}>
          Lifetime totals. The workspace replies at {base}%. Winners reply at twice the rest of the workspace with bounces under their limit; losers at half.</span>
      </div>
      <div style={{ maxHeight: 'calc(100vh - 230px)', overflowY: 'auto' }}>
        <table>
          <thead><tr>
            <th>Verdict</th><th>Campaign</th><th>Status</th><th>Sent</th><th>Replied</th><th>Bounced</th>
            <th>Refused</th><th>Positives per 1,000</th><th>Copy</th><th>Leads left</th><th>Last sent</th>
          </tr></thead>
          <tbody>
            {shown.map(r => [
              <tr key={r.id} title={r.action} className="click" onClick={() => setOpen(open === r.id ? null : r.id)}>
                <td><span className={'verdict ' + TONE[r.verdict]}>{r.label}</span></td>
                <td className="dom">{R(r.name)}</td>
                <td className="dim small">{(r.status || '').toLowerCase()}</td>
                <td>{num(r.sent)}</td>
                <td>{pct(r.reply_rate)}</td>
                <td className={r.bounce_rate >= 5 ? 'p1' : r.bounce_rate >= 3 ? 'p2' : ''}>{pct(r.bounce_rate)}</td>
                <td>{pct(r.sender_bounce_rate)}</td>
                <td className="dim">{r.per_1000 == null ? '–' : r.per_1000}</td>
                <td>{copyCell(r.copy)}</td>
                <td>{r.left == null ? '–' : num(r.left)}</td>
                <td className="dim small">{r.last_sent ? day(r.last_sent) : '–'}</td>
              </tr>,
              open === r.id && <tr key={r.id + '-x'} className="detail"><td colSpan={11}>
                <p><b>{r.label}.</b> {r.action}</p>
                {r.copy && r.copy.issues.length > 0 ? <ul className="small">{r.copy.issues.map((i, n) => (
                  <li key={n} className={i.level === 'broken' ? 'p1' : ''}><span className="dim">{i.where}:</span> {i.what}
                    {i.example && <div className="mono faint">{R(i.example)}</div>}</li>))}</ul>
                  : <p className="dim small">{r.copy ? 'Nothing wrong found in the copy.' : 'The copy is read at the next check.'}</p>}
              </td></tr>])}
          </tbody>
        </table>
        {!shown.length && <p className="dim" style={{ marginTop: 14 }}>Nothing here.</p>}
      </div>
    </section>
  )
}
