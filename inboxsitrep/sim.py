# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# A made-up sending fleet that breaks the way real fleets break.
#
# The rates are not invented.  They were measured on a real fleet of ~650
# domains doing ~3 million sends a month (docs/numbers.md says where each
# one comes from): 300 sends a day per domain, Gmail refusing 87-100% of a
# dead domain's mail from one hour to the next, families of look-alike
# domains dying one after another, Gmail episodes that clear by themselves
# in two to four days, Apple refusing 78% of cold mail whatever domain sent
# it.  Every failure that goes in is written to `truth`, so whatever reads
# the fleet can be scored against what really happened.

import random
import datetime
import collections

from .data import Data


START = datetime.date(2026, 9, 1)
PER_DOMAIN = 300        # 100 mailboxes x 3 a day
LOAD = .9               # share of the fleet's capacity the campaigns ask for
PROBE = 10              # Gmail sends a stopped domain keeps, to see if it heals
POSITIVE = .17          # 7,367 positives from 43,309 replies on the real fleet

# Recipients' share, reply rate, recipient bounce rate, on a healthy domain.
# Business lists hold few Apple addresses (person-level lists hold 6% and
# more); everything self-hosted or small-ISP sits in 'other' and bounces
# at 7-11% on the real fleet.  Blended, this bounces ~1.9% (real: 2.1%).
BASE = {
    'google':    (.55, .020, .010),
    'microsoft': (.30, .025, .015),
    'apple':     (.01, .012, .020),
    'other':     (.14, .018, .060),
}

MESSAGES = {
    'gmail_reputation': '550 5.7.1 Gmail has detected that this message is '
                        'likely suspicious due to the very low reputation of '
                        'the sending domain.',
    'surbl': '550 5.7.1 A URL in this email ({dom}) is listed on '
             'https://www.surbl.org/lists',
    'apple_cs01': '550 5.7.1 [CS01] Message rejected due to local policy. '
                  'Please visit https://support.apple.com/en-us/HT204137',
    'auth': '550 5.7.26 This mail has been blocked because the sender is '
            'unauthenticated.',
    'user_unknown': '550 5.1.1 The email account that you tried to reach '
                    'does not exist.',
    'tenant_block': "5.7.237 Your message couldn't be sent because your "
                    "organization's outbound email has been blocked due to "
                    "sending activity that doesn't comply with our terms of "
                    "service.",
    'microsoft_block': '550 5.7.1 Unfortunately, messages from [40.107.1.2] '
                       'weren\'t sent. Please contact your Internet service '
                       'provider since part of their network is on our block '
                       'list (S3150).',
    'throttle': '421 4.7.28 Gmail has detected an unusual rate of unsolicited '
                'mail originating from your domain. To protect our users from '
                'spam, mail sent from your domain has been temporarily rate '
                'limited.',
    'surbl_ip': '550 5.7.1 A URL in this email ({link}) is listed on '
                'https://www.surbl.org/lists',
}

STEMS = ['growthmail', 'leadpilot', 'brightreach', 'salesharbor', 'pipedrift',
         'outboundly', 'reachwise', 'closerdesk', 'prospectly', 'mailcraft',
         'dealflow', 'signalpath', 'inboxward', 'replyfirst', 'meetbase']
TAILS = ['hq', 'pro', 'hub', 'labs', 'go', 'team', 'now', 'app', 'base',
         'line', 'point', 'direct', 'works', 'zone', 'one', 'group', 'link',
         'mail', 'desk', 'center', 'plus', 'online']
TLDS = ['com', 'co', 'io']

try:
    _binomial = random.Random.binomialvariate
except AttributeError:          # Python < 3.12
    def _binomial(rng, n, p):
        return sum(rng.random() < p for _ in range(n))


def date(t):
    return (START + datetime.timedelta(days=t)).isoformat()


class Fleet:

    def __init__(self, seed=1, families=12, size=(8, 20)):
        rng = random.Random(seed)
        self.seed = seed
        self.family = {}
        for stem in rng.sample(STEMS, families):
            for tail in rng.sample(TAILS, rng.randint(*size)):
                self.family['%s%s.%s' % (stem, tail, rng.choice(TLDS))] = stem
        self.doms = sorted(self.family)
        # Some domains simply do better than others.  Real fleets spread a lot.
        self.quality = {d: rng.uniform(.8, 1.2) for d in self.doms}
        self.ghealth = {d: min(99.0, rng.gauss(78, 10)) for d in self.doms}
        self.fail = {d: {} for d in self.doms}
        self.apple = None
        self.greply = 1.0              # < 1 when Gmail files the whole fleet
        self.badlists = {}             # name -> (start, end, domains, extra)
        self.truth = []
        self.off = {'google': {}, 'microsoft': {}}   # operator state, changed by plans
        self.google_off = self.off['google']
        self.slowed = {}
        self.down = {}                 # domain -> day its mailboxes disconnected
        self.link = None               # (day, share, link domain) when the copy's link is listed
        self.link_removed = None
        self.paused = {}
        self.retired = set()
        self.apple_off = None
        self.scrubbed = set()
        self.t = 0
        self.noise = random.Random(seed * 7 + 1)
        self.data = Data()
        self.tally = collections.Counter()
        hosts = {stem: rng.choice(['google', 'google', 'microsoft'])
                 for stem in set(self.family.values())}
        # About a quarter of the families on plain SMTP mailboxes, which reply
        # less and bounce more than Google or Microsoft ones.  A separate
        # generator, so the rest of the fleet comes out as before.
        smtp = random.Random(seed * 31 + 7)
        for stem in sorted(hosts):
            if smtp.random() < .25:
                hosts[stem] = 'other'
        for d in self.doms:
            self.data.workspace[d] = 'simulated'
            self.data.mailboxes[d] = PER_DOMAIN // 3
            self.data.host[d] = hosts[self.family[d]]

    # -- breaking things --------------------------------------------------

    def kill(self, dom, t, listed_after=None):
        self.fail[dom]['dead'] = t
        if listed_after is not None:
            self.fail[dom]['listed'] = t + listed_after
        self.truth.append(('dead', dom, t))

    def episode(self, dom, t, days, share):
        self.fail[dom].setdefault('episodes', []).append((t, t + days, share))
        self.truth.append(('episode', dom, t, t + days))

    def folder(self, doms, t, severity=.15):
        # severity: share of the usual Gmail replies that still come back
        for d in doms:
            self.fail[d]['folder'] = (t, severity)
        self.truth.append(('quiet_spam', tuple(doms), t))

    def list_on_surbl(self, dom, t, share=.25):
        self.fail[dom]['listed'] = t
        self.fail[dom]['listed_share'] = share
        self.truth.append(('listed', dom, t))

    def break_auth(self, dom, t, share=.2):
        self.fail[dom]['auth'] = (t, share)
        self.truth.append(('auth', dom, t))

    def block_tenant(self, doms, t):
        # Microsoft stops the sending tenant: everything refused at source.
        for d in doms:
            self.fail[d]['tenant'] = t
        self.truth.append(('tenant_block', tuple(doms), t))

    def block_microsoft(self, dom, t, share=.5):
        # Microsoft, as a receiver, refusing the domain (S3150 and kin).
        self.fail[dom]['msblock'] = (t, share)
        self.truth.append(('ms_block', dom, t))

    def throttle(self, dom, t, share=.15, grace=3):
        # Gmail rate-limits the domain.  Slowed down in time, it passes;
        # left alone, it turns into the reputation block.  That escalation
        # is Gmail's documented behaviour, not something we measured.
        self.fail[dom]['throttle'] = (t, share, t + grace)
        self.truth.append(('throttle', dom, t))

    def disconnect(self, doms, t):
        for d in doms:
            self.down[d] = t
        self.truth.append(('disconnect', tuple(doms), t))

    def list_link(self, t, share=.1):
        # The link domain in the copy gets listed; every domain carries it.
        self.link = (t, share, 'trk-%s.com' % STEMS[self.seed % len(STEMS)])
        self.truth.append(('link_listed', self.link[2], t))

    def apple_block(self, t):
        self.apple = t
        self.truth.append(('apple', 'fleet', t))

    def bad_list(self, name, doms, t, days, extra=.05):
        self.badlists[name] = (t, t + days, tuple(doms), extra)
        self.truth.append(('bad_list', tuple(doms), t))

    # -- the operator -----------------------------------------------------

    def apply(self, change):
        op, targets = change['op'], change.get('targets', ())
        off = self.off.get(change.get('provider'), self.google_off)
        for d in targets:
            if op == 'stop':
                off.setdefault(d, self.t)
            elif op == 'resume':
                off.pop(d, None)
            elif op == 'slow':
                self.slowed.setdefault(d, self.t)
            elif op == 'restore':
                self.slowed.pop(d, None)
            elif op == 'reconnect':
                self.down.pop(d, None)
            elif op == 'pause':
                self.paused.setdefault(d, self.t)
            elif op == 'unpause':
                self.paused.pop(d, None)
            elif op == 'retire':
                self.retired.add(d)
            elif op == 'fix_dns':
                self.fail[d].setdefault('auth_fixed', self.t + 1)
        if op == 'scrub':
            # Plans name the domains; the fleet knows which list they share.
            for name, (_, _, doms, _) in self.badlists.items():
                if set(doms) & set(targets):
                    self.scrubbed.add(name)
        if op == 'suppress_apple' and self.apple_off is None:
            self.apple_off = self.t
        if op == 'remove_link' and self.link_removed is None:
            self.link_removed = self.t

    # -- one day of sending -----------------------------------------------

    def rates(self, dom, prov, t):
        share, reply, bounce = BASE[prov]
        reply *= self.quality[dom]
        if self.data.host.get(dom) == 'other':
            reply, bounce = reply * .6, bounce * 1.4
        reject, kind = 0.0, None
        f = self.fail[dom]
        if prov == 'google':
            reply *= self.greply
            if self.foldered(dom, t):
                reply *= f['folder'][1]
            for a, b, s in f.get('episodes', ()):
                if a <= t < b:
                    reject, kind, reply = s, 'gmail_reputation', reply * .3
            th = f.get('throttle')
            if th and th[0] <= t and f.get('dead') is None:
                if th[0] <= self.slowed.get(dom, 10**6) <= th[2] or dom in self.google_off:
                    pass        # slowed in time: it passes
                elif t < th[2]:
                    reject, kind = th[1], 'throttle'
                else:
                    f['dead'] = th[2]
            if f.get('dead') is not None and t >= f['dead']:
                reject, kind, reply = .9, 'gmail_reputation', reply * .05
        ms = f.get('msblock')
        if prov == 'microsoft' and ms and t >= ms[0] and ms[1] > reject:
            reject, kind, reply = ms[1], 'microsoft_block', reply * .3
        if self.link and t >= self.link[0] and self.link_removed is None:
            r = self.link[1] * (.4 if prov == 'google' else 1)
            if r > reject:
                reject, kind = r, 'surbl_ip'
        if f.get('listed') is not None and t >= f['listed']:
            r = f.get('listed_share', .25)
            r = r * .4 if prov == 'google' else r
            if r > reject:
                reject, kind = r, 'surbl'
            reply *= .5
        if self.unauthenticated(dom, t):
            r = f['auth'][1]
            r = r * 1.5 if prov == 'google' else r
            if r > reject:
                reject, kind = r, 'auth'
            reply *= .5
        if prov == 'apple' and self.apple is not None and t >= self.apple:
            reject, kind = .78, 'apple_cs01'
        if f.get('tenant') is not None and t >= f['tenant']:
            reject, kind, reply = 1.0, 'tenant_block', 0.0
        for name, (a, b, doms, extra) in self.badlists.items():
            if dom in doms and a <= t < b and name not in self.scrubbed:
                bounce += extra
        return reply, bounce, reject, kind

    def foldered(self, dom, t):
        f = self.fail[dom].get('folder')
        return f is not None and t >= f[0]

    def unauthenticated(self, dom, t):
        f = self.fail[dom]
        return (f.get('auth') is not None
                and f['auth'][0] <= t < f.get('auth_fixed', 10**6))

    def plan_sends(self, t):
        live = [d for d in self.doms if d not in self.paused
                and d not in self.retired and d not in self.down]
        want = PER_DOMAIN * len(self.doms) * LOAD
        sends = collections.defaultdict(dict)
        for prov, (share, _, _) in BASE.items():
            demand = want * share
            if prov == 'apple' and self.apple_off is not None:
                demand = 0
            if prov in self.off:
                off = self.off[prov]
                probes = [d for d in live if d in off]
                for d in probes:
                    sends[d][prov] = PROBE
                senders = [d for d in live if d not in off]
                demand -= PROBE * len(probes)
            else:
                senders = live
            for d in senders:
                sends[d][prov] = demand / len(senders) if senders else 0
        for d, since in self.slowed.items():
            if d in sends and 'google' in sends[d] and t - since < 3:
                sends[d]['google'] *= .5
        # A domain can't send more than its mailboxes allow.  What doesn't
        # fit today is simply not sent; that is what losing domains costs.
        for d, by in sends.items():
            total = sum(by.values())
            if total > PER_DOMAIN:
                for p in by:
                    by[p] *= PER_DOMAIN / total
        return sends, want

    def step(self):
        t, day = self.t, date(self.t)
        rng = random.Random(self.seed * 100003 + t)
        sends, want = self.plan_sends(t)
        self.data.add_day(day)
        sent_total = 0
        for dom, by in sends.items():
            for prov, n in by.items():
                n = int(round(n))
                if not n:
                    continue
                sent_total += n
                reply, bounce, reject, kind = self.rates(dom, prov, t)
                rej = _binomial(rng, n, reject) if reject else 0
                bnc = _binomial(rng, n - rej, bounce)
                rep = _binomial(rng, n - rej - bnc, min(reply, 1.0))
                pos = _binomial(rng, rep, POSITIVE)
                self.data.add(day, dom, prov, [n, rep, pos, rej + bnc, rej])
                k = self.data.kinds[dom, day]
                if rej:
                    k[kind] += rej
                    link = self.link[2] if self.link else ''
                    self.data.samples.setdefault(
                        (dom, kind), MESSAGES[kind].format(dom=dom, link=link))
                    if kind == 'surbl_ip':
                        self.data.links[dom, day][link] += rej
                if bnc:
                    k['user_unknown'] += bnc
                    self.data.samples.setdefault(
                        (dom, 'user_unknown'), MESSAGES['user_unknown'])
                # The score uses expected values, not the dice, so two
                # policies on the same fleet are compared fairly.  A lead
                # refused by the receiver is gone: it is marked bounced and
                # suppressed for good.  A lead filed as spam has had its
                # sequence used up for almost nothing.
                ok = n * (1 - reject) * (1 - bounce)
                self.tally['positives'] += ok * reply * POSITIVE
                self.tally['burned'] += n * reject
                if prov == 'google' and self.foldered(dom, t):
                    self.tally['spammed'] += n * (1 - reject)
        self.tally['unsent'] += max(0, want - sent_total)
        self.tally['sent'] += sent_total
        self.observe(t)
        self.t += 1
        return day

    def observe(self, t):
        # What a DNS check and the warmup scores would show today.
        for d in self.doms:
            f = self.fail[d]
            if f.get('listed') is not None and t >= f['listed']:
                self.data.listed.setdefault(d, ('surbl', date(t)))
            if self.unauthenticated(d, t):
                self.data.auth.setdefault(d, ('dkim', date(t), None))
            elif d in self.data.auth and self.data.auth[d][2] is None:
                self.data.auth[d] = self.data.auth[d][:2] + (date(t),)
            g = self.ghealth[d] * (1 if self.greply == 1 else .8)
            g += self.noise.gauss(0, 2)
            if f.get('dead') is not None and t >= f['dead']:
                g = 3.0
            elif self.foldered(d, t):
                # A seven-day score: it slides over a week to where the
                # spam filing puts it, and mild filing barely moves it.
                start, severity = f['folder']
                floor = 25 + 70 * severity
                g = max(min(g, floor), g - (g - floor) * min(1, (t - start + 1) / 7))
            g = max(0.0, min(100.0, g))
            self.data.health[d] = (round(g, 1), 98.0)
            self.data.history[d][date(t)] = self.data.health[d]
            self.data.accounts[d] = ({'down': self.data.mailboxes[d]} if d in self.down
                                     else {'active': self.data.mailboxes[d]})


MX = {'google': 'GOOGLE_WORKSPACE', 'microsoft': 'MICROSOFT365',
      'apple': 'REGULAR_ACCOUNT', 'other': 'REGULAR_ACCOUNT'}
RCPT = {'google': 'gmail.com', 'microsoft': 'outlook.com', 'apple': 'icloud.com',
        'other': 'example-isp.net'}


def export(fleet, path):
    # The fleet as a sequencer's lead export: one row per lead, made-up
    # addresses, the columns PlusVibe uses.  For trying `inboxsitrep import`.
    import csv
    n = 0
    with open(path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['email', 'email_acc_name', 'status', 'last_sent_at', 'mx',
                    'label', 'bounce_type', 'bounce_msg'])
        for (dom, prov), series in sorted(fleet.data.cells.items()):
            for day, (sent, rep, pos, bnc, snd) in sorted(series.items()):
                kinds = fleet.data.kinds[dom, day]
                # Hand out the day's refusal kinds in proportion.
                refused = [k for k, c in sorted(kinds.items())
                           if k != 'user_unknown' for _ in range(c)]
                for i in range(sent):
                    n += 1
                    status, label, btype, msg = 'COMPLETED', '', '', ''
                    if i < snd:
                        status, btype = 'BOUNCED', 'SENDER'
                        k = refused[(n * 7919) % len(refused)] if refused else 'gmail_reputation'
                        msg = MESSAGES[k].format(dom=dom)
                    elif i < bnc:
                        status, btype, msg = 'BOUNCED', 'RECIPIENT', MESSAGES['user_unknown']
                    elif i < bnc + rep:
                        status = 'REPLIED'
                        label = 'INTERESTED' if i < bnc + pos else ''
                    w.writerow(['lead%d@%s' % (n, RCPT[prov]), 'sales@' + dom, status,
                                day + 'T10:00:00Z', MX[prov], label, btype, msg])
    return n


def standard(fleet, rng=None):
    # One messy month: every kind of failure we have seen, at once, on
    # different domains, the way it actually happens.  Severities and
    # timings are drawn from the ranges seen on the real fleet, so some
    # fleets are easy to read and some are not.
    rng = rng or random.Random(fleet.seed + 7)
    fams = collections.defaultdict(list)
    for d, stem in fleet.family.items():
        fams[stem].append(d)
    stems = sorted(fams, key=lambda s: (-len(fams[s]), s))
    used = set()

    def pick(pool, n):
        free = [d for d in pool if d not in used]
        chosen = rng.sample(free, n)
        used.update(chosen)
        return chosen

    # Some fleets are already filed as spam at Gmail across the board, the
    # way the real one was (0.47% from Gmail recipients against 2.53% from
    # Microsoft).  Everything is harder to see from there.
    if rng.random() < .3:
        fleet.greply = rng.uniform(.25, .5)

    # A family that dies one domain at a time, then faster.
    stem = rng.choice(stems[:3])
    victims = pick(sorted(fams[stem]), rng.randint(5, 9))
    used.update(fams[stem])
    t = rng.randint(20, 30)
    fleet.truth.append(('cascade', stem, t))
    gap = 2.0
    for d in victims:
        fleet.kill(d, int(t))
        t += gap
        gap = max(.5, gap * rng.uniform(.5, .9))

    # A family Gmail quietly sends to spam.  Nothing bounces.  Sometimes it
    # is the whole family and blatant, sometimes part of it and subtle.
    stem = rng.choice([s for s in stems if s not in used and fams[s][0] not in used])
    members = sorted(fams[stem])
    share = rng.uniform(.6, 1)
    fleet.folder(sorted(rng.sample(members, max(3, int(len(members) * share)))),
                 rng.randint(25, 38), severity=rng.uniform(.1, .5))
    used.update(members)

    rest = sorted(d for d in fleet.doms if d not in used)
    for d in pick(rest, 2):
        # Most burns get listed within days; some never do.
        later = rng.choice([1, 1, 2, 3, None])
        fleet.kill(d, rng.randint(30, 45), listed_after=later)
    for d in pick(rest, rng.randint(3, 5)):
        fleet.episode(d, rng.randint(30, 48), rng.randint(1, 4),
                      rng.uniform(.3, .8))
    fleet.list_on_surbl(pick(rest, 1)[0], rng.randint(30, 45),
                        share=rng.uniform(.1, .3))
    fleet.break_auth(pick(rest, 1)[0], rng.randint(30, 48),
                     share=rng.uniform(.1, .3))
    fleet.apple_block(rng.randint(15, 30))
    fleet.block_tenant(pick(rest, rng.randint(3, 6)), rng.randint(30, 45))
    fleet.bad_list('list-a', pick(rest, rng.randint(3, 8)),
                   rng.randint(10, 30), rng.randint(5, 12),
                   extra=rng.uniform(.03, .06))
    fleet.block_microsoft(pick(rest, 1)[0], rng.randint(25, 45),
                          share=rng.uniform(.3, .7))
    for d in pick(rest, rng.randint(1, 2)):
        fleet.throttle(d, rng.randint(25, 45), share=rng.uniform(.08, .2))
    fleet.disconnect(pick(rest, rng.randint(1, 3)), rng.randint(20, 45))
    if rng.random() < .5:
        fleet.list_link(rng.randint(25, 45), share=rng.uniform(.05, .15))
    return fleet


def clean(fleet, rng=None):
    # Nothing goes wrong.  Anything flagged here is a false alarm.
    rng = rng or random.Random(fleet.seed + 7)
    if rng.random() < .3:
        fleet.greply = rng.uniform(.25, .5)
    return fleet


SCENARIOS = {'standard': standard, 'clean': clean}


# Campaigns for the simulated fleet: the same sends, split across campaigns
# that do better and worse than the fleet, so the campaign reviews and the
# copy check have something to show.  name, share of sends, positive replies
# against the fleet, bounce rate, copy
CAMPAIGNS = [
    ('Owners - Opener A', .17, 3.2, .012, None),
    ('Owners - Opener B', .15, 1.0, .013, 'run_on'),
    ('Owners - Case study', .14, 1.1, .011, None),
    ('Agencies - Free audit', .12, .3, .014, 'words'),
    ('Agencies - Referral ask', .12, 2.9, .010, None),
    ('Local services - Maps list', .13, 1.0, .055, None),
    ('Local services - Results page', .12, .9, .012, 'links'),
    ('Founders - Short opener', .04, 1.2, .012, None),
    ('Founders - New angle', .0004, 1.3, .010, None),
]
COPY = {
    'run_on': 'Hi {{first_name}}, would you consider selling {{company_name}}for the right number?',
    'words': 'Free audit at no cost to you, guaranteed results. Act now: limited time.',
    'links': 'Here is what we did for a firm like yours: https://results-example.com/case-study',
}


def add_campaigns(fleet, seed=1):
    from . import copycheck
    from .data import SENT, REPLIES, POSITIVES
    rng = random.Random(seed * 13 + 5)
    data = fleet.data
    sent = replies = pos = 0
    for (dom, prov), series in data.cells.items():
        for row in series.values():
            sent, replies, pos = sent + row[SENT], replies + row[REPLIES], pos + row[POSITIVES]
    base, reply = pos / max(1, sent), replies / max(1, sent)
    data.campaigns = {}
    for n, (name, share, lift, bounce, copy) in enumerate(CAMPAIGNS, 1):
        k = int(sent * share)
        bounced = _binomial(rng, k, bounce)
        sequences = [{'step': 1, 'variations': [{'variation': 'A', 'subject': 'Question for {{company_name}}',
                                                 'body': COPY.get(copy, 'Hi {{first_name}}, quick question about '
                                                                        '{{company_name}}.')}]}]
        leads = int(k * rng.uniform(1.05, 1.6))
        data.campaigns['c%d' % n] = {
            'name': name, 'status': 'ACTIVE', 'sent_count': k, 'lead_count': leads, 'lead_contacted_count': k,
            'replied_count': _binomial(rng, k, min(1, reply * lift ** .5)),
            'positive_reply_count': _binomial(rng, k, min(1, base * lift)),
            'bounced_count': bounced, 'sender_bounced_count': int(bounced * (.1 if bounce > .03 else .4)),
            'last_lead_sent': data.days[-1] if data.days else None,
            'copy': copycheck.summary(copycheck.check(sequences, {}))}
    return data.campaigns


# Three workspaces to try everything on (inboxsitrep demo): a third of each
# on Google Workspace mailboxes, a third on Microsoft 365, a third on plain
# SMTP; twelve live campaigns each; and each with its own trouble.

def thirds(fleet):
    stems = sorted(set(fleet.family.values()))
    hosts = {s: ('google', 'microsoft', 'other')[n % 3] for n, s in enumerate(stems)}
    for d in fleet.doms:
        fleet.data.host[d] = hosts[fleet.family[d]]


def on(fleet, host):
    return sorted(d for d in fleet.doms if fleet.data.host[d] == host)


def by_family(fleet, host):
    fams = collections.defaultdict(list)
    for d in on(fleet, host):
        fams[fleet.family[d]].append(d)
    return [fams[s] for s in sorted(fams)]


def northwind(fleet, rng):
    # An agency fleet: Gmail turning on a Google-hosted family one domain at
    # a time, a Microsoft 365 tenant shut down, a bad list, leads running
    # out, broken copy.
    fam = by_family(fleet, 'google')[0]
    fleet.truth.append(('cascade', fleet.family[fam[0]], 30))
    for n, d in enumerate(fam[:6]):
        fleet.kill(d, 30 + n * 2)
    fleet.block_tenant(on(fleet, 'microsoft')[:5], 38)
    fleet.bad_list('scraped-list', on(fleet, 'other')[:6], 33, 9, extra=.06)
    fleet.apple_block(20)


def atlas(fleet, rng):
    # A growth team: a family on SURBL that still delivers, Microsoft
    # refusing one domain, mailboxes disconnected, Gmail quietly filing an
    # SMTP family as spam, a tracking link listed.
    fam = by_family(fleet, 'microsoft')[0]
    for d in fam[:7]:
        fleet.list_on_surbl(d, 34, share=.04)
    fleet.block_microsoft(on(fleet, 'google')[2], 36, share=.6)
    fleet.disconnect(on(fleet, 'google')[5:7], 40)
    fleet.folder(by_family(fleet, 'other')[0], 33, severity=.2)
    fleet.throttle(on(fleet, 'google')[8], 41, share=.12)


def summit(fleet, rng):
    # The steady one: an episode Gmail gets over, one domain failing
    # authentication, a throttle; most of it healthy, as a control.
    fleet.episode(on(fleet, 'google')[1], 36, 3, .6)
    fleet.break_auth(on(fleet, 'microsoft')[3], 39, share=.2)
    fleet.throttle(on(fleet, 'other')[4], 42, share=.1)


# Twelve campaigns per workspace: name, share of sends, reply rate against
# the workspace's, bounce rate, copy fault, leads left in days of sending.
TWELVE = {
    'Northwind Outbound': [
        ('SaaS founders - Opener A', .12, 2.4, .012, None, 9),
        ('SaaS founders - Opener B', .11, 1.0, .013, 'run_on', 9),
        ('Agencies - Case study', .10, 1.1, .011, 'links', 6),
        ('Agencies - Free audit', .09, .35, .014, 'words', 5),
        ('Ecommerce - Shopify list', .10, 1.0, .068, None, 1),
        ('Ecommerce - Referral ask', .08, 2.2, .010, None, 1),
        ('Local services - Maps list', .09, .9, .011, 'empty', 1),
        ('Local services - Follow-up', .07, 1.1, .012, None, 1),
        ('Fintech - Compliance angle', .07, 1.0, .012, 'unmapped', 2),
        ('Fintech - Short opener', .07, 1.3, .011, None, 2),
        ('Healthcare - Webinar', .06, .4, .013, 'shout', 1),
        ('Healthcare - New angle', .0005, 1.2, .010, None, 30)],
    'Atlas Growth': [
        ('Owners - Direct offer', .12, 1.0, .012, 'tracking', 12),
        ('Owners - AI agent', .12, 2.6, .011, None, 12),
        ('CFOs - Benchmark report', .10, 1.1, .012, 'links', 8),
        ('CFOs - Short opener', .09, 1.0, .012, None, 8),
        ('IT leaders - Security audit', .09, .3, .013, 'words', 7),
        ('IT leaders - Peer story', .08, 1.0, .012, None, 7),
        ('Recruiters - Hiring signal', .08, 2.1, .011, None, 10),
        ('Recruiters - Follow-up', .07, 1.0, .052, None, 10),
        ('Logistics - Cost angle', .07, 1.0, .012, 'identical', 9),
        ('Logistics - Case study', .07, .9, .012, None, 9),
        ('Real estate - Off-market', .06, 1.2, .012, 'unmapped', 6),
        ('Real estate - New list', .0004, 1.0, .010, None, 40)],
    'Summit Pipeline': [
        ('Manufacturing - Opener A', .12, 1.0, .011, None, 20),
        ('Manufacturing - Opener B', .11, 1.1, .011, None, 20),
        ('Construction - Referral', .10, 2.3, .010, None, 18),
        ('Construction - Case study', .10, 1.0, .011, None, 18),
        ('Legal - Partner intro', .09, .9, .012, None, 15),
        ('Legal - Short opener', .08, 1.0, .012, None, 15),
        ('Insurance - Renewal angle', .08, 1.0, .011, None, 14),
        ('Insurance - Follow-up', .07, .4, .012, None, 14),
        ('Dental - Local list', .07, 1.0, .011, None, 12),
        ('Dental - New offer', .07, 1.1, .011, None, 12),
        ('Accounting - Tax season', .06, 1.2, .011, None, 11),
        ('Accounting - New angle', .0005, 1.0, .010, None, 40)],
}
OPENER = ('{% if first_name != blank %}Hi {{first_name}},{% else %}Hi there,{% endif %}\n'
          '{{random|Quick question|A quick one}} about {{company_name}}: are you the right person for outbound?')
FAULTS = {
    'run_on': 'Hi {{first_name}}, would you consider selling {{company_name}}for the right number?',
    'links': OPENER + ' Here is what we did for a firm like yours: https://results-example.com/case-study',
    'words': OPENER + ' Free audit at no cost to you, guaranteed results. Act now.',
    'empty': 'Hi {{first_name}}, saw {{company_name}} on Google Maps. Worth a quick call?',
    'unmapped': OPENER + ' We help {{custom_industry}} teams cut review time.',
    'shout': OPENER + ' Join us live.',
    'identical': 'Hi, we cut freight costs 12% for companies like yours. Worth a call?',
    'tracking': OPENER,
}


def twelve(fleet, name, rng):
    # The campaigns, their copy and its findings, from the fleet's own sends.
    from . import copycheck
    from .data import SENT, REPLIES, POSITIVES
    data = fleet.data
    sent = replies = pos = 0
    for series in data.cells.values():
        for row in series.values():
            sent, replies, pos = sent + row[SENT], replies + row[REPLIES], pos + row[POSITIVES]
    reply, base = replies / max(1, sent), pos / max(1, sent)
    for day in data.days:
        data.fresh[day] = int(sum(data.row(d, p, day)[SENT] for d in fleet.doms
                                  for p in ('google', 'microsoft', 'apple', 'other')) * .4)
    pace = sum(data.fresh[d] for d in data.days[-3:]) / 3
    data.campaigns = {}
    for n, (camp, share, lift, bounce, fault, days_left) in enumerate(TWELVE[name], 1):
        k = max(1, int(sent * share))
        body = FAULTS.get(fault, OPENER)
        subject = 'JOIN OUR WEBINAR!' if fault == 'shout' else 'Question for {{company_name}}'
        variations = [{'variation': 'A', 'subject': subject, 'body': body}]
        if fault != 'identical':
            variations.append({'variation': 'B', 'subject': subject, 'body': body.replace('Quick question', 'One question')})
        sequences = [{'step': 1, 'variations': variations}]
        issues = copycheck.check(sequences, {'is_emailopened_tracking': int(fault == 'tracking')})
        if fault in ('empty', 'unmapped'):
            sample = [{'first_name': '' if i % 4 == 0 else 'Ann', 'company_name': 'Acme'} for i in range(40)]
            issues += copycheck.mapping(sequences, sample, [{'first_name': 'Ann', 'company_name': 'Acme'}])
        bounced = _binomial(rng, k, bounce)
        data.campaigns['c%d' % n] = {
            'name': camp, 'status': 'ACTIVE', 'sent_count': k,
            'lead_count': k + int(pace * share * days_left), 'lead_contacted_count': k,
            'replied_count': _binomial(rng, k, min(1, reply * lift)),
            'positive_reply_count': _binomial(rng, k, min(1, base * lift)),
            'bounced_count': bounced, 'sender_bounced_count': int(bounced * (.1 if bounce > .04 else .4)),
            'bounce_rate_limit': 5, 'is_emailopened_tracking': int(fault == 'tracking'),
            'last_lead_sent': data.days[-1], 'copy': copycheck.summary(issues)}


# Warmed domains in no campaign, ready to swap in: few where domains are
# being lost fastest.
SPARES = {'Northwind Outbound': 3, 'Atlas Growth': 9, 'Summit Pipeline': 22}


def spares(fleet, n):
    data = fleet.data
    stems = sorted(set(fleet.family.values()))
    for i in range(n):
        dom = '%s%s.com' % (stems[i % len(stems)], ('reserve', 'standby', 'spare', 'backup')[i // len(stems) % 4])
        data.mailboxes[dom] = 100
        data.accounts[dom] = {'active': 100, 'idle': 100}
        data.host[dom] = data.host[fleet.doms[i % len(fleet.doms)]]
    for d in fleet.doms:
        data.accounts.setdefault(d, {'active': data.mailboxes.get(d) or 100})


DEMO = [('Northwind Outbound', 31, northwind), ('Atlas Growth', 32, atlas), ('Summit Pipeline', 33, summit)]


def demo(days=45, today=None):
    # [(name, fleet)], each ending yesterday.
    global START
    today = today or datetime.date.today()
    was, START = START, today - datetime.timedelta(days=days)
    out = []
    for name, seed, trouble in DEMO:
        fleet = Fleet(seed)
        thirds(fleet)
        trouble(fleet, random.Random(seed))
        for _ in range(days):
            fleet.step()
        twelve(fleet, name, random.Random(seed + 1))
        spares(fleet, SPARES[name])
        out.append((name, fleet))
    START = was
    return out
