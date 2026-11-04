# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# The dashboard: a small JSON API over one or more fleet databases, and the
# built React app in static/.  Standard library only.
#
# Approving a plan works on simulated fleets only.  On a real fleet the
# dashboard is read-only: nothing here can change a workspace.  The one
# thing it accepts from outside is a sequencer's webhook, and only on a
# path carrying the shared secret; those events are only stored and shown.

import os
import re
import json
import hmac
import mimetypes
import collections
import http.server
import urllib.parse

from . import actions, detect, incidents, report, brief, live, checks, verdicts
from . import data as store
from .data import SENT, REPLIES, POSITIVES, BOUNCES, SENDER


STATIC = os.path.join(os.path.dirname(__file__), 'static')
PROVIDERS = ('google', 'microsoft', 'apple', 'other')
# One letter per domain per day for the fleet map, worst wins.
#   .  nothing sent       o  fine
#   w  some refused (5-30%) or the warmup score is low
#   x  most refused: Gmail's block, Microsoft's tenant block
#   l  a blocklist names it
BLOCKS = ('tenant_block', 'microsoft_block', 'gmail_reputation')


def pct(n, d):
    return round(100.0 * n / d, 2) if d else None


def cell(data, dom, day):
    sent = refused = 0
    for p in PROVIDERS:
        r = data.row(dom, p, day)
        sent += r[SENT]
        refused += r[SENDER]
    kinds = data.kinds.get((dom, day)) or {}
    listed = kinds.get('spamhaus', 0) + kinds.get('surbl', 0)
    blocked = sum(kinds.get(k, 0) for k in BLOCKS)
    if listed >= 3 or data.listed_on(dom, day):
        return 'l'
    if blocked and not sent:
        # Blocked at the source: the tenant never sent, the bounces say so.
        return 'x'
    if not sent:
        return '.'
    g = data.row(dom, 'google', day)
    if refused / sent >= .3 or (g[SENT] >= 8 and g[SENDER] / g[SENT] >= .3):
        return 'x'
    h = data.history.get(dom, {}).get(day)
    if refused / sent >= .05 or (h and h[0] is not None and h[0] < 50):
        return 'w'
    return 'o'


def fleet_map(data, fams):
    groups = collections.defaultdict(list)
    for dom in data.domains():
        groups[fams.get(dom) or ''].append(dom)
    out = []
    for name in sorted(groups, key=lambda n: (n == '', -len(groups[n]), n)):
        rows = [{'domain': d, 's': ''.join(cell(data, d, day) for day in data.days)}
                for d in sorted(groups[name])]
        out.append({'family': name, 'rows': rows})
    return {'days': data.days, 'families': out}


def totals(data, doms, days):
    out = {}
    for p in PROVIDERS:
        t = data.fleet(p, days, doms)
        out[p] = {'sent': t[SENT], 'refused': pct(t[SENDER], t[SENT]),
                  'replied': pct(t[REPLIES], t[SENT] - t[BOUNCES]),
                  'positives': t[POSITIVES]}
    return out


def pick_days(data, query, default=7):
    # The days a range covers: the last 7, 14 or 30 calendar days up to the
    # latest day collected, or from/to dates.
    import datetime
    last = data.days[-1]
    q = lambda k: (query.get(k) or [''])[0]
    if q('from') or q('to'):
        lo, hi = q('from') or data.days[0], q('to') or last
    else:
        try:
            n = max(1, int(q('range') or default))
        except ValueError:
            n = default
        lo, hi = (datetime.date.fromisoformat(last) - datetime.timedelta(days=n - 1)).isoformat(), last
    return [d for d in data.days if lo <= d <= hi] or data.days[-1:]


def overview(fleets):
    # Every workspace side by side: what needs a decision, and the week.
    import datetime
    out = []
    for f in fleets:
        f.fresh()
        d = f.data
        if not d.days:
            continue
        week = range_stats(d, pick_days(d, {}, 7))
        decide = [i for i in f.incidents if i['status'] == 'open' and i['kind'] in brief.DECIDE]
        worst = sorted(decide, key=lambda i: -brief.severity(i))
        # Status, count and first line as the SITREP has them, so the app
        # and the message never disagree.
        from . import reports
        new, old = reports.decisions(d, f.incidents, f.name, (), datetime.date.today())
        left, pace = checks.leads_left(d, d.days[-1])
        out.append({'name': f.name, 'source': f.meta['source'], 'collected': f.meta['collected'],
                    'last_day': d.days[-1],
                    'quiet': (datetime.date.today() - datetime.date.fromisoformat(d.days[-1])).days
                    if f.meta['source'] != 'simulated' else 0,
                    'domains': len(d.domains()), 'mailboxes': sum(v or 0 for v in d.mailboxes.values()),
                    'down': sum((a or {}).get('down', 0) for a in d.accounts.values()),
                    'decide': [short(i, f.fams, f.planned) for i in decide],
                    'worst': worst[0]['title'] if worst else None,
                    'status': reports.status(f.incidents), 'decisions': len(new) + len(old),
                    'reserve': checks.reserve(d, f.incidents, d.days[-1]),
                    'top': (new + old)[0].split('  → ')[0] if new or old else None,
                    'listed': len(brief.listed_domains(d, f.incidents)),
                    'sent': week['sent'], 'per_1000': week['per_1000'],
                    'sender_bounces': week['sender_bounces'], 'recipient_bounces': week['recipient_bounces'],
                    'replied': {p: (week['providers'].get(p) or {}).get('replied') for p in ('google', 'microsoft', 'other')},
                    'runway': round(sum(left.values()) / pace, 1) if left and pace else None})
    return out


def range_stats(data, days):
    # Sends, bounces by whose fault, replies by provider, positives.
    t = [sum(data.fleet(p, days)[k] for p in PROVIDERS) for k in range(5)]
    return {'from': days[0], 'to': days[-1], 'sent': t[SENT],
            'sender_bounces': pct(t[SENDER], t[SENT]),
            'recipient_bounces': pct(t[BOUNCES] - t[SENDER], t[SENT]),
            'positives': t[POSITIVES],
            'per_1000': round(1000 * t[POSITIVES] / t[SENT], 1) if t[SENT] else None,
            'providers': totals(data, None, days),
            'infra': by_infra(data, days)}


INFRA = {'google': 'Google Workspace', 'microsoft': 'Microsoft 365', 'other': 'SMTP and others'}


def by_infra(data, days):
    # The same numbers by what the mailboxes are: Google Workspace,
    # Microsoft 365 or plain SMTP.  A fleet that mixes them learns here which
    # one earns its keep.
    out = {}
    for host, name in INFRA.items():
        doms = [d for d in data.domains() if (data.host.get(d) or 'other') == host]
        t = [sum(data.fleet(p, days, doms)[k] for p in PROVIDERS) for k in range(5)] if doms else [0] * 5
        if not t[SENT]:
            continue
        out[host] = {'name': name, 'domains': len(doms),
                     'mailboxes': sum(data.mailboxes.get(d) or 0 for d in doms), 'sent': t[SENT],
                     'replied': pct(t[REPLIES], t[SENT] - t[BOUNCES]),
                     'per_1000': round(1000 * t[POSITIVES] / t[SENT], 1),
                     'sender_bounces': pct(t[SENDER], t[SENT]),
                     'recipient_bounces': pct(t[BOUNCES] - t[SENDER], t[SENT])}
    return out


def today(app, fleet):
    if not app or 'settings' not in app:
        raise ValueError('today\'s numbers come live from the sequencer; open the desktop app')
    return today_numbers(fleet_cfg(app, fleet.name))


def today_numbers(cfg):
    # Today so far, live from PlusVibe: the collected days end yesterday.
    import datetime
    from . import watch
    if cfg.get('sequencer') != 'plusvibe':
        raise ValueError('today\'s numbers come live from PlusVibe; not available for %s yet' % cfg.get('sequencer'))
    api = watch.client(cfg)
    day = datetime.date.today().isoformat()
    get = lambda **k: (api.get('/account/email-stats', start_date=day, end_date=day, **k).get('header') or {})
    h = get()
    sent = h.get('total_sent_count') or 0
    provs = {}
    for name, code in (('google', 'GOOGLE_WORKSPACE'), ('microsoft', 'MICROSOFT365'), ('other', 'REGULAR_ACCOUNT')):
        p = get(recp_provider=code)
        n = p.get('total_sent_count') or 0
        provs[name] = {'sent': n, 'positives': p.get('total_pos_reply_count') or 0,
                       'refused': pct(p.get('sender_bounce_count') or 0, n),
                       'replied': pct(p.get('total_reply_count') or 0, n - (p.get('total_bounce_count') or 0))}
    return {'from': day, 'to': day, 'live': True, 'sent': sent,
            'sender_bounces': pct(h.get('sender_bounce_count') or 0, sent),
            'recipient_bounces': pct(h.get('recipient_bounce_count') or 0, sent),
            'positives': h.get('total_pos_reply_count') or 0,
            'replied': pct(h.get('total_reply_count') or 0, sent - (h.get('total_bounce_count') or 0)),
            'per_1000': round(1000 * (h.get('total_pos_reply_count') or 0) / sent, 1) if sent else None,
            'providers': provs}


def fleet_rows(data, fams, days):
    rows = []
    for dom in data.domains():
        dns = data.dns.get(dom) or {}
        row = {'domain': dom, 'family': fams.get(dom), 'mailboxes': data.mailboxes.get(dom),
               'host': data.host.get(dom), 'health': data.health.get(dom),
               'listed': data.listed_on(dom, days[-1]),
               'auth': data.auth_missing(dom, days[-1]),
               'down': (data.accounts.get(dom) or {}).get('down', 0),
               'dns': dns.get('issues'),
               'strip': ''.join(cell(data, dom, day) for day in days)}
        for p in ('google', 'microsoft'):
            t = data.total(dom, p, days)
            row[p] = {'sent': t[SENT], 'refused': pct(t[SENDER], t[SENT]),
                      'replied': pct(t[REPLIES], t[SENT] - t[BOUNCES])}
        rows.append(row)
    return rows


def daily(data, doms, provs=PROVIDERS):
    out = []
    for day in data.days:
        point = {'day': day}
        for p in provs:
            t = data.fleet(p, [day], doms)
            point[p] = {'sent': t[SENT], 'refused': pct(t[SENDER], t[SENT]),
                        'replied': pct(t[REPLIES], t[SENT] - t[BOUNCES])}
        out.append(point)
    return out


def targets(inc, fams):
    ev = inc['evidence']
    if inc['scope'] == 'domain':
        return [inc['target']]
    if isinstance(ev.get('domains'), list) and ev['domains']:
        return ev['domains']
    if inc['scope'] == 'family':
        return ev.get('members') or sorted(d for d, n in fams.items() if n == inc['target'])
    return None


def short(inc, fams=None, planned=None):
    keep = ('id', 'kind', 'scope', 'target', 'opened', 'updated', 'status',
            'confidence', 'title')
    out = {k: inc[k] for k in keep}
    if inc['scope'] == 'domain' and fams:
        out['family'] = fams.get(inc['target'])
    out['decide'] = inc['kind'] in brief.DECIDE
    out['severity'] = brief.severity(inc)
    out['plan'] = brief.plan_line(inc, planned)
    if out['plan'] == 'already planned':
        out['plan'] = ''
    doms = inc['evidence'].get('domains') or inc['evidence'].get('members')
    out['size'] = len(doms) if isinstance(doms, list) else doms or 1
    return out


class Fleet:
    # One fleet database, reloaded when something else writes to it (the
    # watch and live commands do).

    def __init__(self, path):
        self.path = path
        self.stamp = None
        self.fresh()

    def fresh(self):
        stamp = os.stat(self.path).st_mtime
        if stamp != self.stamp:
            self.stamp = stamp
            self.data = store.load(self.path)
            self.fams = detect.families(self.data.domains())
            self.incidents = incidents.load(self.path)
            self.planned = brief.planned_by(self.incidents)
            self.meta = {k: store.get_meta(self.path, k) for k in
                         ('source', 'workspace', 'collected', 'approved', 'live', 'briefing')}
            self.approved = self.meta['approved'] or []
            self.map = None
        return self

    @property
    def name(self):
        return self.meta['workspace'] or os.path.splitext(os.path.basename(self.path))[0]

    def stats(self, days):
        return range_stats(self.data, days)

    def summary(self):
        d = self.data
        found = self.incidents
        open_ = [short(i, self.fams, self.planned) for i in found if i['status'] == 'open']
        status = collections.Counter(i['status'] for i in found)
        last = d.days[-1]
        listed = brief.listed_domains(d, found)
        states = collections.Counter(cell(d, x, last) for x in d.domains())
        week = totals(d, None, d.days[-7:])
        sent = sum(t['sent'] for t in week.values())
        refused = sum(t['sent'] * (t['refused'] or 0) / 100 for t in week.values())
        left, pace = checks.leads_left(d, last)
        # Last 7 days across every provider: bounces split by whose fault
        # (the sender: blocks, blocklists; the address: no such mailbox,
        # full inbox), and the replies that matter.
        all7 = [sum(d.fleet(p, d.days[-7:])[k] for p in PROVIDERS) for k in range(5)]
        kpis = {'listed': len(listed), 'sent': sent,
                'refused': round(100 * refused / sent, 2) if sent else None,
                'sender_bounces': pct(all7[SENDER], all7[SENT]),
                'recipient_bounces': pct(all7[BOUNCES] - all7[SENDER], all7[SENT]),
                'positives': all7[POSITIVES],
                'per_1000': round(1000 * all7[POSITIVES] / all7[SENT], 1) if all7[SENT] else None,
                'down': sum((a or {}).get('down', 0) for a in d.accounts.values()),
                'left': sum(left.values()) if left else None,
                'runway': round(sum(left.values()) / pace, 1) if left and pace else None,
                'dry': next((f['numbers'].get('dry') or [] for f in checks.runway(d, last)), []),
                'states': {k: states.get(k, 0) for k in 'owxl.'}}
        # Status and bottom line as the SITREP has them.
        import datetime
        from . import reports
        new, old = reports.decisions(d, found, self.name, (), datetime.date.today())
        kpis['reserve'] = checks.reserve(d, found, last)
        kpis['broken_copy'] = sum(1 for c in d.campaigns.values() if c.get('status') == 'ACTIVE'
                                  and (c.get('copy') or {}).get('broken'))
        return {'name': self.name, 'source': self.meta['source'] or 'unknown', 'kpis': kpis,
                'status': reports.status(found), 'top': (new + old)[0].split('  → ')[0] if new or old else None,
                'first': d.days[0], 'last': d.days[-1], 'domains': len(d.domains()),
                'mailboxes': sum(v or 0 for v in d.mailboxes.values()),
                'collected': self.meta['collected'], 'live': self.meta['live'],
                'briefing': self.meta['briefing'],
                'providers': totals(d, None, d.days[-7:]),
                'series': daily(d, None),
                'incidents': dict(status),
                'decide': [i for i in open_ if i['decide']],
                'watch': [i for i in open_ if not i['decide']],
                'over': [short(i) for i in found if i['status'] != 'open'
                         and incidents.gap(i['updated'], d.days[-1]) <= 3],
                'brief': brief.brief(d, found, self.meta['workspace']),
                'writable': self.meta['source'] == 'simulated'}

    def incident(self, n):
        inc = next((i for i in self.incidents if i['id'] == n), None)
        if inc is None:
            return None
        doms = targets(inc, self.fams)
        want = report.RELEVANT.get(inc['kind'])
        samples = []
        for (dom, kind), text in sorted(self.data.samples.items()):
            if (doms is None or dom in doms) and (want is None or kind in want):
                samples.append({'domain': dom, 'kind': kind, 'text': text})
            if len(samples) >= 6:
                break
        return dict(inc, report=report.plain(inc, self.data),
                    why=report.WHY.get(inc['kind'], ''),
                    series=daily(self.data, doms), domains=doms or [],
                    samples=samples, approved=n in self.approved,
                    covered=brief.covered(inc, self.planned),
                    applied=actions.applied(inc['plan'], actions.history(self.path)))

    def domain(self, dom):
        d = self.data
        if dom not in d.domains():
            return None
        kinds = []
        for day in d.days:
            c = d.kinds.get((dom, day))
            if c:
                kinds.append({'day': day, 'kinds': dict(c)})
        touching = [short(i, self.fams, self.planned) for i in self.incidents
                    if dom in (targets(i, self.fams) or ())]
        by = {}
        for p in PROVIDERS:
            row = {}
            for label, days in (('week', d.days[-7:]), ('all', d.days)):
                t = d.total(dom, p, days)
                row[label] = {'sent': t[SENT], 'refused': t[SENDER], 'bounced': t[BOUNCES],
                              'replies': t[REPLIES], 'positives': t[POSITIVES],
                              'refused_pct': pct(t[SENDER], t[SENT]),
                              'replied_pct': pct(t[REPLIES], t[SENT] - t[BOUNCES])}
            if row['all']['sent']:
                by[p] = row
        return {'domain': dom, 'family': self.fams.get(dom), 'host': d.host.get(dom),
                'mailboxes': d.mailboxes.get(dom), 'accounts': d.accounts.get(dom),
                'health': d.health.get(dom),
                'history': sorted(d.history.get(dom, {}).items()),
                'listed': d.listed.get(dom), 'dns': d.dns.get(dom),
                'series': daily(d, [dom]), 'kinds': kinds,
                'samples': [{'kind': k, 'text': t} for (x, k), t in sorted(d.samples.items())
                            if x == dom],
                'strip': ''.join(cell(d, dom, day) for day in d.days),
                'days': d.days, 'by_provider': by,
                'verdict': verdicts.domains(d, pick_days(d, {}, 14)).get(dom),
                'buckets': [[label, side, verdicts.buckets(d, dom, pick_days(d, {}, 14)).get(key, 0)]
                            for key, label, side, _ in verdicts.BUCKETS],
                'incidents': touching}

    def fleet_map(self):
        if self.map is None:
            self.map = fleet_map(self.data, self.fams)
        return self.map

    def approve(self, n):
        if self.meta['source'] != 'simulated':
            return {'ok': False, 'why': 'This fleet is real.  The dashboard '
                    'never changes a real workspace.'}
        if n not in self.approved:
            self.approved.append(n)
            db = store.connect(self.path)
            with db:
                db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',
                           ('approved', json.dumps(self.approved)))
            db.close()
        return {'ok': True}


def handler(fleets, secret=None, app=None):
    # With `app` (the desktop app), every API call must carry the token the
    # app opened its window with, and the Host must be the loopback address
    # it listens on: no other program, and no web page that rebinds a name
    # to 127.0.0.1, can read the fleet or change a setting.

    def pick(query):
        want = query.get('fleet', [None])[0]
        for f in fleets:
            base = os.path.basename(f.path)
            if want in (f.name, base, os.path.splitext(base)[0]):
                return f.fresh()
        return fleets[0].fresh()

    class Handler(http.server.BaseHTTPRequestHandler):

        def send(self, code, body, kind='application/json'):
            if not isinstance(body, bytes):
                body = json.dumps(body).encode()
            self.send_response(code)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def allowed(self, query):
            if not app:
                return True
            host = self.headers.get('Host', '')
            if host not in ('127.0.0.1:%d' % app['port'], 'localhost:%d' % app['port']):
                return False
            given = self.headers.get('X-Token') or query.get('t', [''])[0]
            return hmac.compare_digest(given, app['token'])

        def body(self):
            size = int(self.headers.get('Content-Length') or 0)
            if size > 1 << 20:
                raise ValueError('too big')
            return json.loads(self.rfile.read(size) or b'{}')

        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            path, query = url.path, urllib.parse.parse_qs(url.query)
            if not path.startswith('/api/'):
                return self.static(path)
            if not self.allowed(query):
                return self.send(403, {'error': 'no'})
            if path == '/api/app':
                return self.send(200, app_state(app))
            if path == '/api/app/log' and app:
                return self.send(200, app_log(app))
            if path == '/api/overview':
                return self.send(200, overview(fleets))
            if path == '/api/fleets':
                return self.send(200, [{'name': x.fresh().name, 'source': x.meta['source']}
                                       for x in fleets])
            if not fleets:
                return self.send(404, {'error': 'no fleets yet'})
            f = pick(query)
            if path == '/api/summary':
                return self.send(200, f.summary())
            if path == '/api/incidents':
                return self.send(200, [short(i, f.fams, f.planned) for i in f.incidents])
            if path.startswith('/api/incidents/'):
                inc = f.incident(int(path.rsplit('/', 1)[1]))
                return self.send(200 if inc else 404, inc or {'error': 'no such incident'})
            if path.startswith('/api/domains/'):
                dom = f.domain(urllib.parse.unquote(path.rsplit('/', 1)[1]))
                return self.send(200 if dom else 404, dom or {'error': 'no such domain'})
            if path == '/api/map':
                return self.send(200, f.fleet_map())
            if path == '/api/fleet':
                days = pick_days(f.data, query, 14)
                rows = fleet_rows(f.data, f.fams, days)
                v = verdicts.domains(f.data, days)
                for r in rows:
                    r['verdict'] = v.get(r['domain'])
                return self.send(200, {'from': days[0], 'to': days[-1], 'rows': rows})
            if path == '/api/stats':
                if (query.get('range') or [''])[0] == 'today':
                    try:
                        return self.send(200, today(app, f))
                    except (ValueError, OSError, RuntimeError) as e:
                        return self.send(200, {'error': str(e)[:200]})
                return self.send(200, f.stats(pick_days(f.data, query, 7)))
            if path == '/api/campaigns':
                return self.send(200, verdicts.campaigns(f.data))
            if path == '/api/why-listed':
                from . import rootcause
                hits = rootcause.analyze(f.data, found=f.incidents)
                return self.send(200, {'lines': [rootcause.words(h) for h in hits[:10]],
                                       'hits': [dict(h, odds=float('%.3g' % h['odds']), domains=len(h['domains']))
                                                for h in hits[:10]],
                                       'checked': len(f.data.dns)})
            if path == '/api/changes':
                from . import actions
                return self.send(200, actions.history(f.path))
            if path == '/api/feed':
                return self.send(200, live.events(f.path, query.get('since', [''])[0]))
            self.send(404, {'error': 'not found'})

        def do_POST(self):
            url = urllib.parse.urlparse(self.path)
            path, query = url.path, urllib.parse.parse_qs(url.query)
            if path.startswith('/api/') and not self.allowed(query):
                return self.send(403, {'error': 'no'})
            if path.startswith('/api/app/') and app:
                try:
                    body = self.body()
                except ValueError:
                    return self.send(400, {'error': 'not json'})
                try:
                    return self.send(200, app_post(app, path.rsplit('/', 1)[1], body))
                except ValueError as e:
                    return self.send(400, {'error': str(e)})
            if path == '/api/ask':
                from . import agent
                try:
                    body = self.body()
                except ValueError:
                    return self.send(400, {'error': 'not json'})
                f = pick(query)
                paths = [f.path] + [x.path for x in fleets if x is not f]
                hist = [tuple(h) for h in body.get('history') or []]
                text, err = agent.ask(body.get('question', ''), paths, hist,
                                      model=(app['settings'].cfg.get('model') if app else None))
                return self.send(200, {'answer': text, 'error': err})
            if path.startswith('/api/incidents/') and path.endswith('/approve'):
                return self.send(200, pick(query).approve(int(path.split('/')[3])))
            if path.startswith('/hook/') and secret:
                if not hmac.compare_digest(path.split('/')[2], secret):
                    return self.send(403, {'error': 'no'})
                size = int(self.headers.get('Content-Length') or 0)
                if size > 1 << 20:
                    return self.send(413, {'error': 'too big'})
                try:
                    body = json.loads(self.rfile.read(size) or b'{}')
                except ValueError:
                    return self.send(400, {'error': 'not json'})
                return self.send(200, {'ok': True, 'events': live.hook(pick(query).path, body)})
            self.send(404, {'error': 'not found'})

        def static(self, path):
            name = os.path.normpath(path.lstrip('/')) or 'index.html'
            if name.startswith('..'):
                return self.send(403, {'error': 'no'})
            full = os.path.join(STATIC, name)
            if not os.path.isfile(full):
                full = os.path.join(STATIC, 'index.html')
            if not os.path.isfile(full):
                return self.send(500, b'dashboard not built: cd ui && npm install '
                                      b'&& npm run build', 'text/plain')
            kind = mimetypes.guess_type(full)[0] or 'application/octet-stream'
            with open(full, 'rb') as f:
                self.send(200, f.read(), kind)

        def log_message(self, *args):
            pass

    return Handler


# What a log line is about, for the Logs page's filters.
LOG_KINDS = (
    ('problem', re.compile(r'failed|could not|refused it|error', re.I)),
    ('alert', re.compile(r'^ALERT ')),
    ('change', re.compile(r'^(applying|undoing): |daily limit|warmup|blocklist|reading the mailboxes back|mailbox(es)? (shows?|do not)|'
                          r'take .* off the|add .* to the|tracking|risky', re.I)),
    ('report', re.compile(r'sending the .*report|report to ', re.I)),
)


def app_log(app, limit=1500):
    # The app's own log (every check, step, alert and change) and the log of
    # what was sent out, newest first.
    from . import notify
    names = sorted((f['name'] for f in app['settings'].cfg['fleets']), key=len, reverse=True)
    out = []
    path = os.path.join(os.path.dirname(notify.LOG), 'app.log')
    if 'checker' in app and getattr(app['checker'], 'logfile', None):
        path = app['checker'].logfile
    for line in tail(path, limit):
        m = re.match(r'(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d)  (.*)$', line)
        if not m:
            continue
        text, who = m.group(3), ''
        for n in names:
            if text.startswith(n + ': '):
                who, text = n, text[len(n) + 2:]
                break
        kind = next((k for k, rx in LOG_KINDS if rx.search(text)), 'check')
        out.append({'at': m.group(1) + ' ' + m.group(2), 'who': who, 'text': text, 'kind': kind})
    for line in tail(notify.LOG, 300):
        m = re.match(r'(\d{4}-\d\d-\d\d)T(\d\d:\d\d:\d\d)  (.*)$', line)
        if m:
            text = m.group(3)
            out.append({'at': m.group(1) + ' ' + m.group(2), 'who': '', 'text': 'sent: ' + text,
                        'kind': 'problem' if 'failed:' in text else 'report'})
    out.sort(key=lambda r: r['at'], reverse=True)
    return out[:limit]


def tail(path, n):
    try:
        with open(path, errors='replace') as f:
            return f.read().splitlines()[-n:]
    except OSError:
        return []


def app_state(app):
    from . import agent
    if not app:
        return {'app': False}
    return {'app': True, 'settings': app['settings'].public(),
            'state': app['checker'].state, 'analyst': bool(agent.claude()),
            'slack_bot': app['bot'].state if app.get('bot') else 'off',
            'telegram': app['telegram'].state if app.get('telegram') else 'off',
            'slack_manifest_url': slackbot_url(),
            'rules': getattr(app['settings'], 'rules_path', None),
            'rules_problems': getattr(app['settings'], 'rules_problems', [])}


def slackbot_url():
    from . import slackbot
    return slackbot.manifest_url()


def app_post(app, what, body):
    from . import notify
    if what == 'settings':
        app['settings'].update(body)
        return app_state(app)
    if what == 'check':
        app['checker'].now(body.get('fleet'))
        return {'ok': True}
    if what == 'stop':
        app['checker'].stop()
        return {'ok': True}
    if what == 'send-report':
        # The daily or weekly SITREP now, from the last checks, to see it.
        if not notify.channels(app['settings'].cfg.get('notify') or {}):
            raise ValueError('connect Slack or Telegram first')
        app['checker'].digest(app['settings'].cfg, 'weekly' if body.get('kind') == 'weekly' else 'now')
        return {'ok': True}
    if what == 'test-workspace':
        from . import watch
        cfg = next((f for f in app['settings'].cfg['fleets'] if f['name'] == body.get('fleet')), None)
        if cfg is None:
            raise ValueError('no workspace named %s' % body.get('fleet'))
        return {'ok': watch.probe(cfg)}
    if what == 'test-notify':
        ch = body.get('channel')
        cfg = app['settings'].cfg.get('notify') or {}
        if ch not in notify.channels(cfg):
            raise ValueError('%s is not set up yet; save the settings first' % (ch or 'that channel'))
        failed = notify.send(cfg, 'Inbox Sitrep test', 'This is a test.  Reports will arrive here.', only=ch)
        if failed:
            raise ValueError('%s: %s' % (ch, failed[ch]))
        return {'ok': True}
    if what in ('preview', 'custom', 'instruct', 'apply', 'undo'):
        return change_post(app, what, body)
    raise ValueError('unknown action %s' % what)


def fleet_cfg(app, name):
    for f in app['settings'].cfg['fleets']:
        if f['name'] == name:
            return f
    raise ValueError('no workspace named %s' % name)


def writer_for(cfg):
    from . import actions, watch
    seq = cfg.get('sequencer')
    if seq == 'emailbison':
        return actions.BisonWriter(watch.client(cfg))
    if seq == 'instantly':
        return actions.InstantlyWriter(watch.client(cfg))
    return actions.Writer(watch.key(cfg), cfg['workspace'])


def make_act(fleet, cfg, change, incident=None):
    # A preview of one change on this workspace, as it is now.  Never writes.
    from . import actions, watch
    seq = cfg.get('sequencer')
    if seq == 'sim':
        act = {'id': 'sim-%s' % os.urandom(4).hex(), 'op': change['op'], 'change': change, 'steps': [],
               'undo': [], 'manual': None, 'big': False,
               'lines': ['Demo data: %s.  Nothing real is changed.'
                         % brief.step_words(change['op'], change.get('provider'), change.get('targets') or [],
                                            change.get('limit'), change.get('warmup'))]}
    elif seq == 'emailbison':
        act = actions.bison_prepare(change, watch.client(cfg), fleet.name)
    elif seq == 'instantly':
        act = actions.instantly_prepare(change, watch.client(cfg), fleet.name)
    elif seq == 'plusvibe':
        act = actions.prepare(change, fleet.data, watch.client(cfg), fleet.name)
    else:
        act = {'id': 'manual-%s' % os.urandom(4).hex(), 'op': change['op'], 'change': change, 'steps': [],
               'undo': [], 'lines': [], 'big': False,
               'manual': actions.manual(change, fleet.data) + '  (Changes from the app are built '
                         'for PlusVibe, EmailBison and Instantly; for %s, make it in the sequencer.)' % seq}
    act['incident'] = incident
    guard(act, fleet)
    return act


def guard(act, fleet):
    # Before a domain is paused or slowed: how it has done over the last 14
    # days.  Changing one that is doing fine needs a second yes.  (Built the
    # day seven healthy domains were paused over one stray bounce each.)
    from . import actions
    change = act.get('change') or {}
    if change.get('op') not in actions.MAILBOX_OPS or not fleet.data.days:
        return
    days = pick_days(fleet.data, {}, 14)
    v = verdicts.domains(fleet.data, days)
    rows = [v[d] for d in change.get('targets') or () if d in v]
    act['verdicts'] = [{k: r[k] for k in ('domain', 'verdict', 'label', 'sent', 'per_1000', 'fleet_per_1000',
                                          'reply_rate', 'bounce_rate')} for r in rows]
    act['healthy'] = [r['domain'] for r in rows if r['verdict'] in verdicts.GOOD]
    # protected in rules.ini: always the second yes
    from . import rules
    fams = detect.families(fleet.data.domains())
    act['protected'] = [d for d in change.get('targets') or () if rules.protected(d, fams.get(d))]
    act['healthy'] += [d for d in act['protected'] if d not in act['healthy']]


def ask_context(fleet, cfg, inc=None, change=None):
    # What the analyst needs to turn words into a change: the workspace,
    # the incident and its suggested change, the domains it can name.
    doms = fleet.data.domains()
    fams = collections.Counter(detect.families(doms).values())
    lines = ['Workspace: %s on %s, %d sending domains.' % (fleet.name, cfg.get('sequencer'), len(doms))]
    if inc:
        lines.append('Incident #%s: %s' % (inc['id'], inc['title']))
        mine = brief.concerned(inc)
        if mine:
            lines.append('Domains in this incident: %s' % ', '.join(mine[:80]))
    if change:
        lines.append('Suggested change: %s' % json.dumps(change))
    lines.append('Families: %s' % ', '.join('%s* (%d)' % kv for kv in fams.most_common(40)))
    alone = sorted(d for d in doms if d not in detect.families(doms))
    if alone:
        lines.append('Domains without a family: %s' % ', '.join(alone[:60]))
    names = sorted({c.get('name') or c.get('camp_name') for c in fleet.data.campaigns.values()} - {None})
    if names and cfg.get('sequencer') == 'plusvibe':
        lines.append('Campaigns: %s' % '; '.join(names[:40]))
    return '\n'.join(lines)


def change_post(app, what, body):
    # Preview, apply and undo of approved changes; see actions.py.
    from . import actions, watch
    fleet = next((f for f in app['fleets'] if f.name == body.get('fleet')), app['fleets'][0])
    cfg = fleet_cfg(app, fleet.name)
    sim = cfg.get('sequencer') == 'sim'
    inc, change = None, None
    if body.get('incident') not in (None, ''):
        inc = next((i for i in fleet.incidents if i['id'] == int(body['incident'])), None)
        if inc is None:
            raise ValueError('no such incident')
        if body.get('step') not in (None, '') and int(body['step']) < len(inc['plan']):
            change = dict(inc['plan'][int(body['step'])])
    if what in ('preview', 'custom', 'instruct'):
        note = None
        if what == 'preview':
            # A recommended step, with what the person ticked: warmup too.
            if change is None:
                raise ValueError('no such step')
            if (body.get('options') or {}).get('warmup') == 'off' and change['op'] in actions.MAILBOX_OPS:
                change['warmup'] = 'off'
            changes = [change]
        elif what == 'custom':
            # A change spelled out (Claude Code's propose_change).
            changes = [body.get('change') or {}]
        else:
            # A change in the person's words, turned into changes by the
            # analyst, then held to the same rules as any other.
            from . import agent
            text = (body.get('text') or '').strip()
            if not text:
                raise ValueError('say what you want changed')
            changes, note, err = agent.instruct(text, ask_context(fleet, cfg, inc, change),
                                                model=app['settings'].cfg.get('model') if 'settings' in app else None)
            if err:
                raise ValueError(err)
            if not changes:
                return {'id': None, 'manual': note or 'That can\'t be done from here.', 'lines': [], 'steps': [],
                        'undo': [], 'understood': note, 'allowed': True, 'big': False}
        if what != 'preview':
            seq = cfg.get('sequencer')
            names = [c.get('name') or c.get('camp_name') for c in fleet.data.campaigns.values()]
            changes = [actions.clean(c, fleet.data.domains(), seq, names) for c in changes]
        act = actions.combine([make_act(fleet, cfg, c, inc['id'] if inc else None) for c in changes])
        act['understood'] = note
        act['allowed'] = sim or bool(cfg.get('writes'))
        app['pending'][act['id']] = act
        return act
    if what == 'apply':
        act = app['pending'].get(body.get('id'))
        if not act:
            raise ValueError('preview again: that preview is gone')
        if not (sim or fleet_cfg(app, fleet.name).get('writes')):
            raise ValueError('changes are off for %s; allow them first' % fleet.name)
        if act['big'] and body.get('confirm', '').strip() != fleet.name:
            raise ValueError('type the workspace name to confirm a change this big')
        if act.get('healthy') and not body.get('healthy_ok'):
            raise ValueError('%d of these domains are doing well; confirm that you want to change them anyway'
                             % len(act['healthy']))
        if sim:
            # Journaled like a real change, so the Changes page shows it.
            if act['incident'] is not None:
                fleet.approve(act['incident'])
            actions.apply(act, None, fleet.path, act['incident'], say=lambda m: None)
            app['pending'].pop(act['id'], None)
            return {'status': 'applied (simulated)'}
        writer = writer_for(cfg)
        say = lambda m: app['checker'].say('%s: %s' % (fleet.name, m))
        say('applying: %s' % (act['lines'][0] if act['lines'] else act['op']))
        status = actions.apply(act, writer, fleet.path, act['incident'],
                               say=say, read=watch.client(cfg), seq=cfg.get('sequencer'))
        app['pending'].pop(act['id'], None)
        done = next((c for c in actions.history(fleet.path) if c['id'] == act['id']), {})
        return {'status': status, 'checked': done.get('checked')}
    if what == 'undo':
        writer = None if sim else writer_for(cfg)
        say = lambda m: app['checker'].say('%s: %s' % (fleet.name, m))
        title = next((h['title'] for h in actions.history(fleet.path) if h['id'] == body['id']), body['id'])
        say('undoing: %s' % title)
        return {'status': actions.undo(fleet.path, body['id'], writer, say=say)}


def serve(paths, port=8008, secret=None, host='127.0.0.1'):
    if isinstance(paths, str):
        paths = [paths]
    fleets = [Fleet(p) for p in paths]
    server = http.server.ThreadingHTTPServer((host, port), handler(fleets, secret))
    print('inboxsitrep: http://%s:%d  (%s, Ctrl-C to stop)'
          % (host, port, ', '.join(f.name for f in fleets)))
    if secret:
        print('  webhooks: POST http://%s:%d/hook/<secret>' % (host, port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
