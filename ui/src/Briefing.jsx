// Home, in the order you act on it: what needs a decision, the numbers,
// senders against recipients, the fleet day by day, the written report.
import { useEffect, useState } from 'react'
import Map, { Legend } from './Map'
import { Briefing as Brief } from './Text'
import { fold } from './group'
import Range, { rangeQuery, rangeName } from './Range'
import { PROVIDER, ago, day, get, num, pct, words, priority, R } from './api'

// Replies per delivered email, each provider side by side.
function Replies({ p, when }) {
  const rows = [['Gmail', p.google], ['Microsoft', p.microsoft], ['Others', p.other]].filter(([, t]) => t && t.sent)
  return (
    <div className="stat" title="Replies per delivered email, by the recipient's email provider. Gmail far below Microsoft usually means Gmail is filing it as spam.">
      <div className="k">Reply rate by recipient, {when}</div>
      <div className="trio">{rows.length ? rows.map(([name, t]) => (
        <div key={name}><span className="pv">{pct(t.replied)}</span><span className="pn">{name}</span></div>))
        : <div><span className="pv">–</span><span className="pn">nothing sent</span></div>}</div>
    </div>
  )
}

function Card({ k, v, sub, cls, help }) {
  return (
    <div className={'stat ' + (cls || '')} title={help}>
      <div className="k">{k}</div><div className="v">{v}</div><div className="s" title={sub}>{sub}</div>
    </div>
  )
}

// The same mail two ways: by what your mailboxes are, and by whose inbox it
// went to.  A sender problem shows on the left, a receiver's on the right.
function Split({ infra, providers, when }) {
  const mine = Object.values(infra || {})
  const theirs = Object.entries(providers || {}).filter(([, t]) => t.sent)
  return (
    <div className="cols even">
      {mine.length > 1 && <section className="sec">
        <header><h2>By your mailboxes, {when}</h2></header>
        <table><thead><tr><th>Mailboxes on</th><th>Domains</th><th>Sent</th><th>Replied</th>
          <th>Positives /1,000</th><th>Refused</th></tr></thead>
          <tbody>{mine.map(r => (
            <tr key={r.name}><td>{r.name}</td><td>{num(r.domains)}</td><td>{num(r.sent)}</td><td>{pct(r.replied)}</td>
              <td>{r.per_1000}</td><td className={r.sender_bounces >= 3 ? 'p1' : ''}>{pct(r.sender_bounces)}</td></tr>))}
          </tbody></table>
      </section>}
      <section className="sec">
        <header><h2>By their provider, {when}</h2></header>
        <table><thead><tr><th>Inbox at</th><th>Sent</th><th>Replied</th><th>Refused</th></tr></thead>
          <tbody>{theirs.map(([p, t]) => (
            <tr key={p}><td>{PROVIDER[p]}</td><td>{num(t.sent)}</td><td>{pct(t.replied)}</td>
              <td className={t.refused >= 5 ? 'p1' : ''}>{pct(t.refused)}</td></tr>))}
          </tbody></table>
      </section>
    </div>
  )
}

export function Status({ s }) {
  const k = s.kpis
  const [range, setRange] = useState({ range: '7' })
  const [r, setR] = useState(null)
  useEffect(() => {
    setR(null)
    get('/api/stats?' + rangeQuery(range)).then(setR)
  }, [range, s.collected])
  const when = rangeName(range)
  const quiet = s.source !== 'simulated' && s.last ? Math.floor((Date.now() - Date.parse(s.last)) / 864e5) : 0
  const ok = r && !r.error
  return (
    <>
      {quiet > 3 && <p className="note" style={{ marginBottom: 12 }}>Nothing has been sent since {day(s.last)}, {quiet} days ago:
        the campaigns or the mailboxes have stopped.</p>}
      <Range value={range} onChange={setRange} first={s.first} last={s.last}
             note={r && r.error ? r.error : r && r.live ? 'Today so far, live from PlusVibe.'
               : r && r.from ? day(r.from) + ' to ' + day(r.to) : ''} />
      <div className="status">
        <Card k={'Sent, ' + when} v={ok ? num(r.sent) : '…'}
              sub={k.reserve ? num(k.reserve.sending) + ' domains sending, ' + num(k.reserve.spare) + ' spare'
                : num(s.domains) + ' domains, ' + num(s.mailboxes) + ' mailboxes'}
              help={k.reserve ? 'Spare: warmed domains whose mailboxes are in no campaign, ready to swap in for a burned one. '
                + k.reserve.lost + ' domains lost in the last 30 days.' : 'Emails sent in the range, every provider.'} />
        {ok ? <Replies p={r.providers} when={when} /> : <Card k={'Reply rate, ' + when} v="…" />}
        <Card k={'Positive replies, ' + when} v={ok ? (r.per_1000 == null ? '–' : r.per_1000 + ' per 1,000') : '…'}
              sub={ok ? num(r.positives) + ' positive replies' : ''}
              help="Replies marked interested, per 1,000 emails sent." />
        <Card k="Leads left" v={k.runway == null ? '–' : k.runway < 1.5 ? '~1 day' : Math.round(k.runway) + ' days'}
              sub={k.left == null ? 'no active campaigns' : k.dry && k.dry.length
                ? `${k.dry.length} campaign${k.dry.length > 1 ? 's' : ''} dry within 2 days`
                : num(k.left) + ' not yet contacted'}
              cls={(k.runway != null && k.runway < 3) || (k.dry && k.dry.length) ? 'p1' : ''}
              help="How long the uncontacted leads in active campaigns last at the current pace." />
        <Card k={'Refused, ' + when} v={ok ? pct(r.sender_bounces) : '…'}
              sub="refused because of the sending domain" cls={ok && r.sender_bounces >= 2 ? 'p2' : ''}
              help="Emails refused because of who sent them: a blocked domain, a blocklist, poor reputation. Your domains are the problem." />
        <Card k={'Bad addresses, ' + when} v={ok ? pct(r.recipient_bounces) : '…'}
              sub="no such address, inbox full: the list" cls={ok && r.recipient_bounces >= 3 ? 'p2' : ''}
              help="Emails refused because of the address: no such mailbox, inbox full, account disabled. Your lists are the problem." />
        <Card k="Domains blocklisted" v={num(k.listed)} sub={'of ' + num(s.domains) + ', as of the last check'} cls={k.listed ? 'p2' : ''}
              help="Domains that Spamhaus or SURBL name, in bounces or in DNS. Something to get off, not a reason to stop a domain that still delivers." />
        <Card k="Disconnected mailboxes" v={num(k.down)} sub={'of ' + num(s.mailboxes)} cls={k.down ? 'p2' : ''}
              help="Mailboxes whose connection to the sequencer broke (password changed, token expired, account suspended). They send nothing until reconnected." />
      </div>
      {ok && !r.live && <Split infra={r.infra} providers={r.providers} when={when} />}
    </>
  )
}

export function Prio({ n }) {
  const [label, cls] = priority(n)
  return <span className={'prio ' + cls}>{label}</span>
}

// "#18" in a plan opens incident 18.
export function cite(text) {
  return text.split(/(#\d+)/).map((part, n) => /^#\d+$/.test(part)
    ? <a key={n} href={'#/incidents/' + part.slice(1)} onClick={e => e.stopPropagation()}>{part}</a>
    : part)
}

export function Row({ i, onClick }) {
  return (
    <li onClick={onClick}>
      <Prio n={i.severity} />
      <div>
        <div className="t">{words(i.title)}</div>
        {i.plan && <div className="m">To do: {cite(i.plan)}</div>}
      </div>
      <span className="age">{i.status === 'open' ? 'for ' + ago(i.opened) : 'over'}</span>
    </li>
  )
}

const KEY = [['o', 'fine'], ['w', 'some mail refused'], ['x', 'most mail refused'], ['l', 'on a blocklist']]

export default function Briefing({ s, go }) {
  const [map, setMap] = useState(null)
  useEffect(() => { get('/api/map').then(setMap) }, [s.collected])
  const decide = fold(s.decide, s.kpis.listed)
  const st = s.kpis.states
  const b = s.briefing
  const written = b && b.how === 'analyst'
  return (
    <>
      <section className="sec first">
        <header><h2>Needs a decision{decide.length ? ' (' + decide.length + ')' : ''}</h2>
          <span className="r"><a href="#/incidents">All incidents</a></span></header>
        {decide.length ? (
          <ul className="rows">
            {decide.slice(0, 6).map(i => (
              <Row key={i.kind + i.id} i={i} onClick={() => go(i.group ? '#/incidents?kind=' + i.kind : '#/incidents/' + i.id)} />
            ))}
            {decide.length > 6 && <li className="more" onClick={() => go('#/incidents')}><span />
              <div className="dim">and {decide.length - 6} more</div><span /></li>}
          </ul>
        ) : <p className="body dim">Nothing needs a decision.</p>}
      </section>
      <Status s={s} />
      <section className="sec">
        <header><h2>The fleet, day by day</h2>
          <span className="r">On {day(s.last)}: {KEY.filter(([c]) => st[c]).map(([c, label]) => num(st[c]) + ' ' + label).join(', ')}.
            {' '}<a href="#/map">Full map</a></span></header>
        <div className="body"><Legend /><div style={{ marginTop: 10 }}><Map map={map} go={go} first={8} /></div></div>
      </section>
      <details className="sec report" open={written}>
        <summary><h2>{written ? 'Report, written by the analyst' : 'Report, as sent'}</h2>
          <span className="r">the same findings in prose, to paste or forward</span></summary>
        <div className="body">
          <Brief text={b ? b.text : s.brief} at={b ? b.at : s.collected} how={b ? b.how : 'plain'} fleet={R(s.name)}
                 cond={s.status} />
        </div>
      </details>
    </>
  )
}
