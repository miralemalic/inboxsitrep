# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# What goes to Slack and Telegram, for every workspace in one message.
#
# The daily SITREP at the report times: the bottom line, today's numbers,
# the decisions (new ones first, then the ones still open), and anything
# else that needs a hand.  The weekly SITREP once Friday's sending is done: the
# week, domain and campaign verdicts, the changes made.  Urgent alerts in
# between are rare by design (live.py); everything else waits for these.

import re
import datetime
import collections

from . import brief, checks, incidents, verdicts
from . import data as store
from .data import SENT, REPLIES, POSITIVES, BOUNCES, SENDER

PER_WORKSPACE = 5           # decisions listed per workspace; the rest are in the app
RANK = {'RED': 0, 'AMBER': 1, 'GREEN': 2}
RED = ('tenant_block', 'cascade', 'runway', 'link_listed', 'bounce_guard')
LIVE_ENOUGH = .25           # today so far is shown once it is a quarter of a day's sending
NAMES = {'google': 'Gmail', 'microsoft': 'Microsoft', 'other': 'other'}


def pct(n, d):
    return '%.1f%%' % (100.0 * n / d) if d > 0 else '-'


def load(fleet):
    data = store.load(fleet['db'])
    found = incidents.load(fleet['db'])
    return data, found


def day(d, weekday=True):
    # "Tue 3 Nov"; strftime's %-d does not exist on Windows.
    return ('%s %d %s' % (d.strftime('%a'), d.day, d.strftime('%b'))) if weekday else '%d %s' % (d.day, d.strftime('%b'))


def dates(text):
    # 2026-10-27 -> 27 Oct, as the app writes them.
    return re.sub(r'\b(\d{4}-\d{2}-\d{2})\b',
                  lambda m: day(datetime.date.fromisoformat(m.group(1)), False), text)


def short(title, width=100):
    # A report line, not the whole finding: up to the first colon or
    # semicolon once it runs long.  The rest is in the app.
    if len(title) <= width:
        return title
    cuts = [i for i in (title.find(': ', 40), title.find('; ', 40)) if 0 < i <= width]
    if cuts:
        return title[:min(cuts)]
    return title[:width].rsplit(' ', 1)[0] + '...'


def decisions(data, found, name, fresh, today):
    # The report's "needs a decision" lines, split into new and still open.
    # Lines another line already covers, and findings with nothing left to
    # do, stay in the app.
    text = brief.brief(data, found, name, fresh)
    by_id = {i['id']: i for i in found}
    new, old = [], []
    on = False
    for line in text.splitlines():
        if line.startswith('Needs a decision'):
            on = True
            continue
        if on and not line.strip():
            break
        if not (on and line.startswith('- ')):
            continue
        line = line[2:]
        title, _, plan = line.partition('.  Plan: ')
        if plan.startswith('already') or not plan and 'already' in title:
            continue
        ref = title[2:title.index(']')] if title.startswith('[#') else None
        inc = by_id.get(int(ref)) if ref and ref.isdigit() else None
        if inc and brief.severity(inc) < 2:
            continue
        is_new = 'New: ' in title
        title = title.replace('New: ', '')
        if ref:
            title = title[title.index(']') + 2:]
        item = short(title) + ('  → ' + plan if plan else '') + ('  (#%s)' % ref if ref else '')
        if inc and not is_new:
            days = (today - datetime.date.fromisoformat(inc['opened'])).days
            if days >= 2:
                item += ', open %d days' % days
        (new if is_new else old).append(item)
    return new, old


def numbers(t, change='', short=False):
    # sent · reply rate · positives · bounces split · replies by recipient
    sent = t['sent']
    if not sent:
        return 'nothing sent'
    rep = ', '.join('%s %s' % (NAMES[p], t['replied'][p]) for p in ('google', 'microsoft', 'other')
                    if t['replied'].get(p))
    return '%s sent%s, %s replied, %s positive; %s refused, %s bad addresses.%s' % (
        format(sent, ','), change, t['rate'], format(t['positives'], ','), t['sender'], t['list'],
        '  Replies by recipient: %s.' % rep if rep and not short else '')


def from_data(data, days):
    t = [sum(data.fleet(p, days)[k] for p in ('google', 'microsoft', 'apple', 'other')) for k in range(5)]
    replied = {}
    for p in ('google', 'microsoft', 'other'):
        r = data.fleet(p, days)
        if r[SENT]:
            replied[p] = pct(r[REPLIES], r[SENT] - r[BOUNCES])
    return {'sent': t[SENT], 'rate': pct(t[REPLIES], t[SENT] - t[BOUNCES]), 'replied': replied,
            'positives': t[POSITIVES], 'sender': pct(t[SENDER], t[SENT]), 'list': pct(t[BOUNCES] - t[SENDER], t[SENT])}


def from_live(live):
    return {'sent': live['sent'], 'positives': live['positives'],
            'rate': '%.1f%%' % live['replied'] if live.get('replied') is not None else '–',
            'replied': {p: '%.1f%%' % v['replied'] for p, v in live['providers'].items() if v.get('sent') and v.get('replied') is not None},
            'sender': '%.1f%%' % (live['sender_bounces'] or 0), 'list': '%.1f%%' % (live['recipient_bounces'] or 0)}


def infra(data, days):
    # Positives per 1,000 by what the mailboxes are, when a fleet mixes them.
    from .web import by_infra
    rows = by_infra(data, days)
    if len(rows) < 2:
        return ''
    short_names = {'google': 'Google', 'microsoft': 'Microsoft 365', 'other': 'SMTP'}
    return 'Positives per 1,000 by sender, 7 days: %s.' % ', '.join(
        '%s %.1f' % (short_names[h], r['per_1000']) for h, r in rows.items())


def status(found):
    # RED: the workspace, a family or its leads are going down now.  AMBER:
    # decisions open.  GREEN: nothing needs a hand.
    open_ = [i for i in found if i['status'] == 'open' and i['kind'] in brief.DECIDE and brief.severity(i) >= 2]
    if any(i['kind'] in RED or (i['kind'] == 'auth' and i['scope'] != 'domain') for i in open_):
        return 'RED'
    return 'AMBER' if open_ else 'GREEN'


def spares(r):
    # "3 warmed spare domains; 11 lost in 30 days, about a week's worth.  Buy and warm 15 ..."
    if not r:
        return ''
    text = '%d warmed spare domain%s' % (r['spare'], '' if r['spare'] == 1 else 's')
    if r['lost']:
        w = r['weeks']
        text += '; %d lost in 30 days, %s' % (r['lost'], 'so none to swap in' if not r['spare']
                                              else 'under a week\'s worth' if w < 1
                                              else 'about a week\'s worth' if w < 1.5
                                              else 'about %.0f weeks\' worth' % w)
    if r['buy']:
        text += '.  Buy and warm %d %s' % (r['buy'], 'to cover a month of losses' if r['why'] == 'month'
                                           else 'to keep at least 10% in reserve')
    return text + '.'


def day_name(iso, today):
    d = datetime.date.fromisoformat(iso)
    gap = (today - d).days
    if gap in (0, 1):
        return '%s (%s)' % ('Today' if gap == 0 else 'Yesterday', day(d))
    return day(d)


def names(rows, state, most=2):
    # "Atlas Growth, Summit Pipeline" or "Northwind Outbound, Fleet 01 +3"
    got = [r[1] for r in rows if r[2] == state]
    return ', '.join(got[:most]) + (' +%d' % (len(got) - most) if len(got) > most else '')


def daily(cfg, today_live=None, now=None):
    # The end-of-day SITREP, every workspace in one message, once the day's
    # sending is done: what needs a decision, then the numbers.  A workspace
    # with no campaign running is quiet, not a problem: one line at the end.
    # today_live(fleet cfg) -> today's counts from the sequencer ({'sent',
    # 'replies', 'positives', 'bounces'}), or None for the last day collected.
    now = now or datetime.datetime.now()
    today = now.date()
    rows, quiet, leads, extras = [], [], [], []
    total_new = 0
    for f in cfg['fleets']:
        try:
            data, found = load(f)
        except (OSError, ValueError):
            continue
        name = f['name']
        if not data.days:
            quiet.append((name, 0))
            continue
        # Leads are a line of their own, not a decision.
        found = [i for i in found if i['kind'] != 'runway']
        gap = (today - datetime.date.fromisoformat(data.days[-1])).days
        t = None
        if today_live:
            try:
                t = today_live(f)
            except (OSError, RuntimeError, ValueError):
                t = None
        if t is None and gap > 1:
            # Nothing collected since before yesterday: nothing sent since.
            t = {'sent': 0, 'replies': 0, 'positives': 0, 'bounces': 0}
        elif t is None:
            got = [sum(data.fleet(p, data.days[-1:])[k] for p in ('google', 'microsoft', 'apple', 'other'))
                   for k in range(5)]
            t = {'sent': got[SENT], 'replies': got[REPLIES], 'positives': got[POSITIVES], 'bounces': got[BOUNCES]}
            t['day'] = data.days[-1]
        running = [c for c in data.campaigns.values() if c.get('status') == 'ACTIVE' and not c.get('idle')]
        fresh = set(store.get_meta(f['db'], 'fresh_ids', []) or [])
        new, old = decisions(data, found, name, fresh, today)
        if not t['sent'] and not running and gap > 1:
            quiet.append((name, len(new) + len(old)))
            continue
        total_new += len(new)
        items = ['NEW ' + x for x in new] + old
        if not t['sent'] and running:
            items.insert(0, 'Nothing sent today with %d campaign%s running  -> check the campaigns and mailboxes'
                         % (len(running), '' if len(running) == 1 else 's'))
        state = status(found) if items else 'GREEN'
        if state == 'GREEN' and items:
            state = 'AMBER'
        acc = [a for a in data.accounts.values()]
        down = sum(a.get('down', 0) for a in acc)
        boxes = sum(a.get('down', 0) + a.get('active', 0) for a in acc)
        rows.append({'name': name, 'state': state, 'items': items, 't': t, 'down': down, 'boxes': boxes})
        left = runway_days(data)
        if left and left[2] < LEADS_DAYS:
            leads.append('%s: %s sending days of new leads left in running campaigns (%s left, %s a sending day).'
                         % (name, '%.1f' % left[2] if left[2] < 10 else '%.0f' % left[2],
                            format(left[0], ','), format(round(left[1]), ',')))
        r = checks.reserve(data, found, data.days[-1])
        if r and r['buy'] and (r['weeks'] is None or r['weeks'] < 4):
            extras.append('%s, reserve: %s' % (name, spares(r)))
        broken = [c['name'] for c in verdicts.campaigns(data) if c['status'] == 'ACTIVE'
                  and (c.get('copy') or {}).get('broken')]
        if broken:
            extras.append('%s, copy: %d active campaign%s with broken sentences (%s).'
                          % (name, len(broken), 's' if len(broken) > 1 else '', ', '.join(broken[:3])))
    if not rows and not quiet:
        return None, None
    rows.sort(key=lambda r: (RANK[r['state']], r['name'].lower()))
    marks = ['%s: %s' % (st, ', '.join(r['name'] for r in rows if r['state'] == st)[:60])
             for st in ('RED', 'AMBER') if any(r['state'] == st for r in rows)]
    title = 'SITREP | %s | %s' % (day(now), ' | '.join(marks) or 'all clear')
    need = [r for r in rows if r['state'] != 'GREEN']
    text = ['%d sending | %d need a decision | %d quiet%s' % (
        len(rows), len(need), len(quiet), ' | %d new since the last report' % total_new if total_new else '')]
    if need:
        text += ['', '*DECISIONS*']
        for r in need:
            top = r['items'][:PER_WORKSPACE_DAILY]
            text += ['- %s  *%s*: %s' % (r['state'], r['name'], top[0])] + ['  %s' % x for x in top[1:]]
            if len(r['items']) > len(top):
                text.append('  +%d more in the app' % (len(r['items']) - len(top)))
    if rows:
        old = [r['t']['day'] for r in rows if r['t'].get('day')]
        mixed = old and len(old) < len(rows)
        when = ('today; (*) the last day collected' if mixed else
                day_name(max(old), today) if old else 'today')
        text += ['', '*NUMBERS*, %s' % when, '```', table(rows, mark=mixed), '```']
    if leads:
        text += ['', '*LEADS*'] + ['- ' + x for x in leads]
    if extras:
        text += ['', '*ALSO*'] + ['- ' + e for e in extras]
    if quiet:
        named = ', '.join('%s%s' % (n, ' (%d open)' % k if k else '') for n, k in quiet)
        text += ['', '*QUIET*, nothing running: %s.' % named]
    return title, dates('\n'.join(text))


PER_WORKSPACE_DAILY = 3     # decision lines per workspace in the daily; the rest are in the app
LEADS_DAYS = 3              # a leads line under this many sending days of new leads


def table(rows, mark=False):
    # The numbers per workspace, in columns a phone keeps lined up.  mark:
    # star the rows that are the last day collected rather than today.
    body = []
    for r in rows:
        t = r['t']
        name = (r['name'] if len(r['name']) <= 17 else r['name'][:16] + '.') + ('*' if mark and t.get('day') else '')
        body.append([name, format(t['sent'], ','), pct(t['replies'], t['sent'] - t['bounces']),
                     format(t['positives'], ','), pct(t['bounces'], t['sent']),
                     '%s/%s' % (format(r['down'], ','), format(r['boxes'], ',')) if r['boxes'] else '-'])
    return columns(['', 'sent', 'repl', 'pos', 'bnc', 'mbx down'], body)


def columns(head, body):
    # Left for the name, right for the numbers, each as wide as it needs.
    width = [max(len(str(x[i])) for x in [head] + body) for i in range(len(head))]
    line = lambda x: '  '.join(str(v).ljust(width[i]) if i == 0 else str(v).rjust(width[i]) for i, v in enumerate(x))
    return '\n'.join(line(x).rstrip() for x in [head] + body)


def runway_days(data):
    # (leads left, new leads a day, days) in campaigns still taking new
    # leads, or None while the pace isn't known.
    if not data.days:
        return None
    left, pace = checks.leads_left(data, data.days[-1])
    if not left or pace <= 0:
        return None
    total = sum(left.values())
    return total, pace, total / pace


def today_counts(f):
    # Today so far, straight from the sequencer: what was sent, and the
    # replies, positives and bounces that came in.  PlusVibe and EmailBison.
    from . import watch
    seq = f.get('sequencer')
    if seq == 'emailbison':
        from . import emailbison
        return emailbison.today(watch.client(f))
    if seq == 'plusvibe':
        d = datetime.date.today().isoformat()
        h = watch.client(f).get('/account/email-stats', start_date=d, end_date=d).get('header') or {}
        return {'sent': h.get('total_sent_count') or 0, 'replies': h.get('total_reply_count') or 0,
                'positives': h.get('total_pos_reply_count') or 0,
                'bounces': h.get('total_bounce_count') or
                (h.get('sender_bounce_count') or 0) + (h.get('recipient_bounce_count') or 0)}
    return None


def weekly(cfg, now=None):
    # The week to today, every workspace in one message: the numbers against
    # the same days last week, what the domains need, the campaigns to scale
    # or stop, the changes made.  Saturday afternoon by default, once
    # Friday's US sending is done.
    from .actions import history
    now = now or datetime.datetime.now()
    today = now.date()
    start = today - datetime.timedelta(days=today.weekday())         # Monday
    prev = start - datetime.timedelta(days=7)
    rows, doms, camps, made, quiet = [], [], [], [], []
    for f in cfg['fleets']:
        try:
            data, found = load(f)
        except (OSError, ValueError):
            continue
        name = f['name']
        week = [d for d in data.days if start.isoformat() <= d <= today.isoformat()]
        # the same weekdays of last week, so a Friday compares five days to five
        before = [d for d in data.days if prev.isoformat() <= d <= (today - datetime.timedelta(days=7)).isoformat()]
        if not week:
            quiet.append(name)
            continue
        got = [sum(data.fleet(p, week)[k] for p in ('google', 'microsoft', 'apple', 'other')) for k in range(5)]
        was = sum(data.fleet(p, before)[SENT] for p in ('google', 'microsoft', 'apple', 'other'))
        acc = list(data.accounts.values())
        rows.append({'name': name, 'state': status(found),
                     't': {'sent': got[SENT], 'replies': got[REPLIES], 'positives': got[POSITIVES],
                           'bounces': got[BOUNCES], 'was': was},
                     'down': sum(a.get('down', 0) for a in acc),
                     'boxes': sum(a.get('down', 0) + a.get('active', 0) for a in acc)})
        last14 = [d for d in data.days if d >= (today - datetime.timedelta(days=13)).isoformat()]
        count = collections.Counter(r['verdict'] for r in verdicts.domains(data, last14).values())
        stop = count['burned'] + count['account_blocked']
        fix = sum(count[k] for k in ('gmail_blocked', 'microsoft_blocked', 'fix_auth', 'bad_list', 'mixed',
                                     'copy', 'refused', 'underperforming'))
        delist = count['listed'] + count['listed_low']
        if stop or fix or delist:
            doms.append((name, stop, fix, delist, count['healthy'] + count['strong']))
        running = [c for c in verdicts.campaigns(data) if c['status'] == 'ACTIVE']
        win = [c['name'] for c in running if c['verdict'] == 'winner']
        lose = [c['name'] for c in running if c['verdict'] in ('loser', 'bounces_high', 'bad_list')]
        if win or lose:
            camps.append('%s: %s' % (name, '; '.join(x for x in (
                'scale %s' % ', '.join(win[:3]) + (' +%d' % (len(win) - 3) if len(win) > 3 else '') if win else '',
                'stop or fix %s' % ', '.join(lose[:3]) + (' +%d' % (len(lose) - 3) if len(lose) > 3 else '') if lose else '')
                if x)))
        done = [h for h in history(f['db']) if start.isoformat() <= h['at'][:10] <= today.isoformat()]
        if done:
            made.append('%s: %s' % (name, '; '.join('%s (%s)' % (h['title'].rstrip('.'), h['status']) for h in done[:3])
                                    + (' +%d' % (len(done) - 3) if len(done) > 3 else '')))
    if not rows and not quiet:
        return None, None
    rows.sort(key=lambda r: -r['t']['sent'])
    sent = sum(r['t']['sent'] for r in rows)
    was = sum(r['t']['was'] for r in rows)
    pos = sum(r['t']['positives'] for r in rows)
    title = 'SITREP | WEEKLY | week to %s | %s sent, %s positive' % (day(now), format(sent, ','), format(pos, ','))
    text = ['%d sending | %d quiet | %s sent%s | %s positive' % (
        len(rows), len(quiet), format(sent, ','),
        ' (%+d%% on the same days last week)' % round(100 * (sent - was) / was) if was else '', format(pos, ','))]
    if rows:
        body = []
        for r in rows:
            t = r['t']
            body.append([r['name'] if len(r['name']) <= 17 else r['name'][:16] + '.', format(t['sent'], ','),
                         '%+d%%' % round(100 * (t['sent'] - t['was']) / t['was']) if t['was'] else '-',
                         pct(t['replies'], t['sent'] - t['bounces']), format(t['positives'], ','),
                         pct(t['bounces'], t['sent'])])
        text += ['', '*NUMBERS*, Monday to today', '```',
                 columns(['', 'sent', 'vs lw', 'repl', 'pos', 'bnc'], body), '```']
    if doms:
        body = [[name if len(name) <= 17 else name[:16] + '.', stop, fix, delist, ok]
                for name, stop, fix, delist, ok in sorted(doms, key=lambda d: (-d[1], -d[2], -d[3]))]
        text += ['', '*DOMAINS*, 14 days', '```', columns(['', 'stop', 'fix', 'delist', 'healthy'], body), '```']
    if camps:
        text += ['', '*CAMPAIGNS*'] + ['- ' + x for x in camps]
    if made:
        text += ['', '*CHANGES MADE*'] + ['- ' + x for x in made]
    if quiet:
        text += ['', '*QUIET* all week: %s.' % ', '.join(quiet)]
    text += ['', 'Replies to the last days are still coming in; judge those reply rates next week.']
    return title, dates('\n'.join(text))


def monthly(cfg, now=None):
    # The last 30 days, every workspace: the numbers, the domains it lost and
    # what to buy to keep sending, the leads left, the changes made.  On the
    # 1st by default; Inbox Sitrep keeps about a month of days.
    from .actions import history
    now = now or datetime.datetime.now()
    today = now.date()
    since = (today - datetime.timedelta(days=30)).isoformat()
    rows, infra_, leads, quiet, made = [], [], [], [], collections.Counter()
    for f in cfg['fleets']:
        try:
            data, found = load(f)
        except (OSError, ValueError):
            continue
        days = [d for d in data.days if d >= since]
        if not days:
            quiet.append(f['name'])
            continue
        got = [sum(data.fleet(p, days)[k] for p in ('google', 'microsoft', 'apple', 'other')) for k in range(5)]
        acc = list(data.accounts.values())
        rows.append({'name': f['name'], 'state': status(found),
                     't': {'sent': got[SENT], 'replies': got[REPLIES], 'positives': got[POSITIVES],
                           'bounces': got[BOUNCES]},
                     'down': sum(a.get('down', 0) for a in acc),
                     'boxes': sum(a.get('down', 0) + a.get('active', 0) for a in acc)})
        r = checks.reserve(data, found, data.days[-1])
        if r:
            infra_.append('%s: %d sending, %s' % (f['name'], r['sending'], spares(r)))
        left = runway_days(data)
        if left:
            leads.append('%s: %s sending days of new leads in running campaigns (%s left).'
                         % (f['name'], '%.0f' % left[2], format(left[0], ',')))
        for h in history(f['db']):
            if h['at'][:10] >= since:
                made[f['name']] += 1
    if not rows:
        return None, None
    rows.sort(key=lambda r: -r['t']['sent'])
    sent = sum(r['t']['sent'] for r in rows)
    pos = sum(r['t']['positives'] for r in rows)
    title = 'SITREP | MONTHLY | 30 days to %s' % day(now)
    text = ['%s sent | %s positive | %.1f per 1,000 | %d workspaces sending' % (
        format(sent, ','), format(pos, ','), 1000.0 * pos / sent if sent else 0, len(rows)),
        '', '*NUMBERS*, 30 days', '```', table(rows), '```']
    if infra_:
        text += ['', '*INFRASTRUCTURE*'] + ['- ' + x for x in infra_]
    if leads:
        text += ['', '*LEADS*'] + ['- ' + x for x in leads]
    if made:
        text += ['', '*CHANGES MADE*: %s.' % ', '.join('%s %d' % kv for kv in made.most_common())]
    if quiet:
        text += ['', '*QUIET* all month: %s.' % ', '.join(quiet)]
    return title, dates('\n'.join(text))

