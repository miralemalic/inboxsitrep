# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# Read a PlusVibe workspace into inboxsitrep's counts.
#
# Read-only by construction: the client below can only send GET requests.
# There is no other method to call by mistake.
#
# PlusVibe gives three views that each miss something: per-mailbox daily
# totals (no recipient provider), per-provider daily totals for the whole
# workspace (no domain), and lead records (provider and domain, but only
# for leads that bounced or replied).  inboxsitrep needs sends per domain per
# provider per day, so it takes each domain's provider mix from the
# campaigns its mailboxes send for, then balances the split until it adds up
# both ways: to every domain's daily total and to every provider's.

import json
import time
import concurrent.futures
import datetime
import threading
import collections
import urllib.error
import urllib.parse
import urllib.request

from . import copycheck
from .data import Data
from .bounces import mask


API = 'https://api.plusvibe.ai/api/v1'
UA = 'curl/8.9.1'           # the API answers 403 to Python's own User-Agent
PER_SECOND = 4              # the limit is 5
PROVIDERS = {'GOOGLE_WORKSPACE': 'google', 'MICROSOFT365': 'microsoft',
             'REGULAR_ACCOUNT': 'other'}
APPLE = ('icloud.com', 'me.com', 'mac.com')


class Client:

    def __init__(self, key, workspace, verbose=False):
        self.key = key
        self.workspace = workspace
        self.verbose = verbose
        self.lock = threading.Lock()
        self.last = 0.0
        self.calls = 0

    def get(self, path, **params):
        params['workspace_id'] = self.workspace
        url = API + path + '?' + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={'x-api-key': self.key,
                                                   'User-Agent': UA})
        for attempt in range(6):
            with self.lock:
                wait = self.last + 1 / PER_SECOND - time.time()
                if wait > 0:
                    time.sleep(wait)
                self.last = time.time()
                self.calls += 1
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    return json.loads(r.read())
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                code = getattr(e, 'code', None)
                if code is not None and code < 500 and code != 429:
                    raise
                if self.verbose:
                    print('  retry %s %s (%s)' % (path, attempt + 1, e))
                time.sleep(2 ** attempt)
        raise RuntimeError('gave up on %s' % path)


def domain_of(email):
    return (email or '').rsplit('@', 1)[-1].lower()


def provider_of(mx, email):
    if domain_of(email) in APPLE:
        return 'apple'
    return PROVIDERS.get(mx, 'other')


def pages(fetch, workers=4, say=None, what='pages'):
    # Fetch numbered pages a few at a time until one comes back short.  The
    # client's limiter keeps the pace; this only keeps requests in flight,
    # because each one takes a second or two to answer.
    out, n = [], 1
    with concurrent.futures.ThreadPoolExecutor(workers) as pool:
        while True:
            wave = list(pool.map(fetch, range(n, n + workers)))
            for items, more in wave:
                out.extend(items)
            if say and n % (workers * 5) == 1 and n > 1:
                say('%s: %s so far' % (what, format(len(out), ',')))
            if not all(more for _, more in wave):
                return out
            n += workers


def accounts(api, say=None):
    def fetch(n):
        page = api.get('/account/list', skip=(n - 1) * 500, limit=500).get('accounts') or []
        return page, len(page) == 500
    return pages(fetch, say=say, what='mailboxes read')


def daily_totals(api, start, end, fresh=None, say=None):
    # Per mailbox, per day: sent, replies (without out-of-office), positives,
    # bounces, sender-side bounces.  New leads contacted go into `fresh`.
    out = collections.defaultdict(lambda: [0, 0, 0, 0, 0])
    fresh = fresh if fresh is not None else {}

    def fetch(n):
        body = api.get('/account/email-stats/bulk', start_date=start, end_date=end,
                       page=n, limit=100, include_chart='true')
        got = body.get('accounts') or []
        return got, bool(body.get('has_more')) and bool(got)

    for acc in pages(fetch, say=say, what='mailbox stats read'):
        dom = domain_of(acc.get('email'))
        for c in acc.get('chart') or []:
            day = c['date'][:10]
            row = out[dom, day]
            row[0] += c.get('total_sent_count') or 0
            row[1] += max(0, (c.get('total_reply_count') or 0)
                          - (c.get('total_ooo_reply_count') or 0))
            row[2] += c.get('total_pos_reply_count') or 0
            row[3] += c.get('total_bounce_count') or 0
            row[4] += c.get('sender_bounce_count') or 0
            fresh[day] = fresh.get(day, 0) + (c.get('total_new_lead_contacted_count') or 0)
    return out


def provider_totals(api, start, end):
    out = {}
    for mx, prov in PROVIDERS.items():
        body = api.get('/account/email-stats', start_date=start, end_date=end,
                       recp_provider=mx)
        for c in body.get('chart') or []:
            out[prov, c['date'][:10]] = c.get('total_sent_count') or 0
    return out


def campaigns(api):
    out, skip = [], 0
    while True:
        page = api.get('/campaign/list-all', skip=skip, limit=100)
        if isinstance(page, dict):
            page = page.get('items') or page.get('data') or []
        out.extend(page)
        if len(page) < 100:
            return out
        skip += 100


# What a campaign's own counters say, kept for the lead runway and for
# PlusVibe's bounce guard (it pauses a campaign past its bounce limit).
CAMPAIGN = ('name', 'camp_name', 'status', 'lead_count', 'lead_contacted_count',
            'completed_lead_count', 'sent_count', 'replied_count',
            'positive_reply_count', 'negative_reply_count', 'unsubscribed_count',
            'bounced_count', 'sender_bounced_count', 'bounce_rate_limit',
            'last_paused_at_bounced', 'last_lead_sent', 'is_emailopened_tracking',
            'send_risky_email', 'is_esp_match', 'is_unsubscribed_link')


def state_of(acc):
    # ACTIVE, or something that stops the mailbox sending (ALERT is
    # PlusVibe's word for a mailbox whose connection broke).
    if (acc.get('status') or 'ACTIVE').upper() != 'ACTIVE':
        return 'down'
    if acc.get('in_recovery'):
        return 'recovery'
    return 'active'


def campaign_mix(api, cid):
    # Who a campaign sends to, from a sample of the leads it has contacted.
    count = collections.Counter()
    for status in ('COMPLETED', 'CONTACTED'):
        leads = api.get('/lead/workspace-leads', campaign_id=cid,
                        status=status, limit=500, page=1)
        for lead in leads or []:
            count[provider_of(lead.get('mx'), lead.get('email'))] += 1
        if sum(count.values()) >= 200:
            break
    n = sum(count.values())
    return {p: c / n for p, c in count.items()} if n else None


def mapped(api, campaign, before, cache, today):
    # Whether the copy's merge fields are on the campaign's leads: 100 of
    # them from the lead list, three read in full for their custom fields.
    # Read again when the copy changes, or after a week.
    import hashlib
    if not copycheck.fields(campaign.get('sequences'))[0]:
        return []                       # no lead fields in the copy, nothing to read
    cid = campaign['id']
    sig = hashlib.sha1(json.dumps(campaign.get('sequences'), sort_keys=True).encode()).hexdigest()[:16]
    old = before.get(cid)
    if old and old.get('sig') == sig and old.get('day', '') >= (today - datetime.timedelta(days=7)).isoformat():
        cache[cid] = old
        return old['issues']
    try:
        sample = api.get('/lead/workspace-leads', campaign_id=cid, limit=100, page=1) or []
        sample = sample if isinstance(sample, list) else sample.get('data') or []
        details = []
        for lead in sample[:3]:
            got = api.get('/lead/get', email=lead.get('email'))
            got = got if isinstance(got, list) else [got]
            hit = next((g for g in got if g.get('campaign') == cid), got[0] if got else None)
            if hit and hit.get('lead_data'):
                details.append(hit['lead_data'])
        issues = copycheck.mapping(campaign.get('sequences'), sample, details) if sample else []
    except (OSError, RuntimeError, ValueError):
        return old['issues'] if old else []
    cache[cid] = {'sig': sig, 'day': today.isoformat(), 'issues': issues}
    return issues


def sweep(api, status, start, say=None):
    # Leads with a status, newest change first, until changes predate start.
    def fetch(n):
        leads = api.get('/lead/workspace-leads', status=status, limit=1000, page=n,
                        sort='modified_at', direction='desc') or []
        more = len(leads) == 1000 and (leads[-1].get('modified_at') or '')[:10] >= start
        return leads, more
    return pages(fetch, say=say, what='%s leads read' % status.lower())


def balance(guess, dom_totals, prov_totals, rounds=20):
    # Iterative proportional fitting: scale the domain x provider split
    # until rows match each domain's total and columns each provider's.
    split = dict(guess)
    for _ in range(rounds):
        by_prov = collections.Counter()
        for (dom, prov), v in split.items():
            by_prov[prov] += v
        for key in split:
            want = prov_totals.get(key[1])
            if want and by_prov[key[1]]:
                split[key] *= want / by_prov[key[1]]
        by_dom = collections.Counter()
        for (dom, prov), v in split.items():
            by_dom[dom] += v
        for key in split:
            want = dom_totals.get(key[0], 0)
            split[key] = split[key] * want / by_dom[key[0]] if by_dom[key[0]] else 0
    return split


def collect(api, days=31, today=None, say=print, prior=None):
    # With `prior` (the last collection of this workspace) only what changed
    # is fetched: the last few days of totals, which still move as late
    # bounces arrive, and the leads that changed since the last sweep.  On
    # a 50,000-mailbox workspace that is minutes instead of most of an hour.
    today = today or datetime.date.today()
    end = (today - datetime.timedelta(days=1)).isoformat()
    start = (today - datetime.timedelta(days=days)).isoformat()
    raw = (prior.raw if prior is not None else None) or {}
    if raw.get('start', '9999') > start:
        # The window grew (or the last read predates this note): read it
        # all once, so the earlier days are there.
        raw = {}
    since = raw.get('watermark')
    recent = start if not since else max(start, (today - datetime.timedelta(days=4)).isoformat())
    data = Data()

    say('mailboxes')
    boxes = accounts(api, say)
    camp_of = collections.defaultdict(list)
    health = collections.defaultdict(list)
    hosts = collections.defaultdict(collections.Counter)
    for a in boxes:
        dom = domain_of(a.get('email'))
        payload = a.get('payload') or {}
        data.mailboxes[dom] = data.mailboxes.get(dom, 0) + 1
        states = data.accounts.setdefault(dom, {})
        st = state_of(a)
        states[st] = states.get(st, 0) + 1
        warm = (a.get('warmup_status') or 'ACTIVE').upper() == 'ACTIVE'
        if not warm and st == 'active':
            states['warmup_off'] = states.get('warmup_off', 0) + 1
        # Warming and in no campaign: a spare, ready to swap in.
        if warm and st == 'active' and not payload.get('cmps'):
            states['idle'] = states.get('idle', 0) + 1
        hosts[dom][PROVIDERS.get(a.get('provider'), 'other')] += 1
        camp_of[dom].extend(c['id'] for c in payload.get('cmps') or [])
        h = (payload.get('analytics') or {}).get('health_scores') or {}
        g, m = h.get('7d_google_warmup_health'), h.get('7d_microsoft_warmup_health')
        # -1 means "not scored yet", not "worse than zero"
        if g is not None and g >= 0:
            health[dom].append((g, m if m is not None and m >= 0 else None))
    for dom, hs in health.items():
        gs = [g for g, _ in hs]
        ms = [m for _, m in hs if m is not None]
        data.health[dom] = (round(sum(gs) / len(gs), 1),
                            round(sum(ms) / len(ms), 1) if ms else None)
        data.history[dom][today.isoformat()] = data.health[dom]
    for dom, c in hosts.items():
        data.host[dom] = c.most_common(1)[0][0]

    say('daily totals per mailbox, %s to %s' % (recent, end))
    totals = {k: v for k, v in decode(raw.get('totals')).items() if start <= k[1] < recent}
    fresh = {d: n for d, n in (raw.get('fresh') or {}).items() if start <= d < recent}
    totals.update(daily_totals(api, recent, end, fresh, say))
    data.fresh = fresh
    say('daily totals per recipient provider')
    by_prov = provider_totals(api, start, end)

    say('campaigns, their copy and merge fields')
    mapcache = {}
    camps = campaigns(api)
    for c in camps:
        if c.get('status') in ('ACTIVE', 'PAUSED') or (c.get('last_lead_sent') or '')[:10] >= start:
            data.campaigns[c['id']] = {k: c.get(k) for k in CAMPAIGN if k in c}
            # The copy itself is large (hundreds of KB with spintax); keep
            # only what is wrong with it.
            issues = copycheck.check(c.get('sequences'), c)
            if c.get('status') == 'ACTIVE':
                issues += mapped(api, c, raw.get('mapping') or {}, mapcache, today)
            data.campaigns[c['id']]['copy'] = copycheck.summary(issues)
    live = {c['id'] for c in camps if (c.get('last_lead_sent') or '')[:10] >= start}
    mixes = {k: v for k, v in (raw.get('mixes') or {}).items() if k in live}
    for cid in sorted({c for cs in camp_of.values() for c in cs} & live - set(mixes)):
        mixes[cid] = campaign_mix(api, cid)
    fleet_mix = collections.Counter()
    for m in mixes.values():
        for p, v in (m or {}).items():
            fleet_mix[p] += v

    def mix_of(dom):
        got = collections.Counter()
        for cid in camp_of.get(dom, ()):
            for p, v in (mixes.get(cid) or {}).items():
                got[p] += v
        got = got or fleet_mix or collections.Counter(google=1)
        n = sum(got.values())
        return {p: v / n for p, v in got.items()}

    # Leads that bounced or replied.  Each is counted once, by its id and
    # status, however many sweeps see it.
    leads = {k: v for k, v in decode(raw.get('leads')).items() if start <= k[2] <= end}
    # Leads already counted, with the day they were sent, so the record
    # can be pruned to the window instead of growing forever.
    seen = raw.get('seen') or {}
    if isinstance(seen, list):              # written by 0.3.0 before pruning
        seen = dict.fromkeys(seen, start)
    seen = {k: d for k, d in seen.items() if d >= start}
    # Earlier bounce texts carry over only when we know which leads they
    # came from; otherwise the full sweep below reads them all again.
    data.bounces = [b for b in (prior.bounces if prior is not None and since else [])
                    if start <= b[0] <= end]
    mark = since or start
    say('bounced and replied leads changed since %s' % mark[:16])
    newest = since or ''
    for status in ('BOUNCED', 'REPLIED'):
        for lead in sweep(api, status, mark[:10], say):
            newest = max(newest, lead.get('modified_at') or '')
            key = '%s:%s' % (status[0], lead.get('_id') or lead.get('email'))
            day = (lead.get('last_sent_at') or '')[:10]
            if key in seen or not (start <= day <= end):
                continue
            seen[key] = day
            dom = domain_of(lead.get('email_acc_name'))
            prov = provider_of(lead.get('mx'), lead.get('email'))
            row = leads.setdefault((dom, prov, day), [0, 0, 0, 0])
            if status == 'REPLIED':
                row[0] += 1
                row[1] += (lead.get('label') or '').upper() == 'INTERESTED'
            else:
                row[2] += 1
                sender = (lead.get('bounce_type') or '').upper() == 'SENDER'
                row[3] += sender
                text = mask(lead.get('bounce_msg'))[:1000]
                data.bounces.append((day, dom, prov, int(sender), text))
    for day, dom, prov, sender, text in data.bounces:
        data.note_bounce(day, dom, text)

    say('splitting sends by provider')
    build(data, totals, by_prov, leads, mix_of)
    data.raw = {'totals': encode(totals), 'fresh': fresh, 'leads': encode(leads),
                'seen': seen, 'mixes': mixes, 'watermark': newest or since, 'start': start,
                'mapping': mapcache, 'unlabeled': data.raw.get('unlabeled', [])}
    say('%d domains, %d days, %d API calls' % (len(data.domains()), len(data.days), api.calls))
    return data


def build(data, totals, by_prov, leads, mix_of):
    # Domains whose leads account for less than half the replies and bounces
    # their mailboxes counted: their campaigns' leads are gone, so the
    # mailbox counts stand in (fill).  Elsewhere the leads are the record;
    # the two date a reply differently, so mixing them would count some twice.
    counted, kept = collections.Counter(), collections.Counter()
    for (dom, d), row in totals.items():
        counted[dom] += row[1] + row[3]
    for (dom, p, d), row in leads.items():
        kept[dom] += row[0] + row[2]
    lost = {dom for dom, n in counted.items() if n >= 10 and kept[dom] < n / 2}
    data.raw = dict(data.raw or {}, unlabeled=sorted(lost))
    for day in sorted({d for _, d in totals}):
        guess, dom_totals = {}, {}
        for (dom, d), row in totals.items():
            if d != day or not row[0]:
                continue
            dom_totals[dom] = row[0]
            for p, share in mix_of(dom).items():
                guess[dom, p] = row[0] * share
        prov_day = {p: by_prov.get((p, day), 0) for p in ('google', 'microsoft', 'other')}
        # PlusVibe counts Apple inside REGULAR_ACCOUNT; keep our Apple guess
        # and take it out of 'other'.
        apple = sum(v for (dom, p), v in guess.items() if p == 'apple')
        prov_day['other'] = max(0, prov_day['other'] - apple)
        prov_day['apple'] = apple
        split = balance(guess, dom_totals, prov_day)
        rows = {}
        for (dom, p), sent in split.items():
            rows[dom, p] = [int(round(sent))] + list(leads.get((dom, p, day), (0, 0, 0, 0)))
        fill({k: v for k, v in rows.items() if k[0] in lost}, totals, day)
        for (dom, p), row in rows.items():
            row[0] = max(row[0], row[3])
            if row[0]:
                data.add(day, dom, p, row)
        data.add_day(day)
    for dom, _ in totals:
        data.workspace.setdefault(dom, None)


def fill(rows, totals, day):
    # Replies and bounces come from the workspace's leads, which say which
    # provider and which label.  When a campaign is deleted its leads go with
    # it, but the mailbox's own counts stay: what the leads cannot account
    # for is taken from those, split by where the domain's mail went.
    by_dom = collections.defaultdict(list)
    for (dom, p) in rows:
        by_dom[dom].append(p)
    for dom, provs in by_dom.items():
        mailbox = totals.get((dom, day))
        if not mailbox:
            continue
        sent = sum(rows[dom, p][0] for p in provs) or 1
        provs.sort(key=lambda p: -rows[dom, p][0])
        for i in range(1, 5):
            missing = mailbox[i] - sum(rows[dom, p][i] for p in provs)
            if missing <= 0:
                continue
            given = 0
            for p in provs:
                n = int(missing * rows[dom, p][0] / sent)
                rows[dom, p][i] += n
                given += n
            rows[dom, provs[0]][i] += missing - given


# Tuple keys don't survive JSON; join them with tabs.
def encode(d):
    return {'\t'.join(k): v for k, v in d.items()}


def decode(d):
    return {tuple(k.split('\t')): v for k, v in (d or {}).items()}
