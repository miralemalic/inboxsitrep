# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# Checks on the state of the fleet rather than on how its mail is doing:
# mailboxes that broke, domains that went quiet, campaigns about to run
# out of leads, a sequencer's own safety switch, DNS.  What these read is a
# snapshot taken when the fleet was collected, so they only speak for the
# last day; replaying old days can't know what the mailboxes looked like.

import math
import datetime
import collections

from . import copycheck
from .data import SENT


PROVS = ('google', 'microsoft', 'apple', 'other')
RUNWAY_DAYS = 5         # warn when new leads run out sooner than this
DRY_DAYS = 2            # and when one campaign runs dry sooner than this
PER_MAILBOX = 50        # cold sends a day per mailbox past which replies fall
CAP = 30                # what to cap them at
COVER_DAYS = 30         # spares to cover this many days of losses
RESERVE_SHARE = .10     # and never fewer than this share of the domains sending


def finding(kind, scope, target, day, confidence, title, **numbers):
    return {'kind': kind, 'scope': scope, 'target': target, 'day': day,
            'confidence': confidence, 'title': title, 'numbers': numbers}


def sent(data, dom, days):
    return sum(data.total(dom, p, days)[SENT] for p in PROVS)


def date(s):
    return datetime.date.fromisoformat(s[:10])


def mailboxes(data, day):
    # Mailboxes whose connection broke send nothing and say nothing: the
    # campaign just gets slower.  On a real 50,000-mailbox workspace, 100
    # were down on an ordinary day.
    down = {d: a['down'] for d, a in data.accounts.items() if a.get('down')}
    if not down:
        return []
    boxes = sum(sum(v for k, v in a.items() if k not in ('warmup_off', 'idle'))
                for a in data.accounts.values())
    week = data.window(day, 7)
    total = sum(sent(data, d, week) for d in data.domains())
    lost = round(sum(down.values()) * total / 7 / boxes) if boxes else 0
    n = sum(down.values())
    return [finding(
        'mailboxes', 'fleet', 'mailboxes', day, 'high',
        '%d mailbox%s on %d domain%s %s disconnected and sending nothing'
        '%s' % (n, 'es' if n > 1 else '', len(down), 's' if len(down) > 1 else '',
                'are' if n > 1 else 'is',
                ' (about %s sends a day)' % format(lost, ',') if lost else ''),
        domains=sorted(down), down=n, lost=lost,
        recovery=sum(a.get('recovery', 0) for a in data.accounts.values()),
        warmup_off=sum(a.get('warmup_off', 0) for a in data.accounts.values()))]


def stalled(data, day, held=()):
    # A domain that was sending and stopped, with its mailboxes still here.
    # Out of leads, a paused campaign, mailboxes moved: whatever it is,
    # capacity is sitting idle.  Days the whole fleet rests (weekends) don't
    # count, or every Monday would be an incident.
    days = data.window(day, 21)
    total = {d: sum(data.fleet(p, [d])[SENT] for p in PROVS) for d in days}
    usual = sorted(total.values())[len(total) // 2] if total else 0
    working = [d for d in days if total[d] >= .3 * usual]
    if len(working) < 9 or working[-1] != day:
        return []
    recent, before = working[-2:], working[-9:-2]
    blocked = set()
    for (dom, d), k in data.kinds.items():
        if k['tenant_block'] and d in days:
            blocked.add(dom)
    hits = {}
    for dom in data.domains():
        if dom in held or dom in blocked or not data.mailboxes.get(dom):
            continue
        if data.accounts.get(dom, {}).get('down'):
            continue
        was = sent(data, dom, before) / len(before)
        now = sent(data, dom, recent) / len(recent)
        if was >= 50 and now < .1 * was:
            hits[dom] = (round(was), round(now))
    if not hits:
        return []
    doms = sorted(hits)
    was = sum(v[0] for v in hits.values())
    if len(doms) == 1:
        title = '%s stopped sending: %d a day last week, %d now' % (
            doms[0], hits[doms[0]][0], hits[doms[0]][1])
    else:
        title = ('%d domains stopped sending with their mailboxes still here: '
                 '%s sends a day idle' % (len(doms), format(was, ',')))
    return [finding('stalled', 'fleet' if len(doms) > 1 else 'domain',
                    'stalled' if len(doms) > 1 else doms[0], day, 'medium',
                    title, domains=doms, idle=was)]


def leads_left(data, day):
    # Unsent leads per active campaign, and new leads contacted a day lately.
    live = [c for c in data.campaigns.values() if c.get('status') == 'ACTIVE']
    left = {}
    for c in live:
        n = (c.get('lead_count') or 0) - (c.get('lead_contacted_count') or 0)
        left[c.get('name') or c.get('camp_name') or '?'] = max(0, n)
    recent = [data.fresh[d] for d in sorted(data.fresh) if d <= day][-3:]
    pace = sum(recent) / len(recent) if recent else 0
    return left, pace


def runway(data, day):
    # Campaigns running out of new leads.  A fleet with nothing to send is
    # the most expensive kind of healthy.  Each campaign's pace is its share
    # of the leads contacted so far; a campaign that runs dry tomorrow is
    # news even when the workspace as a whole has a week left.
    left, pace = leads_left(data, day)
    if not left or pace <= 0:
        return []
    total = sum(left.values())
    days = total / pace
    live = [c for c in data.campaigns.values() if c.get('status') == 'ACTIVE']
    contacted = sum(c.get('lead_contacted_count') or 0 for c in live) or 1
    dry = []
    for c in live:
        name = c.get('name') or c.get('camp_name') or '?'
        rate = pace * (c.get('lead_contacted_count') or 0) / contacted
        if rate and left.get(name, 0) / rate < DRY_DAYS:
            dry.append(name)
    if days >= RUNWAY_DAYS and not dry:
        return []
    low = sorted(left.items(), key=lambda kv: kv[1])[:5]
    if total == 0:
        title = 'Active campaigns have no new leads left'
    else:
        title = ('Active campaigns run out of new leads in about %s: %s left, '
                 '%s new leads contacted a day'
                 % ('%.0f days' % days if days >= 1.5 else 'a day',
                    format(total, ','), format(round(pace), ',')))
        if dry:
            title = ('%d campaign%s run%s out of new leads within %d days (%s%s); the workspace in about %s'
                     % (len(dry), 's' if len(dry) > 1 else '', '' if len(dry) > 1 else 's', DRY_DAYS,
                        ', '.join(sorted(dry)[:3]), ' and %d more' % (len(dry) - 3) if len(dry) > 3 else '',
                        '%.0f days' % days if days >= 1.5 else 'a day'))
    return [finding('runway', 'fleet', 'leads', day, 'high', title,
                    left=total, pace=round(pace), days=round(days, 1),
                    campaigns=len(live), lowest=low, dry=sorted(dry))]


def bounce_guard(data, day):
    # The sequencer's own safety switch: PlusVibe pauses a campaign whose
    # bounce rate passes its limit.  On a real fleet it tripped on the day
    # Microsoft blocked the sending tenants, so the cause is usually in
    # another incident; this one says the campaigns stopped.
    hits = []
    for c in data.campaigns.values():
        at = c.get('last_paused_at_bounced')
        if at and 0 <= (date(day) - date(at)).days <= 3:
            hits.append((at[:10], c.get('name') or c.get('camp_name') or '?',
                         c.get('status')))
    if not hits:
        return []
    hits.sort()
    still = [h for h in hits if h[2] == 'PAUSED']
    return [finding(
        'bounce_guard', 'fleet', 'campaigns', day, 'high',
        'PlusVibe paused %d campaign%s for bounce rate since %s; %d still paused'
        % (len(hits), '' if len(hits) == 1 else 's', hits[0][0], len(still)),
        campaigns=[h[1] for h in hits], paused=[h[1] for h in still],
        limit=next((c.get('bounce_rate_limit') for c in data.campaigns.values()
                    if c.get('bounce_rate_limit')), None))]


def dns_posture(data, day):
    # What a DNS check found, grouped by what is wrong.  Gmail and Yahoo
    # refuse bulk mail without SPF, DKIM and DMARC since February 2024.
    issues = collections.defaultdict(list)
    for dom, r in data.dns.items():
        if not data.mailboxes.get(dom) and dom not in data.workspace:
            continue
        for issue in r.get('issues', ()):
            issues[issue].append(dom)
    out = []
    for issue, doms in sorted(issues.items()):
        what = ISSUES.get(issue, issue)
        out.append(finding(
            'dns', 'dns', issue, day, 'high',
            what % (len(doms), 's' if len(doms) > 1 else ''),
            domains=sorted(doms), issue=issue))
    return out


ISSUES = {
    'no_mx': '%d domain%s with no mail server (MX): replies have nowhere to go',
    'parked': '%d domain%s still parked at the registrar: DNS was never set up for mail',
    'no_spf': '%d domain%s with no SPF record',
    'spf_misses_host': '%d domain%s whose SPF doesn\'t name the provider that sends their mail',
    'spf_lookups': '%d domain%s whose SPF needs more than 10 DNS lookups (receivers treat it as broken)',
    'spf_many': '%d domain%s with more than one SPF record (receivers treat it as broken)',
    'no_dmarc': '%d domain%s with no DMARC record',
    'no_dkim': '%d domain%s with no DKIM key where their provider puts it',
    'expired': '%d domain%s whose registration has expired: renew or retire',
    'expiring': '%d domain%s whose registration runs out within 30 days',
    'unregistered': '%d domain%s no longer registered to anyone',
}


def campaign_settings(data, day):
    # Campaign settings that cost delivery, from the sequencer's own record.
    # Open tracking puts a pixel and a tracking link domain in every email;
    # filters key on both.  Risky (catch-all, unverified) addresses bounce
    # more and some are traps.
    live = [c for c in data.campaigns.values() if c.get('status') == 'ACTIVE']
    out = []
    for key, kind, title in (
            ('is_emailopened_tracking', 'tracking', '%d of %d active campaigns track opens: '
             'the pixel and its link domain cost inbox placement'),
            ('send_risky_email', 'risky', '%d of %d active campaigns send to risky '
             '(catch-all or unverified) addresses')):
        hits = [c.get('name') or c.get('camp_name') or '?' for c in live if copycheck.switched_on(c.get(key))]
        if hits:
            out.append(finding('settings', 'campaigns', kind, day, 'high',
                               title % (len(hits), len(live)), campaigns=sorted(hits)))
    return out


def volume(data, day):
    # Mailboxes pushed too hard.  Field data from 2026: 20-49 sends a day
    # per mailbox replied at 5.7%, 100+ at 1.4% with bounces 4x higher.
    days = [d for d in data.window(day, 7) if any(data.fleet(p, [d])[SENT] for p in PROVS)][-3:]
    if not days:
        return []
    hot = {}
    for dom in data.domains():
        boxes = (data.accounts.get(dom) or {}).get('active') or data.mailboxes.get(dom) or 0
        if boxes:
            rate = sent(data, dom, days) / len(days) / boxes
            if rate > PER_MAILBOX:
                hot[dom] = round(rate)
    if not hot:
        return []
    top = max(hot.values())
    return [finding('volume', 'fleet', 'volume', day, 'high',
                    '%d domain%s send over %d a day per mailbox (up to %d): replies fall and bounces '
                    'climb past that' % (len(hot), 's' if len(hot) > 1 else '', PER_MAILBOX, top),
                    domains=sorted(hot), per_mailbox=hot, cap=CAP)]


# Domains lost to these, counted for the burn rate.
LOST = ('burned', 'tenant_block', 'ms_block', 'cascade')


def reserve(data, found, day):
    # How long the spares last at the rate domains are being lost.  At scale
    # 10-20% of sending domains burn a month, so operators keep spares warmed
    # and idle: here a month of the workspace's own losses, never under 10%.
    # Needs the sequencer
    # to say which mailboxes are in no campaign (PlusVibe does).
    if not any('idle' in a for a in data.accounts.values()):
        return None
    recent = [d for d in data.window(day, 7) if any(data.fleet(p, [d])[SENT] for p in PROVS)][-3:]
    sending = {d for d in data.domains() if sent(data, d, recent)}
    def warmed(d):
        score = (data.health.get(d) or (None,))[0]
        return score is None or score >= 70
    spare = {d for d, a in data.accounts.items() if a.get('active') and a.get('idle', 0) >= a['active']
             and d not in sending and warmed(d)}
    since = (date(day) - datetime.timedelta(days=30)).isoformat()
    lost = set()
    for i in found:
        if i['kind'] in LOST and i['opened'] >= since:
            ev = i['evidence']
            got = set(ev.get('dead') or ()) | set(ev.get('domains') or ()) | set(ev.get('gone') or ())
            lost |= (got or ({i['target']} if i['scope'] == 'domain' else set())) - set(ev.get('recovered') or ())
    month, tenth = math.ceil(len(lost) * COVER_DAYS / 30), math.ceil(len(sending) * RESERVE_SHARE)
    return {'sending': len(sending), 'spare': len(spare), 'lost': len(lost), 'want': max(month, tenth),
            'buy': max(0, max(month, tenth) - len(spare)), 'why': 'month' if month >= tenth else 'tenth',
            'weeks': round(4.3 * len(spare) / len(lost), 1) if lost else None}


def run(data, day, held=()):
    if not data.days or day != data.days[-1]:
        return []
    return (mailboxes(data, day) + stalled(data, day, held) + runway(data, day)
            + bounce_guard(data, day) + dns_posture(data, day)
            + campaign_settings(data, day) + volume(data, day))
