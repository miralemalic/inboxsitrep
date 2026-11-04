# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# What each sending domain's DNS says: mail server, SPF, DKIM, DMARC, and
# whether SURBL lists it.  Asked over DNS-over-HTTPS: plain DNS on port 53
# is intercepted on some networks (it was on the one this was written on),
# and Spamhaus refuses public resolvers outright, so DBL is left to the
# bounces, which name the list anyway.

import json
import time
import datetime
import urllib.error
import urllib.parse
import urllib.request
import concurrent.futures

DOH = 'https://cloudflare-dns.com/dns-query'
# Cloudflare can't reach SURBL's servers ("no reachable authority");
# Google's resolver can, and answers its test entry correctly.
DOH_SURBL = 'https://dns.google/resolve'
TYPES = {'A': 1, 'CNAME': 5, 'MX': 15, 'TXT': 16}
# Where each provider's SPF include and DKIM keys live.
INCLUDE = {'google': '_spf.google.com', 'microsoft': 'spf.protection.outlook.com'}
SELECTORS = {'google': ('google',), 'microsoft': ('selector1', 'selector2')}
# Registrars' parking services.  A sending domain whose MX points here was
# never set up for mail: found on a real fleet sending from Microsoft 365
# with the parking service's SPF (a private IPv6 range, then -all).
PARKED = ('above.com', 'sedoparking', 'parkingcrew', 'bodis', 'parklogic',
          'dan.com', 'afternic', 'namebrightdns', 'h-email.net')
SPF_TERMS = ('include:', 'a', 'a:', 'mx', 'mx:', 'ptr', 'exists:', 'redirect=')


def query(name, kind, doh=DOH):
    url = doh + '?' + urllib.parse.urlencode({'name': name, 'type': kind})
    req = urllib.request.Request(url, headers={'accept': 'application/dns-json',
                                               'User-Agent': 'inboxsitrep'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                body = json.loads(r.read())
            break
        except OSError:
            if attempt == 2:
                raise
    if body.get('Status') not in (0, 3):        # 3 is NXDOMAIN: an answer
        raise LookupError('%s %s: status %s' % (name, kind, body.get('Status')))
    return [(a['type'], a['data']) for a in body.get('Answer') or ()]


def txt(name):
    return [d.replace('" "', '').strip('"') for t, d in query(name, 'TXT')
            if t == TYPES['TXT']]


def lookups(record, depth=0):
    # DNS lookups an SPF check costs; past 10 receivers call it broken.
    n = 0
    for term in record.split()[1:]:
        term = term.lstrip('+-~?').lower()
        if not term.startswith(SPF_TERMS) and term not in ('a', 'mx'):
            continue
        n += 1
        if depth < 5 and term.startswith(('include:', 'redirect=')):
            target = term.split(':', 1)[-1].split('=', 1)[-1]
            for r in txt(target):
                if r.lower().startswith('v=spf1'):
                    n += lookups(r, depth + 1)
    return n


def names(record, depth=0):
    # Every domain an SPF record includes, followed down.
    out = []
    for term in record.split()[1:]:
        term = term.lstrip('+-~?').lower()
        if term.startswith(('include:', 'redirect=')):
            target = term.split(':', 1)[-1].split('=', 1)[-1]
            out.append(target)
            if depth < 3:
                for r in txt(target):
                    if r.lower().startswith('v=spf1'):
                        out += names(r, depth + 1)
    return out


def host_of(mx):
    # Who hosts the mailboxes, from the MX, when the sequencer didn't say.
    mx = ' '.join(mx).lower()
    if 'google.com' in mx or 'googlemail.com' in mx:
        return 'google'
    if 'outlook.com' in mx:
        return 'microsoft'
    return None


def check(dom, host=None, today=None):
    r = {'checked': (today or datetime.date.today()).isoformat(), 'issues': []}
    issues = r['issues']
    mx = [d for t, d in query(dom, 'MX') if t == TYPES['MX']]
    r['mx'] = mx
    if not mx:
        issues.append('no_mx')
    elif any(p in m.lower() for m in mx for p in PARKED):
        issues.append('parked')
    host = host or host_of(mx)
    r['host'] = host
    spf = [t for t in txt(dom) if t.lower().startswith('v=spf1')]
    r['spf'] = spf
    if not spf:
        issues.append('no_spf')
    elif len(spf) > 1:
        issues.append('spf_many')
    else:
        if host in INCLUDE and INCLUDE[host] not in names(spf[0]):
            issues.append('spf_misses_host')
        r['spf_lookups'] = lookups(spf[0])
        if r['spf_lookups'] > 10:
            issues.append('spf_lookups')
    dmarc = [t for t in txt('_dmarc.' + dom) if t.lower().startswith('v=dmarc1')]
    r['dmarc'] = dmarc[0] if dmarc else None
    if not dmarc:
        issues.append('no_dmarc')
    if host in SELECTORS:
        found = [s for s in SELECTORS[host] if query('%s._domainkey.%s' % (s, dom), 'TXT')]
        r['dkim'] = found
        if not found:
            issues.append('no_dkim')
    # SURBL answers 127.0.0.x, one bit per list; 127.0.0.1 means it
    # refused to answer, which is not a listing.
    reg = rdap(dom)
    when = reg if reg == 'unregistered' else (reg or {}).get('expires')
    if isinstance(reg, dict):
        r['created'] = reg.get('created')
        r['registrar'] = reg.get('registrar')
    if when == 'unregistered' and (mx or spf):
        # Some registries (.io among them) publish no RDAP at all; a domain
        # that still answers in DNS is registered whatever RDAP says.
        when = None
    r['expires'] = when
    if when == 'unregistered':
        issues.append('unregistered')
    elif when and when <= r['checked']:
        issues.append('expired')
    elif when and (datetime.date.fromisoformat(when) - datetime.date.fromisoformat(r['checked'])).days <= 30:
        issues.append('expiring')
    try:
        r['ns'] = sorted(d.rstrip('.').lower() for t, d in query(dom, 'NS') if t == 2)
        r['web'] = sorted(d for t, d in query(dom, 'A') if t == TYPES['A'])
    except (OSError, LookupError):
        pass
    r['redirect'] = redirect(dom)
    try:
        hits = [d for t, d in query(dom + '.multi.surbl.org', 'A', DOH_SURBL)
                if t == TYPES['A']]
        r['surbl'] = [h for h in hits if h.startswith('127.0.0.') and h != '127.0.0.1']
    except (OSError, LookupError):
        r['surbl'] = None           # unknown, not clean
    return r


# .com and .net answer straight from Verisign; everything else goes through
# rdap.org, which forwards to the right registry.
RDAP = {'com': 'https://rdap.verisign.com/com/v1/domain/', 'net': 'https://rdap.verisign.com/net/v1/domain/'}


def rdap(dom):
    # The registry's record: when the domain was registered, when it runs
    # out, and who sold it.  A sending domain that lapses loses its DNS
    # overnight; domains bought together tend to get listed together.
    url = RDAP.get(dom.rsplit('.', 1)[-1], 'https://rdap.org/domain/') + dom
    req = urllib.request.Request(url, headers={'accept': 'application/rdap+json',
                                               'User-Agent': 'inboxsitrep'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                body = json.loads(r.read())
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return 'unregistered'
            if e.code != 429 or attempt == 2:
                return None
            time.sleep(2 + 3 * attempt)
        except (OSError, ValueError):
            return None
    out = {}
    for e in body.get('events') or ():
        if e.get('eventAction') == 'expiration':
            out['expires'] = e.get('eventDate', '')[:10]
        if e.get('eventAction') == 'registration':
            out['created'] = e.get('eventDate', '')[:10]
    for ent in body.get('entities') or ():
        if 'registrar' in (ent.get('roles') or ()):
            card = (ent.get('vcardArray') or [None, []])[1]
            out['registrar'] = next((v[3] for v in card if v and v[0] == 'fn'), None)
    return out


def redirect(dom):
    # Where the domain's website sends visitors, if anywhere.  Cold email
    # domains usually forward to one main site, and a listed main site
    # drags every domain pointing at it.
    class Stop(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    opener = urllib.request.build_opener(Stop)
    for scheme in ('https://', 'http://'):
        try:
            req = urllib.request.Request(scheme + dom, method='HEAD', headers={'User-Agent': 'inboxsitrep'})
            with opener.open(req, timeout=6):
                return None
        except urllib.error.HTTPError as e:
            if 300 <= e.code < 400 and e.headers.get('Location'):
                host = urllib.parse.urlparse(e.headers['Location']).hostname or ''
                host = host.lower().removeprefix('www.')
                return host if host and host != dom else None
            return None
        except (OSError, ValueError):
            continue
    return None


def check_all(data, doms=None, threads=32, say=print):
    doms = sorted(doms or [d for d in data.domains() if data.mailboxes.get(d)])
    today = datetime.date.today()
    done = errors = 0
    with concurrent.futures.ThreadPoolExecutor(threads) as pool:
        jobs = {pool.submit(check, d, data.host.get(d), today): d for d in doms}
        for job in concurrent.futures.as_completed(jobs):
            dom = jobs[job]
            try:
                r = job.result()
            except (OSError, LookupError, ValueError):
                errors += 1
                continue
            data.dns[dom] = r
            if r['surbl'] and dom not in data.listed:
                # Dated to the last day of sending data, so it is seen there.
                data.listed[dom] = ('surbl', min(r['checked'], data.days[-1]) if data.days
                                    else r['checked'])
            done += 1
            if done % 250 == 0:
                say('  %d of %d checked' % (done, len(doms)))
    say('%d domains checked, %d could not be' % (done, errors))
    return data
