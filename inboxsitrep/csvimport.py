# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# Read a lead export from any sequencer (PlusVibe, Instantly, Smartlead...)
# into inboxsitrep's counts, so it can be tried without an API key.
#
# One row is one lead: who sent to it, when it was last sent, and what
# happened (bounced, replied, nothing).  Counts are per lead, not per email,
# so a five-step sequence counts once, on its last send.  That is coarser
# than the API, and plenty to see a domain die or a family go to spam.

import csv
import json
import collections
import concurrent.futures
import urllib.parse
import urllib.request

from .data import Data
from .bounces import mask, SENDER_KINDS


# Column names seen in real exports, lower-cased, most specific first.
COLUMNS = {
    'sender': ('email_acc_name', 'sender', 'sender email', 'from email',
               'from_email', 'email account', 'sending account', 'mailbox',
               'account', 'from'),
    'recipient': ('email', 'lead email', 'lead_email', 'recipient', 'to'),
    'status': ('status', 'lead status', 'lead_status'),
    'sent': ('last_sent_at', 'last sent at', 'last sent', 'sent_at',
             'last contacted', 'last_contacted_date', 'date'),
    'bounce': ('bounce_msg', 'bounce reason', 'bounce_reason',
               'bounce message', 'error', 'reason'),
    'bounce_type': ('bounce_type', 'bounce type'),
    'mx': ('mx', 'esp', 'provider', 'recipient provider'),
    'label': ('label', 'lead label', 'interest status', 'lead_label'),
}
PROVIDERS = {'google_workspace': 'google', 'google': 'google', 'gmail': 'google',
             'microsoft365': 'microsoft', 'microsoft': 'microsoft',
             'outlook': 'microsoft', 'regular_account': 'other'}
FREE = {'gmail.com': 'google', 'googlemail.com': 'google',
        'outlook.com': 'microsoft', 'hotmail.com': 'microsoft',
        'live.com': 'microsoft', 'msn.com': 'microsoft',
        'icloud.com': 'apple', 'me.com': 'apple', 'mac.com': 'apple'}
POSITIVE = ('interested', 'meeting', 'positive')


def find_columns(header):
    names = {h.strip().lower(): h for h in header}
    found = {}
    for key, options in COLUMNS.items():
        for o in options:
            if o in names:
                found[key] = names[o]
                break
    missing = [k for k in ('sender', 'recipient', 'status', 'sent') if k not in found]
    return found, missing


def domain_of(email):
    return (email or '').strip().rsplit('@', 1)[-1].lower()


def mx_provider(domain):
    # Who hosts a recipient's inbox, from its MX record over DNS-over-HTTPS.
    url = 'https://cloudflare-dns.com/dns-query?' + urllib.parse.urlencode(
        {'name': domain, 'type': 'MX'})
    req = urllib.request.Request(url, headers={'accept': 'application/dns-json'})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            hosts = ' '.join(a['data'].lower() for a in json.loads(r.read()).get('Answer') or [])
    except (OSError, ValueError):
        return 'other'
    if 'google.com' in hosts or 'googlemail.com' in hosts:
        return 'google'
    if 'outlook.com' in hosts:
        return 'microsoft'
    return 'other'


def providers_for(domains, lookup=True):
    out = {d: FREE[d] for d in domains if d in FREE}
    rest = sorted(d for d in domains if d not in out)
    if lookup and rest:
        with concurrent.futures.ThreadPoolExecutor(64) as pool:
            out.update(zip(rest, pool.map(mx_provider, rest)))
    for d in rest:
        out.setdefault(d, 'other')
    return out


def read(path, lookup=True):
    with open(path, newline='', encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError('%s is empty' % path)
    cols, missing = find_columns(rows[0].keys())
    if missing:
        raise ValueError('could not find column(s) for: %s' % ', '.join(missing))
    need_lookup = 'mx' not in cols
    providers = {}
    if need_lookup:
        providers = providers_for({domain_of(r[cols['recipient']]) for r in rows}, lookup)

    data = Data()
    counts = collections.defaultdict(lambda: [0, 0, 0, 0, 0])
    for r in rows:
        day = (r.get(cols['sent']) or '')[:10]
        sender = domain_of(r.get(cols['sender']))
        if not day or not sender:
            continue
        rcpt = domain_of(r.get(cols['recipient']))
        if rcpt in ('icloud.com', 'me.com', 'mac.com'):
            prov = 'apple'
        elif need_lookup:
            prov = providers.get(rcpt, 'other')
        else:
            prov = PROVIDERS.get((r.get(cols['mx']) or '').strip().lower(), 'other')
        status = (r.get(cols['status']) or '').strip().lower()
        row = counts[day, sender, prov]
        row[0] += 1
        if status.startswith('replied') or status == 'interested':
            row[1] += 1
            label = (r.get(cols.get('label', ''), '') or '').lower()
            row[2] += any(p in label or p in status for p in POSITIVE)
        elif status.startswith('bounced'):
            row[3] += 1
            text = mask(r.get(cols.get('bounce', ''), '') or '')[:1000]
            btype = (r.get(cols.get('bounce_type', ''), '') or '').strip().lower()
            kind = data.note_bounce(day, sender, text)
            # Without a bounce type, a refusal of the sender is judged from
            # the text: reputation, blocklists, policy and auth blocks.
            sender_side = btype == 'sender' or (not btype and kind in SENDER_KINDS)
            row[4] += sender_side
            data.bounces.append((day, sender, prov, int(sender_side), text))
    for (day, dom, prov), row in counts.items():
        data.add(day, dom, prov, row)
    for dom in {dom for _, dom, _ in counts}:
        data.workspace[dom] = 'import'
    return data, cols
