// Details slide in from the right over whatever page you are on: an
// incident or a domain.  Esc or the scrim closes it.
import { useEffect, useState } from 'react'
import Chart from './Chart'
import { Strip, Legend } from './Map'
import Change from './Change'
import { cite } from './Briefing'
import { Verdict } from './Domains'
import { PROVIDER, kindName, day, when, get, num, pct, words, serial, standing, priority, R } from './api'

export default function Drawer({ close, children }) {
  useEffect(() => {
    const key = e => { if (e.key === 'Escape') close() }
    addEventListener('keydown', key)
    return () => removeEventListener('keydown', key)
  }, [close])
  return (
    <>
      <div className="scrim" onClick={close} />
      <aside className="drawer">
        <div className="dhead"><span className="dim small">Esc to close</span>
          <button className="line close" onClick={close}>Close</button></div>
        {children}
      </aside>
    </>
  )
}

const FACTS = {
  rate: ['Refused', v => (v * 100).toFixed(0) + '%'], days: ['Days running', num], since: ['Since', day],
  blocked: ['Sends stopped', num], last_3_days: ['Last 3 days', num], refused: ['Refused', num],
  sent: ['Sent', num], deferred: ['Deferred', num], bounces: ['Bounces', num],
  bounces_naming_them: ['Bounces naming them', num], bounces_naming_it: ['Bounces naming it', num],
  left: ['Leads left', num], pace: ['New leads a day', num], idle: ['Sends a day idle', num],
  down: ['Disconnected mailboxes', num], lost: ['Sends a day lost', num], limit: ['Bounce limit', v => v + '%'],
}

const VERBS = { stop: 'Stop', resume: 'Resume', pause: 'Pause', unpause: 'Unpause', slow: 'Slow down',
  restore: 'Restore', fix_dns: 'Fix DNS on', scrub: 'Clean the list behind', route: 'Route Gmail leads to',
  suppress_apple: 'Drop Apple addresses', hold_receiver: 'Hold recipients at', review_copy: 'Review the copy',
  remove_link: 'Remove the link', reconnect: 'Reconnect the mailboxes on', check: 'Check',
  load_leads: 'Load new leads', investigate: 'Look into' }

function verb(c) {
  const v = VERBS[c.op] || c.op.replace('_', ' ')
  return c.provider && ['stop', 'resume', 'slow', 'restore'].includes(c.op) ? v + ' ' + PROVIDER[c.provider] + ' on' : v
}

function names(t) {
  if (!t || !t.length) return ''
  return t.slice(0, 6).join(', ') + (t.length > 6 ? ' and ' + (t.length - 6) + ' more' : '')
}

// Steps the app can carry out after approval; the rest are done by hand,
// and the review says exactly how.
const CAN = { pause: 1, suppress_apple: 1, hold_receiver: 1, change_settings: 1 }

export function IncidentView({ id, strips, fleet, toast }) {
  const [inc, setInc] = useState(null)
  // #/incidents/12?review=0 opens the review of step 0 directly.
  const [said, setSaid] = useState('')
  const [ask, setAsk] = useState(null)
  const [review, setReview] = useState(() => {
    const r = new URLSearchParams(location.hash.split('?')[1] || '').get('review')
    return r == null ? null : Number(r)
  })
  useEffect(() => { setInc(null); get('/api/incidents/' + id).then(setInc) }, [id])
  if (!inc) return <p className="dim">Reading.</p>
  const facts = Object.entries(inc.evidence).filter(([k, v]) => FACTS[k] && v != null && v !== '')
  const doms = inc.domains || []
  const lines = ['receiver', 'listed', 'link_listed', 'complaints'].includes(inc.kind)
    ? ['google', 'microsoft', 'other'] : inc.kind === 'apple' ? ['apple', 'google', 'microsoft'] : ['google', 'microsoft']
  return (
    <>
      <div style={{ display: 'flex', gap: 6, marginTop: 16 }}>
        <span className="label">{serial(inc.id)}</span>
        <span className="label p1">{kindName(inc.kind)}</span>
        <span className="label">{inc.confidence} confidence</span>
        <span className="label">{inc.status === 'open' ? 'open' : 'over'}</span>
      </div>
      <h1>{words(inc.title)}</h1>
      <p className="dim small">First seen {day(inc.opened)}, last seen {day(inc.updated)}.
        {inc.ending ? ' ' + words(inc.ending) + '.' : ''}</p>

      <h3>Assessment</h3>
      <p>{inc.why}</p>
      {facts.length > 0 && <dl className="kv">
        {facts.map(([k, v]) => <div key={k}><dt>{FACTS[k][0]}</dt><dd>{FACTS[k][1](v)}</dd></div>)}
      </dl>}

      <h3>Recommended action</h3>
      {inc.plan.length ? (
        <ol className="steps">{inc.plan.map((c, n) => (
          <li key={n}>
            <div><b>{verb(c)}</b> <span className="tgt">{names(c.targets)}</span><div className="why">{c.why}</div></div>
            {inc.applied && inc.applied[n]
              ? <span className="done small">Applied {when(inc.applied[n].at)}. <a href="#/changes">Undo is on Changes</a></span>
              : <button className={CAN[c.op] ? '' : 'line'} onClick={() => setReview(n)}>{CAN[c.op] ? 'Review' : 'How'}</button>}
          </li>
        ))}</ol>
      ) : <p className="dim">{inc.covered ? <>Nothing new here: {cite(inc.covered)}.</>
                                          : 'Nothing new: what this needs is already in place.'}</p>}
      {inc.plan.length > 0 && inc.covered && <p className="dim small">And {cite(inc.covered)}.</p>}
      <div className="say">
        <label className="small dim">Or say what you want done instead, in your own words. You see the exact change before anything happens.</label>
        <div className="row">
          <input value={said} onChange={e => setSaid(e.target.value)}
                 onKeyDown={e => e.key === 'Enter' && said.trim() && setAsk(said)}
                 placeholder="e.g. pause only the two worst domains and their warmup" />
          <button className="line" disabled={!said.trim()} onClick={() => setAsk(said)}>Preview</button>
        </div>
      </div>
      {review != null && <Change fleet={fleet} incident={inc.id} step={review} close={() => setReview(null)}
                                 planned={inc.plan[review] && { what: verb(inc.plan[review]) + ' ' + names(inc.plan[review].targets), why: inc.plan[review].why }}
                                 done={status => toast('Change ' + status + '. It is on the Changes page, with Undo.')} />}
      {ask && <Change fleet={fleet} incident={inc.id} request={ask} close={() => setAsk(null)}
                      done={status => { setSaid(''); toast('Change ' + status + '. It is on the Changes page, with Undo.') }} />}

      <h3>Evidence</h3>
      <div className="charts">
        <Chart series={inc.series} field="refused" title="Refused, % of sends" lines={lines} />
        <Chart series={inc.series} field="replied" title="Replied, % of delivered" lines={lines} />
      </div>

      {inc.samples.length > 0 && <>
        <h3>What the receivers said</h3>
        {inc.samples.map((s, n) => <blockquote key={n}><b>{s.domain}</b>{'\n'}{s.text}</blockquote>)}
      </>}

      {doms.length > 0 && <>
        <h3>{doms.length === 1 ? 'Domain' : doms.length + ' domains'}</h3>
        <ul className="domlist">
          {doms.slice(0, 40).map(d => (
            <li key={d}><a href={'#/domains/' + d}>{R(d)}</a>{strips && strips[d] && <Strip s={strips[d].slice(-21)} />}</li>
          ))}
        </ul>
        {doms.length > 40 && <p className="dim small">and {doms.length - 40} more</p>}
      </>}
    </>
  )
}

const KIND = {
  gmail_reputation: 'Gmail: sender reputation', surbl: 'SURBL names it', spamhaus: 'Spamhaus names it',
  surbl_ip: 'SURBL, an IP or link', spamhaus_ip: 'Spamhaus, an IP', apple_cs01: 'Apple policy block',
  auth: 'failed authentication', tenant_block: 'Microsoft blocked the tenant', isp_block: 'an ISP refused it',
  gateway_block: 'a corporate gateway refused it', microsoft_block: 'Microsoft refused it',
  restricted: 'internal-only mailbox', ip_block: 'IP blocked', mailbox_full: 'mailbox full',
  user_unknown: 'address does not exist', loop: 'mail loop', spam_filter: 'spam filter on the content',
  expired: 'gave up retrying', policy: 'receiver policy', throttle: 'Gmail rate limit',
  complaints: 'user complaints', other: 'other',
}
const ISSUE = {
  no_mx: 'No mail server (MX)', parked: 'Parked at the registrar', no_spf: 'No SPF record',
  spf_misses_host: 'SPF does not name the sending provider', spf_lookups: 'SPF needs more than 10 lookups',
  spf_many: 'More than one SPF record', no_dmarc: 'No DMARC record', no_dkim: 'No DKIM key found',
}
const PROV_LONG = { google: 'Gmail and Google Workspace', microsoft: 'Outlook and Microsoft 365',
  apple: 'iCloud (Apple)', other: 'Other providers' }
const HOSTS = { google: 'Google Workspace', microsoft: 'Microsoft 365', other: 'other hosting' }

export function DomainView({ name }) {
  const [d, setD] = useState(null)
  useEffect(() => { setD(null); get('/api/domains/' + encodeURIComponent(name)).then(setD) }, [name])
  if (!d) return <p className="dim">Reading {name}.</p>
  if (d.error) return <p>{name} is not in this workspace.</p>
  const totals = {}
  for (const k of d.kinds) for (const [kind, n] of Object.entries(k.kinds)) totals[kind] = (totals[kind] || 0) + n
  const kinds = Object.entries(totals).sort((a, b) => b[1] - a[1])
  const acc = d.accounts || {}
  const dns = d.dns
  return (
    <>
      <div style={{ display: 'flex', gap: 6, marginTop: 16 }}>
        {d.family && <span className="label">{R(d.family + '*')} family</span>}
        {d.host && <span className="label">{HOSTS[d.host]}</span>}
        {d.listed && <span className="label p1">listed on {d.listed[0]}</span>}
      </div>
      <div className="dossier">
        <div className="dk">DOSSIER</div>
        <h1 className="mono" style={{ fontFamily: 'var(--mono)', fontSize: 19, margin: '2px 0 0' }}>{R(d.domain)}</h1>
        <span className={'stamp ' + standing(d.strip)[1]} title={standing(d.strip)[2]}>{standing(d.strip)[0]}</span>
      </div>
      <p className="dim small">{d.mailboxes ? num(d.mailboxes) + ' mailboxes' : 'No mailboxes here'}
        {acc.down ? ', ' + acc.down + ' disconnected' : ''}{d.health ? ', warmup score ' + d.health[0] : ''}.</p>
      <div style={{ marginTop: 14 }}><Strip s={d.strip} days={d.days} size="big" /></div>
      <div className="strip-days dim small" style={{ display: 'flex', justifyContent: 'space-between', marginTop: 4 }}>
        <span>{day(d.days[0])}</span><span>{day(d.days[d.days.length - 1])}</span></div>
      <Legend />

      {d.verdict && <>
        <h3>{d.verdict.judged ? 'Verdict, on its latest ' + num(d.verdict.judged.sent) + ' sends (' + day(d.verdict.judged.from)
          + ' to ' + day(d.verdict.judged.to) + ')' : 'Verdict, last 14 days'}</h3>
        <p><Verdict v={d.verdict} /> <span className="dim">{d.verdict.action}</span></p>
        {d.verdict.delist && d.verdict.delist.length > 0 && <ul className="small">
          {d.verdict.delist.map((t, i) => <li key={i}>{t}</li>)}
          <li className="dim">First stop what put it there (a link or tracking domain in the copy, a jump in volume,
            complaints), or it is listed again within days.</li></ul>}
        {d.verdict.judged && <p className="small dim">Over those sends: {pct(d.verdict.judged.bounce_rate)} bounced,
          {' '}{pct(d.verdict.judged.sender_bounce_rate)} refused because of the sending domain, {d.verdict.judged.per_1000 ?? 'unknown'} positive replies per 1,000.</p>}
        {d.verdict.sent >= 500 && <p className="small dim">Last 14 days: {num(d.verdict.sent)} sent, {d.verdict.per_1000 ?? 'unknown'} positive replies per 1,000
          (workspace {d.verdict.fleet_per_1000}), {pct(d.verdict.sender_bounce_rate)} refused, {pct(d.verdict.list_bounce_rate)} bad addresses.</p>}
        {d.buckets && d.buckets.some(b => b[2]) && <dl className="kv buckets">
          {d.buckets.filter(b => b[2]).map(([label, side, n]) => (
            <div key={label}><dt>{label}</dt><dd>{num(n)} <span className="faint small">{
              { list: 'your list', sender: 'your domain', copy: 'your copy', none: 'not a signal' }[side]}</span></dd></div>))}
        </dl>}
      </>}
      <h3>By recipient's email provider</h3>
      <table className="byprov">
        <thead><tr><th>Provider</th><th>Sent, 7 days</th><th>Refused</th><th>Replied</th><th>Sent, {d.days.length} days</th><th>Refused</th><th>Replied</th></tr></thead>
        <tbody>
          {['google', 'microsoft', 'apple', 'other'].filter(p => d.by_provider[p]).map(p => {
            const w = d.by_provider[p].week, a = d.by_provider[p].all
            const tone = v => v >= 30 ? 'p1' : v >= 5 ? 'p2' : ''
            return (
              <tr key={p}>
                <td><i className={'sw ' + p} />{PROV_LONG[p]}</td>
                <td>{num(w.sent)}</td><td className={tone(w.refused_pct)}>{pct(w.refused_pct)}</td><td>{pct(w.replied_pct)}</td>
                <td>{num(a.sent)}</td><td className={tone(a.refused_pct)}>{pct(a.refused_pct)}</td><td>{pct(a.replied_pct)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <p className="faint small" style={{ marginTop: 6 }}>Refused: the receiving server turned the email away because of the sender.
        Replied: replies per delivered email.  Gmail means Google-hosted inboxes (gmail.com and Google Workspace); Microsoft
        means Outlook and Microsoft 365; other providers covers everything else.</p>

      <h3>Day by day</h3>
      <div className="charts">
        <Chart series={d.series} field="refused" title="Refused, % of sends" lines={['google', 'microsoft', 'other']} />
        <Chart series={d.series} field="replied" title="Replied, % of delivered" lines={['google', 'microsoft', 'other']} />
      </div>

      {d.incidents.length > 0 && <>
        <h3>Incidents</h3>
        <ul className="rows">
          {d.incidents.map(i => (
            <li key={i.id} onClick={() => { location.hash = '#/incidents/' + i.id }}>
              <span className={'prio ' + priority(i.severity)[1]}>{priority(i.severity)[0]}</span>
              <div><div className="t">{words(i.title)}</div><div className="m">{serial(i.id)}, {day(i.opened)} to {day(i.updated)}</div></div>
              <span className="age">{i.status === 'open' ? 'open' : 'over'}</span>
            </li>
          ))}
        </ul>
      </>}

      <h3>Why mail bounced</h3>
      {kinds.length ? <table><tbody>{kinds.map(([k, n]) => <tr key={k}><td>{KIND[k] || k}</td><td>{num(n)}</td></tr>)}</tbody></table>
        : <p className="dim">Nothing bounced.</p>}

      <h3>DNS</h3>
      {dns ? <>
        {dns.issues.length ? dns.issues.map(i => <p key={i} className="p1">{ISSUE[i] || i}</p>)
          : <p>SPF, DKIM, DMARC and MX in place.</p>}
        <dl className="kv">
          <div><dt>MX</dt><dd className="small">{(dns.mx || []).join(', ') || 'none'}</dd></div>
          <div><dt>SPF</dt><dd className="small">{(dns.spf || []).join(' | ') || 'none'}</dd></div>
          <div><dt>DMARC</dt><dd className="small">{dns.dmarc || 'none'}</dd></div>
          <div><dt>SURBL</dt><dd className="small">{dns.surbl == null ? 'unknown' : dns.surbl.length ? 'listed' : 'clean'}</dd></div>
          {dns.registrar && <div><dt>Registrar</dt><dd className="small">{dns.registrar}</dd></div>}
          {dns.created && <div><dt>Registered</dt><dd className="small">{day(dns.created)} {dns.created.slice(0, 4)}</dd></div>}
          {dns.expires && <div><dt>Expires</dt><dd className="small">{day(dns.expires)} {dns.expires.slice(0, 4)}</dd></div>}
          {dns.ns && dns.ns.length > 0 && <div><dt>Nameservers</dt><dd className="small">{dns.ns.slice(0, 2).join(', ')}</dd></div>}
          {dns.redirect && <div><dt>Website forwards to</dt><dd className="small">{R(dns.redirect)}</dd></div>}
        </dl>
        <p className="faint small" style={{ marginTop: 6 }}>Checked {day(dns.checked)}.</p>
      </> : <p className="dim">Not checked yet.</p>}

      {d.samples.length > 0 && <>
        <h3>What the receivers said</h3>
        {d.samples.slice(0, 6).map((s, n) => <blockquote key={n}><b>{KIND[s.kind] || s.kind}</b>{'\n'}{s.text}</blockquote>)}
      </>}
    </>
  )
}
