# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# Read an EmailBison workspace into Inbox Sitrep's counts.
#
# Written from EmailBison's published OpenAPI reference (served by every
# instance at /api/reference.openapi) and run read-only on live
# workspaces, from a few hundred sender emails to nine thousand.  Each
# customer has their own address (https://send.example.com), and a Bearer
# token per workspace from Settings > Developer API.  Reads with GET only.
#
# EmailBison gives the sender email list (status, daily limit, warmup),
# a breakdown of campaign events by date that can be narrowed to a set of
# sender emails, and a replies inbox with a "bounced" folder holding the
# bounce messages.  Sends per day come per domain from the breakdown; who
# hosts the recipients comes from the bounce and reply addresses.
#
# What the reference doesn't say: every list comes 15 a page whatever
# per_page asks for, page numbers stop at 1000, and the replies have no
# date filter but come newest first.  A big workspace has more than 15,000
# bounces, so the replies are read by cursor and only back to the window.

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
TIMEOUT = 30        # seconds to wait for one page
LIVE_MOST = 600     # replies read per folder in one live look


class Stay(urllib.request.HTTPRedirectHandler):
    # Python follows a redirect with the Authorization header still on, to
    # wherever it points; the key goes to your instance and nowhere else.
    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(Stay)


class Client:

    def __init__(self, key, base, verbose=False):
        self.key = key
        # The address as EmailBison's API page shows it ends in /api; the
        # paths here start with /api already.
        self.base = re.sub(r'/api$', '', base.strip().rstrip('/'))
        if not re.match(r'https://|http://(localhost|127\.0\.0\.1)(:\d+)?$', self.base):
            raise ValueError("EmailBison: use your instance's https:// address, so the key isn't sent in the clear")
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
                # A page comes back in under a second, the slowest in a few;
                # one that hangs is asked again rather than waited on.
                with OPENER.open(req, timeout=TIMEOUT) as r:
                    return json.loads(r.read() or b'{}')
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                code = getattr(e, 'code', None)
                if code is not None and code < 500 and code != 429:
                    raise
                time.sleep(2 ** attempt)
        raise RuntimeError('gave up on %s' % path)

    def get(self, path, **params):
        return self.request('GET', path, params)

    def pages(self, path, limit=500, cursor=False, **params):
        # length_aware pagination: data + meta.last_page.  EmailBison serves
        # 15 a page whatever per_page says and refuses pages past 1000 (422),
        # so long lists (the replies) follow meta.next_cursor instead.
        if cursor:
            after = None
            while True:
                body = self.get(path, pagination_type='cursor', **dict(params, **({'cursor': after} if after else {})))
                for item in body.get('data') or []:
                    yield item
                after = (body.get('meta') or {}).get('next_cursor')
                if not body.get('data') or not after:
                    return
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


def recipient(r, text, dom):
    # Who a bounce was for: the lead it belongs to, or failing that the
    # first address in the message that is neither ours nor a mail server.
    lead = (r.get('lead') or {}).get('email')
    if lead:
        return lead
    return next((e for e in EMAIL.findall(text) if not SERVICE.search(e)
                 and domain_of(e) != dom), '')


def collect(api, days=21, today=None, say=print, lookup=True, prior=None):
    # prior: the last collection, for the leads' pace between checks.
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
    older = 0
    for r in api.pages('/api/replies', cursor=True, folder='bounced'):
        day = (r.get('date_received') or '')[:10]
        # Newest first: once a run of them is from before the window, the
        # rest are too (a big workspace holds a year of bounces).
        older = older + 1 if day and day < start else 0
        if older >= 50:
            break
        dom = ids.get(r.get('sender_email_id'))
        if not dom or not (start <= day <= end):
            continue
        text = r.get('text_body') or r.get('raw_body') or ''
        bounced.append((day, dom, domain_of(recipient(r, text, dom)), text))
    # The replies say who hosts the people being written to now; the same
    # window keeps a big inbox from taking an hour (old pages come slowly).
    older = 0
    for r in api.pages('/api/replies', cursor=True, folder='inbox'):
        day = (r.get('date_received') or '')[:10]
        older = older + 1 if day and day < start else 0
        if older >= 50:
            break
        if day and day < start:
            continue
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
        # A spare: connected mailboxes on a domain no campaign sent from in
        # the whole window (EmailBison doesn't say which are in a campaign).
        busy = any(n[0] for (d, _), n in totals.items() if d == dom)
        data.accounts[dom]['idle'] = 0 if busy else data.accounts[dom].get('active', 0)
    say('campaigns and their leads')
    campaigns(api, data, prior, today)
    say('%d domains, %d days, %d API calls' % (len(data.domains()), len(data.days), api.calls))
    return data


SNAPSHOTS = 15      # days of lead counts kept, for the pace
IDLE_DAYS = 2       # sending days an active campaign may start nobody before it counts as stalled


def campaigns(api, data, prior=None, today=None):
    # The campaigns and their leads.  Leads left counts only an active
    # campaign that is still taking new leads: one paused, done, or active
    # but no longer starting anyone holds leads nobody will mail.
    # EmailBison gives each campaign's totals as they are now; the pace is
    # what moved between one day's look and the next, kept in raw.
    day = today or datetime.date.today()
    snaps = dict(((prior.raw if prior is not None else None) or {}).get('contacted') or {})
    now = {}
    for c in api.pages('/api/campaigns'):
        status = (c.get('status') or '').lower()
        if status in ('draft', 'archived'):
            continue
        cid = str(c['id'])
        data.campaigns[cid] = {'name': c.get('name'), 'status': status.upper(),
                               'lead_count': c.get('total_leads') or 0,
                               'lead_contacted_count': c.get('total_leads_contacted') or 0,
                               'sent_count': c.get('emails_sent') or 0,
                               'replied_count': c.get('unique_replies') or 0,
                               'positive_reply_count': c.get('interested') or 0,
                               'bounced_count': c.get('bounced') or 0}
        if status == 'active':
            now[cid] = c.get('total_leads_contacted') or 0
    snaps[day.isoformat()] = now
    snaps = {d: snaps[d] for d in sorted(snaps)[-SNAPSHOTS:]}
    data.raw['contacted'] = snaps
    days = sorted(snaps)
    date = datetime.date.fromisoformat
    steps = []
    for a, b in zip(days, days[1:]):
        moved = {cid: max(0, n - snaps[a][cid]) for cid, n in snaps[b].items() if cid in snaps[a]}
        steps.append((a, b, moved))
        # A day's new leads: from one day's last look to the next day's.  A
        # gap (the app was closed) says nothing about any one day; a day
        # that started nobody (a weekend) is left out of the pace.
        if (date(b) - date(a)).days == 1:
            data.fresh[b] = sum(moved.values())
    # Stalled: active, but it started nobody on the last IDLE_DAYS looks
    # that the rest of the workspace did.  A weekend, when nothing sends,
    # stalls nobody.
    busy = [m for a, b, m in steps if sum(m.values())][-IDLE_DAYS:]
    if len(busy) == IDLE_DAYS:
        for cid in now:
            if all(cid in m and not m[cid] for m in busy):
                data.campaigns[cid]['idle'] = True
    return data.campaigns


DOWN_NAMED = 5          # mailboxes named in an alert; more are counted by domain
STOPPED_HOURS = 2       # no sends this long inside the sending hours
DONE_SHARE = .8         # unless today already sent this share of a usual day
SPIKE_TIMES = 3         # bounces in the last hour against the usual rate
SPIKE_SENT = 200        # out of at least this many sent in that hour
SPIKE_BOUNCES = 10      # and at least this many bounces
DAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')


def today(api, day=None, camps=None):
    # Today so far for the whole workspace: what its campaigns sent, and the
    # replies, positives and bounces that came in.  EmailBison counts by
    # campaign; drafts never send.
    day = day if isinstance(day, str) else (day or datetime.date.today()).isoformat()
    camps = list(api.pages('/api/campaigns')) if camps is None else camps
    ids = [c['id'] for c in camps if (c.get('status') or '').lower() != 'draft']
    if not ids:
        return {'sent': 0, 'replies': 0, 'positives': 0, 'bounces': 0}
    rows = api.get('/api/campaign-events/stats', start_date=day, end_date=day,
                   **{'campaign_ids[]': ids}).get('data') or []
    n = lambda label: sum(series(rows, label).values())
    return {'sent': n('sent'), 'replies': n('replied'), 'positives': n('interested'), 'bounces': n('bounced')}


def hours(api, camps, now):
    # Today's sending hours of the active campaigns, as times here: each
    # campaign keeps its own days, hours and timezone.
    from zoneinfo import ZoneInfo
    out = []
    for c in camps:
        if (c.get('status') or '').lower() != 'active':
            continue
        try:
            s = api.get('/api/campaigns/%s/schedule' % c['id']).get('data') or {}
            zone = ZoneInfo(s['timezone'])
            there = now.astimezone(zone).date()
            if not s.get(DAYS[there.weekday()]):
                continue
            a, b = (datetime.datetime.combine(there, datetime.time.fromisoformat(s[k]), zone).astimezone(now.tzinfo)
                    for k in ('start_time', 'end_time'))
        except (OSError, RuntimeError, KeyError, ValueError, TypeError):
            continue
        out.append([a.isoformat(timespec='minutes'), b.isoformat(timespec='minutes')])
    return out


def pulse(api, state, usual=None, now=None):
    # Between checks, every 15 minutes, in a few calls: what can't wait for
    # the report.  Mailboxes disconnecting, a workspace that stopped sending
    # inside its hours, bounces spiking on a list being sent right now.
    # state is kept by the caller between looks; usual is what the last
    # check saw ({'bounce_rate', 'sent'}: the rate over two weeks, a usual
    # day's sends).  Returns [(title, body)], each problem once a day.
    usual = usual or {}
    now = now or datetime.datetime.now().astimezone()
    day = now.date().isoformat()
    if (state.get('today') or {}).get('day') != day:
        state['today'] = {'day': day, 'samples': [], 'alerted': [], 'hours': None}
    t = state['today']
    camps = list(api.pages('/api/campaigns'))
    got = today(api, day, camps)
    t['samples'] = (t['samples'] + [[now.isoformat(timespec='minutes'), got['sent'], got['bounces']]])[-120:]
    if t['hours'] is None:
        t['hours'] = hours(api, camps, now)
    out = []

    def once(key, title, body):
        if key not in t['alerted']:
            t['alerted'].append(key)
            out.append((title, body))

    def back(h):
        # the latest look at least h hours ago, today
        cut = now - datetime.timedelta(hours=h)
        old = [x for x in t['samples'] if datetime.datetime.fromisoformat(x[0]) <= cut]
        return old[-1] if old else None

    # Disconnected mailboxes: every one is news, named, once a day each.
    # The count is one call; the list, to say which, is read only when the
    # count moved (one dropping as another comes back in the same quarter
    # hour goes unseen until the report).
    down = (api.get('/api/sender-emails', status='not_connected').get('meta') or {}).get('total') or 0
    if not t.get('boxes'):
        t['boxes'] = (api.get('/api/sender-emails').get('meta') or {}).get('total') or 0
    known, last = state.get('down_list'), state.get('last')
    if known is None or down != state.get('down'):
        now_down = sorted({x.get('email') for x in api.pages('/api/sender-emails', status='not_connected')} - {None})
    else:
        now_down = known
    gone = [e for e in now_down if e not in set(known or ()) and e not in set(t.get('down_told') or ())] \
        if known is not None else []
    state['down_list'], state['down'], state['last'] = now_down, down, now.isoformat(timespec='minutes')
    if gone:
        t['down_told'] = (t.get('down_told') or []) + gone
        doms = collections.Counter(domain_of(e) for e in gone)
        which = (', '.join(gone) if len(gone) <= DOWN_NAMED else
                 'on ' + ', '.join('%s (%d)' % kv for kv in doms.most_common(4)) +
                 (' and %d more domains' % (len(doms) - 4) if len(doms) > 4 else ''))
        then = datetime.datetime.fromisoformat(last) if last else now
        since = then.strftime('%H:%M') if then.date() == now.date() else then.strftime('%a %H:%M')
        out.append(('%d mailbox%s disconnected since %s' % (len(gone), 'es' if len(gone) > 1 else '', since),
                    '%s.  %s down now of %s.\nACTION: reconnect %s in EmailBison, Email Accounts.'
                    % (which, format(down, ','), format(t['boxes'], ','), 'them' if len(gone) > 1 else 'it')))

    open_ = [(datetime.datetime.fromisoformat(a), datetime.datetime.fromisoformat(b)) for a, b in t['hours']
             if datetime.datetime.fromisoformat(a) <= now <= datetime.datetime.fromisoformat(b)]
    then = back(STOPPED_HOURS)
    if (open_ and then and min(a for a, b in open_) <= now - datetime.timedelta(hours=STOPPED_HOURS)
            and got['sent'] == then[1] and got['sent'] < DONE_SHARE * (usual.get('sent') or 0)):
        once('stopped', 'Sent nothing since %s, inside its sending hours' % then[0][11:16],
             '%s sent today against %s on a usual day; %d active campaign%s.\nACTION: check the campaigns '
             'and the mailboxes in EmailBison: paused, out of leads, or disconnected.'
             % (format(got['sent'], ','), format(round(usual.get('sent') or 0), ','), len(open_), '' if len(open_) == 1 else 's'))

    hour = back(1)
    rate = usual.get('bounce_rate')
    if hour and rate is not None:
        sent, bounced = got['sent'] - hour[1], got['bounces'] - hour[2]
        if sent >= SPIKE_SENT and bounced >= SPIKE_BOUNCES and bounced / sent >= max(SPIKE_TIMES * rate, .03):
            once('spike:%d' % int(bounced / sent * 20), 'Bounces at %.1f%% in the last hour, %.0fx the usual'
                 % (100.0 * bounced / sent, bounced / sent / rate if rate else SPIKE_TIMES),
                 '%d of %s bounced since %s; usually %.1f%%.\nACTION: find the campaign that started a new '
                 'list today, pause it and verify the list.'
                 % (bounced, format(sent, ','), hour[0][11:16], 100.0 * rate))
    return out


def poll(api, path, since, say=print):
    # Live mode: bounces and replies since `since` (UTC, as live keeps it),
    # newest first, for the alerts that can't wait for the next check.  A
    # bounce is addressed to the sender email that sent the mail; of the
    # person it was for, only who hosts them is kept.
    from . import live
    got = []
    for folder in ('bounced', 'inbox'):
        for n, r in enumerate(api.pages('/api/replies', cursor=True, folder=folder)):
            ts = (r.get('date_received') or '')[:19].replace('T', ' ')
            if ts <= since or n >= LIVE_MOST:
                break
            ours = r.get('primary_to_email_address')
            if folder == 'bounced':
                text = r.get('text_body') or r.get('raw_body') or ''
                got.append(live.event(ts, 'poll', 'bounce', ours, recipient(r, text, domain_of(ours)), text))
            else:
                got.append(live.event(ts, 'poll', 'positive' if r.get('interested') else 'reply',
                                      ours, r.get('from_email_address')))
    n = live.store(path, got)
    say('%d new events' % n)
    return got
