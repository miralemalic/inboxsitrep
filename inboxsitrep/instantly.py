# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# Read an Instantly workspace (API v2) into inboxsitrep's counts.
#
# Written from Instantly's published API reference; not yet run against a
# live workspace.  Read-only: GET, plus the one POST Instantly uses for
# listing leads (a read with a body).  Nothing else can be sent.
#
# Instantly gives daily totals per mailbox, and leads that bounced or
# replied with the recipient's provider (esp_code) and the mailbox that
# last wrote to them.  It does not give bounce text through the API, so
# the blocklist and tenant detectors only see what webhooks bring in.

import json
import time
import datetime
import collections
import urllib.error
import urllib.parse
import urllib.request

from .data import Data
from .plusvibe import balance, domain_of


API = 'https://api.instantly.ai'
READ_POSTS = ('/api/v2/leads/list',)
PER_SECOND = 5
ESP = {1: 'google', 2: 'microsoft'}
HOST = {2: 'google', 3: 'microsoft'}
DOWN = (-1, -2, -3)          # connection, soft bounce, sending errors
APPLE = ('icloud.com', 'me.com', 'mac.com')


class Client:

    def __init__(self, key, verbose=False):
        self.key = key
        self.verbose = verbose
        self.last = 0.0
        self.calls = 0

    def request(self, path, params=None, body=None):
        if body is not None and path not in READ_POSTS:
            raise ValueError('inboxsitrep only reads: refusing POST %s' % path)
        url = API + path + ('?' + urllib.parse.urlencode(params, doseq=True) if params else '')
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers={
            'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json',
            'User-Agent': 'inboxsitrep'})
        for attempt in range(6):
            wait = self.last + 1 / PER_SECOND - time.time()
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            self.calls += 1
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    return json.loads(r.read())
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                code = getattr(e, 'code', None)
                if code is not None and code < 500 and code != 429:
                    raise
                time.sleep(2 ** attempt)
        raise RuntimeError('gave up on %s' % path)

    def get(self, path, **params):
        return self.request(path, params)

    def write(self, method, path, body):
        # Only for approved changes; actions.InstantlyWriter decides what may
        # be sent.  Reads never come through here.
        url = API + path
        req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
                                     method=method, headers={'Authorization': 'Bearer ' + self.key,
                                                             'Content-Type': 'application/json',
                                                             'User-Agent': 'inboxsitrep'})
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read() or b'{}')

    def pages(self, path, body=None, **params):
        after = None
        while True:
            if body is not None:
                page = self.request(path, body=dict(body, limit=100,
                                                    **({'starting_after': after} if after else {})))
            else:
                page = self.get(path, limit=100, **({'starting_after': after} if after else {}),
                                **params)
            for item in page.get('items') or []:
                yield item
            after = page.get('next_starting_after')
            if not after:
                return


def provider_of(lead):
    if domain_of(lead.get('email')) in APPLE:
        return 'apple'
    return ESP.get(lead.get('esp_code'), 'other')


def collect(api, days=21, today=None, say=print):
    today = today or datetime.date.today()
    end = (today - datetime.timedelta(days=1)).isoformat()
    start = (today - datetime.timedelta(days=days)).isoformat()
    data = Data()

    say('mailboxes')
    emails = []
    hosts = collections.defaultdict(collections.Counter)
    scores = collections.defaultdict(list)
    for a in api.pages('/api/v2/accounts'):
        dom = domain_of(a.get('email'))
        emails.append(a.get('email'))
        data.mailboxes[dom] = data.mailboxes.get(dom, 0) + 1
        states = data.accounts.setdefault(dom, {})
        st = 'down' if a.get('status') in DOWN else 'active'
        states[st] = states.get(st, 0) + 1
        hosts[dom][HOST.get(a.get('provider_code'), 'other')] += 1
        if a.get('stat_warmup_score') is not None:
            scores[dom].append(a['stat_warmup_score'])
    for dom, c in hosts.items():
        data.host[dom] = c.most_common(1)[0][0]
    for dom, s in scores.items():
        # Instantly's one warmup score, filed where Google's would go.
        data.health[dom] = (round(sum(s) / len(s), 1), None)
        data.history[dom][today.isoformat()] = data.health[dom]

    say('daily totals per mailbox, %s to %s' % (start, end))
    totals = collections.defaultdict(lambda: [0, 0, 0])      # sent, replies, bounces
    for i in range(0, len(emails), 200):
        rows = api.get('/api/v2/accounts/analytics/daily', start_date=start,
                       end_date=end, emails=emails[i:i + 200])
        for r in rows or []:
            t = totals[domain_of(r.get('email_account')), r['date'][:10]]
            t[0] += r.get('sent') or 0
            t[1] += max(0, (r.get('replies') or 0) - (r.get('replies_automatic') or 0))
            t[2] += r.get('bounced') or 0
            data.fresh[r['date'][:10]] = data.fresh.get(r['date'][:10], 0) + (
                r.get('new_leads_contacted') or 0)

    say('bounced and replied leads')
    leads = collections.defaultdict(lambda: [0, 0, 0])        # rep, pos, bnc
    mix = collections.defaultdict(collections.Counter)
    for flt, col in (('FILTER_VAL_BOUNCED', 2), ('FILTER_VAL_REPLIED', 0)):
        for lead in api.pages('/api/v2/leads/list', body={'filter': flt}):
            day = (lead.get('timestamp_last_contact') or '')[:10]
            if not (start <= day <= end):
                continue
            dom = domain_of(lead.get('last_contacted_from'))
            prov = provider_of(lead)
            mix[dom][prov] += 1
            row = leads[dom, prov, day]
            row[col] += 1
            if col == 0 and lead.get('lt_interest_status') == 1:
                row[1] += 1
            if col == 2:
                data.note_bounce(day, dom, '')

    say('splitting sends by provider')
    # No per-provider totals here: the split comes from who bounced and
    # replied on each domain, and from the fleet when a domain has neither.
    fleet = collections.Counter()
    for c in mix.values():
        fleet.update(c)
    for (dom, day), (sent, rep, bnc) in sorted(totals.items()):
        if not sent:
            continue
        got = mix.get(dom) or fleet or collections.Counter(google=1)
        n = sum(got.values())
        split = balance({(dom, p): sent * v / n for p, v in got.items()}, {dom: sent}, {})
        for (d, p), s in split.items():
            r, pos, b = leads.get((dom, p, day), (0, 0, 0))
            s = max(int(round(s)), b)
            if s:
                data.add(day, dom, p, [s, r, pos, b, 0])
        data.add_day(day)
    for dom, _ in totals:
        data.workspace.setdefault(dom, None)
    say('%d domains, %d days, %d API calls' % (len(data.domains()), len(data.days), api.calls))
    return data
