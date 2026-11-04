# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# Read a Smartlead account's mailboxes: connection state and warmup
# reputation.  Written from Smartlead's published API reference; not yet
# run against a live account.  GET only.
#
# Smartlead's API has no per-mailbox daily sending totals, so the sending
# side comes from a lead export (`inboxsitrep import`) and from its
# webhooks (`inboxsitrep serve --hook-secret`).  What this adds on top is
# what only the account knows: which mailboxes are disconnected, and whose
# warmup mail is landing in spam.

import json
import time
import datetime
import urllib.error
import urllib.parse
import urllib.request

from .data import Data
from .plusvibe import domain_of


API = 'https://server.smartlead.ai/api/v1'
HOST = {'GMAIL': 'google', 'OUTLOOK': 'microsoft'}


class Client:

    def __init__(self, key, verbose=False):
        self.key = key
        self.calls = 0
        self.last = 0.0

    def get(self, path, **params):
        params['api_key'] = self.key
        url = API + path + '?' + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={'User-Agent': 'inboxsitrep'})
        for attempt in range(6):
            wait = self.last + .2 - time.time()
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


def reputation(v):
    # "95%" or 95 or None
    if v is None:
        return None
    try:
        return float(str(v).rstrip('%'))
    except ValueError:
        return None


def mailboxes(api, data=None, today=None, say=print):
    data = data or Data()
    day = (today or datetime.date.today()).isoformat()
    scores = {}
    offset = 0
    while True:
        page = api.get('/email-accounts/', offset=offset, limit=100) or []
        for a in page:
            dom = domain_of(a.get('from_email'))
            data.mailboxes[dom] = data.mailboxes.get(dom, 0) + 1
            states = data.accounts.setdefault(dom, {})
            ok = a.get('is_smtp_success') is not False and a.get('is_imap_success') is not False
            st = 'active' if ok else 'down'
            states[st] = states.get(st, 0) + 1
            data.host.setdefault(dom, HOST.get(a.get('type'), 'other'))
            w = a.get('warmup_details') or {}
            r = reputation(w.get('warmup_reputation'))
            if r is not None:
                scores.setdefault(dom, []).append(r)
        if len(page) < 100:
            break
        offset += 100
    for dom, s in scores.items():
        data.health[dom] = (round(sum(s) / len(s), 1), None)
        data.history[dom][day] = data.health[dom]
    say('%d mailboxes on %d domains' % (sum(data.mailboxes.values()), len(data.mailboxes)))
    return data
