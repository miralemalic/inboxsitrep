// Every sending domain over a range of days, with its verdict: keep it, fix
// it or stop it.  Click a heading to sort, a row to open it.
import { useEffect, useState } from 'react'
import { day, get, num, pct, R, standing } from './api'
import Range, { rangeQuery, rangeName } from './Range'

// Worst first when sorted by verdict.
const ORDER = ['account_blocked', 'burned', 'gmail_blocked', 'microsoft_blocked', 'fix_auth', 'bad_list', 'mixed', 'copy', 'refused', 'not_enough_bounces',
  'listed_low', 'underperforming', 'listed', 'healthy', 'strong', 'silent', 'early', 'not_enough_data']
const BAD = ['account_blocked', 'burned']
// Sends counted, nothing else: a data question, not a verdict on the domain.
const CHECK = ['silent']
const FIX = ['gmail_blocked', 'microsoft_blocked', 'fix_auth', 'bad_list', 'mixed', 'copy', 'refused', 'not_enough_bounces', 'underperforming']
const DELIST = ['listed', 'listed_low']
const KEEP = ['healthy', 'strong', 'listed', 'early']

export function verdictTone(v) {
  return BAD.includes(v) ? 'p1' : FIX.includes(v) || DELIST.includes(v) ? 'p2'
    : ['not_enough_data', 'early', 'silent'].includes(v) ? 'faint' : 'ok'
}

const COLS = [
  ['verdict', 'Verdict', r => r.verdict ? ORDER.indexOf(r.verdict.verdict) : 99],
  ['status', 'Latest day', r => ['Blocklisted', 'Refused', 'Refused today', 'Some refused', 'Recovering', 'Not sending', 'Healthy'].indexOf(standing(r.strip)[0])],
  ['domain', 'Domain', r => r.domain],
  ['sent', 'Sent', r => r.verdict ? r.verdict.sent : 0],
  ['per1000', 'Positives per 1,000', r => r.verdict ? r.verdict.per_1000 : null],
  ['greplied', 'Gmail replied', r => r.google.replied],
  ['mreplied', 'Microsoft replied', r => r.microsoft.replied],
  ['sender', 'Refused', r => r.verdict ? r.verdict.sender_bounce_rate : null],
  ['list', 'Bad addresses', r => r.verdict ? r.verdict.list_bounce_rate : null],
  ['health', 'Warmup', r => r.health ? r.health[0] : null],
]

function tone(v, warn, bad) {
  if (v == null) return ''
  return v >= bad ? 'p1' : v >= warn ? 'p2' : ''
}

export function Verdict({ v }) {
  if (!v) return <span className="faint">not sending</span>
  const j = v.judged
  const on = j ? '\n\nJudged on its latest ' + num(j.sent) + ' sends, ' + day(j.from) + ' to ' + day(j.to) + ': '
    + pct(j.bounce_rate) + ' bounced, ' + pct(j.sender_bounce_rate) + ' refused.' : ''
  return <span className={'verdict ' + verdictTone(v.verdict)} title={v.action + on}>{v.label}</span>
}

export default function Domains({ go, first, last, reserve, source }) {
  const [got, setGot] = useState(null)
  const [range, setRange] = useState({ range: '14' })
  const [filter, setFilter] = useState('')
  const [sort, setSort] = useState(['verdict', 1])
  const [only, setOnly] = useState('')
  useEffect(() => { setGot(null); get('/api/fleet?' + rangeQuery(range)).then(setGot) }, [range])
  const rows = got && got.rows
  const [key, dir] = sort
  const col = COLS.find(c => c[0] === key)
  const shown = (rows || []).filter(r => r.domain.includes(filter.toLowerCase())
    && (!only || (only === 'stop' && r.verdict && BAD.includes(r.verdict.verdict))
      || (only === 'fix' && r.verdict && FIX.includes(r.verdict.verdict))
      || (only === 'delist' && r.verdict && DELIST.includes(r.verdict.verdict))
      || (only === 'good' && r.verdict && KEEP.includes(r.verdict.verdict))
      || (only === 'check' && r.verdict && CHECK.includes(r.verdict.verdict))
      || (only === 'listed' && r.listed) || (only === 'dns' && r.dns && r.dns.length) || (only === 'down' && r.down)))
    .sort((a, b) => {
      const x = col[2](a), y = col[2](b)
      if (x == null) return 1
      if (y == null) return -1
      return (x < y ? -1 : x > y ? 1 : 0) * dir
    })
  const count = v => (rows || []).filter(r => r.verdict && v.includes(r.verdict.verdict)).length
  return (
    <section>
      {reserve ? <Reserve r={reserve} /> : source === 'plusvibe' &&
        <p className="note" style={{ marginBottom: 18 }}>Burn rate and spare domains show here after the next check of this workspace.</p>}
      <Range value={range} onChange={setRange} today={false} first={first} last={last}
             note="Columns show the selected range. Verdicts use each domain's latest 1,000 sends, so they show where it stands today." />
      <div className="toolbar">
        <input placeholder="Find a domain" value={filter} onChange={e => setFilter(e.target.value)} />
        <div className="chips">
          {[['', 'All'], ['stop', 'Stop (' + count(BAD) + ')'], ['fix', 'Fix (' + count(FIX) + ')'],
            ['delist', 'Delist (' + count(DELIST) + ')'], ['good', 'Keep (' + count(KEEP) + ')'],
            ...(count(CHECK) ? [['check', 'No data (' + count(CHECK) + ')']] : []),
            ['dns', 'DNS problems'], ['down', 'Disconnected mailboxes']].map(([v, l]) =>
            <button key={v} className={only === v ? 'on' : ''} onClick={() => setOnly(v)}>{l}</button>)}
        </div>
        <span className="faint small" style={{ marginLeft: 'auto' }}>{rows ? num(shown.length) + ' domains, ' + (got ? day(got.from) + ' to ' + day(got.to) : rangeName(range)) : 'Reading.'}</span>
      </div>
      {rows && <div style={{ maxHeight: 'calc(100vh - 260px)', overflow: 'auto' }}>
        <table>
          <thead><tr>
            {COLS.map(([k, label]) => (
              <th key={k} className={k === key ? 'sorted' : ''}
                  onClick={() => setSort([k, k === key ? -dir : ['domain', 'verdict'].includes(k) ? 1 : -1])}>
                {label}{k === key ? (dir > 0 ? ' ↑' : ' ↓') : ''}</th>))}
            <th>Notes</th>
          </tr></thead>
          <tbody>
            {shown.slice(0, 600).map(r => (
              <tr key={r.domain} className="click" onClick={() => go('#/domains/' + r.domain)}>
                <td><Verdict v={r.verdict} /></td>
                <td><span className={'asset ' + standing(r.strip)[1]} title={standing(r.strip)[2]}>{standing(r.strip)[0]}</span></td>
                <td className="dom">{R(r.domain)}</td>
                <td>{r.verdict ? num(r.verdict.sent) : '–'}</td>
                {r.verdict && CHECK.includes(r.verdict.verdict)
                  ? <td colSpan={5} className="faint notes">no replies or bounces recorded for its sends</td>
                  : <>
                    <td title={r.verdict && r.verdict.unlabeled ? 'Unknown: the campaigns behind these sends were deleted, and their positive-reply labels with them' : ''}>{r.verdict && r.verdict.per_1000 != null ? r.verdict.per_1000 : '–'}</td>
                    <td>{pct(r.google.replied)}</td>
                    <td>{pct(r.microsoft.replied)}</td>
                    <td className={tone(r.verdict && r.verdict.sender_bounce_rate, 1, 3)}>{r.verdict ? pct(r.verdict.sender_bounce_rate) : '–'}</td>
                    <td className={tone(r.verdict && r.verdict.list_bounce_rate, 2, 3)}>{r.verdict ? pct(r.verdict.list_bounce_rate) : '–'}</td>
                  </>}
                <td className={tone(r.health && 100 - r.health[0], 30, 50)}>{r.health ? r.health[0] : '–'}</td>
                <td className="notes">{notes(r)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>}
    </section>
  )
}

// Burn rate and the spares that cover it: how many domains were lost in 30
// days, how many are warmed and waiting, and how many to buy.
function Reserve({ r }) {
  const weeks = r.weeks == null ? null : r.weeks < 1 ? 'under a week' : Math.round(r.weeks) + (Math.round(r.weeks) === 1 ? ' week' : ' weeks')
  return (
    <div className={'reserve' + (r.buy ? ' short' : '')}>
      <div><span className="k">Sending</span><b>{num(r.sending)}</b><span className="s">domains</span></div>
      <div><span className="k">Lost, 30 days</span><b>{num(r.lost)}</b><span className="s">burned or blocked</span></div>
      <div><span className="k">Warmed spares</span><b>{num(r.spare)}</b><span className="s">in no campaign, ready to swap in</span></div>
      <div><span className="k">Cover</span><b>{weeks || '–'}</b><span className="s">at this rate</span></div>
      <div className="act">{r.buy
        ? <>Buy and warm <b>{num(r.buy)}</b> {r.why === 'month' ? 'to cover a month of losses.' : 'to keep at least 10% in reserve.'}</>
        : 'Spares cover a month of losses.'}</div>
    </div>
  )
}

function notes(r) {
  const out = []
  if (r.listed) out.push('listed on ' + ({ surbl: 'SURBL', spamhaus: 'Spamhaus', dbl: 'Spamhaus DBL' }[r.listed] || r.listed))
  if (r.auth) out.push('missing ' + r.auth.toUpperCase())
  if (r.down) out.push(r.down + ' mailboxes disconnected')
  if (r.dns && r.dns.length) out.push(r.dns.length + ' DNS ' + (r.dns.length === 1 ? 'issue' : 'issues'))
  return out.join(', ')
}
