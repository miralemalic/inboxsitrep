import { useCallback, useEffect, useState } from 'react'
import Briefing from './Briefing'
import Incidents from './Incidents'
import Domains from './Domains'
import Analyst from './Analyst'
import Settings from './Settings'
import Changes from './Changes'
import Logs from './Logs'
import Campaigns from './Campaigns'
import Copy from './Copy'
import Overview from './Overview'
import Map, { Legend } from './Map'
import Drawer, { IncidentView, DomainView } from './Drawer'
import { fold } from './group'
import { get, post, setFleet, day, when, words, R, redacted, setRedact, setNames } from './api'

// Pages live in the hash so the window can be reloaded where it was.
function useHash() {
  const [hash, setHash] = useState(location.hash || '#/')
  useEffect(() => {
    const on = () => setHash(location.hash || '#/')
    addEventListener('hashchange', on)
    return () => removeEventListener('hashchange', on)
  }, [])
  return hash
}

// The workspace's pages, then what you do about them, then the app itself.
// The digit is the keyboard shortcut.
const NAV = [['', 'Briefing', '1'], ['incidents', 'Incidents', '2'], ['domains', 'Domains', '3'],
  ['campaigns', 'Campaigns', '4'], ['copy', 'Copy', '5'], ['map', 'Domain map', '6'], null,
  ['changes', 'Changes', '7'], ['analyst', 'Analyst', '8'], null,
  ['logs', 'Logs', '9'], ['settings', 'Settings', '0']]
const THEME = 'inboxsitrep-theme'
const TONE = { RED: 'p1', AMBER: 'p2', GREEN: 'ok' }
// One line under each page title saying what the page is for.
const ABOUT = {
  '': 'What needs a decision, the numbers behind it, and every domain day by day.',
  incidents: 'Every finding, most urgent first.  Open one for the evidence and the action.',
  map: 'Each row a domain family, each column a day.  Colour is how its mail was received.',
  domains: 'Every sending domain with its verdict: keep, fix, delist or stop.  Open one for its record.',
  campaigns: 'Every campaign with its verdict: scale, iterate or stop.',
  copy: 'Faults in the copy: broken sentences first, then what spam filters hold against it.',
  analyst: 'Ask about your domains, campaigns or receivers.  Answers cite the numbers.',
  changes: 'Every change made from here, each with an exact undo.',
  all: 'All workspaces side by side, worst first.  Open one for its own pages.',
  logs: 'Everything the app has done: checks, changes, alerts, reports sent.  Newest first.',
  settings: 'Workspaces, reports and where they go, this computer, the analyst.',
}

// How far a check run is, from how long each workspace took last time:
// [share done, seconds left].  Never shows done before it is.
function progress(st) {
  const ex = st.expect || []
  const total = ex.reduce((a, b) => a + b, 0) || 1
  const past = ex.slice(0, st.done).reduce((a, b) => a + b, 0)
  const cur = ex[st.done] || 0
  const ran = st.started ? Math.max(0, (Date.now() - Date.parse(st.started)) / 1000) : 0
  const share = Math.min(0.97, (past + Math.min(ran, cur * 0.97)) / total)
  const left = Math.max(0, cur - ran) + ex.slice(st.done + 1).reduce((a, b) => a + b, 0)
  return [share, left]
}

function leftWords(s) {
  return s < 45 ? 'almost done' : s < 90 ? 'about a minute left' : 'about ' + Math.round(s / 60) + ' min left'
}

export default function App() {
  const hash = useHash()
  const [app, setApp] = useState(null)
  const [fleets, setFleets] = useState(null)
  const [s, setS] = useState(null)
  const [map, setMap] = useState(null)
  const [chat, setChat] = useState([])
  const [toast, setToast] = useState('')
  const [, tick] = useState(0)
  const [redact, setRedactState] = useState(redacted())
  const [theme, setTheme] = useState(() => {
    try { return localStorage.getItem(THEME) === 'light' ? 'light' : 'dark' } catch (e) { return 'dark' }
  })
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try { localStorage.setItem(THEME, theme) } catch (e) { /* private window */ }
  }, [theme])
  useEffect(() => { if (toast) { const t = setTimeout(() => setToast(''), 6000); return () => clearTimeout(t) } }, [toast])

  const reload = useCallback(() => {
    get('/api/app').then(setApp)
    get('/api/fleets').then(f => {
      setFleets(Array.isArray(f) ? f : [])
      if (Array.isArray(f)) setNames(f.map(x => x.name))
      if (Array.isArray(f) && f.length) get('/api/summary').then(setS)
    })
  }, [])
  useEffect(() => { reload() }, [reload])
  // Follow a running check closely; read the results when it ends.
  useEffect(() => {
    const t = setInterval(() => {
      tick(n => n + 1)
      get('/api/app').then(a => setApp(prev => {
        if (prev && prev.state && prev.state.busy && !a.state.busy) {
          reload()
          setToast('Check finished. ' + (a.state.log.slice(-1)[0] || '').replace(/^\S+\s+/, ''))
        }
        return a
      }))
    }, 1500)
    return () => clearInterval(t)
  }, [reload])
  useEffect(() => { if (s) get('/api/map').then(setMap) }, [s && s.collected, s && s.name])

  const go = h => { location.hash = h }
  useEffect(() => {
    const key = e => {
      if (/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) || e.metaKey || e.ctrlKey || e.altKey) return
      const n = NAV.find(x => x && x[2] === e.key)
      if (n) go('#/' + n[0])
      if (e.key === 'r') toggleRedact()
    }
    addEventListener('keydown', key)
    return () => removeEventListener('keydown', key)
  }, [])

  const [path, query] = hash.split('?')
  const [, page, arg] = path.split('/')
  const state = app && app.state
  const busy = state && state.busy
  const decide = s ? fold(s.decide, s.kpis.listed) : []

  function toggleRedact() {
    const on = !redacted()
    setRedact(on)
    setRedactState(on)
    setToast(on ? 'Redacted: domain, family and workspace names are hidden. Press r to show them.' : 'Names shown.')
  }

  // A simulated fleet with planted problems, for a first look without a key.
  function tryDemo() {
    post('/api/app/settings', { fleets: [{ name: 'Demo', sequencer: 'sim' }] }).then(r => {
      if (r.error) return setToast(r.error)
      post('/api/app/check', { fleet: 'Demo' }).then(reload)
    })
  }

  let body, title = (NAV.find(n => n && n[0] === (page || '')) || NAV[0])[1]
  if (!fleets || !app) body = <p className="dim">Starting.</p>
  else if (page === 'settings') {
    body = app.app ? <Settings app={app} reload={reload} redact={redact} toggleRedact={toggleRedact} />
      : <div className="empty"><h2>Settings live in the desktop app.</h2><p>Start it with <code>inboxsitrep app</code>.</p></div>
  } else if (page === 'logs') body = <Logs app={app} />
  else if (page === 'all' && fleets.length > 1) {
    title = 'All workspaces'
    body = <Overview app={app} pick={name => { setFleet(name); setS(null); setMap(null); reload(); go('#/') }}
                     check={name => post('/api/app/check', name === '*' ? {} : { fleet: name }).then(() => {
                       setToast(name === '*' ? 'Checking every workspace, one after another.' : 'Checking ' + name + '.')
                       get('/api/app').then(setApp) })}
                     stop={() => post('/api/app/stop', {}).then(() => setToast('Stopping at the next step.'))} />
  }
  else if (!fleets.length) {
    title = 'Welcome'
    body = (
      <div className="empty">
        <h2>{busy ? 'Reading your workspace for the first time.' : 'Add a workspace to begin.'}</h2>
        <p>{busy ? state.step : 'Inbox Sitrep reads your sequencer and tells you what is wrong and what to do. It changes nothing unless you allow it.'}</p>
        {!busy && <p style={{ marginTop: 16, display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          <button onClick={() => go('#/settings')}>Add a workspace</button>
          {app.app && <button className="line" onClick={tryDemo}>Try it on demo data first</button>}</p>}
      </div>
    )
  } else if (!s) body = <p className="dim">Loading the workspace.</p>
  else if (page === 'incidents') body = <Incidents go={go} query={query} listed={s.kpis.listed} />
  else if (page === 'domains') body = <Domains go={go} first={s.first} last={s.last} reserve={s.kpis.reserve} source={s.source} />
  else if (page === 'campaigns') body = <Campaigns fleet={s.name} />
  else if (page === 'copy') body = <Copy fleet={s.name} />
  else if (page === 'map') body = <section className="sec"><header><h2>Every family, every day</h2></header>
    <div className="body"><Legend /><div style={{ marginTop: 10 }}><Map map={map} go={go} first={60} /></div></div></section>
  else if (page === 'analyst') body = <Analyst available={app.analyst} chat={chat} setChat={setChat} />
  else if (page === 'changes') body = <Changes fleet={s.name} toast={setToast} />
  else body = <Briefing s={s} go={go} />

  const strips = {}
  if (map) for (const f of map.families) for (const r of f.rows) strips[r.domain] = r.s
  const drawer = page === 'incidents' && arg ? <IncidentView id={arg} strips={strips} fleet={s && s.name} toast={setToast} />
    : page === 'domains' && arg ? <DomainView name={decodeURIComponent(arg)} /> : null
  const back = () => go('#/' + page + (query ? '?' + query : ''))

  const cond = s ? s.status : null
  const scoped = s && !['settings', 'all', 'logs'].includes(page)
  return (
    <div className={'shell cond-' + (cond || 'NONE')} key={redact ? 'r' : 'n'}>
    <div className="frame">
      <aside className="side">
        <div className="brand"><img src="/icon.svg" alt="" />Inbox Sitrep</div>
        {fleets && fleets.length > 1 && (
          <nav className="nav top">
            <a href="#/all" className={page === 'all' ? 'on' : ''}><span>All workspaces</span></a>
          </nav>
        )}
        {fleets && fleets.length > 1 && s && (
          <div className="fleet">
            <select value={s.name} aria-label="Workspace"
                    onChange={e => { setFleet(e.target.value); setS(null); setMap(null); reload(); if (page === 'all') go('#/') }}>
              {fleets.map(f => <option key={f.name} value={f.name}>{R(f.name)}</option>)}
            </select>
          </div>
        )}
        <nav className="nav">
          {NAV.map((n, i) => {
            if (!n) return <hr key={i} />
            const [p, label, k] = n
            const count = p === 'incidents' ? decide.length : p === 'copy' && s ? s.kpis.broken_copy : 0
            return (
              <a key={p} href={'#/' + p} className={page !== 'all' && (page || '') === p ? 'on' : ''} title={'Shortcut: ' + k}>
                <span>{label}</span>{count ? <span className="n">{count}</span> : null}
              </a>
            )
          })}
        </nav>
        <div className="watch">
          {busy ? (() => {
            const [share, left] = progress(state)
            return <div className="progress" title={state.step}>
              <div className="state"><span className="pip busy" />Checking {R(state.fleet)}</div>
              <div className="track"><span style={{ width: Math.round(100 * share) + '%' }} /></div>
              <div className="meta"><span>{state.total > 1 ? (state.done + 1) + ' of ' + state.total + ', ' : ''}{leftWords(left)}</span>
                <a href="#" onClick={e => { e.preventDefault(); post('/api/app/stop', {}).then(() => setToast('Stopping at the next step. The last complete check stays on screen.')) }}>Stop</a></div>
            </div>
          })() : <>
            <div className="state"><span className="pip" />
              {!app || !app.app ? 'Read-only view' : app.settings.fleets.length ? 'Watching' : 'No workspaces yet'}</div>
            <div className="step">
              {state && state.queued ? 'Queued.' : state && state.next ? 'Next check ' + when(state.next) + (state.next_report ? '. Report ' + when(state.next_report) : '') : ''}
            </div>
          </>}
          <div className="row">
            <div className="themes">
              {[['dark', 'Dark'], ['light', 'Light']].map(([t, label]) =>
                <button key={t} className={'line' + (theme === t ? ' on' : '')} onClick={() => setTheme(t)}>{label}</button>)}
            </div>
          </div>
        </div>
      </aside>
      <main className="main">
        <div className="head">
          <h1>{title}</h1>
          {scoped && <span className="where">{R(s.name)}</span>}
          {scoped && <span className={'prio ' + TONE[s.status]} title={s.top ? words(s.top) : 'Nothing needs a decision'}>{s.status}</span>}
          {s && scoped && <span className="when">{s.source === 'simulated' ? 'Demo data · ' : ''}data to {day(s.last)}{s.collected ? ' · checked ' + when(s.collected) : ''}
            {app && app.app && <button className="line tiny check" disabled={busy && state.fleet === s.name}
              onClick={() => post('/api/app/check', { fleet: s.name }).then(() => { setToast(busy ? 'Queued: ' + s.name + ' is checked after the current run.' : 'Checking ' + s.name + '.'); get('/api/app').then(setApp) })}>
              {busy && state.fleet === s.name ? 'Checking' : 'Check this workspace'}</button>}</span>}
          <span className="what">{ABOUT[page || ''] || ''}</span>
        </div>
        <div className="content">{body}</div>
      </main>
      {drawer && <Drawer close={back}>{drawer}</Drawer>}
      {toast && <div className="toast" onClick={() => setToast('')}>{toast}</div>}
    </div>
    </div>
  )
}
