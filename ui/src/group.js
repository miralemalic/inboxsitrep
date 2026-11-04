// Fold incidents the way a person reads them: forty blocklisted domains are
// one problem, eight dead domains of one family are one line.

const ORDER = { tenant_block: 0, runway: 1, link_listed: 2, bounce_guard: 3, listed: 4, cascade: 5,
  mailboxes: 6, receiver: 7, burned: 8, ms_block: 9, auth: 10, dns: 11 }

const DEAD = {
  burned: n => `Gmail has stopped taking mail from ${n} domains`,
  rejecting: n => `Gmail is refusing mail from ${n} domains`,
  warmup_drop: n => `Google warmup scores fell on ${n} domains`,
  dns: n => `${n} DNS problems across the fleet`,
}
// What to do about the whole group, not just its first incident.
const GROUP_PLAN = {
  burned: 'stop sending to Gmail from them; keep 10 probe sends a day',
  rejecting: 'send less to Gmail from them and watch for 2-4 days',
  warmup_drop: 'send their Gmail leads from healthier domains',
  dns: 'fix the records; open it for each domain',
}

// `listed`: the fleet's count of listed domains, so this line and the key
// numbers never disagree.
export function fold(list, listed) {
  const out = [], by = {}
  // A family going down one domain at a time is the cascade's line; Gmail
  // slowing a domain is old news once it refuses it.
  const cascade = new Set(list.filter(i => i.kind === 'cascade').map(i => i.target))
  const refused = new Set(list.filter(i => i.kind === 'rejecting' || i.kind === 'burned').map(i => i.target))
  list = list.filter(i => !((i.kind === 'burned' || i.kind === 'rejecting') && cascade.has(i.family))
    && !(i.kind === 'throttle' && refused.has(i.target)))
  for (const i of list) (by[i.kind] ||= []).push(i)
  for (const [kind, g] of Object.entries(by)) {
    if (kind === 'listed' && g.length > 2) {
      const doms = listed ?? g.reduce((n, i) => n + (i.size || 1), 0)
      const fams = g.filter(i => i.scope === 'family').length
      out.push({ id: g[0].id, kind, group: g, severity: 2, opened: g.map(i => i.opened).sort()[0],
        updated: g.map(i => i.updated).sort().pop(), status: 'open', size: doms,
        title: `${doms} domains on blocklists` + (fams ? `, across ${fams} ${fams === 1 ? 'family' : 'families'}` : ''),
        plan: 'request removal and keep sending the ones still delivering; pause the ones receivers refuse' })
    } else if (DEAD[kind] && g.length > 3) {
      out.push({ id: g[0].id, kind, group: g, severity: Math.max(...g.map(i => i.severity)),
        opened: g.map(i => i.opened).sort()[0], updated: g.map(i => i.updated).sort().pop(),
        status: 'open', size: g.length, title: DEAD[kind](g.length), plan: GROUP_PLAN[kind] })
    } else out.push(...g)
  }
  return out.sort((a, b) => b.severity - a.severity || (ORDER[a.kind] ?? 30) - (ORDER[b.kind] ?? 30)
    || b.size - a.size)
}
