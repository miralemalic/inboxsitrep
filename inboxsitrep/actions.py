# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# Approved changes to a real workspace.
#
# Nothing here runs on its own.  A plan step becomes a preview that says
# exactly what will change (which mailboxes, which campaigns, which
# blocklist entries, what they are now); a person reads it and presses
# Apply; big changes need the workspace name typed.  Every change is
# written to a journal with its exact inverse, so Undo puts back what was
# there, mailbox by mailbox.  Workspaces are read-only until changes are
# allowed for them in settings.
#
# PlusVibe gives the writes that cover the plans, and the client below can
# make only those:
#   pause a domain         daily limit 0 on its mailboxes (or any limit)
#   pause warmup           warmup off on its mailboxes, with or without a pause
#   hold a receiver        its consumer domains on the workspace blocklist
#   drop Apple addresses   icloud.com, me.com, mac.com on the blocklist
#   campaign settings      open tracking off, risky addresses off
# PlusVibe cannot stop one recipient provider for one domain, so "stop
# Gmail on x.com" stays a manual step, with the campaigns to edit listed.

import json
import time
import uuid
import datetime
import collections
import urllib.error
import urllib.parse
import urllib.request

from . import plusvibe
from .data import connect

WRITES = {('PUT', '/account/bulk-update'), ('PATCH', '/account/bulk-update-warmup'),
          ('POST', '/blocklist/add/entries'), ('POST', '/blocklist/delete/entries'),
          ('PATCH', '/campaign/update/campaign')}
# EmailBison: the same kinds of change, its own paths.
BISON_WRITES = {('PATCH', '/api/sender-emails/daily-limits/bulk'), ('POST', '/api/blacklisted-domains'),
                ('DELETE', '/api/blacklisted-domains/{id}'), ('PATCH', '/api/warmup/sender-emails/disable'),
                ('PATCH', '/api/warmup/sender-emails/enable')}
# Changes to a domain's mailboxes: a daily limit, warmup, or both.
MAILBOX_OPS = ('pause', 'set_limit', 'warmup_off')
# What a person (or the analyst, or Claude Code) may ask for, in words the
# analyst is shown.  clean() holds every request to this list.
OPS = {
    'pause': 'daily limit 0 on every mailbox of the target domains; add "warmup": "off" to pause their warmup too',
    'set_limit': 'daily limit "limit" (0-500) on every mailbox of the target domains; "warmup": "off" also pauses warmup',
    'warmup_off': 'pause warmup on every mailbox of the target domains; sending is left as it is',
    'suppress_apple': 'add icloud.com, me.com and mac.com to the workspace blocklist; no targets',
    'hold_receiver': 'blocklist the addresses of one consumer ISP; targets: one of Spectrum, Yahoo, AT&T, Comcast, Cox',
    'change_settings': 'PlusVibe only: turn a setting off in campaigns; targets: campaign names; '
                       '"setting": "tracking" (open tracking) or "risky" (sending to risky addresses)',
}
CHUNK = 500
SETTLE = 5          # seconds to let a sequencer apply a change before reading it back
BIG = .10           # more than this share of the fleet's mailboxes: type the name to confirm
APPLE = ['icloud.com', 'me.com', 'mac.com']
# The addresses a consumer ISP's block covers.  Corporate gateways
# (Mimecast, Proofpoint) protect thousands of company domains: no list.
RECEIVER_DOMAINS = {
    'Spectrum': ['charter.net', 'spectrum.net', 'rr.com', 'twc.com', 'roadrunner.com',
                 'brighthouse.com', 'bresnan.net'],
    'Yahoo': ['yahoo.com', 'aol.com', 'ymail.com', 'rocketmail.com'],
    'AT&T': ['att.net', 'sbcglobal.net', 'bellsouth.net', 'pacbell.net', 'swbell.net'],
    'Comcast': ['comcast.net'],
    'Cox': ['cox.net'],
}

SCHEMA = '''CREATE TABLE IF NOT EXISTS actions (
    id TEXT PRIMARY KEY, at TEXT, incident INTEGER, op TEXT, title TEXT,
    body TEXT, status TEXT)'''


class Writer:
    # Can send exactly the requests in WRITES, nothing else.

    def __init__(self, key, workspace):
        self.key = key
        self.workspace = workspace
        self.last = 0.0

    def send(self, method, path, body):
        if (method, path) not in WRITES:
            raise ValueError('inboxsitrep does not make %s %s' % (method, path))
        body = dict(body, workspace_id=self.workspace)
        req = urllib.request.Request(plusvibe.API + path, method=method,
                                     data=json.dumps(body).encode(),
                                     headers={'x-api-key': self.key, 'User-Agent': plusvibe.UA,
                                              'Content-Type': 'application/json'})
        for attempt in range(4):
            wait = self.last + .3 - time.time()
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    return json.loads(r.read() or b'{}')
            except urllib.error.HTTPError as e:
                if e.code < 500 and e.code != 429:
                    raise RuntimeError('%s %s: %s %s' % (method, path, e.code,
                                                         e.read()[:300].decode('utf-8', 'replace')))
                time.sleep(2 ** attempt)
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                time.sleep(2 ** attempt)
        raise RuntimeError('gave up on %s %s' % (method, path))


class BisonWriter:
    # EmailBison's writes, limited the same way.  Deleting a blocklist entry
    # needs its id, which only the list knows, so that one is looked up.

    def __init__(self, api):
        self.api = api

    def send(self, method, path, body):
        if path == '/api/blacklisted-domains/{id}' and method == 'DELETE':
            for e in self.api.pages('/api/blacklisted-domains'):
                if (e.get('domain') or '').lower() == body['domain']:
                    return self.api.request('DELETE', '/api/blacklisted-domains/%s' % e['id'])
            return {}
        if (method, path) not in BISON_WRITES:
            raise ValueError('Inbox Sitrep does not make %s %s' % (method, path))
        try:
            return self.api.request(method, path, body=body)
        except OSError as e:
            raise RuntimeError('%s %s: %s' % (method, path, e))


INSTANTLY_WRITES = {('PATCH', '/api/v2/accounts/{email}'), ('POST', '/api/v2/block-lists-entries'),
                    ('DELETE', '/api/v2/block-lists-entries/{id}'), ('POST', '/api/v2/accounts/warmup/disable'),
                    ('POST', '/api/v2/accounts/warmup/enable')}


class InstantlyWriter:
    # Instantly's writes, limited the same way.  A mailbox is changed by its
    # address; a blocklist entry is deleted by the id the list gives it.

    def __init__(self, api):
        self.api = api

    def send(self, method, path, body):
        if (method, path) not in INSTANTLY_WRITES:
            raise ValueError('Inbox Sitrep does not make %s %s' % (method, path))
        body = dict(body)
        if '{email}' in path:
            real = path.replace('{email}', urllib.parse.quote(body.pop('email')))
        elif '{id}' in path:
            value = body.pop('bl_value')
            hit = next((e for e in self.api.pages('/api/v2/block-lists-entries')
                        if (e.get('bl_value') or '').lower() == value), None)
            if not hit:
                return {}
            real = path.replace('{id}', str(hit['id']))
        else:
            real = path
        try:
            return self.api.write(method, real, body)
        except OSError as e:
            raise RuntimeError('%s %s: %s' % (method, real, e))


def instantly_prepare(change, api, fleet_name):
    op, t = change['op'], list(change.get('targets') or ())
    act = {'id': uuid.uuid4().hex[:12], 'op': op, 'fleet': fleet_name, 'change': change,
           'steps': [], 'undo': [], 'lines': [], 'manual': None, 'big': False}
    if op in MAILBOX_OPS:
        hits, total = [], 0
        for a in api.pages('/api/v2/accounts'):
            total += 1
            if plusvibe.domain_of(a.get('email')) in t:
                hits.append(a)
        if not hits:
            act['manual'] = 'None of these domains has a mailbox in this workspace any more.'
            return act
        limit, warm = wanted(change)
        if limit is not None:
            for a in hits:
                act['steps'].append(step('PATCH', '/api/v2/accounts/{email}', {'email': a['email'], 'daily_limit': limit},
                                         'daily limit %s on %s' % (limit, a['email'])))
                act['undo'].append(step('PATCH', '/api/v2/accounts/{email}',
                                        {'email': a['email'], 'daily_limit': a.get('daily_limit') or 0},
                                        'daily limit back to %s on %s' % (a.get('daily_limit') or 0, a['email'])))
        on = [a['email'] for a in hits if a.get('warmup_status') == 1] if warm else []
        for chunk in chunks(on, 100):
            act['steps'].append(step('POST', '/api/v2/accounts/warmup/disable', {'emails': chunk},
                                     'pause warmup on %d mailboxes' % len(chunk)))
            act['undo'].append(step('POST', '/api/v2/accounts/warmup/enable', {'emails': chunk},
                                    'warmup back on for %d mailboxes' % len(chunk)))
        act['lines'] = [mailbox_line(limit, warm, len(hits), len({plusvibe.domain_of(a['email']) for a in hits}),
                                     len(on))]
        act['share'] = len(hits) / total if total else 0
        act['big'] = act['share'] > BIG
        return act
    if op in ('suppress_apple', 'hold_receiver'):
        want = APPLE if op == 'suppress_apple' else RECEIVER_DOMAINS.get(t[0] if t else '')
        if not want:
            act['manual'] = manual(change, None)
            return act
        have = {(e.get('bl_value') or '').lower() for e in api.pages('/api/v2/block-lists-entries')}
        new = [d for d in want if d not in have]
        if not new:
            act['manual'] = 'Already on the blocklist: %s.' % ', '.join(want)
            return act
        act['steps'] = [step('POST', '/api/v2/block-lists-entries', {'bl_value': d}, 'blocklist %s' % d) for d in new]
        act['undo'] = [step('DELETE', '/api/v2/block-lists-entries/{id}', {'bl_value': d}, 'unblock %s' % d) for d in new]
        act['lines'] = ['Stop mailing %s: add %s to the blocklist.'
                        % ('iCloud addresses' if op == 'suppress_apple' else t[0] + ' addresses', ', '.join(new))]
        return act
    act['manual'] = manual(change, None)
    return act


def bison_prepare(change, api, fleet_name):
    # The EmailBison version of prepare(): pausing and blocklisting.
    op, t = change['op'], list(change.get('targets') or ())
    act = {'id': uuid.uuid4().hex[:12], 'op': op, 'fleet': fleet_name, 'change': change,
           'steps': [], 'undo': [], 'lines': [], 'manual': None, 'big': False}
    if op in MAILBOX_OPS:
        boxes, total = collections.defaultdict(list), 0
        for s in api.pages('/api/sender-emails'):
            total += 1
            dom = plusvibe.domain_of(s.get('email'))
            if dom in t:
                boxes[dom].append({'id': s['id'], 'limit': s.get('daily_limit') or 0,
                                   'warmup': bison_warm(s)})
        ids = [b for bs in boxes.values() for b in bs]
        if not ids:
            act['manual'] = 'None of these domains has a mailbox in this workspace any more.'
            return act
        limit, warm = wanted(change)
        if limit is not None:
            act['steps'] = [step('PATCH', '/api/sender-emails/daily-limits/bulk',
                                 {'sender_email_ids': [b['id'] for b in ids], 'daily_limit': limit},
                                 'set the daily limit to %s on %d mailboxes' % (limit, len(ids)))]
            by = collections.defaultdict(list)
            for b in ids:
                by[b['limit']].append(b['id'])
            act['undo'] = [step('PATCH', '/api/sender-emails/daily-limits/bulk',
                                {'sender_email_ids': g, 'daily_limit': old},
                                'set the daily limit back to %s on %d mailboxes' % (old, len(g)))
                           for old, g in sorted(by.items())]
        on = [b['id'] for b in ids if b['warmup']] if warm else []
        for chunk in chunks(on):
            act['steps'].append(step('PATCH', '/api/warmup/sender-emails/disable', {'sender_email_ids': chunk},
                                     'pause warmup on %d mailboxes' % len(chunk)))
            act['undo'].append(step('PATCH', '/api/warmup/sender-emails/enable', {'sender_email_ids': chunk},
                                    'warmup back on for %d mailboxes' % len(chunk)))
        act['lines'] = [mailbox_line(limit, warm, len(ids), len(boxes), len(on))]
        act['share'] = len(ids) / total if total else 0
        act['big'] = act['share'] > BIG
        return act
    if op in ('suppress_apple', 'hold_receiver'):
        want = APPLE if op == 'suppress_apple' else RECEIVER_DOMAINS.get(t[0] if t else '')
        if not want:
            act['manual'] = manual(change, None)
            return act
        have = {(e.get('domain') or '').lower() for e in api.pages('/api/blacklisted-domains')}
        new = [d for d in want if d not in have]
        if not new:
            act['manual'] = 'Already on the blocklist: %s.' % ', '.join(want)
            return act
        act['steps'] = [step('POST', '/api/blacklisted-domains', {'domain': d}, 'blocklist %s' % d) for d in new]
        act['undo'] = [step('DELETE', '/api/blacklisted-domains/{id}', {'domain': d}, 'unblock %s' % d) for d in new]
        act['lines'] = ['Stop mailing %s: add %s to the blocklist.'
                        % ('iCloud addresses' if op == 'suppress_apple' else t[0] + ' addresses', ', '.join(new))]
        return act
    act['manual'] = manual(change, None)
    return act


def step(method, path, body, what):
    return {'method': method, 'path': path, 'body': body, 'what': what}


def chunks(xs, n=CHUNK):
    return [xs[i:i + n] for i in range(0, len(xs), n)]


def wanted(change):
    # (daily limit or None, pause warmup?) for a mailbox change.
    op = change['op']
    limit = None if op == 'warmup_off' else int(change.get('limit') or 0) if op == 'set_limit' else 0
    return limit, op == 'warmup_off' or change.get('warmup') == 'off'


def mailbox_line(limit, warm, boxes, doms, warm_on, plusvibe_note=False):
    if limit is None:
        line = 'Pause warmup on %d mailboxes on %d domains; sending is left as it is' % (boxes, doms)
    elif limit == 0:
        line = 'Pause %d mailboxes on %d domains: daily limit to 0' % (boxes, doms)
    else:
        line = 'Daily limit to %d on %d mailboxes on %d domains' % (limit, boxes, doms)
    if warm and limit is not None:
        line += '; warmup paused too'
    if warm and warm_on < boxes:
        line += ' (%d had warmup off already)' % (boxes - warm_on)
    elif not warm and plusvibe_note:
        line += '.  Warmup carries on'
    return line + '.'


def bison_warm(s):
    # Whether EmailBison has warmup on for a sender email.  The field name is
    # from the API reference; if it is missing, assume on, so undo turns it
    # back on rather than leave it off.
    for k in ('warmup_enabled', 'warmup_is_enabled'):
        if isinstance(s.get(k), bool):
            return s[k]
    return (s.get('warmup_status') or 'enabled') not in ('disabled', 'paused', 'inactive')


def mailboxes(read, doms):
    # The mailboxes on these domains as PlusVibe has them right now.
    doms = set(doms)
    out = collections.defaultdict(list)
    total = 0
    for a in plusvibe.accounts(read):
        total += 1
        dom = plusvibe.domain_of(a.get('email'))
        if dom in doms:
            p = a.get('payload') or {}
            out[dom].append({'id': a.get('id') or a.get('_id'), 'email': a.get('email'),
                             'limit': p.get('daily_limit'),
                             'warmup': (a.get('warmup_status') or 'ACTIVE').upper() == 'ACTIVE',
                             'campaigns': [c.get('id') for c in p.get('cmps') or []]})
    return out, total


def blocklist(read):
    have, skip = set(), 0
    while True:
        page = read.get('/blocklist/list', skip=skip, limit=100)
        rows = page if isinstance(page, list) else (page.get('data') or page.get('items') or [])
        have.update((r.get('value') or '').lower() for r in rows)
        if len(rows) < 100:
            return have
        skip += 100


def manual(change, data, boxes=None):
    # What a person does by hand, said precisely.
    op, t = change['op'], change['targets']
    names = {cid: (c.get('name') or c.get('camp_name')) for cid, c in data.campaigns.items()} if data else {}
    if op == 'stop' and boxes:
        camps = sorted({names.get(c) or c for bs in boxes.values() for b in bs for c in b['campaigns']
                        if names.get(c)})
        return ('PlusVibe cannot stop one recipient provider for one domain.  In each campaign '
                'that sends to %s, remove the mailboxes of %s.  Their campaigns: %s.'
                % ({'google': 'Gmail', 'microsoft': 'Microsoft'}.get(change.get('provider'), 'them'),
                   ', '.join(t[:6]) + (' and %d more' % (len(t) - 6) if len(t) > 6 else ''),
                   ', '.join(camps[:8]) or 'none found'))
    return {
        'stop': 'In PlusVibe, take these domains out of the campaigns that send to that provider.',
        'resume': 'Put these domains back into the campaigns for that provider.',
        'fix_dns': 'At the registrar or DNS host: fix the record named in the incident.',
        'reconnect': 'In PlusVibe, Email accounts: reconnect the disconnected mailboxes.',
        'load_leads': 'Upload new leads to the active campaigns.',
        'investigate': 'Find the cause of the bounces before resuming these campaigns.',
        'review_copy': 'Rewrite the copy: fewer links, no tracking, plain wording.  Test it.',
        'delist': 'Request removal from each list naming these domains and keep them sending: Spamhaus at '
                  'check.spamhaus.org, SURBL at surbl.org/surbl-analysis, URIBL at admin.uribl.com.  First take '
                  'out what put them there (a link or tracking domain in the copy, a jump in volume).',
        'remove_link': 'Take the listed link out of the copy and the signature.',
        'scrub': 'Verify the list these domains send to; drop what bounces.',
        'check': 'Find out why these domains stopped: leads, a paused campaign, moved mailboxes.',
        'route': 'Send Gmail leads only from the healthiest mailboxes.',
        'hold_receiver': 'This receiver protects company domains; there is no list of its '
                         'recipients to hold.  Send to them from your healthiest domains only.',
        'unpause': 'Undo the pause from the Changes page, which restores each mailbox\'s limit.',
    }.get(op, 'Do this in the sequencer.')


def prepare(change, data, read, fleet_name):
    # A preview: what would change, and how to put it back.  Reads the
    # workspace as it is now.  Never writes.
    op, t = change['op'], list(change.get('targets') or ())
    act = {'id': uuid.uuid4().hex[:12], 'op': op, 'fleet': fleet_name, 'change': change,
           'steps': [], 'undo': [], 'lines': [], 'manual': None, 'big': False}
    if op in MAILBOX_OPS:
        boxes, total = mailboxes(read, t)
        ids = [b for bs in boxes.values() for b in bs]
        if not ids:
            act['manual'] = 'None of these domains has a mailbox in this workspace any more.'
            return act
        limit, warm = wanted(change)
        if limit is not None:
            for chunk in chunks([b['id'] for b in ids]):
                act['steps'].append(step('PUT', '/account/bulk-update', {'ids': chunk, 'daily_limit': limit},
                                         'set the daily limit to %s on %d mailboxes' % (limit, len(chunk))))
            by = collections.defaultdict(list)
            for b in ids:
                by[b['limit'] if b['limit'] is not None else 0].append(b['id'])
            for old, group in sorted(by.items()):
                for chunk in chunks(group):
                    act['undo'].append(step('PUT', '/account/bulk-update',
                                            {'ids': chunk, 'daily_limit': old},
                                            'set the daily limit back to %s on %d mailboxes' % (old, len(chunk))))
        # Warmup is paused, not set to 0: PlusVibe's warmup limit can't go
        # below 1.  Only mailboxes with warmup on are touched, and undo turns
        # exactly those back on.
        on = [b['id'] for b in ids if b['warmup']] if warm else []
        for chunk in chunks(on):
            act['steps'].append(step('PATCH', '/account/bulk-update-warmup',
                                     {'ids': chunk, 'warmup_status': 'INACTIVE'},
                                     'pause warmup on %d mailboxes' % len(chunk)))
            act['undo'].append(step('PATCH', '/account/bulk-update-warmup',
                                    {'ids': chunk, 'warmup_status': 'ACTIVE'},
                                    'warmup back on for %d mailboxes' % len(chunk)))
        act['lines'] = [mailbox_line(limit, warm, len(ids), len(boxes), len(on), plusvibe_note=True)]
        act['lines'] += ['%s: %d mailboxes, daily limit now %s%s' % (d, len(bs), ', '.join(
            sorted({str(b['limit']) for b in bs})), '' if not warm else ', warmup on for %d'
            % sum(1 for b in bs if b['warmup'])) for d, bs in sorted(boxes.items())[:12]]
        if len(boxes) > 12:
            act['lines'].append('and %d more domains' % (len(boxes) - 12))
        missing = sorted(set(t) - set(boxes))
        if missing:
            act['lines'].append('No mailboxes here any more: %s' % ', '.join(missing[:6]))
        act['share'] = len(ids) / total if total else 0
        act['big'] = act['share'] > BIG
        return act
    if op in ('suppress_apple', 'hold_receiver'):
        want = APPLE if op == 'suppress_apple' else RECEIVER_DOMAINS.get(t[0] if t else '')
        if not want:
            act['manual'] = manual(change, data)
            return act
        have = blocklist(read)
        new = [d for d in want if d not in have]
        if not new:
            act['manual'] = 'Already on the blocklist: %s.' % ', '.join(want)
            return act
        act['steps'] = [step('POST', '/blocklist/add/entries', {'entries': new},
                             'add %s to the blocklist' % ', '.join(new))]
        act['undo'] = [step('POST', '/blocklist/delete/entries', {'entries': new},
                            'take %s off the blocklist' % ', '.join(new))]
        who = 'iCloud addresses' if op == 'suppress_apple' else '%s addresses' % t[0]
        act['lines'] = ['Stop mailing %s: add %s to the workspace blocklist.' % (who, ', '.join(new)),
                        'Already there, left alone: %s' % (', '.join(sorted(set(want) - set(new))) or 'none')]
        return act
    if op == 'change_settings':
        target = ('is_emailopened_tracking' if change.get('setting') == 'tracking'
                  else 'send_risky_email')
        ids = {(c.get('name') or c.get('camp_name')): cid for cid, c in data.campaigns.items()}
        for name in t:
            cid = ids.get(name)
            if cid:
                act['steps'].append(step('PATCH', '/campaign/update/campaign',
                                         {'campaign_id': cid, target: 'no'},
                                         '%s: %s off' % (name, target.replace('_', ' '))))
                act['undo'].append(step('PATCH', '/campaign/update/campaign',
                                        {'campaign_id': cid, target: 'yes'},
                                        '%s: %s back on' % (name, target.replace('_', ' '))))
        what = 'open tracking' if target == 'is_emailopened_tracking' else 'sending to risky addresses'
        act['lines'] = ['Turn off %s in %d campaigns.' % (what, len(act['steps']))] + [
            s['what'] for s in act['steps'][:12]]
        return act
    boxes = None
    if op == 'stop':
        boxes, _ = mailboxes(read, t)
    act['manual'] = manual(change, data, boxes)
    return act


def clean(change, domains, seq, campaigns=()):
    # A change someone asked for (in their words, through the analyst, or
    # through Claude Code), held to OPS and to this workspace's own domains
    # and campaigns.  Raises ValueError with what is wrong.
    op = change.get('op')
    if op not in OPS or (op == 'change_settings' and seq not in ('plusvibe', 'sim')):
        raise ValueError('Inbox Sitrep can\'t make that change ("%s") here' % op)
    out = {'op': op, 'targets': [], 'why': str(change.get('why') or 'you asked for it')[:200]}
    if op in MAILBOX_OPS:
        domains = set(domains)
        doms = set()
        for t in change.get('targets') or []:
            t = str(t).strip().lower()
            hits = {d for d in domains if d.startswith(t[:-1])} if t.endswith('*') else {t} & domains
            if not hits:
                raise ValueError('%s is not a sending domain in this workspace' % t)
            doms |= hits
        if not doms:
            raise ValueError('say which domains or families')
        out['targets'] = sorted(doms)
        if op == 'set_limit':
            try:
                out['limit'] = int(change.get('limit'))
            except (TypeError, ValueError):
                raise ValueError('say the daily limit, e.g. 10')
            if not 0 <= out['limit'] <= 500:
                raise ValueError('a daily limit between 0 and 500')
        if op != 'warmup_off' and change.get('warmup') == 'off':
            out['warmup'] = 'off'
    elif op == 'hold_receiver':
        asked = {str(x).lower() for x in change.get('targets') or []}
        names = [n for n in RECEIVER_DOMAINS if n.lower() in asked]
        if not names:
            raise ValueError('holding works for %s' % ', '.join(RECEIVER_DOMAINS))
        out['targets'] = names[:1]
    elif op == 'change_settings':
        out['targets'] = [c for c in change.get('targets') or [] if c in set(campaigns)]
        if not out['targets']:
            raise ValueError('no campaign by those names in this workspace')
        if change.get('setting') not in ('tracking', 'risky'):
            raise ValueError('the setting is "tracking" or "risky"')
        out['setting'] = change['setting']
    return out


def combine(acts):
    # Several previews as one change: applied in order, undone in reverse.
    if len(acts) == 1:
        return acts[0]
    first = acts[0]
    out = dict(first, op='+'.join(a['op'] for a in acts),
               steps=[s for a in acts for s in a['steps']],
               undo=[s for a in reversed(acts) for s in a['undo']],
               lines=[l for a in acts for l in a['lines']],
               manual=' '.join(a['manual'] for a in acts if a.get('manual')) or None,
               big=any(a['big'] for a in acts), share=max(a.get('share', 0) for a in acts),
               verdicts=[v for a in acts for v in a.get('verdicts') or ()],
               healthy=sorted({d for a in acts for d in a.get('healthy') or ()}))
    if out['steps']:
        out['manual'] = None
        out['lines'] += ['(not possible here: %s)' % a['manual'] for a in acts if a.get('manual')]
    return out


def applied(plan, past):
    # For each step of a plan, the change from the journal that already
    # made it (applied, not undone), or None.  Matched on what was done,
    # not on incident numbers, which change between checks.
    done = [h for h in past if h['status'] == 'applied' and h.get('change')]
    out = []
    for c in plan:
        hit = next((h for h in done if h['change'].get('op') == c['op']
                    and set(c.get('targets') or ()) <= set(h['change'].get('targets') or ())), None)
        out.append({'id': hit['id'], 'at': hit['at']} if hit else None)
    return out


def journal(path):
    db = connect(path)
    db.execute(SCHEMA)
    return db


def verify(act, read, seq):
    # After a mailbox change, read the mailboxes back: how many don't show it
    # yet?  Sequencers apply bulk changes with a short delay, so this waits a
    # few seconds first.  None if it could not look.
    if act['op'] not in MAILBOX_OPS or read is None:
        return None
    limit, off = {}, set()
    for s in act['steps']:
        b, path = s['body'], s['path']
        ids = b.get('ids') or b.get('sender_email_ids') or b.get('emails') or [b.get('email')]
        if 'daily_limit' in b:
            limit.update(dict.fromkeys(ids, b['daily_limit']))
        elif 'warmup' in path:
            off.update(ids)
    time.sleep(SETTLE)
    if seq == 'plusvibe':
        rows = ((a.get('id') or a.get('_id'), (a.get('payload') or {}).get('daily_limit'),
                 (a.get('warmup_status') or 'ACTIVE').upper() == 'ACTIVE') for a in plusvibe.accounts(read))
    elif seq == 'emailbison':
        rows = ((a.get('id'), a.get('daily_limit'), bison_warm(a)) for a in read.pages('/api/sender-emails'))
    elif seq == 'instantly':
        rows = ((a.get('email'), a.get('daily_limit'), a.get('warmup_status') == 1)
                for a in read.pages('/api/v2/accounts'))
    else:
        return None
    return sum(1 for i, lim, warm in rows
               if (i in limit and (lim or 0) != limit[i]) or (i in off and warm))


def apply(act, writer, path, incident=None, say=print, read=None, seq=None):
    # Run the steps in order; stop at the first failure and say how far it
    # got, so Undo knows what to put back.
    done = []
    status = 'applied'
    for s in act['steps']:
        say(s['what'])
        try:
            writer.send(s['method'], s['path'], s['body'])
        except RuntimeError as e:
            status = 'failed after %d of %d steps: %s' % (len(done), len(act['steps']), e)
            break
        done.append(s)
    record = dict(act, done=len(done), at=datetime.datetime.now().isoformat(timespec='seconds'))
    if status != 'applied':
        # Undo only what was done.  For pauses, the inverse is per limit
        # group, so it is safe to replay whole; for the rest, trim.
        # (A combined change replays its whole undo: each part only puts
        # back what it changed.)
        if act['op'] not in MAILBOX_OPS and '+' not in act['op']:
            record['undo'] = act['undo'][:len(done)]

    def save():
        db = journal(path)
        with db:
            db.execute('INSERT OR REPLACE INTO actions VALUES (?,?,?,?,?,?,?)',
                       (act['id'], record['at'], incident, act['op'],
                        act['lines'][0] if act['lines'] else act['op'],
                        json.dumps(record), status))
        db.close()
    # Journaled the moment the sequencer has it, so Undo exists even if the
    # read-back below never finishes.
    save()
    if status == 'applied' and read is not None:
        say('reading the mailboxes back')
        try:
            left = verify(act, read, seq)
        except (OSError, RuntimeError, ValueError, KeyError):
            left = None
        if left is not None:
            record['checked'] = ('every mailbox shows the change' if not left else
                                 '%d mailboxes do not show it yet; look again in a minute' % left)
            say(record['checked'])
            save()
    return status


def history(path):
    db = journal(path)
    rows = db.execute('SELECT id, at, incident, op, title, status, body FROM actions '
                      'ORDER BY at DESC').fetchall()
    db.close()
    out = []
    for i, at, inc, op, title, status, body in rows:
        b = json.loads(body)
        out.append({'id': i, 'at': at, 'incident': inc, 'op': op, 'title': title, 'change': b.get('change'),
                    'status': status, 'checked': b.get('checked'), 'lines': b.get('lines', []),
                    'undo': [s['what'] for s in b.get('undo', [])]})
    return out


def undo(path, act_id, writer, say=print):
    db = journal(path)
    row = db.execute('SELECT body, status FROM actions WHERE id = ?', (act_id,)).fetchone()
    db.close()
    if not row:
        raise ValueError('no change %s' % act_id)
    body, status = json.loads(row[0]), row[1]
    if status == 'undone':
        raise ValueError('already undone')
    for s in body.get('undo', []):
        say(s['what'])
        writer.send(s['method'], s['path'], s['body'])
    db = journal(path)
    with db:
        db.execute('UPDATE actions SET status = ? WHERE id = ?', ('undone', act_id))
    db.close()
    return 'undone'
