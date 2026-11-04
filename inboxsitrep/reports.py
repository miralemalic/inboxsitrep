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
    return '%.1f%%' % (100.0 * n / d) if d else '–'


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
    # (title, text) for every workspace.  today_live(fleet cfg) -> PlusVibe's
    # numbers for today, or None.  The title is what a phone shows: it names
    # the workspaces that need you.
    now = now or datetime.datetime.now()
    today = now.date()
    rows, extras = [], []
    total_new = 0
    # Many workspaces: amber ones get a line each, only red ones a block.
    compact = len(cfg['fleets']) > 4
    for f in cfg['fleets']:
        try:
            data, found = load(f)
        except (OSError, ValueError):
            continue
        if not data.days:
            continue
        name = f['name']
        fresh = set(store.get_meta(f['db'], 'fresh_ids', []) or [])
        quiet = (today - datetime.date.fromisoformat(data.days[-1])).days
        live = None
        if today_live and quiet <= 3:
            try:
                live = today_live(f)
            except (OSError, RuntimeError, ValueError):
                live = None
        # Today so far only once it means something; sending to the US from
        # Europe, a morning report would otherwise show an empty day.
        if live and live['sent'] < LIVE_ENOUGH * max(1, from_data(data, data.days[-1:])['sent']):
            live = None
        new, old = decisions(data, found, name, fresh, today)
        total_new += len(new)
        items = ['NEW ' + x for x in new] + old
        state = 'RED' if quiet > 3 else status(found)
        if quiet > 3:
            nums = 'Nothing sent for %d days.' % quiet
        elif live:
            nums = 'Today so far: ' + numbers(from_live(live), short=True)
        else:
            nums = '%s: %s' % (day_name(data.days[-1], today), numbers(from_data(data, data.days[-1:]), short=True))
        top = items[:3 if compact else 4]
        lines = ['*%s* · %s' % (name, state), nums] + ['• ' + x for x in top]
        if len(items) > len(top):
            lines.append('• +%d more in the app' % (len(items) - len(top)))
        first = short(items[0].split('  → ')[0], 90) if items else 'nothing sent for %d days' % quiet
        rows.append((RANK[state], name, state, lines, first, len(items)))
        r = checks.reserve(data, found, data.days[-1])
        if r and r['buy'] and (r['weeks'] is None or r['weeks'] < 4):
            extras.append('%s, reserve: %s' % (name, spares(r)))
        broken = [c['name'] for c in verdicts.campaigns(data) if c['status'] == 'ACTIVE'
                  and (c.get('copy') or {}).get('broken')]
        if broken:
            extras.append('%s, copy: %d active campaign%s with broken sentences (%s).'
                          % (name, len(broken), 's' if len(broken) > 1 else '', ', '.join(broken[:3])))
    if not rows:
        return None, None
    rows.sort(key=lambda r: r[0])
    marks = ['%s: %s' % (st, names(rows, st)) for st in ('RED', 'AMBER') if any(r[2] == st for r in rows)]
    title = 'SITREP · %s, %s · %s' % (day(now), now.strftime('%H:%M'), ' · '.join(marks) or 'all clear')
    count = collections.Counter(r[2] for r in rows)
    text = ['%d workspace%s: %s.%s' % (len(rows), '' if len(rows) == 1 else 's',
                                       ', '.join('%d %s' % (count[st], st) for st in ('RED', 'AMBER', 'GREEN') if count[st]),
                                       '  %d new since the last report.' % total_new if total_new else '')]
    for rank, name, state, lines, first, n in rows:
        if state == 'GREEN':
            continue
        if compact and state == 'AMBER':
            continue
        text += [''] + lines
    if compact and count['AMBER']:
        text += ['', '*AMBER*'] + ['• %s: %s%s' % (r[1], r[4], ' (+%d more)' % (r[5] - 1) if r[5] > 1 else '')
                                   for r in rows if r[2] == 'AMBER']
    if count['GREEN']:
        text += ['', 'GREEN, nothing needs you: %s.' % ', '.join(r[1] for r in rows if r[2] == 'GREEN')]
    if extras:
        text += ['', '*Also*'] + ['• ' + e for e in extras]
    return title, dates('\n'.join(text))


def weekly(cfg, now=None):
    # The week to today, for every workspace.  Saturday morning by default:
    # US sending on Friday ends after midnight in Europe.
    from .actions import history
    now = now or datetime.datetime.now()
    today = now.date()
    start = today - datetime.timedelta(days=today.weekday())         # Monday
    prev = start - datetime.timedelta(days=7)
    parts, lines = [], []
    for f in cfg['fleets']:
        try:
            data, found = load(f)
        except (OSError, ValueError):
            continue
        week = [d for d in data.days if start.isoformat() <= d <= today.isoformat()]
        # the same weekdays of last week, so a Friday compares five days to five
        before = [d for d in data.days if prev.isoformat() <= d <= (today - datetime.timedelta(days=7)).isoformat()]
        if not week:
            parts.append('*%s*\nNothing sent this week.' % f['name'])
            continue
        t, b = from_data(data, week), from_data(data, before)
        change = ''
        if b['sent']:
            change = ' (%+d%% on the same days last week)' % round(100 * (t['sent'] - b['sent']) / b['sent'])
        state = status(found)
        block = ['*%s*  %s' % (f['name'], state), 'Week: %s' % numbers(t, change)]
        mix = infra(data, week)
        if mix:
            block.append(mix.replace(', 7 days', ''))
        last14 = [d for d in data.days if d >= (today - datetime.timedelta(days=13)).isoformat()]
        v = verdicts.domains(data, last14)
        count = collections.Counter(r['verdict'] for r in v.values())
        stop = count['burned'] + count['account_blocked']
        fix = sum(count[k] for k in ('gmail_blocked', 'microsoft_blocked', 'fix_auth', 'bad_list', 'mixed',
                                     'copy', 'refused', 'underperforming'))
        delist = count['listed'] + count['listed_low']
        block.append('Domains, 14 days: %d to stop, %d to fix, %d to delist, %d healthy%s' % (
            stop, fix, delist, count['healthy'] + count['strong'],
            '; %d with sends but no reply or bounce data, to check' % count['silent'] if count['silent'] else ''))
        camps = [c for c in verdicts.campaigns(data) if c['status'] == 'ACTIVE']
        win = [c['name'] for c in camps if c['verdict'] == 'winner']
        lose = [c['name'] for c in camps if c['verdict'] in ('loser', 'bounces_high', 'bad_list')]
        if win:
            block.append('Campaigns to scale: %s' % ', '.join(win[:3]) + (' and %d more' % (len(win) - 3) if len(win) > 3 else ''))
        if lose:
            block.append('Campaigns to stop or fix: %s' % ', '.join(lose[:3]) + (' and %d more' % (len(lose) - 3) if len(lose) > 3 else ''))
        r = checks.reserve(data, found, data.days[-1])
        if r:
            block.append('Reserve: %d domains sending, %s' % (r['sending'], spares(r)))
        made = [h for h in history(f['db']) if start.isoformat() <= h['at'][:10] <= today.isoformat()]
        if made:
            block.append('Changes made: %s' % '; '.join('%s (%s)' % (h['title'].rstrip('.'), h['status']) for h in made[:4]))
        new, old = decisions(data, found, f['name'], (), today)
        block.append('Open decisions: %d (the daily SITREP lists them)' % (len(new) + len(old)))
        parts.append('\n'.join(block))
        lines.append('%s %s: %s sent, %s positive%s' % (
            f['name'], state, format(t['sent'], ','), format(t['positives'], ','),
            ', %d to scale' % len(win) if win else ''))
    if not parts:
        return None, None
    shown = [f['name'] for f in cfg['fleets']]
    title = 'Weekly SITREP · week to %s · %s' % (day(now), ', '.join(shown[:3]) + (' +%d' % (len(shown) - 3) if len(shown) > 3 else ''))
    text = '\n'.join(['*Bottom line*'] + ['• ' + l for l in lines] + [
        'Replies to the last days are still coming in; judge those reply rates next week.', '']) + '\n' + '\n\n'.join(parts)
    return title, dates(text)
