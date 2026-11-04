# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# Read an EmailBison workspace into Inbox Sitrep's counts.
#
# Written from EmailBison's published OpenAPI reference (served by every
# instance at /api/reference.openapi); not yet run against a live account.
# Each customer has their own address (https://send.example.com), and a
# Bearer token from Settings > Developer API.  Reads with GET only.
#
# EmailBison gives the sender email list (status, daily limit, warmup),
# a breakdown of campaign events by date that can be narrowed to a set of
# sender emails, and a replies inbox with a "bounced" folder holding the
# bounce messages.  Sends per day come per domain from the breakdown; who
# hosts the recipients comes from the bounce and reply addresses.

import re
import json
import time
import datetime
import collections
import urllib.error
import urllib.parse
import urllib.request

from .data import Data
from .plusvibe import balance, domain_of
from . import bounces

FREE = {'gmail.com': 'google', 'googlemail.com': 'google', 'outlook.com': 'microsoft',
        'hotmail.com': 'microsoft', 'live.com': 'microsoft', 'msn.com': 'microsoft',
        'icloud.com': 'apple', 'me.com': 'apple', 'mac.com': 'apple'}
EMAIL = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')
SERVICE = re.compile(r'mailer-daemon|postmaster|no-?reply', re.I)


class Client:

    def __init__(self, key, base, verbose=False):
        self.key = key
        self.base = base.rstrip('/')
        self.calls = 0
        self.last = 0.0

    def request(self, method, path, params=None, body=None):
        url = self.base + path
        if params:
            url += '?' + urllib.parse.urlencode(params, doseq=True)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            'Authorization': 'Bearer ' + self.key, 'Accept': 'application/json',
            'Content-Type': 'application/json', 'User-Agent': 'inboxsitrep'})
        for attempt in range(6):
            wait = self.last + .25 - time.time()
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            self.calls += 1
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    return json.loads(r.read() or b'{}')
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                code = getattr(e, 'code', None)
                if code is not None and code < 500 and code != 429:
                    raise
                time.sleep(2 ** attempt)
        raise RuntimeError('gave up on %s' % path)

    def get(self, path, **params):
        return self.request('GET', path, params)

    def pages(self, path, limit=500, **params):
        # length_aware pagination: data + meta.last_page.
        page = 1
        while True:
            body = self.get(path, page=page, per_page=limit, **params)
            items = body.get('data') or []
            for item in items:
                yield item
            meta = body.get('meta') or {}
            if not items or page >= int(meta.get('last_page') or page):
                return
            page += 1


def series(rows, label):
    for s in rows:
        if (s.get('label') or '').lower() == label:
            return {d[:10]: n for d, n in s.get('dates') or []}
    return {}


def collect(api, days=21, today=None, say=print, lookup=True):
    today = today or datetime.date.today()
    end = (today - datetime.timedelta(days=1)).isoformat()
    start = (today - datetime.timedelta(days=days)).isoformat()
    data = Data()

    say('mailboxes')
    by_dom = collections.defaultdict(list)
    for s in api.pages('/api/sender-emails'):
        dom = domain_of(s.get('email'))
        by_dom[dom].append(s['id'])
        data.mailboxes[dom] = data.mailboxes.get(dom, 0) + 1
        states = data.accounts.setdefault(dom, {})
        st = 'active' if (s.get('status') or '').lower() == 'connected' else 'down'
        states[st] = states.get(st, 0) + 1
        tags = ' '.join((t.get('name') or '') for t in s.get('tags') or []).lower()
        host = 'google' if 'google' in tags else 'microsoft' if ('microsoft' in tags or 'outlook' in tags) else None
        if host:
            data.host.setdefault(dom, host)
    scores = collections.defaultdict(list)
    for w in api.pages('/api/warmup/sender-emails', start_date=start, end_date=end):
        v = w.get('warmup_score')
        if v is not None:
            scores[domain_of(w.get('email'))].append(float(v))
    for dom, s in scores.items():
        data.health[dom] = (round(sum(s) / len(s), 1), None)
        data.history[dom][today.isoformat()] = data.health[dom]

    say('daily events per domain, %s to %s' % (start, end))
    totals = {}
    for n, (dom, ids) in enumerate(sorted(by_dom.items()), 1):
        rows = api.get('/api/campaign-events/stats', start_date=start, end_date=end,
                       **{'sender_email_ids[]': ids}).get('data') or []
        sent, rep, bnc, pos = (series(rows, k) for k in ('sent', 'replied', 'bounced', 'interested'))
        for day in set(sent) | set(bnc):
            totals[dom, day] = [sent.get(day, 0), rep.get(day, 0), pos.get(day, 0), bnc.get(day, 0)]
        if n % 50 == 0:
            say('daily events: %d of %d domains' % (n, len(by_dom)))

    say('bounce messages and replies')
    ids = {i: dom for dom, l in by_dom.items() for i in l}
    bounced, replied = [], []
    for r in api.pages('/api/replies', folder='bounced'):
        day = (r.get('date_received') or '')[:10]
        dom = ids.get(r.get('sender_email_id'))
        if not dom or not (start <= day <= end):
            continue
        text = r.get('text_body') or r.get('raw_body') or ''
        rcpt = next((e for e in EMAIL.findall(text) if not SERVICE.search(e)
                     and domain_of(e) != dom), '')
        bounced.append((day, dom, domain_of(rcpt), text))
    for r in api.pages('/api/replies', folder='inbox'):
        dom = ids.get(r.get('sender_email_id'))
        if dom:
            replied.append((dom, domain_of(r.get('from_email_address'))))
    # Who hosts each recipient: gmail.com is Google, but so is any company
    # on Google Workspace; the MX record says which.
    from .csvimport import providers_for
    say('looking up who hosts the recipients')
    hosts = providers_for({d for _, _, d, _ in bounced} | {d for _, d in replied}, lookup=lookup)
    hosts.update({d: 'apple' for d in ('icloud.com', 'me.com', 'mac.com')})
    mix = collections.defaultdict(collections.Counter)
    refused = collections.Counter()         # (dom, prov, day): bounces that refused the sender
    for day, dom, rdom, text in bounced:
        prov = hosts.get(rdom, 'other')
        mix[dom][prov] += 1
        masked = bounces.mask(text)[:1000]
        kind = data.note_bounce(day, dom, masked)
        side = kind in bounces.SENDER_KINDS
        refused[dom, prov, day] += side
        data.bounces.append((day, dom, prov, int(side), masked))
    for dom, rdom in replied:
        mix[dom][hosts.get(rdom, 'other')] += 1

    say('splitting sends by provider')
    fleet = collections.Counter()
    for c in mix.values():
        fleet.update(c)
    for (dom, day), (sent, rep, pos, bnc) in sorted(totals.items()):
        if not sent:
            continue
        got = mix.get(dom) or fleet or collections.Counter(google=1)
        n = sum(got.values())
        split = balance({(dom, p): sent * v / n for p, v in got.items()}, {dom: sent}, {})
        for (d, p), s in split.items():
            share = s / sent
            snd = refused.get((dom, p, day), 0)
            data.add(day, dom, p, [max(int(round(s)), snd), int(round(rep * share)),
                                   int(round(pos * share)), max(int(round(bnc * share)), snd), snd])
        data.add_day(day)
    for dom in by_dom:
        data.workspace.setdefault(dom, None)
    say('%d domains, %d days, %d API calls' % (len(data.domains()), len(data.days), api.calls))
    return data
