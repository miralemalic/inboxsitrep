# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# The detectors.  Each is a rule a deliverability engineer applies by hand,
# written down with the numbers that made it worth trusting.  They look at
# one day at a time and say what they see; deciding what to do about it is
# plan.py's job.

import math
import datetime
import collections

from . import checks
from .data import SENT, REPLIES, BOUNCES, SENDER, rate


MIN_PROBE = 8       # fewest Gmail sends a day we'll judge a domain on
DEAD = .30          # On the real fleet a domain was 39-79% or under 8%.
CLEAR = .05         # Nothing lived in between, so the line is easy to draw.
BURN_DAYS = 3       # Episodes cleared in 2-4 days.  Burns stayed pinned.
SURPRISE = .001     # how unlikely a reply drop must be before we call it
LIST_KINDS = ('surbl', 'spamhaus')
LISTS = {'surbl': 'SURBL', 'spamhaus': 'Spamhaus DBL', 'dbl': 'Spamhaus DBL'}


def list_name(where):
    return LISTS.get(where, where) if where else 'a blocklist'


def label(dom):
    return dom.split('.')[0]


def shared_start(names):
    # The letters every name begins with.  os.path.commonprefix used to do
    # this, but Python 3.15 deprecated it in favour of commonpath, which
    # splits on slashes and would give back nothing here.  The first and last
    # names in sorted order are the two that differ soonest, so comparing
    # just those two is enough.
    first, last = min(names), max(names)
    n = 0
    while n < len(first) and n < len(last) and first[n] == last[n]:
        n += 1
    return first[:n]


def families(doms, stem=6, size=3):
    # Look-alike domains (growthmailads, growthmailpro, ...) are judged by
    # Gmail as one sender: on the real fleet nine of thirty died one after
    # another.  Group names that share their first letters; the family is
    # named by everything its members have in common.
    groups = collections.defaultdict(list)
    for d in doms:
        groups[label(d)[:stem]].append(d)
    out = {}
    for members in groups.values():
        if len(members) >= size:
            name = shared_start([label(d) for d in members])
            for d in members:
                out[d] = name
    return out


def below(k, n, p):
    # P(X <= k) for X ~ Binomial(n, p): how odd it is to see this few.
    if n <= 0 or p <= 0:
        return 1.0
    if p >= 1:
        return 0.0 if k < n else 1.0
    lp, lq = math.log(p), math.log1p(-p)
    total = 0.0
    for i in range(min(k, n) + 1):
        total += math.exp(math.lgamma(n + 1) - math.lgamma(i + 1)
                          - math.lgamma(n - i + 1) + i * lp + (n - i) * lq)
    return min(total, 1.0)


def plural(n, word):
    return '%d %s%s' % (n, word, '' if n == 1 else 's')


def finding(kind, scope, target, day, confidence, title, **numbers):
    return {'kind': kind, 'scope': scope, 'target': target, 'day': day,
            'confidence': confidence, 'title': title, 'numbers': numbers}


# Refusals that are not Gmail's verdict on the domain's reputation: the
# sender's own tenant shut (the mail never left), authentication failures
# (a DNS fix, and the auth detector's job), other receivers' blocks, lists
# naming an IP or a link rather than the domain, and Gmail's rate limit,
# which is a warning, not the verdict.
NOT_GMAIL = ('tenant_block', 'auth', 'gateway_block', 'isp_block',
             'microsoft_block', 'apple_cs01', 'ip_block', 'surbl_ip',
             'spamhaus_ip', 'throttle', 'complaints')


def gmail_rate(data, dom, day):
    r = data.row(dom, 'google', day)
    if r[SENT] < MIN_PROBE:
        return None
    kinds = data.kinds[dom, day]
    refused = max(0, r[SENDER] - sum(kinds[k] for k in NOT_GMAIL))
    return refused / r[SENT]


def google_blocks(data, day):
    # Gmail refusing a domain's mail ("very low reputation of the sending
    # domain").  For the first day or two a dead domain and a passing
    # episode look exactly the same.  What tells them apart, every time on
    # the real fleet, was the next few days and the blocklists: episodes
    # fell back to normal while volume held; burns stayed pinned, and a
    # SURBL or Spamhaus listing usually named the domain.
    out = []
    days = data.window(day, 10)
    for dom in data.domains():
        rates = [gmail_rate(data, dom, d) for d in days]
        today = rates[-1]
        if today is None:
            continue
        bad = 0
        for r in reversed(rates):
            if r is None or r < DEAD:
                break
            bad += 1
        where = data.listed_on(dom, day)
        listed = where or any(
            data.kinds[dom, d][k] for d in days[-3:] for k in LIST_KINDS)
        was_bad = any(r is not None and r >= DEAD for r in rates[:-1])
        if bad:
            first = days[len(days) - bad]
            if listed or bad >= BURN_DAYS:
                why = 'listed on %s' % list_name(where) \
                    if listed else 'refused %d days running' % bad
                out.append(finding(
                    'burned', 'domain', dom, day, 'high' if listed else 'medium',
                    'Gmail has stopped taking mail from %s (%s)' % (dom, why),
                    rate=round(today, 3), days=bad, since=first,
                    listed=bool(listed)))
            else:
                out.append(finding(
                    'rejecting', 'domain', dom, day, 'medium',
                    'Gmail is refusing %.0f%% of mail from %s' % (today * 100, dom),
                    rate=round(today, 3), days=bad, since=first))
        elif was_bad and today < CLEAR:
            out.append(finding(
                'cleared', 'domain', dom, day, 'medium',
                'Gmail is taking mail from %s again' % dom,
                rate=round(today, 3)))
    return out


def cascades(data, day, fams, blocks):
    # Members of one family dying one after another.  Pulling the dead ones
    # did not stop it on the real fleet; it went from one domain a day to
    # two.  The rest of the family is next, so act on the family.  It is a
    # cascade while deaths keep coming: two or more dead, one in the last
    # week.
    dead = collections.defaultdict(list)
    for f in blocks:
        if f['kind'] in ('burned', 'rejecting') and f['target'] in fams:
            dead[fams[f['target']]].append((f['numbers']['since'], f['target']))
    week = set(data.window(day, 7))
    out = []
    for name, hits in dead.items():
        hits.sort()
        if len(hits) < 2 or not any(h[0] in week for h in hits):
            continue
        members = sorted(d for d, n in fams.items() if n == name)
        gone = {h[1] for h in hits}
        out.append(finding(
            'cascade', 'family', name, day, 'high' if len(hits) >= 3 else 'medium',
            'Gmail is refusing the %s* family one domain after another: %d of %d since %s'
            % (name, len(hits), len(members), hits[0][0]),
            dead=[h[1] for h in hits], dates=[h[0] for h in hits],
            last=hits[-1][0], members=members,
            at_risk=[d for d in members if d not in gone]))
    return out


def reply_drop(data, doms, recent, before):
    # Gmail replies now against Gmail replies before, for a set of domains.
    # None when there isn't enough to say anything, or when the drop isn't
    # Gmail's doing: bounces climbing means a different problem, and
    # Microsoft replies falling too means the copy or the list, not Gmail.
    g_new = data.fleet('google', recent, doms)
    g_old = data.fleet('google', before, doms)
    if g_old[REPLIES] < 10 or g_new[SENT] < 300:
        return None
    if rate(g_new[SENDER], g_new[SENT]) >= CLEAR:
        return None
    m_new = data.fleet('microsoft', recent, doms)
    m_old = data.fleet('microsoft', before, doms)
    if rate(m_new[REPLIES], m_new[SENT]) < .7 * rate(m_old[REPLIES], m_old[SENT]):
        return None
    p_old = rate(g_old[REPLIES], g_old[SENT])
    p_new = rate(g_new[REPLIES], g_new[SENT])
    # Both rates are estimates, so test one against the other, not against
    # a fixed rate: if nothing changed, each reply is "now" with chance
    # n_new / (n_new + n_old).  How odd is it that so few of them are?
    replies = g_new[REPLIES] + g_old[REPLIES]
    share = g_new[SENT] / (g_new[SENT] + g_old[SENT])
    return p_old, p_new, g_new[SENT], below(g_new[REPLIES], replies, share)


def against_fleet(data, doms, days):
    # The same question asked across the fleet instead of across time: do
    # these domains get Gmail replies at the rate their own Microsoft
    # replies say they should?  The expected Gmail rate is their Microsoft
    # rate times the Gmail/Microsoft ratio on every other domain, so domain
    # quality and fleet-wide trouble both cancel out.  Unlike a drop over
    # time, this doesn't fade while the problem lasts.
    g = data.fleet('google', days, doms)
    m = data.fleet('microsoft', days, doms)
    g_all = data.fleet('google', days)
    m_all = data.fleet('microsoft', days)
    rest = [a - b for a, b in zip(g_all, g)], [a - b for a, b in zip(m_all, m)]
    if g[SENT] < 500 or m[SENT] < 200 or m[REPLIES] < 10:
        return None
    if rate(g[SENDER], g[SENT]) >= CLEAR:
        return None
    gmail_rest, ms_rest = rate(rest[0][REPLIES], rest[0][SENT]), rate(rest[1][REPLIES], rest[1][SENT])
    if not ms_rest or not gmail_rest:
        return None
    expect = rate(m[REPLIES], m[SENT]) * gmail_rest / ms_rest
    if expect > .10:
        # Nobody's cold email gets that; this traffic is something else.
        return None
    got = rate(g[REPLIES], g[SENT])
    # Both counts are noisy, so test the split of the family's replies:
    # with nothing wrong, a reply comes from a Gmail recipient with the
    # chance the fleet's rates give.  How odd is it that so few did?
    share = g[SENT] * gmail_rest / (g[SENT] * gmail_rest + m[SENT] * ms_rest)
    replies = g[REPLIES] + m[REPLIES]
    return expect, got, g[SENT], below(g[REPLIES], replies, share)


def gmail_drop(data, doms, day, line):
    # Gmail replies over the last 5 days against the 14 before, when they
    # fell past `line` and clearly more than Microsoft's did.
    recent = data.window(day, 5)
    start = data.days.index(recent[0])
    before = data.days[max(0, start - 14):start]
    if len(before) < 7:
        return None
    drop = reply_drop(data, doms, recent, before)
    if not drop or not drop[0] or drop[1] > line * drop[0] or drop[3] >= SURPRISE / 100:
        return None
    # Replies falling everywhere (a tired list) is not Gmail's spam folder.
    mb, mr = data.fleet('microsoft', before, doms), data.fleet('microsoft', recent, doms)
    if mb[SENT] >= 200 and mr[SENT] >= 200 and mb[REPLIES] >= 10:
        if drop[1] / drop[0] > .8 * rate(mr[REPLIES], mr[SENT]) / rate(mb[REPLIES], mb[SENT]):
            return None
    return drop


def quiet_spam(data, day, fams):
    # Gmail taking the mail and putting it in spam: replies from Gmail
    # recipients fall away while nothing bounces, so no bounce monitor ever
    # notices.  On the real fleet Gmail recipients replied at 0.47% against
    # 2.53% at Microsoft.  One domain rarely has enough replies to be sure,
    # but a family of fifteen has, even when only some of it is hit.
    # Two tests: a drop over time catches it starting; a comparison with
    # the rest of the fleet keeps it in view for as long as it lasts.
    week = data.window(day, 7)
    groups = collections.defaultdict(list)
    for d in data.domains():
        groups[fams.get(d, d)].append(d)
    out = []
    for name, doms in groups.items():
        scope = 'family' if len(doms) > 1 else 'domain'
        star = '*' if scope == 'family' else ''
        line = .75 if len(doms) > 1 else .5
        drop = gmail_drop(data, doms, day, line)
        # Spam placement lasts; a one-day dip is noise.  The drop has to show
        # on the day before as well.
        if drop and data.days.index(day) > 0 and not gmail_drop(data, doms, data.days[data.days.index(day) - 1], line):
            drop = None
        if drop:
            p_old, p_new, sent, odds = drop
            title = ('Gmail is putting mail from %s%s in spam: Gmail replies fell '
                     'from %.2f%% to %.2f%%, nothing bounced'
                     % (name, star, p_old * 100, p_new * 100))
        else:
            versus = against_fleet(data, doms, week) if len(groups) > 1 else None
            if not (versus and versus[1] <= .5 * versus[0] and versus[3] < SURPRISE / 100):
                continue
            p_old, p_new, sent, odds = versus
            title = ('Gmail is putting mail from %s%s in spam: %.2f%% of Gmail '
                     'recipients reply where %.2f%% should, going by their '
                     'Microsoft replies; nothing bounced'
                     % (name, star, p_new * 100, p_old * 100))
        out.append(finding(
            'quiet_spam', scope, name, day, 'high' if odds < 1e-8 else 'medium',
            title, members=sorted(doms), before=round(p_old, 4),
            after=round(p_new, 4), sent=sent, odds=odds))
    return out


def listings(data, day, fams=None):
    # A blocklist naming the domain, from a DNS check or from receivers
    # quoting the list in their bounces (the better evidence: 97-99% of
    # Spamhaus and SURBL bounces on the real fleet named the sending domain
    # itself).  When three or more of one family are listed together it is
    # one problem with the family, reported once.
    fams = fams or {}
    days = data.window(day, 3)
    hits = {}
    for dom in data.domains():
        named = sum(data.kinds[dom, d][k] for d in days for k in LIST_KINDS)
        where = data.listed_on(dom, day)
        if where or named >= 3:
            hits[dom] = (where, named)
    # Listed is not the same as refused: on a real fleet most listed domains
    # kept delivering.  Only the ones receivers turn away are failing.
    provs = {p for _, p in data.cells}

    def failing(dom):
        sent = sum(data.row(dom, p, d)[SENT] for p in provs for d in days)
        refused = sum(data.row(dom, p, d)[SENDER] for p in provs for d in days)
        return sent >= MIN_PROBE and refused >= .1 * sent
    waves = collections.defaultdict(list)
    for dom in hits:
        if dom in fams:
            waves[fams[dom]].append(dom)
    out = []
    for name, doms in waves.items():
        if len(doms) < 3:
            continue
        members = sorted(d for d, n in fams.items() if n == name)
        out.append(finding(
            'listed', 'family', name, day, 'high',
            '%s of the %s* family\'s %s are on a blocklist'
            % (len(doms), name, plural(len(members), 'domain')),
            domains=sorted(doms), members=members,
            failing=sorted(d for d in doms if failing(d)),
            bounces_naming_them=sum(hits[d][1] for d in doms)))
        for d in doms:
            del hits[d]
    for dom, (where, named) in sorted(hits.items()):
        out.append(finding(
            'listed', 'domain', dom, day, 'high',
            '%s is on %s' % (dom, list_name(where) if where else 'a blocklist (named in bounces)'),
            list=where, failing=[dom] if failing(dom) else [], bounces_naming_it=named))
    return out


def tenant_blocks(data, day):
    # Microsoft stopping the sender's own Microsoft 365 tenant (5.7.237,
    # "terms of service").  Not a receiver's verdict: every recipient is
    # cut off at once, and the domains on one tenant go down together.
    # Read straight from the bounces: blocked mailboxes are often pulled
    # out of the workspace afterwards, and then have no sending stats.
    # Ongoing if seen in the last three days; told over the last two weeks.
    recent = set(data.window(day, 3))
    weeks = set(data.window(day, 14))
    hit, daily, now = collections.Counter(), collections.Counter(), 0
    for (dom, d), kinds in data.kinds.items():
        n = kinds['tenant_block']
        if n and d in weeks:
            hit[dom] += n
            daily[d] += n
            now += n if d in recent else 0
    if now < 10:
        return []
    # A stray block or two isn't the start of it.
    first = min((d for d, n in daily.items() if n >= 10), default=day)
    here = set(data.domains())
    # A domain still here that has sent normally on two days since its last
    # block bounce is not blocked: a stray bounce, or a block Microsoft
    # lifted.  (Seen on a real fleet: seven domains with one 5.7.237 each on
    # the same day, then 200 sends a day and better replies than the rest.)
    provs = {p for _, p in data.cells}
    days = data.window(day, 14)

    def recovered(dom):
        last = max(d for d in days if data.kinds[dom, d]['tenant_block'])
        clean = [d for d in days if d > last and not data.kinds[dom, d]['tenant_block']
                 and sum(data.row(dom, p, d)[SENT] for p in provs) >= 20]
        return len(clean) >= 2
    fine = sorted(d for d in hit if d in here and recovered(d))
    blocked = {d: n for d, n in hit.items() if d not in fine}
    if sum(blocked.values()) < 10:
        return []
    still = sum(1 for d in blocked if d in here)
    return [finding(
        'tenant_block', 'tenant', 'microsoft 365', day, 'high',
        'Microsoft has blocked outbound mail for %s on Microsoft 365 since '
        '%s: %s sends stopped at the source; %s%s'
        % (plural(len(blocked), 'domain'), first, format(sum(blocked.values()), ','),
           '%d still in this workspace' % still if still else
           'none still in this workspace',
           '; %s with a stray block %s sending normally' % (plural(len(fine), 'more domain'),
                                                          'is' if len(fine) == 1 else 'are')
           if fine else ''),
        domains=sorted(d for d in blocked if d in here),
        gone=sorted(d for d in blocked if d not in here),
        recovered=fine, blocked=sum(blocked.values()), last_3_days=now)]


def broken_auth(data, day):
    out = []
    for dom in data.domains():
        days = data.window(day, 2)
        sent = sum(data.total(dom, p, days)[SENT]
                   for p in ('google', 'microsoft'))
        hits = sum(data.kinds[dom, d]['auth'] for d in days)
        missing = data.auth_missing(dom, day)
        if not (missing or (sent and hits / sent >= .05)):
            continue
        out.append(finding(
            'auth', 'domain', dom, day, 'high' if missing else 'medium',
            'Mail from %s fails authentication%s'
            % (dom, ' (%s missing)' % missing.upper() if missing else ''),
            missing=missing, refused=hits, sent=sent))
    return out


def apple(data, day):
    # Apple refuses cold mail with [CS01] whatever domain sends it.  Spread
    # evenly over every sending domain it looks like a fleet in trouble; it
    # is really a list that shouldn't hold Apple addresses.
    days = data.window(day, 3)
    a = data.fleet('apple', days)
    if a[SENT] < 100 or rate(a[SENDER], a[SENT]) < .5:
        return []
    spread = sum(1 for d in data.domains()
                 if any(data.kinds[d, x]['apple_cs01'] for x in days))
    if spread < 5:
        return []
    return [finding(
        'apple', 'fleet', 'apple', day, 'high',
        'Apple is refusing %.0f%% of mail to iCloud addresses, from %d '
        'different domains' % (rate(a[SENDER], a[SENT]) * 100, spread),
        rate=round(rate(a[SENDER], a[SENT]), 3), sent=a[SENT], domains=spread)]


def bad_lists(data, day):
    # Many domains at 5-8% bounces, all recipient-side, all sending the same
    # 300 a day with the same ratios, is one bad list, not many bad domains.
    # Retiring them throws away healthy infrastructure.
    days = data.window(day, 3)
    by = {}
    for dom in data.domains():
        sent = bounced = refused = 0
        for p in ('google', 'microsoft', 'apple', 'other'):
            r = data.total(dom, p, days)
            sent += r[SENT]
            bounced += r[BOUNCES]
            refused += r[SENDER]
        if sent >= 300:
            by[dom] = rate(bounced - refused, sent)
    if len(by) < 10:
        return []
    usual = sorted(by.values())[len(by) // 2]
    high = sorted(d for d, r in by.items() if r >= max(.04, 2.5 * usual))
    if len(high) < 3:
        return []
    # One incident however the set of domains wobbles from day to day.
    return [finding(
        'bad_list', 'list', 'bad list', day, 'medium',
        '%d domains bounce %.1f%%-%.1f%% on addresses that do not exist; '
        'that is one bad list, the domains are fine'
        % (len(high), min(by[d] for d in high) * 100,
           max(by[d] for d in high) * 100),
        domains=high, usual=round(usual, 4))]


def gmail_gap(data, day):
    # Fleet-wide, not one domain: Gmail recipients replying a fraction as
    # often as Microsoft ones while Gmail bounces stay low.  That is sender
    # reputation at Google.  On the real fleet the gap was 4.5-5.4x in two
    # workspaces that shared nothing, which is what proved it.
    days = data.window(day, 7)
    g = data.fleet('google', days)
    m = data.fleet('microsoft', days)
    if g[SENT] < 2000 or m[SENT] < 1000:
        return []
    pg, pm = rate(g[REPLIES], g[SENT]), rate(m[REPLIES], m[SENT])
    if not pg or pm / pg < 3 or rate(g[SENDER], g[SENT]) >= .03:
        return []
    healthy = sorted(d for d, (gh, _) in data.health.items()
                     if gh is not None and gh >= 80)
    return [finding(
        'gmail_gap', 'fleet', 'google', day, 'high',
        'Gmail recipients reply %.1fx less than Microsoft ones and almost '
        'nothing bounces: Gmail is filing your mail as spam'
        % (pm / pg), google=round(pg, 4), microsoft=round(pm, 4),
        healthy=healthy)]


def rollup(kind, hits, fams, one, many, confidence='medium', **numbers):
    # One finding per domain while there are a few; past that, one for the
    # lot, because forty lines saying the same thing hide the forty-first.
    # hits: {domain: numbers}.  one(dom, n) and many(doms) make the titles.
    if not hits:
        return []
    day = numbers.pop('day')
    if len(hits) < 4:
        return [finding(kind, 'domain', d, day, confidence, one(d, n), **n)
                for d, n in sorted(hits.items())]
    doms = sorted(hits)
    return [finding(kind, 'fleet', kind, day, confidence, many(doms),
                    domains=doms, **numbers)]


def microsoft_blocks(data, day):
    # Microsoft refusing a domain outright: S3150, 5.7.708 "traffic not
    # accepted", 5.7.511 banned sender.  Rarer than Gmail's verdicts and
    # slower to lift.  Stop Microsoft recipients on that domain; Gmail and
    # the rest still take its mail.
    days = data.window(day, 2)
    hits = {}
    for dom in data.domains():
        r = data.total(dom, 'microsoft', days)
        n = sum(data.kinds[dom, d]['microsoft_block'] for d in days)
        if r[SENT] >= 2 * MIN_PROBE and n >= 10 and n >= .2 * r[SENT]:
            hits[dom] = {'rate': round(n / r[SENT], 3), 'refused': n, 'sent': r[SENT]}
    return rollup(
        'ms_block', hits, None,
        lambda d, n: 'Microsoft is refusing %.0f%% of mail from %s' % (n['rate'] * 100, d),
        lambda doms: 'Microsoft is refusing mail from %d domains' % len(doms),
        confidence='high', day=day)


def throttles(data, day, skip=()):
    # Gmail deferring a domain's mail for an "unusual rate of unsolicited
    # mail" (421 4.7.28).  Not a block yet.  It is the last warning before
    # the reputation block and the cheapest one to act on: slow down.
    days = data.window(day, 2)
    hits = {}
    for dom in data.domains():
        if dom in skip:
            continue
        g = data.total(dom, 'google', days)
        n = sum(data.kinds[dom, d]['throttle'] for d in days)
        if n >= 5 and n >= .03 * max(g[SENT], 1):
            hits[dom] = {'deferred': n, 'sent': g[SENT]}
    return rollup(
        'throttle', hits, None,
        lambda d, n: 'Gmail is slowing down mail from %s: %d deferrals in two '
                     'days.  Ease off before it turns into a block' % (d, n['deferred']),
        lambda doms: 'Gmail is slowing down mail from %d domains for an unusual '
                     'rate of unsolicited mail' % len(doms),
        day=day)


def complaints(data, day):
    # Receivers deferring or refusing over their own users' complaints:
    # Yahoo and AOL's [TSS04], "too many complaints from domain".  Spam
    # reports are what Gmail and Yahoo's 0.3% bulk-sender line is about.
    days = data.window(day, 3)
    hits = {}
    for dom in data.domains():
        n = sum(data.kinds[dom, d]['complaints'] for d in days)
        if n >= 3:
            hits[dom] = {'refused': n}
    return rollup(
        'complaints', hits, None,
        lambda d, n: 'Receivers are refusing mail from %s over their users\' '
                     'complaints (%d in three days)' % (d, n['refused']),
        lambda doms: 'Receivers are refusing mail from %d domains over their '
                     'users\' complaints' % len(doms),
        confidence='high', day=day)


def receiver_blocks(data, day):
    # One receiver refusing mail from many of your domains: Spectrum's AUP
    # block, a Mimecast or Proofpoint gateway.  Pulling the domains does
    # nothing; the receiver has made up its mind about the sender.  Hold
    # its recipients, or ask it to lift the block.
    days = set(data.window(day, 7))
    by = collections.defaultdict(collections.Counter)
    for (dom, d), c in data.blockers.items():
        if d in days:
            for who, n in c.items():
                by[who][dom] += n
    out = []
    for who, doms in sorted(by.items()):
        n = sum(doms.values())
        if n >= 50 and len(doms) >= 5:
            out.append(finding(
                'receiver', 'receiver', who, day, 'high',
                '%s refused %s messages from %d of your domains in the last week'
                % (who, format(n, ','), len(doms)),
                refused=n, domains=sorted(doms), worst=doms.most_common(5)))
    return out


def content_flags(data, day):
    # Spam filters judging the message rather than the sender ("detected as
    # spam", "high probability of spam").  Spread over many domains, it is
    # the copy, its links or its attachments, and moving domains won't help.
    days = data.window(day, 7)
    doms = collections.Counter()
    for dom in data.domains():
        n = sum(data.kinds[dom, d]['spam_filter'] for d in days)
        if n:
            doms[dom] = n
    n = sum(doms.values())
    sent = sum(data.fleet(p, days)[SENT] for p in ('google', 'microsoft', 'apple', 'other'))
    if n < 30 or len(doms) < 5 or n < .001 * sent:
        return []
    return [finding(
        'content', 'fleet', 'copy', day, 'medium',
        'Spam filters are refusing the message itself: %s refusals from %d '
        'domains this week.  That is the copy, not the domains'
        % (format(n, ','), len(doms)),
        refused=n, domains=sorted(doms), worst=doms.most_common(5))]


def link_listings(data, day):
    # A link or tracking domain in the copy on a blocklist.  Every domain
    # that sends the copy carries it, so it looks like the whole fleet is
    # listed when only one domain is.  Receivers name it in the bounce.
    days = set(data.window(day, 7))
    by = collections.defaultdict(collections.Counter)
    for (dom, d), c in data.links.items():
        if d in days:
            for link, n in c.items():
                by[link][dom] += n
    out = []
    for link, doms in sorted(by.items()):
        n = sum(doms.values())
        if len(doms) >= 3 or n >= 10:
            out.append(finding(
                'link_listed', 'link', link, day, 'high',
                'The link domain %s in your copy is on a blocklist: named in %s '
                'bounces from %d sending domains' % (link, format(n, ','), len(doms)),
                bounces=n, domains=sorted(doms)))
    return out


def warmup_drops(data, day, fams, skip=()):
    # Google's warmup score falling: the warmup mail a domain trades with
    # other inboxes is landing in spam at Gmail.  Campaign mail from the
    # same mailboxes goes the same way, usually before replies show it.
    # Only builds up over time: one score per collection.
    out = {}
    for dom, h in data.history.items():
        if dom in skip:
            continue
        seen = sorted(d for d in h if d <= day and h[d][0] is not None)
        if len(seen) < 2 or gap(seen[-1], day) > 2:
            continue
        now = h[seen[-1]][0]
        before = [h[d][0] for d in seen[:-1] if gap(d, seen[-1]) <= 14]
        if not before:
            continue
        peak = max(before)
        if now < 60 and peak - now >= 25:
            out[dom] = {'before': peak, 'now': now}
    by = collections.defaultdict(list)
    for dom in out:
        by[fams.get(dom, dom)].append(dom)
    found = []
    for name, doms in sorted(by.items()):
        if len(doms) >= 3:
            found.append(finding(
                'warmup_drop', 'family', name, day, 'medium',
                'Google warmup scores fell on %d of the %s* family\'s domains: '
                'their mail is landing in spam at Gmail' % (len(doms), name),
                domains=sorted(doms), members=sorted(doms),
                scores={d: out[d] for d in doms}))
            continue
        for dom in doms:
            n = out[dom]
            found.append(finding(
                'warmup_drop', 'domain', dom, day, 'medium',
                'Google warmup score on %s fell from %.0f to %.0f: its mail is '
                'landing in spam at Gmail' % (dom, n['before'], n['now']),
                members=[dom], **n))
    return found


def gap(a, b):
    return (datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days


def run(data, day=None, fams=None, held=()):
    day = day or data.days[-1]
    fams = fams if fams is not None else families(data.domains())
    blocks = google_blocks(data, day)
    dying = {f['target'] for f in blocks if f['kind'] in ('burned', 'rejecting')}
    return (blocks + cascades(data, day, fams, blocks)
            + quiet_spam(data, day, fams) + listings(data, day, fams)
            + tenant_blocks(data, day)
            + broken_auth(data, day) + apple(data, day)
            + bad_lists(data, day) + gmail_gap(data, day)
            + microsoft_blocks(data, day) + throttles(data, day, dying)
            + complaints(data, day) + receiver_blocks(data, day)
            + content_flags(data, day) + link_listings(data, day)
            + warmup_drops(data, day, fams, dying)
            + checks.run(data, day, held))
