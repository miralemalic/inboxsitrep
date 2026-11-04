// The incident inbox: everything found, worst first, with filters.  A row
// opens in the drawer.
import { useEffect, useState } from 'react'
import { Row } from './Briefing'
import { fold } from './group'
import { get, kindName, R } from './api'

const GROUPS = {
  all: null,
  reputation: ['burned', 'rejecting', 'cascade', 'quiet_spam', 'gmail_gap', 'warmup_drop', 'throttle', 'ms_block', 'complaints', 'episode'],
  blocks: ['listed', 'link_listed', 'tenant_block', 'receiver', 'content', 'apple'],
  operations: ['auth', 'dns', 'bad_list', 'mailboxes', 'stalled', 'runway', 'bounce_guard'],
}

export default function Incidents({ go, query, listed }) {
  const [list, setList] = useState(null)
  const [status, setStatus] = useState('open')
  const [group, setGroup] = useState('all')
  const kind = new URLSearchParams(query || '').get('kind')
  const [why, setWhy] = useState(null)
  useEffect(() => { get('/api/incidents').then(setList) }, [])
  useEffect(() => { if (kind === 'listed') get('/api/why-listed').then(setWhy) }, [kind])
  if (!list) return <p className="dim">Reading.</p>

  let shown = list.filter(i => (status === 'all' || (status === 'open') === (i.status === 'open'))
    && (!GROUPS[group] || GROUPS[group].includes(i.kind)) && (!kind || i.kind === kind))
  shown = kind ? shown.sort((a, b) => b.size - a.size) : fold(shown, status === 'open' ? listed : undefined)
  return (
    <section>
      <div className="toolbar">
        <div className="chips">
          {['open', 'over', 'all'].map(s => <button key={s} className={status === s ? 'on' : ''} onClick={() => setStatus(s)}>{s === 'over' ? 'Closed' : s === 'open' ? 'Open' : 'All'}</button>)}
        </div>
        <div className="chips">
          {Object.keys(GROUPS).map(g => <button key={g} className={group === g ? 'on' : ''} onClick={() => setGroup(g)}>{g[0].toUpperCase() + g.slice(1)}</button>)}
        </div>
        {kind && <a href="#/incidents" className="small">Showing {kindName(kind)} only. Show everything</a>}
        <span className="faint small" style={{ marginLeft: 'auto' }}>{shown.length} {shown.length === 1 ? 'item' : 'items'}</span>
      </div>
      {kind === 'listed' && why && (
        <div className="note" style={{ marginBottom: 16 }}>
          <b>What the listed domains have in common.</b>{' '}
          {why.lines.length ? 'Compared with the clean domains, these stand out:'
            : why.checked ? 'Nothing the DNS check knows sets them apart; look at the links in the copy and the lists they mailed.'
              : 'Not checked yet; the app checks registrations and DNS once a week.'}
          {why.lines.length > 0 && <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
            {why.lines.slice(0, 6).map((l, i) => <li key={i}>{R(l)}</li>)}</ul>}
        </div>
      )}
      {shown.length ? (
        <ul className="rows">
          {shown.map(i => <Row key={i.kind + i.id} i={i}
            onClick={() => go(i.group ? '#/incidents?kind=' + i.kind : '#/incidents/' + i.id + (query ? '?' + query : ''))} />)}
        </ul>
      ) : <div className="empty"><h2>Nothing here.</h2><p>No incidents match these filters.</p></div>}
    </section>
  )
}
