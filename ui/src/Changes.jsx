// Every change made to the workspace from this app, newest first, each
// with its exact undo; and a box to ask for a change in your own words.
import { useEffect, useState } from 'react'
import { get, post, when, R } from './api'
import Change from './Change'

function Ask({ fleet, toast, reload }) {
  const [words, setWords] = useState('')
  const [open, setOpen] = useState(null)
  return (
    <section className="sec">
      <header><h2>Ask for a change</h2><span className="r">You see exactly what it will do before anything changes</span></header>
      <div className="body say">
        <div className="row">
          <input value={words} onChange={e => setWords(e.target.value)} onKeyDown={e => e.key === 'Enter' && words.trim() && setOpen(words)}
                 placeholder="e.g. send at most 10 a day from the growthmail domains and pause their warmup" />
          <button disabled={!words.trim()} onClick={() => setOpen(words)}>Preview</button>
        </div>
        <p className="faint small">Pause domains, set a daily limit, pause warmup, hold a receiver, stop mailing iCloud, or turn off a campaign setting. Name domains or families (growthmail*).</p>
      </div>
      {open && <Change fleet={fleet} request={open} close={() => setOpen(null)}
                       done={status => { toast('Change ' + status + '. Undo is below.'); setWords(''); reload() }} />}
    </section>
  )
}

export default function Changes({ fleet, toast }) {
  const [list, setList] = useState(null)
  const [busy, setBusy] = useState('')
  const load = () => get('/api/changes').then(setList)
  useEffect(() => { load() }, [fleet])

  function undo(c) {
    setBusy(c.id)
    post('/api/app/undo', { fleet, id: c.id }).then(r => {
      setBusy('')
      toast(r.error ? r.error : 'Undone: ' + c.title)
      load()
    })
  }

  if (!list) return <p className="dim">Reading.</p>
  const ask = <Ask fleet={fleet} toast={toast} reload={load} />
  if (!list.length) return (
    <>{ask}
    <div className="empty"><h2>No changes yet.</h2>
      <p>Ask for one above, or open an incident and press Review on a recommended action. Every change lands here with its undo.</p></div></>
  )
  return (
    <>{ask}
    <section className="sec">
      <header><h2>Changes made from this app</h2><span className="r">{list.length}</span></header>
      <ul className="rows">
        {list.map(c => (
          <li key={c.id} style={{ cursor: 'default' }}>
            <span className={'prio ' + (c.status === 'applied' ? 'p2' : 'p0')}>{c.status === 'applied' ? 'ON' : c.status === 'undone' ? 'OFF' : '!'}</span>
            <div>
              <div className="t">{R(c.title)}</div>
              <div className="m">{when(c.at)}{c.incident ? ', from incident ' + c.incident : ''}. {c.status}{c.checked ? '; read back: ' + c.checked : ''}.</div>
              {c.lines.slice(1, 4).map((l, i) => <div key={i} className="m">{R(l)}</div>)}
            </div>
            {c.status === 'applied' && c.undo.length > 0
              ? <button className="line" disabled={busy === c.id} onClick={() => undo(c)}>{busy === c.id ? 'Undoing' : 'Undo'}</button>
              : <span />}
          </li>
        ))}
      </ul>
    </section></>
  )
}
