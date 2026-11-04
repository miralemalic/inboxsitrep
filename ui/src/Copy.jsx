// What is wrong with the copy, across every campaign, worst first: sentences
// recipients will see broken, then what spam filters hold against it.
import { useEffect, useState } from 'react'
import { get, num, R } from './api'

const KINDS = {
  run_on: ['Merge field run into the next word', 'broken', 'Recipients see "Acmefor the right number". Add the space in the template.'],
  unbalanced: ['Spintax or a condition that does not close', 'broken', 'Recipients can see raw template text. Close the block.'],
  unmapped: ['Merge field not on the leads', 'broken', 'The copy asks for a field the leads do not have, so it comes out empty. Fix the field name or the upload mapping.'],
  empty_field: ['Merge field empty with no fallback', 'broken', 'Some leads have no value and the copy has no {% if %} or fallback, so they get a hole in the sentence.'],
  links: ['Links in the first email', 'risky', 'Cold first emails do best with no links at all.'],
  shortener: ['Link shorteners', 'risky', 'Filters treat bit.ly and the like as spam.'],
  images: ['Images in the first email', 'risky', 'Images make a cold email look like a newsletter.'],
  tracking: ['Open tracking on', 'risky', 'A tracking pixel in every email. Turn it off in the campaign settings.'],
  words: ['Words spam filters score', 'risky', 'Rephrase them; the meaning rarely needs the exact words.'],
  subject: ['Subject shouts', 'risky', 'Capitals and exclamation marks in a subject read as promotion.'],
  identical: ['One version for everyone', 'risky', 'The same text to every recipient. Add variations or spintax.'],
  long: ['Long first email', 'risky', 'Over 125 words. Shorter first emails get more replies.'],
}

export default function Copy({ fleet }) {
  const [rows, setRows] = useState(null)
  const [active, setActive] = useState(true)
  const [open, setOpen] = useState(null)
  useEffect(() => { setRows(null); get('/api/campaigns').then(setRows) }, [fleet])
  if (!rows) return <p className="dim">Reading.</p>
  const shown = rows.filter(r => (!active || r.status === 'ACTIVE'))
  const checked = shown.filter(r => r.copy)
  if (!checked.length) return <div className="empty"><h2>No copy read yet.</h2>
    <p>Copy is read on every check. Press Check this workspace at the top.</p></div>
  const by = {}
  for (const r of checked) for (const i of r.copy.issues) (by[i.kind] ||= []).push({ ...i, campaign: r.name })
  const broken = checked.filter(r => r.copy.broken).length
  const risky = checked.filter(r => !r.copy.broken && r.copy.risky).length
  return (
    <section>
      <div className="toolbar">
        <span><b>{num(checked.length)}</b> campaigns read: <span className={broken ? 'p1' : ''}>{broken} with broken sentences</span>,
          {' '}<span className={risky ? 'p2' : ''}>{risky} risky</span>, {checked.length - broken - risky} clean.</span>
        <label className="toggle"><input type="checkbox" checked={active} onChange={e => setActive(e.target.checked)} /> Active only</label>
      </div>
      {!Object.keys(by).length && <div className="empty"><h2>The copy is clean.</h2>
        <p>No broken sentences, links, tracking or filter words in {active ? 'the active' : 'any'} campaigns.</p></div>}
      <ul className="rows">
        {Object.keys(KINDS).filter(k => by[k]).map(k => {
          const [label, level, fix] = KINDS[k]
          const camps = [...new Set(by[k].map(i => i.campaign))]
          return [
            <li key={k} onClick={() => setOpen(open === k ? null : k)}>
              <span className={'prio ' + (level === 'broken' ? 'p1' : 'p2')}>{level === 'broken' ? 'Broken' : 'Risky'}</span>
              <div><div className="t">{label}</div>
                <div className="m">{fix} In {camps.length} campaign{camps.length > 1 ? 's' : ''}: {camps.slice(0, 3).map(c => R(c)).join(', ')}{camps.length > 3 ? ' and ' + (camps.length - 3) + ' more' : ''}.</div></div>
              <span className="age">{num(by[k].length)} found</span>
            </li>,
            open === k && <li key={k + '-x'} className="detail" style={{ cursor: 'default' }}><span />
              <ul className="small">{by[k].slice(0, 60).map((i, n) => (
                <li key={n}><b>{R(i.campaign)}</b> <span className="dim">{i.where}:</span> {i.what}
                  {i.example && <div className="mono faint">{R(i.example)}</div>}</li>))}</ul><span /></li>]
        })}
      </ul>
    </section>
  )
}
