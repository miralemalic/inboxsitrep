// Ask the analyst.  It answers from the fleet's own tools, read-only, on
// the Claude login of whoever runs the app.
import { useState, useRef, useEffect } from 'react'
import Text from './Text'
import { post } from './api'

const SUGGEST = [
  'What should I fix first, and why?',
  'Which domain families are at risk next?',
  'Why are so many domains on blocklists?',
  'What changed since yesterday?',
]

export default function Analyst({ available, chat, setChat }) {
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const end = useRef(null)
  useEffect(() => { end.current && end.current.scrollIntoView({ behavior: 'smooth' }) }, [chat, busy])

  function ask(question) {
    if (!question.trim() || busy) return
    const history = chat.filter(m => m.a).map(m => [m.q, m.a])
    setChat([...chat, { q: question }])
    setQ('')
    setBusy(true)
    post('/api/ask', { question, history }).then(r => {
      setChat(c => c.map((m, i) => i === c.length - 1 ? { ...m, a: r.answer, err: r.error } : m))
      setBusy(false)
    }).catch(() => setBusy(false))
  }

  if (!available) return (
    <div className="empty"><h2>The analyst needs Claude Code.</h2>
      <p>Install Claude Code and log in with your Claude account; the analyst runs on that login.</p></div>
  )
  return (
    <div className="chat">
      {!chat.length && (
        <div>
          <p className="dim" style={{ maxWidth: 640 }}>The analyst reads your workspaces through Inbox Sitrep's own tools and answers
            like a duty officer: bottom line first, exact numbers, what it is sure of and what it isn't. It cannot change anything.</p>
          <div className="suggest">{SUGGEST.map(s => <button key={s} className="line" onClick={() => ask(s)}>{s}</button>)}</div>
        </div>
      )}
      {chat.map((m, n) => (
        <div key={n} style={{ display: 'contents' }}>
          <div className="q">{m.q}</div>
          {m.a && <div className="a"><Text text={m.a} /></div>}
          {m.err && <div className="a p1">{m.err}</div>}
        </div>
      ))}
      {busy && <div className="a dim"><span className="pip busy" style={{ display: 'inline-block', marginRight: 8 }} />
        Reading the numbers. Answers take 20 to 60 seconds.</div>}
      <div ref={end} />
      <div className="ask">
        <textarea rows={2} value={q} placeholder="Ask about a domain, a family, a campaign, a receiver, or what to fix first"
                  onChange={e => setQ(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(q) } }} />
        <button onClick={() => ask(q)} disabled={busy || !q.trim()}>Ask</button>
      </div>
    </div>
  )
}
