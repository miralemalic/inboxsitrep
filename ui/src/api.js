// Talking to the inboxsitrep process, and the few formatting rules every
// page shares.

// The app opens its window with a token in the address; every call carries it.
const params = new URLSearchParams(location.search)
const token = params.get('t') || sessionStorage.getItem('t') || ''
if (params.get('t')) {
  sessionStorage.setItem('t', token)
  history.replaceState(null, '', location.pathname + location.hash)
}
let fleet = sessionStorage.getItem('fleet') || ''

export function setFleet(name) {
  fleet = name
  sessionStorage.setItem('fleet', name)
}

function url(path) {
  if (!fleet) return path
  return path + (path.includes('?') ? '&' : '?') + 'fleet=' + encodeURIComponent(fleet)
}

export function get(path) {
  return fetch(url(path), { headers: { 'X-Token': token } }).then(r => r.json())
}

export function post(path, body) {
  return fetch(url(path), {
    method: 'POST', headers: { 'X-Token': token, 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  }).then(r => r.json())
}

export function pct(v) {
  return v == null ? '–' : v.toFixed(v < 10 ? 1 : 0) + '%'
}

export function num(n) {
  return n == null ? '–' : Number(n).toLocaleString('en-US')
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

// "2026-10-23" -> "23 Oct"
export function day(s) {
  if (!s) return ''
  const [, m, d] = s.slice(0, 10).split('-')
  return parseInt(d, 10) + ' ' + MONTHS[parseInt(m, 10) - 1]
}

// "2026-11-01T17:40:12" -> "1 Nov, 17:40"
export function when(s) {
  if (!s) return ''
  return day(s) + ', ' + s.slice(11, 16)
}

export function ago(s) {
  if (!s) return ''
  const days = Math.round((Date.now() - Date.parse(s.slice(0, 10))) / 864e5)
  return days <= 0 ? 'today' : days === 1 ? '1 day' : days + ' days'
}

// Redact mode: domain, family and workspace names become bars, so a
// screen can be shared without naming anyone.
if (params.get('redact')) sessionStorage.setItem('redact', '1')
let redacting = sessionStorage.getItem('redact') === '1'
let names = []
export function setRedact(on) { redacting = on; sessionStorage.setItem('redact', on ? '1' : '') }
export function redacted() { return redacting }
export function setNames(list) { names = list.filter(Boolean) }
const DOMAIN = /\b[a-z0-9][a-z0-9-]*(\.[a-z0-9-]+)*\.(com|co|io|net|org|ai|us|biz|info|xyz|app|dev|club|group|agency|online|site|tech|pro|me|uk|de)\b/gi
const bar = m => '\u2588'.repeat(Math.max(4, Math.min(m.length, 18)))
export function R(text) {
  if (!redacting || !text) return text
  let out = String(text).replace(DOMAIN, bar).replace(/\b[a-z0-9]{3,}\*/gi, bar)
  for (const n of names) out = out.split(n).join(bar(n))
  return out
}

// Titles come with ISO dates in them; say them the way people do.
export function words(text) {
  return R((text || '').replace(/\b\d{4}-\d{2}-\d{2}\b/g, day))
}

// Incident serials, the way a desk files them.
export function serial(id) {
  return 'INC-' + String(id).padStart(4, '0')
}

// How the fleet stands overall: urgent while anything urgent is open.
export function condition(list) {
  const top = Math.max(0, ...list.map(i => i.severity || 0))
  return top >= 3 ? 'RED' : top === 2 ? 'AMBER' : 'GREEN'
}

// A domain's status in plain words, from its last few days.
export function standing(strip) {
  const s = strip || ''
  const last = s.slice(-1), three = s.slice(-3)
  if (last === 'l') return ['Blocklisted', 'p1', 'A blocklist names this domain; receivers that check it refuse its mail.']
  if (three.length === 3 && [...three].every(c => c === 'x')) return ['Refused', 'p1', 'Most of its mail has been refused for 3 days or more.']
  if (last === 'x') return ['Refused today', 'p2', 'Most of its mail was refused on the last day.']
  if (last === 'w') return ['Some refused', 'p2', 'Between 5% and 30% of its mail was refused on the last day.']
  if (s.slice(-2) === '..') return ['Not sending', 'p0', 'It sent nothing in the last two days.']
  if (/[xl]/.test(s.slice(-7, -1))) return ['Recovering', 'p3', 'Accepted again today, after refusals earlier this week.']
  return ['Healthy', 'ok', 'Its mail is being accepted.']
}

// Priority in words: urgent (fix today), important (this week), watch.
export function priority(n) {
  return n >= 3 ? ['Urgent', 'p1'] : n === 2 ? ['Important', 'p2'] : n === 0 ? ['Over', 'p0'] : ['Watch', 'p3']
}

export const PROVIDER = { google: 'Gmail', microsoft: 'Microsoft', apple: 'Apple', other: 'Others' }

// Each kind of incident in a few plain words.
const KINDS = {
  burned: 'Gmail block', rejecting: 'Gmail refusing', cascade: 'family going down', quiet_spam: 'Gmail spam folder',
  gmail_gap: 'Gmail behind Microsoft', warmup_drop: 'warmup score drop', throttle: 'Gmail slowing down',
  episode: 'Gmail episode', listed: 'blocklist', link_listed: 'listed link in copy', tenant_block: 'Microsoft 365 tenant block',
  ms_block: 'Microsoft refusing', receiver: 'receiver block', content: 'copy refused', complaints: 'complaints',
  apple: 'iCloud refusing', auth: 'authentication', dns: 'DNS', bad_list: 'bad lead list', mailboxes: 'disconnected mailboxes',
  runway: 'leads running out', bounce_guard: 'campaigns paused', stalled: 'stopped sending', settings: 'campaign settings',
}
export function kindName(k) { return KINDS[k] || k.replace(/_/g, ' ') }

export const STATE = {
  o: 'fine', w: 'some mail refused', x: 'most mail refused', l: 'on a blocklist', '.': 'no mail sent',
}

export function stateClass(c) {
  return 's-' + (c === '.' ? 'n' : c)
}
