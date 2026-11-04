# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# One verdict per domain, and one per campaign: keep it, fix it, or stop it,
# with the reason in numbers.
#
# The ladder follows the one in Eric Nowoslawski's coldoutboundskills (MIT):
# judge nothing below a floor of data, find out whose fault the bounces are
# before blaming the domain, and only then look at replies.  Changed for
# fleets like the ones this was built on: bounces are read from 500 sends and
# replies from 1,000 (at 200, a 1% reply rate is two replies, which says
# nothing); replies are judged against the fleet's own positive-reply rate
# with a binomial test, not a fixed 1%, because AI-agent fleets reply at 4-6%;
# and a blocklist is something to get off, not a death sentence: on a real
# fleet 70% of the domains were on SURBL and most replied like the rest.

import collections

from .data import SENT, REPLIES, POSITIVES, BOUNCES, SENDER
from .detect import below

FLOOR = 1000            # sends before replies (and campaigns) are judged
BOUNCE_FLOOR = 500      # sends before a bounce rate means something
BOUNCE_SAMPLE = 30      # bounces before saying whose fault they are
HIGH_BOUNCE = .05       # a bounce rate that says something is wrong
REFUSED = .04           # refused by receivers for who sent it: the domain is the problem
MIXED = .02             # refused this much inside a high bounce rate: list and domain both
RECENT_DAYS = 3         # sending days, at least, behind a domain's verdict
REACH = 31              # and at most this many collected days
CAMPAIGN_BOUNCE = .05   # when a campaign sets no bounce limit of its own
WINNER = 2              # a campaign replying this many times the rest is a winner
LOSER = .5              # this fraction or less, a loser
MIN_REPLY = 0           # and under this reply rate, a loser whatever the rest do (0: off)
SURE = .01              # how unlikely a gap must be by chance
PROVIDERS = ('google', 'microsoft', 'apple', 'other')

# Every kind of bounce text, by whose problem it is.  'none' is neither the
# list's nor the domain's: temporary slow-downs, full inboxes, groups that
# only take internal mail, and blocklists naming the provider's shared IP.
BUCKETS = [
    ('list', 'Address does not exist', 'list', ('user_unknown',)),
    ('blocklist', 'Blocklist names the domain', 'sender', ('surbl', 'spamhaus')),
    ('reputation', 'Refused on reputation', 'sender',
     ('gmail_reputation', 'microsoft_block', 'isp_block', 'gateway_block', 'ip_block',
      'complaints', 'policy', 'apple_cs01')),
    ('auth', 'SPF, DKIM or DMARC', 'sender', ('auth',)),
    ('tenant', 'Sending account blocked (Microsoft 365)', 'sender', ('tenant_block',)),
    ('content', 'Spam filter judged the copy', 'copy', ('spam_filter',)),
    ('throttle', 'Slowed down (temporary)', 'none', ('throttle',)),
    ('noise', 'Not a reputation signal', 'none',
     ('mailbox_full', 'restricted', 'expired', 'loop', 'spamhaus_ip', 'surbl_ip', 'other')),
]
BUCKET_OF = {k: b for b, _, _, kinds in BUCKETS for k in kinds}

VERDICTS = {
    'not_enough_data': ('Not enough data', 'Fewer than {bounce_floor} sends in this range.  Pick a longer range.'),
    'early': ('Too early for replies', 'Bounces look normal; replies are judged from {reply_floor} sends.'),
    'account_blocked': ('Account blocked', 'Microsoft has blocked the sending account (5.7.237): nothing it '
                        'sends arrives.  Take it out of campaigns and fix it with the mailbox provider.'),
    'not_enough_bounces': ('Bounces unclear', 'Over {high}% bounced, but there are too few bounces to say whose fault '
                           'they are.  Look again when more have come in.'),
    'bad_list': ('Bad list', 'Over {high}% bounced, almost all bad addresses; receivers refuse under {mixed}% because of '
                 'the domain.  Verify the list; the domain is fine.'),
    'mixed': ('List and domain', 'Over {high}% bounced: bad addresses, and receivers refusing {mixed}-{refused}% because of '
              'the sending domain.  Clean the list first, then watch the domain.'),
    'fix_auth': ('Fix authentication', 'Refused over SPF, DKIM or DMARC.  Fix the record and look again '
                 'in a week.  Do not replace it.'),
    'silent': ('No reply or bounce data', 'Over {reply_floor} sends counted and not one reply or bounce, which real '
               'sending never does.  The sequencer has no record of what happened to this mail.  Check one '
               'mailbox there before acting on the domain.'),
    'gmail_blocked': ('Gmail blocks it', 'Gmail refuses a large share of its mail while Microsoft still takes it.  '
                      'Stop sending it to Gmail addresses, keep it on Microsoft and others, and probe Gmail '
                      'with a few sends a day until it takes mail again.'),
    'microsoft_blocked': ('Microsoft blocks it', 'Microsoft refuses a large share of its mail while Gmail still '
                          'takes it.  Stop sending it to Microsoft addresses and keep it on Gmail.'),
    'copy': ('Copy refused', 'Over {refused}% refused, mostly spam filters judging the message itself.  Fix the copy '
             '(links, tracking, wording) before blaming the domain.'),
    'refused': ('Refused', 'Receivers refuse over {refused}% of its mail because of the domain, but it still '
                'earns replies.  Pause cold sends for a week (keep warmup), then 10-15 a day to verified '
                'leads, 20-25 the week after if bounces stay under 2%.'),
    'burned': ('Burned', 'Receivers refuse over {refused}% of its mail because of the domain, and it earns little: positive '
               'replies well below the rest of the workspace, or replies under 1%.  Pause it and replace it.'),
    'listed_low': ('Blocklisted, replies down', 'On a blocklist and earning well below the rest of the workspace.  Slow it down '
                   'and get it removed from the list (see below).'),
    'listed': ('Blocklisted, still working', 'On a blocklist, but its positive replies hold up.  Get it removed '
               'from the list (see below) and keep it sending.'),
    'underperforming': ('Underperforming', 'It delivers, but replies are under 1% or positive replies are well '
                        'below the rest of the workspace.  Check Gmail placement and which campaigns it sends.'),
    'healthy': ('Healthy', 'Keep it sending.'),
    'strong': ('Strong', 'Well above the rest of the workspace.  Keep it sending.'),
}
GOOD = ('healthy', 'strong', 'listed')        # what the guard protects

# How to get off each list.  First stop what put the domain there (a
# tracking or link domain in the copy, a jump in volume, complaints), or
# it is listed again within days.
DELIST = {
    'spamhaus': 'Spamhaus DBL: look the domain up at check.spamhaus.org and use its removal form.  Removal is '
                'quick the first times; repeat listings take longer.',
    'surbl': 'SURBL: look the domain up at surbl.org/surbl-analysis and send the removal request from there.  '
             'SURBL lists domains seen in spam, so check the links in the copy first.',
    'uribl': 'URIBL: look the domain up at admin.uribl.com and request removal.',
    'barracuda': 'Barracuda: request removal at barracudacentral.org/rbl/removal-request (it lists IPs: that is '
                 'the mailbox provider\'s to fix).',
}


def buckets(data, dom, days):
    out = collections.Counter()
    for day in days:
        for kind, n in (data.kinds.get((dom, day)) or {}).items():
            out[BUCKET_OF.get(kind, 'noise')] += n
    return out


def totals(data, dom, days):
    t = [0, 0, 0, 0, 0]
    for p in PROVIDERS:
        for i, v in enumerate(data.total(dom, p, days)):
            t[i] += v
    return t


def times(x):
    return {2: 'Twice', .5: 'Half of', 3: 'Three times', .25: 'A quarter of'}.get(x, '%g times' % x)


def pct(x):
    return '%g' % round(100 * x, 1)


def said(text):
    # A verdict's words with the numbers the rules file sets (rules.py).
    return text.format(bounce_floor=format(BOUNCE_FLOOR, ','), reply_floor=format(FLOOR, ','), high=pct(HIGH_BOUNCE),
                       refused=pct(REFUSED), mixed=pct(MIXED), winner=times(WINNER), loser=times(LOSER),
                       under=', or under %s%% replies' % pct(MIN_REPLY) if MIN_REPLY else '', limit=pct(CAMPAIGN_BOUNCE))


def latest(data, dom, days):
    # The domain's latest sends in these days: back from the last day until
    # 1,000 sends over at least three sending days.  A verdict says how the
    # domain stands now; averaged over a longer range, a clean week would
    # hide a problem that started three days ago.
    out, sent, sending = [], 0, 0
    for day in reversed(days):
        out.append(day)
        n = sum(data.total(dom, p, [day])[SENT] for p in PROVIDERS)
        sent += n
        sending += 1 if n else 0
        if sent >= FLOOR and sending >= RECENT_DAYS:
            break
    return out[::-1]


def domains(data, days):
    # {domain: verdict row} for every domain that sent in these days.  The
    # numbers cover the days; the verdict, the domain's latest sends up to
    # their last day, reaching back past their start when it takes that, so
    # the verdict does not change with the range picked.
    rows, fleet = {}, [0, 0, 0, 0, 0]
    gone = unlabeled(data)
    for dom in data.domains():
        t = totals(data, dom, days)
        if t[SENT]:
            rows[dom] = t
            if dom not in gone:         # no positives known, so no part of the baseline
                fleet = [a + b for a, b in zip(fleet, t)]
    base = fleet[POSITIVES] / fleet[SENT] if fleet[SENT] else 0
    out = {}
    for dom, t in rows.items():
        row = judge(data, dom, days, t, base)
        now = latest(data, dom, [d for d in data.days if d <= days[-1]][-REACH:])
        v = row if now == list(days) else judge(data, dom, now, totals(data, dom, now), base)
        for k in ('verdict', 'label', 'action', 'delist', 'buckets', 'classified', 'lists'):
            if k in v:
                row[k] = v[k]
            else:
                row.pop(k, None)
        row['judged'] = {'from': now[0], 'to': now[-1], 'sent': v['sent'], 'bounce_rate': v['bounce_rate'],
                         'sender_bounce_rate': v['sender_bounce_rate'], 'per_1000': v['per_1000']}
        out[dom] = row
    return out


def lists_of(data, dom, days):
    # Which blocklists name the domain: in DNS, or in receivers' bounces.
    out = set()
    where = data.listed_on(dom, days[-1])
    if where:
        out.add('spamhaus' if 'spamhaus' in where or where == 'dbl' else where)
    for day in days:
        for kind, n in (data.kinds.get((dom, day)) or {}).items():
            if n and kind in ('surbl', 'spamhaus'):
                out.add(kind)
    return sorted(out)


def judge(data, dom, days, t, base):
    sent, bounced, sender, pos = t[SENT], t[BOUNCES], t[SENDER], t[POSITIVES]
    b = buckets(data, dom, days)
    judged = sum(n for k, n in b.items() if k not in ('noise', 'throttle'))
    lists = lists_of(data, dom, days)
    row = {'domain': dom, 'sent': sent, 'replies': t[REPLIES], 'positives': pos,
           'per_1000': round(1000 * pos / sent, 1) if sent else None,
           'fleet_per_1000': round(1000 * base, 1),
           'reply_rate': round(100 * t[REPLIES] / max(1, sent - bounced), 2),
           'bounce_rate': round(100 * bounced / sent, 2) if sent else None,
           'sender_bounce_rate': round(100 * sender / sent, 2) if sent else None,
           'list_bounce_rate': round(100 * (bounced - sender) / sent, 2) if sent else None,
           'buckets': dict(b), 'classified': judged, 'lists': lists}
    if dom in unlabeled(data):
        row['per_1000'], row['unlabeled'] = None, True
    v = ladder(data, dom, days, sent, bounced, sender, pos, base, b, judged, lists)
    row['verdict'] = v
    row['label'], row['action'] = VERDICTS[v][0], said(VERDICTS[v][1])
    if v in ('listed', 'listed_low'):
        row['delist'] = [DELIST[x] for x in lists if x in DELIST]
    return row


def unlabeled(data):
    # Domains whose replies came from mailbox counts, their leads deleted.
    return set((getattr(data, 'raw', None) or {}).get('unlabeled') or ())


def ladder(data, dom, days, sent, bounced, sender, pos, base, b, judged, lists):
    # First rule that matches wins.  Bounces are read first (from 500
    # sends), then replies (from 1,000).
    if sent < BOUNCE_FLOOR:
        return 'not_enough_data'
    if judged >= BOUNCE_SAMPLE and b['tenant'] > .5 * judged:
        return 'account_blocked'
    # Low: well below the workspace, or under 1% replies whatever the rest
    # do (Eric Nowoslawski's line), so a dead workspace can't make its
    # domains look healthy.
    replies = totals(data, dom, days)[REPLIES]
    # Positive labels live on the leads; when a campaign was deleted with
    # its leads, only the replies are left to judge by.
    labelled = dom not in unlabeled(data)
    low = sent >= FLOOR and ((labelled and bool(base) and pos < .5 * base * sent and below(pos, sent, base) < SURE)
                             or replies < .01 * (sent - bounced))
    # One provider refusing while the other takes the mail: the cure is to
    # stop that provider, not the domain.
    g, m = data.total(dom, 'google', days), data.total(dom, 'microsoft', days)
    def refused(t):
        return t[SENDER] / t[SENT] if t[SENT] >= 200 else 0
    if refused(g) >= .2 and refused(m) < .05:
        return 'gmail_blocked'
    if refused(m) >= .2 and refused(g) < .05:
        return 'microsoft_blocked'
    # Refused for who sent it: the sequencer's own split, or failing that
    # the share of bounce texts that blame the sender.
    sides = sum(n for k, n in b.items() if k in ('blocklist', 'reputation', 'auth', 'tenant', 'content'))
    refusals = sender if sender else (bounced * sides / judged if judged else 0)
    if refusals >= REFUSED * sent:
        if bounced < BOUNCE_SAMPLE:
            return 'not_enough_bounces'
        if b['auth'] >= max(b['reputation'], b['blocklist'], 1) or data.auth_missing(dom, days[-1]):
            return 'fix_auth'
        if b['content'] > b['reputation'] + b['blocklist'] + b['tenant']:
            return 'copy'
        return 'burned' if low else 'refused'
    if bounced >= HIGH_BOUNCE * sent:
        if bounced < BOUNCE_SAMPLE:
            return 'not_enough_bounces'
        return 'mixed' if refusals >= MIXED * sent else 'bad_list'
    if sent < FLOOR:
        return 'early'
    if not bounced and not replies:
        return 'silent'
    if lists:
        return 'listed_low' if low else 'listed'
    if low:
        return 'underperforming'
    if base and pos > 1.5 * base * sent and 1 - below(pos - 1, sent, base) < SURE:
        return 'strong'
    return 'healthy'


# Campaigns: lifetime totals from the sequencer, judged against the
# workspace's own positive-reply rate.  Twice the baseline is a winner to
# scale, half is a loser to stop, in between is worth iterating.
CAMPAIGN_VERDICTS = {
    'too_early': ('Too early', 'Fewer than {reply_floor} sent.  Judge it later.'),
    'winner': ('Winner', '{winner} the rest of the workspace\'s reply rate or better, with bounces under its limit.  Give it '
               'more leads and mailboxes.'),
    'iterate': ('Average', 'Near the workspace\'s reply rate.  Try a new angle or a new list on it.'),
    'loser': ('Loser', '{loser} the rest of the workspace\'s reply rate or less{under}.  Stop it and put its mailboxes elsewhere.'),
    'bad_list': ('Bad list', 'Bounces over the campaign\'s limit ({limit}% unless set), mostly addresses that do not '
                 'exist.  Verify its leads before judging its replies.'),
    'bounces_high': ('Bounces too high', 'Bounces over the campaign\'s limit ({limit}% unless set), much of it receivers '
                     'refusing the sending domains.  Fix that before judging its replies: move it to healthier '
                     'mailboxes and verify the list.'),
}
# Campaigns are judged on what they say for themselves: their reply rate
# against the workspace's, and their bounce rate against their own limit.
# (Replies tagged positive depend on who tags them and how; on one real
# workspace the share ran from 5% to 56% between kinds of campaign.)


def campaigns(data):
    key = 'replies'
    rows = []
    for cid, c in data.campaigns.items():
        sent = c.get('sent_count') or 0
        rows.append({'id': cid, 'name': c.get('name') or c.get('camp_name') or cid, 'status': c.get('status'),
                     'sent': sent, 'replies': c.get('replied_count') or 0,
                     'positives': c.get('positive_reply_count') or 0,
                     'bounced': c.get('bounced_count') or 0,
                     'sender_bounced': c.get('sender_bounced_count') or 0,
                     'leads': c.get('lead_count'), 'contacted': c.get('lead_contacted_count'),
                     'last_sent': (c.get('last_lead_sent') or '')[:10] or None,
                     'copy': c.get('copy'),
                     # the campaign's own pause line, as the sequencer has it
                     'limit': (float(c['bounce_rate_limit']) / 100 if c.get('bounce_rate_limit') else CAMPAIGN_BOUNCE)})
    judged = [r for r in rows if r['sent'] >= FLOOR]
    all_k, all_sent = sum(r[key] for r in judged), sum(r['sent'] for r in judged)
    for r in rows:
        sent, k = r['sent'], r[key]
        # Against the rest of the workspace: a strong campaign must not
        # raise the bar it is judged by.
        rest = all_sent - (sent if r in judged else 0)
        base = (all_k - (k if r in judged else 0)) / rest if rest else 0
        r['per_1000'] = round(1000 * r['positives'] / sent, 1) if sent else None
        r['reply_rate'] = round(100 * r['replies'] / sent, 2) if sent else None
        r['bounce_rate'] = round(100 * r['bounced'] / sent, 2) if sent else None
        r['sender_bounce_rate'] = round(100 * r['sender_bounced'] / sent, 2) if sent else None
        r['left'] = (r['leads'] - r['contacted']) if r['leads'] is not None and r['contacted'] is not None else None
        r['baseline'] = round(100 * base, 2)
        if sent < FLOOR:
            v = 'too_early'
        elif r['bounced'] > r['limit'] * sent:
            # Over the line nothing else is judged: a winner that bounces
            # 6% is spending its domains.
            v = 'bad_list' if r['sender_bounced'] < .25 * r['bounced'] else 'bounces_high'
        # Twice or half the baseline, and too far from it to be luck: with
        # a baseline of 0.2 per 1,000, one reply would otherwise make a winner.
        elif base and k >= WINNER * base * sent and 1 - below(k - 1, sent, base) < SURE:
            v = 'winner'
        elif base and k <= LOSER * base * sent and below(k, sent, base) < SURE:
            v = 'loser'
        elif MIN_REPLY and k < MIN_REPLY * sent:
            v = 'loser'
        else:
            v = 'iterate'
        r['verdict'] = v
        r['label'], r['action'] = CAMPAIGN_VERDICTS[v][0], said(CAMPAIGN_VERDICTS[v][1])
    order = {'winner': 0, 'loser': 1, 'bounces_high': 2, 'bad_list': 3, 'iterate': 4, 'too_early': 5}
    return sorted(rows, key=lambda r: (order[r['verdict']], -(r['sent'] or 0)))
