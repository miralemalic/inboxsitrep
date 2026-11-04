# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# Live mode: bounces and replies as they happen, not the next morning.
#
# Two ways in.  Polling asks the sequencer every few minutes for leads
# that bounced or replied since the last look (read-only, like everything
# else).  Webhooks let the sequencer push the same events to `inboxsitrep
# serve`.  Either way the events land in one table, the dashboard shows
# them, and a few signatures raise an alert at once: the ones where hours
# matter, like a Microsoft tenant block or a domain Gmail just started
# refusing.

import json
import hashlib
import datetime
import collections

from . import bounces
from .data import connect


WINDOW_HOURS = 2
# Signatures worth waking someone for, with how many in the window it takes.
# Only what stops mail at scale is worth an alert between reports.
# Blocklist names, throttling and complaints wait for the report: a
# listed domain usually keeps delivering, and an alert per bounce wave is
# noise.  Each domain is alerted once a day, and a workspace at most
# ALERTS_A_DAY times; past that, it waits for the report.
ALERTS = {
    'tenant_block': (10, 'Microsoft is blocking outbound mail from {dom} at the source',
                     'Pause these domains: nothing they send is delivered.'),
    'gmail_reputation': (20, 'Gmail started refusing mail from {dom}',
                         'Stop sending to Gmail from them for now; it often clears in 2-4 days.'),
    'microsoft_block': (20, 'Microsoft started refusing mail from {dom}',
                        'Stop sending to Microsoft from them; Gmail still takes their mail.'),
    'auth': (20, 'Mail from {dom} is failing authentication', 'Check SPF, DKIM and DMARC on them today.'),
}
ALERTS_A_DAY = 3
FREE = {'gmail.com': 'google', 'googlemail.com': 'google', 'outlook.com': 'microsoft',
        'hotmail.com': 'microsoft', 'live.com': 'microsoft', 'msn.com': 'microsoft',
        'icloud.com': 'apple', 'me.com': 'apple', 'mac.com': 'apple'}
# Each sequencer names things its own way.  Instantly and Smartlead don't
# document their bounce payloads; these are best guesses, marked as such
# in the README.
KIND = {
    'BOUNCED_EMAIL': 'bounce', 'email_bounced': 'bounce', 'EMAIL_BOUNCED': 'bounce',
    'EMAIL_BOUNCE': 'bounce', 'bounce': 'bounce',
    'ALL_EMAIL_REPLIES': 'reply', 'FIRST_EMAIL_REPLIES': 'reply',
    'reply_received': 'reply', 'EMAIL_REPLIED': 'reply', 'EMAIL_REPLY': 'reply',
    'LEAD_MARKED_AS_INTERESTED': 'positive', 'lead_interested': 'positive',
    'lead_unsubscribed': 'unsubscribe', 'EMAIL_UNSUBSCRIBED': 'unsubscribe',
    'account_error': 'mailbox_error', 'EMAIL_ACCOUNT_DISCONNECTED': 'mailbox_error',
    'SYSTEM_ALERT': 'mailbox_error',
}


def first(body, *names):
    for name in names:
        v = body
        for part in name.split('.'):
            v = v.get(part) if isinstance(v, dict) else None
        if v:
            return v
    return None


def domain_of(email):
    return (email or '').rsplit('@', 1)[-1].lower()


def normalize(body, source='webhook'):
    # One event from any sequencer's payload, or None if it isn't one we
    # use.  Recipient addresses are never stored: only who hosts them.
    if isinstance(body, list):
        return [e for b in body for e in [normalize(b, source)] if e]
    name = first(body, 'webhook_event', 'event_type', 'event.type', 'event', 'type')
    kind = KIND.get(name) if isinstance(name, str) else None
    if not kind:
        return None
    sender = first(body, 'sender_email', 'email_account', 'from_email',
                   'sl_email_account', 'sender.email', 'data.sender_email')
    lead = first(body, 'lead_email', 'to_email', 'lead.email', 'email', 'data.lead_email')
    text = first(body, 'msg', 'bounce_msg', 'bounce_reason', 'reason', 'error',
                 'data.msg', 'reply.body') or ''
    ts = first(body, 'date', 'timestamp', 'event_timestamp', 'created_at') or now()
    return event(ts, source, kind, sender, lead, text if kind != 'reply' else '')


def event(ts, source, kind, sender, lead, text=''):
    dom = domain_of(sender)
    detail = bounces.kind_of(text, dom) if kind == 'bounce' else ''
    key = hashlib.sha1(('%s|%s|%s|%s' % (ts, kind, sender, lead)).encode()).hexdigest()[:20]
    return {'key': key, 'ts': ts[:19].replace('T', ' '), 'source': source, 'kind': kind,
            'domain': dom, 'provider': FREE.get(domain_of(lead), 'other'),
            'detail': detail, 'text': bounces.mask(text)[:600]}


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def store(path, evs):
    db = connect(path)
    with db:
        before = db.total_changes
        db.executemany('INSERT OR IGNORE INTO events VALUES '
                       '(:key,:ts,:source,:kind,:domain,:provider,:detail,:text)', evs)
        n = db.total_changes - before
    db.close()
    return n


def hook(path, body):
    evs = normalize(body)
    evs = evs if isinstance(evs, list) else [evs] if evs else []
    return store(path, evs)


def events(path, since='', limit=200):
    db = connect(path)
    cols = ('ts', 'source', 'kind', 'domain', 'provider', 'detail', 'text')
    rows = db.execute('SELECT %s FROM events WHERE ts > ? ORDER BY ts DESC LIMIT ?'
                      % ', '.join(cols), (since or '', limit)).fetchall()
    db.close()
    return [dict(zip(cols, r)) for r in rows]


def alerts(path, hours=WINDOW_HOURS, at=None):
    # What the last couple of hours of bounces say needs someone now.
    at = at or now()
    start = (datetime.datetime.fromisoformat(at) - datetime.timedelta(hours=hours)
             ).strftime('%Y-%m-%d %H:%M:%S')
    db = connect(path)
    rows = db.execute("SELECT domain, detail, count(*) FROM events WHERE kind = 'bounce' "
                      "AND ts > ? GROUP BY domain, detail", (start,)).fetchall()
    down = db.execute("SELECT domain, count(*) FROM events WHERE kind = 'mailbox_error' "
                      "AND ts > ? GROUP BY domain", (start,)).fetchall()
    db.close()
    by = collections.defaultdict(list)
    for dom, detail, n in rows:
        need = ALERTS.get(detail)
        if need and n >= need[0]:
            by[detail].append((dom, n))
    out = []
    for detail, hits in sorted(by.items()):
        hits.sort(key=lambda h: -h[1])
        out.append({'detail': detail, 'hits': hits, 'hours': hours})
    if len(down) >= 3:
        # one mailbox erroring is routine; many at once is an outage
        out.append({'detail': 'mailbox_error', 'hits': sorted(down, key=lambda h: -h[1]), 'hours': hours})
    return out


def words(detail, hits, hours):
    names = ', '.join(d for d, _ in hits[:3]) + (' and %d more' % (len(hits) - 3) if len(hits) > 3 else '')
    if detail == 'mailbox_error':
        return ('%d mailbox errors on %d domains' % (sum(n for _, n in hits), len(hits)),
                '%s.  Reconnect them in the sequencer.' % names)
    return (ALERTS[detail][1].format(dom=names),
            '%d bounces in the last %d hours.  %s' % (sum(n for _, n in hits), hours, ALERTS[detail][2]))


def poll(client, path, since, say=print):
    # PlusVibe: leads that bounced or replied since `since`, newest first.
    got = []
    for status in ('BOUNCED', 'REPLIED'):
        page = 1
        while page <= 20:
            leads = client.get('/lead/workspace-leads', status=status, limit=500, page=page,
                               sort='modified_at', direction='desc')
            if not leads:
                break
            for lead in leads:
                ts = (lead.get('modified_at') or '')[:19].replace('T', ' ')
                if ts <= since:
                    break
                kind = 'bounce' if status == 'BOUNCED' else (
                    'positive' if (lead.get('label') or '').upper() == 'INTERESTED' else 'reply')
                got.append(event(ts, 'poll', kind, lead.get('email_acc_name'),
                                 lead.get('email'), lead.get('bounce_msg') or ''))
            else:
                page += 1
                continue
            break
    n = store(path, got)
    say('%d new events' % n)
    return got


def cycle(client, path, notify=None, say=print):
    # One look: poll, store, alert on what is new.  The caller loops.
    from . import data as store_
    seen = set(store_.get_meta(path, 'alerted', []) or [])
    since = (store_.get_meta(path, 'live') or {}).get('last') or (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=WINDOW_HOURS)
    ).strftime('%Y-%m-%d %H:%M:%S')
    started = now()
    try:
        got = poll(client, path, since, say)
        since = max([e['ts'] for e in got] + [since])
    except (OSError, RuntimeError) as e:
        say('poll failed: %s' % e)
    fired = []
    day = started[:10]
    sent_today = sum(1 for k in seen if k.startswith('sent:%s:' % day))
    for a in alerts(path):
        # each domain once a day, whatever else it is reported with
        fresh = [(d, n) for d, n in a['hits'] if '%s:%s:%s' % (a['detail'], d, day) not in seen]
        if not fresh or sent_today >= ALERTS_A_DAY:
            continue
        seen.update('%s:%s:%s' % (a['detail'], d, day) for d, _ in fresh)
        title, body = words(a['detail'], fresh, a['hours'])
        sent_today += 1
        seen.add('sent:%s:%d' % (day, sent_today))
        if sent_today == ALERTS_A_DAY:
            body += '.  Further alerts today wait for the report.'
        fired.append({'title': title, 'body': body})
        say('ALERT %s (%s)' % (title, body))
        if notify:
            notify(title, body)
    db = connect(path)
    with db:
        db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)', ('live', json.dumps(
            {'last': since, 'checked': started})))
        db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',
                   ('alerted', json.dumps(sorted(seen)[-500:])))
    db.close()
    return fired
