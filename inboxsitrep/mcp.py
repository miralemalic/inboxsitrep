# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# inboxsitrep's findings as tools for an AI agent: a Model Context Protocol
# server over stdin and stdout, the way Claude Code, Codex and Cursor plug
# tools in.  Every tool only reads.
#
#   claude mcp add inboxsitrep -- python -m inboxsitrep mcp --db fleet.db
#
# The protocol is JSON-RPC 2.0, one message per line.  The handful of
# methods a tool server needs are written out below; no SDK.

import os
import sys
import json
import collections

from . import __version__, brief, detect, report, incidents as incs
from . import data as store
from .actions import OPS
from .data import SENT, REPLIES, POSITIVES, BOUNCES, SENDER, rate

VERSION = '2025-06-18'
PROVS = ('google', 'microsoft', 'apple', 'other')


def pct(n, d):
    return round(100.0 * n / d, 2) if d else None


class Fleets:
    # The databases the tools read, loaded once per call so a running watch
    # is always seen as it is now.

    def __init__(self, paths):
        self.paths = paths

    def names(self):
        return [store.get_meta(p, 'workspace') or p for p in self.paths]

    def get(self, name=None):
        if not self.paths:
            raise KeyError('no workspaces yet: add one in the Inbox Sitrep app (inboxsitrep app), '
                           'or start the server with --db')
        for p in self.paths:
            if name in (None, '', p, store.get_meta(p, 'workspace')):
                data = store.reclassify(store.load(p))
                return data, incs.load(p), store.get_meta(p, 'workspace') or p
        raise KeyError('no workspace named %r; there is %s' % (name, ', '.join(self.names())))


def doms_of(i):
    # The domains an incident covers; some (Apple's) only keep a count.
    for key in ('domains', 'members'):
        v = i['evidence'].get(key)
        if isinstance(v, list) and v:
            return v
    return [i['target']]


def line(i, planned=None):
    return {'id': i['id'], 'kind': i['kind'], 'status': i['status'], 'opened': i['opened'],
            'last_seen': i['updated'], 'confidence': i['confidence'], 'title': i['title'],
            'plan': brief.plan_line(i, planned)}


def t_sitrep(f, fleet=None):
    data, found, name = f.get(fleet)
    return brief.brief(data, found, name) + ('\n\nOpen any [#n] with the incident tool.')


def t_incidents(f, fleet=None, status='open', kind=None):
    data, found, _ = f.get(fleet)
    planned = brief.planned_by(found)
    out = [line(i, planned) for i in found if (status == 'all' or i['status'] == status)
           and (not kind or i['kind'] == kind)]
    return out[:80]


def t_incident(f, id, fleet=None):
    data, found, _ = f.get(fleet)
    inc = next((i for i in found if i['id'] == int(id)), None)
    if inc is None:
        return 'no incident %s' % id
    doms = doms_of(inc)
    want = report.RELEVANT.get(inc['kind'])
    samples = [text for (d, k), text in sorted(data.samples.items())
               if d in doms and (want is None or k in want)][:5]
    return {'report': report.plain(inc, data), 'history': inc['history'][-14:],
            'bounce_samples': samples}


def t_domain(f, domain, fleet=None):
    data, found, _ = f.get(fleet)
    if domain not in data.domains():
        return 'no domain %s in this workspace' % domain
    days = []
    for day in data.days[-14:]:
        row = {'day': day}
        for p in PROVS:
            r = data.row(domain, p, day)
            if r[SENT]:
                row[p] = {'sent': r[SENT], 'refused_pct': pct(r[SENDER], r[SENT]),
                          'replies': r[REPLIES], 'positives': r[POSITIVES]}
        kinds = data.kinds.get((domain, day))
        if kinds:
            row['bounce_kinds'] = dict(kinds)
        days.append(row)
    planned = brief.planned_by(found)
    touching = [line(i, planned) for i in found if domain in doms_of(i)]
    return {'domain': domain, 'family': detect.families(data.domains()).get(domain),
            'mailboxes': data.mailboxes.get(domain), 'mailbox_states': data.accounts.get(domain),
            'host': data.host.get(domain), 'warmup_health': data.health.get(domain),
            'warmup_history': sorted(data.history.get(domain, {}).items())[-10:],
            'listed': data.listed.get(domain), 'dns': data.dns.get(domain),
            'last_14_days': days, 'incidents': touching}


def t_family(f, name, fleet=None):
    data, found, _ = f.get(fleet)
    fams = detect.families(data.domains())
    members = sorted(d for d, n in fams.items() if n == name)
    if not members:
        return 'no family %s; families: %s' % (name, ', '.join(sorted(set(fams.values()))))
    week = data.days[-7:]
    out = []
    for d in members:
        g = data.total(d, 'google', week)
        m = data.total(d, 'microsoft', week)
        out.append({'domain': d, 'gmail_sent': g[SENT], 'gmail_refused_pct': pct(g[SENDER], g[SENT]),
                    'gmail_replied_pct': pct(g[REPLIES], g[SENT]),
                    'microsoft_replied_pct': pct(m[REPLIES], m[SENT]),
                    'listed': data.listed_on(d, data.days[-1]),
                    'warmup': (data.health.get(d) or (None,))[0]})
    return out


def t_numbers(f, fleet=None, days=7):
    data, found, name = f.get(fleet)
    window = data.days[-int(days):]
    out = {'fleet': name, 'from': window[0], 'to': window[-1], 'domains': len(data.domains()),
           'mailboxes': sum(v or 0 for v in data.mailboxes.values()), 'by_recipient': {}}
    for p in PROVS:
        t = data.fleet(p, window)
        if t[SENT]:
            out['by_recipient'][p] = {'sent': t[SENT], 'refused_pct': pct(t[SENDER], t[SENT]),
                                      'replied_pct': pct(t[REPLIES], t[SENT] - t[BOUNCES]),
                                      'positives': t[POSITIVES]}
    worst = []
    for d in data.domains():
        sent = refused = 0
        for p in PROVS:
            r = data.total(d, p, window)
            sent += r[SENT]
            refused += r[SENDER]
        if sent >= 100:
            worst.append((rate(refused, sent), d, sent))
    worst.sort(reverse=True)
    out['most_refused'] = [{'domain': d, 'sent': s, 'refused_pct': round(r * 100, 1)}
                           for r, d, s in worst[:10]]
    out['incidents'] = dict(collections.Counter(i['status'] for i in found))
    return out


def t_find(f, what, fleet=None, limit=50):
    data, found, _ = f.get(fleet)
    day = data.days[-1]
    if what == 'listed':
        got = [(d, data.listed_on(d, day)) for d in data.domains() if data.listed_on(d, day)]
    elif what == 'dns':
        got = [(d, r['issues']) for d, r in sorted(data.dns.items()) if r.get('issues')]
    elif what == 'down':
        got = [(d, a) for d, a in sorted(data.accounts.items()) if a.get('down')]
    elif what == 'refused':
        week = data.days[-3:]
        got = []
        for d in data.domains():
            g = data.total(d, 'google', week)
            if g[SENT] >= 20 and g[SENDER] / g[SENT] >= .05:
                got.append((d, '%.0f%% of %d Gmail sends refused' % (100 * g[SENDER] / g[SENT], g[SENT])))
    else:
        return 'what must be one of: listed, dns, down, refused'
    return {'count': len(got), 'domains': [{'domain': d, 'why': w} for d, w in got[:int(limit)]]}


def t_why(f, fleet=None):
    from . import rootcause
    data, found, _ = f.get(fleet)
    return rootcause.report(data, found)


def t_bounces(f, fleet=None, domain=None, kind=None, limit=10):
    data, found, _ = f.get(fleet)
    out = [{'domain': d, 'kind': k, 'text': t} for (d, k), t in sorted(data.samples.items())
           if (not domain or d == domain) and (not kind or k == kind)]
    return out[:int(limit)]


def t_campaigns(f, fleet=None):
    from . import verdicts
    data, found, _ = f.get(fleet)
    keep = ('name', 'status', 'verdict', 'label', 'action', 'sent', 'per_1000', 'baseline',
            'reply_rate', 'bounce_rate', 'sender_bounce_rate', 'left', 'last_sent')
    return [{k: r[k] for k in keep} for r in verdicts.campaigns(data)][:80]


def t_verdicts(f, fleet=None, days=14, verdict=None):
    # One verdict per domain over the last `days` days.
    from . import verdicts, web
    data, found, _ = f.get(fleet)
    rows = verdicts.domains(data, web.pick_days(data, {'range': [str(days)]}, 14))
    counts = collections.Counter(r['verdict'] for r in rows.values())
    keep = ('domain', 'verdict', 'label', 'sent', 'per_1000', 'fleet_per_1000', 'reply_rate',
            'sender_bounce_rate', 'list_bounce_rate', 'buckets')
    pick = [r for r in rows.values() if not verdict or r['verdict'] == verdict]
    pick.sort(key=lambda r: (r['verdict'] in verdicts.GOOD, r['verdict'] == 'not_enough_data', -r['sent']))
    return {'counts': dict(counts), 'domains': [{k: r[k] for k in keep} for r in pick[:60]]}


CTX = {}


def ctx():
    # Approved changes go through the desktop app's own code and settings:
    # the same previews, the same "allow changes" switch per workspace, the
    # same journal and undo.
    if not CTX:
        from . import app as appmod, web

        class Quiet:
            state = {}

            def say(self, msg):
                pass
        settings = appmod.Settings()
        CTX.update(settings=settings, pending={}, checker=Quiet(),
                   fleets=[web.Fleet(f['db']) for f in settings.cfg['fleets'] if os.path.exists(f['db'])])
    return CTX


def change(what, body):
    from . import web
    return web.change_post(ctx(), what, body)


def t_preview(f, incident, step=0, fleet=None):
    act = change('preview', {'fleet': fleet, 'incident': incident, 'step': step})
    return {k: act.get(k) for k in ('id', 'op', 'lines', 'manual', 'big', 'share', 'allowed')} | {
        'undo': [s['what'] for s in act.get('undo', [])]}


def t_propose(f, op, targets=None, limit=None, warmup=None, setting=None, fleet=None):
    act = change('custom', {'fleet': fleet, 'change': {'op': op, 'targets': targets or [], 'limit': limit,
                                                       'warmup': warmup, 'setting': setting}})
    return {k: act.get(k) for k in ('id', 'op', 'lines', 'manual', 'big', 'share', 'allowed')} | {
        'undo': [s['what'] for s in act.get('undo', [])]}


def t_apply(f, change_id, fleet=None, confirm=''):
    return change('apply', {'fleet': fleet, 'id': change_id, 'confirm': confirm})


def t_undo(f, change_id, fleet=None):
    return change('undo', {'fleet': fleet, 'id': change_id})


def t_changes(f, fleet=None):
    from . import actions
    data, found, name = f.get(fleet)
    path = next(p for p in f.paths if (store.get_meta(p, 'workspace') or p) == name)
    return actions.history(path)


FLEET = {'fleet': {'type': 'string', 'description': 'the workspace name; the first if left out'}}
TOOLS = {
    'sitrep': (t_sitrep, 'The current situation report: what needs a decision, what is watched, what just ended.', {}),
    'numbers': (t_numbers, 'Workspace totals by recipient provider (Gmail, Microsoft, Apple, others) and the most refused domains.',
                {'days': {'type': 'integer', 'description': 'days to total, default 7'}}),
    'incidents': (t_incidents, 'Incidents with their plans.  status: open (default), ended, cleared or all.',
                  {'status': {'type': 'string'}, 'kind': {'type': 'string'}}),
    'incident': (t_incident, 'One incident in full: evidence, reasoning, plan, what receivers said.',
                 {'id': {'type': 'integer'}}),
    'domain': (t_domain, 'One sending domain: 14 days by recipient provider, bounce kinds, DNS, warmup, mailboxes, incidents.',
               {'domain': {'type': 'string'}}),
    'family': (t_family, 'Every member of a family of look-alike domains (e.g. growthmail) with its last 7 days.',
               {'name': {'type': 'string'}}),
    'find': (t_find, 'Find domains that are listed, have DNS issues, have mailboxes down, or are being refused.',
             {'what': {'type': 'string', 'enum': ['listed', 'dns', 'down', 'refused']},
              'limit': {'type': 'integer'}}),
    'bounces': (t_bounces, 'Bounce messages as receivers wrote them (addresses removed), by domain and/or kind.',
                {'domain': {'type': 'string'}, 'kind': {'type': 'string'}, 'limit': {'type': 'integer'}}),
    'campaigns': (t_campaigns, 'Campaign reviews: each campaign\'s verdict (winner, average, loser, bad list, too early) '
                  'from its positive replies per 1,000 against the workspace\'s, with bounces and leads left.', {}),
    'verdicts': (t_verdicts, 'One verdict per domain (healthy, strong, listed but working, underperforming, bad list, '
                 'list and domain, fix authentication, burned, blocklisted, account blocked, not enough data) with '
                 'its numbers and its bounces by whose fault they are.  Needs 1,000 sends per domain.',
                 {'days': {'type': 'integer', 'description': 'days to judge, default 14'},
                  'verdict': {'type': 'string', 'description': 'only this verdict, e.g. burned'}}),
    'why_listed': (t_why, 'What the blocklisted domains have in common (registrar, registration month, '
                   'nameservers, website server or redirect, mail server, family), against the clean ones.', {}),
    'preview_change': (t_preview, 'Preview one recommended step of an incident as a change to the sequencer: '
                       'exactly what it would change and how undo works.  Changes nothing.',
                       {'incident': {'type': 'integer'}, 'step': {'type': 'integer', 'description': 'step number, from 0'}}),
    'propose_change': (t_propose, 'Preview a change the user describes, when it differs from the recommended '
                       'steps (e.g. pause these domains and their warmup too).  Changes nothing; '
                       'apply_change after the user says yes.  ' + '  '.join(
                           '%s: %s.' % kv for kv in OPS.items()),
                       {'op': {'type': 'string', 'enum': ['pause', 'set_limit', 'warmup_off', 'suppress_apple',
                                                          'hold_receiver', 'change_settings']},
                        'targets': {'type': 'array', 'items': {'type': 'string'},
                                    'description': 'domains, families as name* (growthmail*), a receiver, or campaign names'},
                        'limit': {'type': 'integer', 'description': 'set_limit: the daily limit'},
                        'warmup': {'type': 'string', 'enum': ['off'], 'description': 'pause warmup too'},
                        'setting': {'type': 'string', 'enum': ['tracking', 'risky']}}),
    'apply_change': (t_apply, 'Apply a previewed change to the real workspace.  Only after the user has seen '
                     'the preview and said yes.  Big changes need confirm set to the workspace name.',
                     {'change_id': {'type': 'string'}, 'confirm': {'type': 'string'}}),
    'undo_change': (t_undo, 'Undo an applied change, restoring exactly what was there before.',
                    {'change_id': {'type': 'string'}}),
    'changes': (t_changes, 'Changes applied from Inbox Sitrep, newest first, with their undo.', {}),
}
REQUIRED = {'incident': ['id'], 'domain': ['domain'], 'family': ['name'], 'find': ['what'],
            'preview_change': ['incident'], 'propose_change': ['op'], 'apply_change': ['change_id'],
            'undo_change': ['change_id']}
WRITES = {'apply_change', 'undo_change'}


def schema(name):
    props = dict(FLEET, **TOOLS[name][2])
    return {'type': 'object', 'properties': props, 'required': REQUIRED.get(name, [])}


def handle(f, msg):
    method, mid = msg.get('method'), msg.get('id')
    if method == 'initialize':
        want = (msg.get('params') or {}).get('protocolVersion') or VERSION
        return {'protocolVersion': want, 'capabilities': {'tools': {}},
                'serverInfo': {'name': 'inboxsitrep', 'version': __version__},
                'instructions': 'Tools over cold email workspaces that Inbox Sitrep has collected and '
                                'checked.  Start with sitrep.  Changes: preview_change first, show '
                                'the user, apply_change only after they say yes.'}
    if method == 'tools/list':
        return {'tools': [{'name': n, 'description': TOOLS[n][1], 'inputSchema': schema(n),
                           'annotations': {'readOnlyHint': n not in WRITES,
                                           'destructiveHint': n in WRITES}} for n in TOOLS]}
    if method == 'tools/call':
        p = msg.get('params') or {}
        name, args = p.get('name'), p.get('arguments') or {}
        if name not in TOOLS:
            return {'content': [{'type': 'text', 'text': 'no tool %s' % name}], 'isError': True}
        try:
            out = TOOLS[name][0](f, **args)
        except (KeyError, TypeError, ValueError, RuntimeError, StopIteration) as e:
            return {'content': [{'type': 'text', 'text': str(e)}], 'isError': True}
        text = out if isinstance(out, str) else json.dumps(out, indent=1, default=str)
        return {'content': [{'type': 'text', 'text': text}]}
    if method == 'ping':
        return {}
    if mid is None:
        return None             # a notification; nothing to answer
    raise LookupError(method)


def serve(paths, inp=sys.stdin, out=sys.stdout):
    f = Fleets(paths)
    for raw in inp:
        if not raw.strip():
            continue
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        try:
            result = handle(f, msg)
            reply = {'jsonrpc': '2.0', 'id': msg.get('id'), 'result': result}
        except LookupError as e:
            reply = {'jsonrpc': '2.0', 'id': msg.get('id'),
                     'error': {'code': -32601, 'message': 'no method %s' % e}}
        if msg.get('id') is not None:
            out.write(json.dumps(reply) + '\n')
            out.flush()
