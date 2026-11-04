// Just enough markdown for what the analyst writes: paragraphs, numbered
// and bulleted lists, **bold**.  Built as elements, never as raw HTML.
import { words } from './api'

function inline(s, key) {
  const out = []
  const parts = words(s).split(/\*\*(.+?)\*\*/g)
  parts.forEach((p, i) => out.push(i % 2 ? <b key={key + '-' + i}>{p}</b> : p))
  return out
}

export default function Text({ text }) {
  const blocks = []
  let list = null
  const flush = () => { if (list) { blocks.push(list); list = null } }
  ;(text || '').split('\n').forEach((raw, n) => {
    const line = raw.trim()
    const num = line.match(/^\d+[.)]\s+(.*)/)
    const dash = line.match(/^[-*•]\s+(.*)/)
    if (num || dash) {
      const kind = num ? 'ol' : 'ul'
      if (!list || list.kind !== kind) { flush(); list = { kind, items: [] } }
      list.items.push(inline((num || dash)[1], 'l' + n))
      return
    }
    flush()
    if (line) blocks.push({ kind: 'p', body: inline(line.replace(/^#+\s*/, ''), 'p' + n) })
  })
  flush()
  return blocks.map((b, i) => b.kind === 'p' ? <p key={i}>{b.body}</p>
    : b.kind === 'ol' ? <ol key={i}>{b.items.map((x, j) => <li key={j}>{x}</li>)}</ol>
      : <ul key={i}>{b.items.map((x, j) => <li key={j}>{x}</li>)}</ul>)
}

// The briefing has a shape, so it is set like a document: a slug line,
// the bottom line, numbered paragraphs, the watch line, the assessment.
const MON = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']

// Date-time group, the way a signal is stamped: 020813Z NOV 26.
export function dtg(iso) {
  if (!iso) return ''
  const d = new Date(iso.length <= 16 ? iso + ':00' : iso)
  const p = n => String(n).padStart(2, '0')
  return p(d.getUTCDate()) + p(d.getUTCHours()) + p(d.getUTCMinutes()) + 'Z ' + MON[d.getUTCMonth()] + ' ' +
    String(d.getUTCFullYear()).slice(2)
}

export function Briefing({ text, at, how, fleet, cond }) {
  const lines = (text || '').split('\n').map(l => l.trim()).filter(Boolean)
  if (lines[0] && /^((FLEET )?SITREP|Report[:,])/i.test(lines[0])) lines.shift()
  const pull = re => {
    const i = lines.findIndex(l => re.test(l))
    return i >= 0 ? lines.splice(i, 1)[0].replace(re, '').trim() : ''
  }
  const bluf = pull(/^(BLUF|Bottom line):\s*/i)
  const assess = pull(/^Assessment:\s*/i)
  return (
    <div className="doc">
      <div className="slug">
        <span><b>SITREP</b> {fleet}</span>
        <span>{dtg(at)}</span>
        {cond && <span className={'cond c-' + cond}>{cond === 'RED' ? 'URGENT' : cond === 'AMBER' ? 'NEEDS ATTENTION' : 'ALL CLEAR'}</span>}
        <span className="by">{how === 'analyst' ? 'WRITTEN BY THE ANALYST' : 'AUTOMATIC SUMMARY'}</span>
      </div>
      <div className="text">
        {bluf && <div className="bluf"><span className="tag">IN SHORT</span><div>{inline(bluf, 'b')}</div></div>}
        <Text text={lines.join('\n')} />
        {assess && <p className="assess"><span>{inline(assess, 'a')}</span></p>}
      </div>
    </div>
  )
}
