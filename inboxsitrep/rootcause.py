# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# Why are they listed?
#
# Blocklists seldom pick domains one at a time.  Domains bought together,
# parked on the same nameservers, hosted on the same server or forwarding
# to the same website tend to be listed together, and pulling them one by
# one never ends.  This compares the listed domains with the clean ones on
# everything the DNS check knows about them, and reports the traits that
# are far more common among the listed than chance allows (and the ones
# that seem to protect).

import collections

from .detect import below, families
from .brief import listed_domains

MIN_GROUP = 5
SURE = 1e-4         # how unlikely the overlap must be by chance


def bucket_ns(ns):
    # ns1.cloudflare.com, ns2.cloudflare.com -> cloudflare.com
    hosts = sorted({'.'.join(n.split('.')[-2:]) for n in ns or ()})
    return ', '.join(hosts) or None


def bucket_web(ips):
    return ('%s.0/24' % ips[0].rsplit('.', 1)[0]) if ips else None


def traits(data):
    fams = families(data.domains())
    out = {}
    for dom, r in data.dns.items():
        created = r.get('created') or ''
        out[dom] = {
            'registrar': r.get('registrar'),
            'registered in': created[:7] or None,
            'nameservers': bucket_ns(r.get('ns')),
            'website server': bucket_web(r.get('web')),
            'website forwards to': r.get('redirect'),
            'mail server': ', '.join(sorted({'.'.join(m.split()[-1].rstrip('.').split('.')[-2:])
                                             for m in r.get('mx') or ()})) or None,
            'family': (fams.get(dom) + '*') if fams.get(dom) else None,
            'sending provider': data.host.get(dom) or r.get('host'),
        }
    return out


def analyze(data, listed=None, found=()):
    # [{trait, value, listed, total, share, base, lift, odds}], strongest first.
    t = traits(data)
    if listed is None:
        listed = listed_domains(data, found) if data.days else set()
    listed = set(listed) & set(t)
    if not t or not listed or len(listed) == len(t):
        return []
    base = len(listed) / len(t)
    hits = []
    for trait in next(iter(t.values())):
        groups = collections.defaultdict(list)
        for dom, v in t.items():
            if v[trait]:
                groups[v[trait]].append(dom)
        for value, doms in groups.items():
            n = len(doms)
            if n < MIN_GROUP or n == len(t):
                continue
            k = sum(1 for d in doms if d in listed)
            share = k / n
            # chance of this many or more (or this few or fewer) if listing
            # had nothing to do with the trait
            more = 1 - below(k - 1, n, base) if k else 1.0
            fewer = below(k, n, base)
            if share > base and more < SURE and k >= MIN_GROUP:
                hits.append(dict(trait=trait, value=value, listed=k, total=n, share=round(share, 3),
                                 base=round(base, 3), lift=round(share / base, 2), odds=more, kind='risk',
                                 domains=sorted(doms)))
            elif share < base and fewer < SURE:
                hits.append(dict(trait=trait, value=value, listed=k, total=n, share=round(share, 3),
                                 base=round(base, 3), lift=round(share / base, 2), odds=fewer, kind='protects',
                                 domains=sorted(doms)))
    hits.sort(key=lambda h: (h['kind'] != 'risk', h['odds'], -h['total']))
    # Two traits picking out (nearly) the same domains are one finding:
    # a family whose sites all forward to its brand is said once.
    out = []
    for h in hits:
        same = next((o for o in out if o['kind'] == h['kind'] and overlap(o['domains'], h['domains']) >= .85), None)
        if same:
            same.setdefault('also', []).append('%s %s' % (h['trait'], h['value']))
        else:
            out.append(h)
    return out


def overlap(a, b):
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a | b else 0


def words(h):
    pct = lambda x: '%.0f%%' % (100 * x)
    also = ' (the same domains as: %s)' % '; '.join(h['also']) if h.get('also') else ''
    if h['kind'] == 'risk':
        return ('%s %s: %d of %d listed (%s), against %s across the workspace%s'
                % (h['trait'].capitalize(), h['value'], h['listed'], h['total'], pct(h['share']), pct(h['base']), also))
    return ('%s %s: only %d of %d listed (%s), against %s across the workspace%s'
            % (h['trait'].capitalize(), h['value'], h['listed'], h['total'], pct(h['share']), pct(h['base']), also))


def report(data, found=(), top=8):
    hits = analyze(data, found=found)
    if not data.dns:
        return 'No DNS check yet.  Run `inboxsitrep dns` (the app runs it weekly).'
    listed = listed_domains(data, found) & set(data.dns)
    if not listed:
        return 'No domain with a DNS check is on a blocklist.'
    lines = ['%d of %d checked domains are on a blocklist.' % (len(listed), len(data.dns))]
    risk = [h for h in hits if h['kind'] == 'risk'][:top]
    safe = [h for h in hits if h['kind'] == 'protects'][:3]
    if risk:
        lines.append('What the listed domains have in common:')
        lines += ['- ' + words(h) for h in risk]
    else:
        lines.append('Nothing the DNS check knows sets the listed domains apart; look at the copy '
                     '(links, tracking) and the lists they mailed.')
    if safe:
        lines.append('Less often listed:')
        lines += ['- ' + words(h) for h in safe]
    return '\n'.join(lines)
