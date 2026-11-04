# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# Score ways of running a fleet against the truth.
#
# Each policy runs the same simulated fleet for sixty days and acts on what
# it can see.  The fleet doesn't know who is running it, so any difference
# in the outcome comes from the decisions alone:
#
#   nothing      never touches anything
#   bounce rule  a common rule of thumb: pull a domain once its bounce rate
#                over the last 3 days passes 5%
#   inboxsitrep    the detectors and plans in this package, every plan
#                approved
#   perfect      knows every failure the morning it starts and does the
#                right thing; a ceiling, not something anyone can reach

import statistics
import collections

from . import PROVIDERS, detect, sim
from .data import SENT, BOUNCES
from .plan import Operator, change


HORIZON = 60
POLICIES = ('nothing', 'bounce rule', 'inboxsitrep', 'perfect')
RULE_DAYS, RULE_LINE = 3, .05


def bounce_rule(fleet, day, days=RULE_DAYS, line=RULE_LINE):
    window = fleet.data.window(day, days)
    out = []
    for d in fleet.doms:
        if d in fleet.retired:
            continue
        sent = bounced = 0
        for p in PROVIDERS:
            r = fleet.data.total(d, p, window)
            sent += r[SENT]
            bounced += r[BOUNCES]
        if sent >= 300 and bounced / sent > line:
            out.append(d)
    if not out:
        return []
    return [change('retire', out, 'bounce rate over %.0f%%' % (line * 100))]


def perfect_plan(fleet):
    fams = collections.defaultdict(list)
    for d, stem in fleet.family.items():
        fams[stem].append(d)
    ahead = collections.defaultdict(list)
    for kind, target, t, *rest in fleet.truth:
        if kind == 'dead':
            ahead[t].append(change('stop', [target], '', 'google'))
        elif kind == 'cascade':
            ahead[t].append(change('stop', fams[target], '', 'google'))
        elif kind == 'quiet_spam':
            ahead[t].append(change('stop', target, '', 'google'))
        elif kind == 'episode':
            ahead[t].append(change('stop', [target], '', 'google'))
            ahead[rest[0]].append(change('resume', [target], '', 'google'))
        elif kind == 'listed':
            ahead[t].append(change('pause', [target], ''))
        elif kind == 'auth':
            ahead[t].append(change('fix_dns', [target], ''))
            ahead[t].append(change('pause', [target], ''))
            ahead[t + 1].append(change('unpause', [target], ''))
        elif kind == 'apple':
            ahead[t].append(change('suppress_apple', [], ''))
        elif kind == 'tenant_block':
            ahead[t].append(change('pause', target, ''))
        elif kind == 'bad_list':
            ahead[t].append(change('scrub', target, ''))
        elif kind == 'ms_block':
            ahead[t].append(change('stop', [target], '', 'microsoft'))
        elif kind == 'throttle':
            ahead[t].append(change('slow', [target], '', 'google'))
        elif kind == 'disconnect':
            ahead[t].append(change('reconnect', target, ''))
        elif kind == 'link_listed':
            ahead[t].append(change('remove_link', [target], ''))
    return ahead


def run(seed, policy, days=HORIZON, scenario='standard'):
    fleet = sim.SCENARIOS[scenario](sim.Fleet(seed))
    op = Operator()
    fams = detect.families(fleet.doms)
    ahead = perfect_plan(fleet) if policy == 'perfect' else {}
    acted = {}

    def act(c, t):
        fleet.apply(c)
        op = c['op']
        if op == 'stop' and c.get('provider') == 'microsoft':
            op = 'stop_microsoft'
        for d in c['targets'] or ['fleet']:
            acted.setdefault((op, d), t)

    for t in range(days):
        for c in ahead.get(t, ()):
            act(c, t)
        day = fleet.step()
        if policy == 'bounce rule':
            todo = bounce_rule(fleet, day)
        elif policy == 'inboxsitrep':
            todo = op.step(detect.run(fleet.data, day, fams, op.held()), fleet.data)
        else:
            todo = []
        for c in todo:
            act(c, t)
    return fleet, acted


def first(acted, ops, doms, share=None):
    # First day the right thing was done; with `share`, the day it had been
    # done to that share of `doms`.
    days = []
    for d in doms:
        hit = [acted[o, d] for o in ops if (o, d) in acted]
        if hit:
            days.append(min(hit))
    if not days:
        return None
    if share is None:
        return min(days)
    days.sort()
    need = max(1, int(len(doms) * share + .5))
    return days[need - 1] if len(days) >= need else None


def judge(fleet, acted):
    fams = collections.defaultdict(list)
    for d, stem in fleet.family.items():
        fams[stem].append(d)
    stops = ('stop', 'pause', 'retire')
    lags, missed = [], []
    for kind, target, t, *_ in fleet.truth:
        if kind == 'dead':
            done = first(acted, stops, [target])
        elif kind == 'cascade':
            done = first(acted, stops, fams[target], share=.5)
        elif kind == 'quiet_spam':
            done = first(acted, stops, target, share=.5)
        elif kind == 'listed':
            # Pausing it, or (when receivers still take its mail) asking for
            # removal and keeping it sending; what it wastes meanwhile still
            # counts against the leads.
            done = first(acted, ('pause', 'retire', 'delist'), [target])
        elif kind == 'auth':
            done = first(acted, ('fix_dns',), [target])
        elif kind == 'apple':
            done = acted.get(('suppress_apple', 'fleet'))
        elif kind == 'bad_list':
            done = first(acted, ('scrub',), target)
        elif kind == 'tenant_block':
            done = first(acted, ('pause', 'retire'), target, share=.5)
        elif kind == 'ms_block':
            done = first(acted, ('stop_microsoft', 'pause', 'retire'), [target])
        elif kind == 'throttle':
            # Only counts if it beat the block: three days.
            done = first(acted, ('slow', 'stop'), [target])
            done = done if done is not None and done - t <= 3 else None
        elif kind == 'disconnect':
            done = first(acted, ('reconnect',), target)
        elif kind == 'link_listed':
            done = first(acted, ('remove_link',), [target])
        else:
            continue
        if done is not None and done - t <= 7:
            lags.append(max(0, done - t))
        else:
            missed.append((kind, target))
    harmed = {target for kind, target, *_ in fleet.truth
              if kind in ('dead', 'listed', 'auth', 'ms_block', 'throttle')}
    for kind, target, *_ in fleet.truth:
        if kind == 'tenant_block':
            harmed.update(target)
    pulled = {d for (o, d) in acted if o in ('pause', 'retire')} - harmed
    touched = {d for (o, d) in acted if o in ('stop', 'stop_microsoft', 'pause', 'retire')}
    tally = fleet.tally
    return {'positives': tally['positives'],
            'wasted': tally['burned'] + tally['spammed'],
            'burned': tally['burned'],
            'spammed': tally['spammed'],
            'unsent': tally['unsent'],
            'handled': len(lags),
            'problems': len(lags) + len(missed),
            'lags': lags,
            'missed': missed,
            'pulled': len(pulled),
            'touched': len(touched)}


def scorecard(seeds=range(1, 21), days=HORIZON, out=print):
    seeds = list(seeds)
    results = collections.defaultdict(list)
    calm = collections.defaultdict(list)
    for seed in seeds:
        for policy in POLICIES:
            fleet, acted = run(seed, policy, days)
            results[policy].append(judge(fleet, acted))
            if policy != 'perfect':
                fleet, acted = run(seed, policy, days, scenario='clean')
                calm[policy].append(judge(fleet, acted))
    best = [r['positives'] for r in results['perfect']]
    rows = []
    for policy in POLICIES:
        rs = results[policy]
        kept = statistics.mean(r['positives'] / b for r, b in zip(rs, best))
        lags = [x for r in rs for x in r['lags']]
        quiet = ('%.1f' % statistics.mean(r['touched'] for r in calm[policy])
                 if policy in calm else '-')
        rows.append((policy,
                     '{:,.0f}'.format(statistics.mean(r['wasted'] for r in rs)),
                     '%d/%d' % (sum(r['handled'] for r in rs),
                                sum(r['problems'] for r in rs)),
                     '%.1f' % statistics.mean(lags) if lags else '-',
                     '%.1f' % statistics.mean(r['pulled'] for r in rs),
                     quiet,
                     '%.1f%%' % (kept * 100)))
    head = ('', 'leads wasted', 'problems handled', 'days to act',
            'healthy domains pulled', 'false alarms', 'positive replies kept')
    table(head, rows, out)
    out('\n%d simulated fleets, %d days each, averages per fleet.\n'
        '  leads wasted      sent from a domain the receiver refused, or '
        'into Gmail spam\n'
        '  problems handled  injected failures acted on correctly within '
        '7 days\n'
        '  false alarms      domains stopped or pulled on the same fleets '
        'with nothing wrong' % (len(seeds), days))
    return results, calm


def table(head, rows, out=print):
    widths = [max(len(str(r[i])) for r in [head] + rows)
              for i in range(len(head))]
    for r in [head] + rows:
        out('  '.join(str(c).rjust(w) if i else str(c).ljust(w)
                      for i, (c, w) in enumerate(zip(r, widths))))
