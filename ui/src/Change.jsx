// One change, reviewed before it is made: a recommended step, or what you
// asked for in your own words.  Nothing reaches the sequencer until Apply is
// pressed on this preview; big changes want the workspace name typed first.
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { post, num, pct } from './api'
import { verdictTone } from './Domains'

const MAILBOX = ['pause', 'set_limit']

export default function Change({ fleet, incident, step, request, planned, close, done }) {
  const [act, setAct] = useState(null)
  const [err, setErr] = useState('')
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  const [warm, setWarm] = useState(false)
  const [words, setWords] = useState(request || '')
  const [asked, setAsked] = useState(!!request)
  const [thinking, setThinking] = useState(false)
  const [healthyOk, setHealthyOk] = useState(false)

  function show(p) {
    setAct(null)
    setErr('')
    p.then(r => { setThinking(false); r.error ? setErr(r.error) : setAct(r) })
      .catch(() => { setThinking(false); setErr('The app is not answering.') })
  }

  // In your words: the analyst turns it into a change, which is checked
  // against what Inbox Sitrep may do and previewed like any other.
  function say(text) {
    if (!text.trim()) return
    setAsked(true)
    setThinking(true)
    show(post('/api/app/instruct', { fleet, incident, step, text }))
  }

  useEffect(() => {
    if (request) say(request)
    else show(post('/api/app/preview', { fleet, incident, step, options: warm ? { warmup: 'off' } : {} }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fleet, incident, step, warm])

  function allow() {
    post('/api/app/settings', { fleets: [{ name: fleet, writes: true }] })
      .then(r => r.error ? setErr(r.error) : setAct({ ...act, allowed: true }))
  }

  function apply() {
    setBusy(true)
    post('/api/app/apply', { fleet, id: act.id, confirm: typed, healthy_ok: healthyOk }).then(r => {
      setBusy(false)
      if (r.error) setErr(r.error)
      else { done(r.checked ? r.status + '. ' + r.checked[0].toUpperCase() + r.checked.slice(1) : r.status); close() }
    })
  }

  const canWarm = !asked && act && !act.manual && MAILBOX.includes(act.op)

  // Drawn on the page itself, not inside the side panel, so nothing clips it.
  return createPortal(
    <div className="modal" onClick={e => { if (e.target === e.currentTarget) close() }}>
      <div className="box">
        <h2>{act && act.manual ? 'A step for you to take' : 'Review the change'}</h2>
        <p className="dim small">{fleet}</p>
        {planned && !asked && <div className="planned">
          <div className="small dim">Recommended</div>
          <div><b>{planned.what}</b></div>
          {planned.why && <div className="small dim">{planned.why}</div>}
        </div>}
        {!act && !err && <p className="dim" style={{ marginTop: 14 }}>
          {thinking ? 'Turning your request into a change, then loading the current settings.'
            : 'Loading the current mailbox settings, so you see exactly what would change. Large workspaces take a minute or two.'}</p>}
        {err && <p className="note" style={{ marginTop: 14 }}>{err}</p>}
        {act && act.understood && <p style={{ marginTop: 14 }}><span className="dim">Understood: </span>{act.understood}</p>}
        {act && act.manual && <p style={{ marginTop: 14 }}>{act.manual}</p>}
        {act && !act.manual && <>
          <ul>{act.lines.map((l, i) => <li key={i}>{l}</li>)}</ul>
          {act.verdicts && act.verdicts.length > 0 && <div className="guard">
            <div className="small dim">How these domains have done over the last 14 days:</div>
            <table><tbody>{act.verdicts.slice(0, 12).map(v => (
              <tr key={v.domain}>
                <td className="mono small">{v.domain}</td>
                <td><span className={'verdict ' + verdictTone(v.verdict)}>{v.label}</span></td>
                <td className="small dim">{v.sent >= 500 ? ((v.per_1000 ?? 'unknown') + ' positives per 1,000 (workspace ' + v.fleet_per_1000 + '), '
                  + pct(v.bounce_rate) + ' bounced') : num(v.sent) + ' sent: too few to judge'}</td>
              </tr>))}</tbody></table>
            {act.verdicts.length > 12 && <div className="small dim">and {act.verdicts.length - 12} more</div>}
          </div>}
          {act.healthy && act.healthy.length > 0 && <div className="note" style={{ marginTop: 10 }}>
            {act.healthy.length === 1 ? act.healthy[0] + ' is' : act.healthy.length + ' of these domains are'}
            {act.protected && act.protected.length ? ' protected in your rules or doing well.' : ' doing well.'}
            Changing {act.healthy.length === 1 ? 'it' : 'them'} costs replies.
            <label className="toggle" style={{ marginTop: 8 }}>
              <input type="checkbox" checked={healthyOk} onChange={e => setHealthyOk(e.target.checked)} />
              I know; change {act.healthy.length === 1 ? 'it' : 'them'} anyway</label>
          </div>}
          {canWarm && <label className="toggle" style={{ marginTop: 6 }}>
            <input type="checkbox" checked={warm} onChange={e => setWarm(e.target.checked)} />
            Pause their warmup too</label>}
          {act.steps.length > 0 && <div className="calls">
            {act.steps.length} request{act.steps.length > 1 ? 's' : ''} to the sequencer. Undo restores exactly what is there now:
            {act.undo.slice(0, 4).map((u, i) => <div key={i}>{u.what}</div>)}
          </div>}
        </>}

        {(act || err || asked) && <div className="say">
          <label className="small dim">Want it done differently? Say it in your own words.</label>
          <div className="row">
            <input value={words} onChange={e => setWords(e.target.value)} onKeyDown={e => e.key === 'Enter' && say(words)}
                   placeholder="e.g. pause these domains and turn off their warmup too" />
            <button className="line" disabled={!words.trim() || thinking} onClick={() => say(words)}>
              {thinking ? 'Working' : asked ? 'Ask again' : 'Change it'}</button>
          </div>
        </div>}

        {act && !act.manual && <>
          {!act.allowed && <div className="note" style={{ marginTop: 14 }}>
            Changes are off for {fleet}. Allow them to apply this one. Each change still waits for your Apply, and keeps an Undo.
            <div style={{ marginTop: 10 }}><button className="line" onClick={allow}>Allow changes for {fleet}</button></div>
          </div>}
          {act.allowed && act.big && <p style={{ marginTop: 14 }}>This touches {Math.round(100 * act.share)}% of the workspace's mailboxes.
            Type <b className="mono">{fleet}</b> to confirm.</p>}
          <div className="go">
            {act.big && act.allowed && <input value={typed} onChange={e => setTyped(e.target.value)} placeholder={fleet} />}
            <button onClick={apply} disabled={busy || !act.allowed || (act.big && typed.trim() !== fleet)
              || (act.healthy && act.healthy.length > 0 && !healthyOk)}>
              {busy ? 'Applying' : 'Apply the change'}</button>
            <button className="line" onClick={close}>Cancel</button>
          </div>
        </>}
        {(err || (act && act.manual)) && <div className="go"><button className="line" onClick={close}>Close</button></div>}
      </div>
    </div>, document.body)
}
