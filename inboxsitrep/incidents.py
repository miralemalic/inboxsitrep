# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# Run the detectors day by day and keep what they find as incidents: one
# problem, with the day it was first seen, the day it was last seen, and
# how it ended.  A domain Gmail refuses is one incident that either clears
# (it was an episode) or turns into a burn; it is not three findings.

import json
import datetime

from . import detect
from .plan import Operator
from .data import connect


# Findings that are stages of the same problem share a key.
STAGE = {'rejecting': 'gmail', 'burned': 'gmail', 'cleared': 'gmail'}
QUIET_DAYS = 2      # unseen this long and it's over
# ...except a blocklisting, which lasts until someone gets it lifted.  A
# listed domain that stops sending stops producing bounces, but it is still
# listed; without this it would flap open and shut with its own volume.
QUIET = {'listed': 14, 'tenant_block': 7, 'receiver': 7, 'link_listed': 14}


def gap(a, b):
    return (datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days


def track(data, fams=None, days=None):
    fams = fams if fams is not None else detect.families(data.domains())
    op = Operator()
    live, closed = {}, []
    for day in days or data.days:
        seen = set()
        for f in detect.run(data, day, fams, op.held()):
            key = (STAGE.get(f['kind'], f['kind']), f['target'])
            inc = live.get(key)
            if f['kind'] == 'cleared':
                if inc:
                    # In hindsight it was an episode, whatever it looked
                    # like on day three.  The plan was the same either way.
                    days = gap(inc['opened'], day)
                    inc.update(kind='episode', status='cleared', updated=day,
                               title='Gmail refused mail from %s for %s, then '
                                     'took it again' % (inc['target'],
                                                         detect.plural(days, 'day')),
                               ending='cleared on %s: an episode, not a burn' % day)
                    closed.append(live.pop(key))
                continue
            seen.add(key)
            # The plan grows with the problem: more of a family listed, more
            # of it dead.  The operator only answers with what is new.
            changes = op.plan(f)
            for c in changes:
                op.record(c, f['kind'])
            if inc is None:
                inc = live[key] = {
                    'kind': f['kind'], 'scope': f['scope'], 'target': f['target'],
                    'opened': day, 'status': 'open', 'plan': [], 'history': []}
            inc['plan'].extend(changes)
            if f['kind'] == 'tenant_block':
                # A domain that has sent normally since its block bounce
                # drops out of the block, and out of the plan to pause it.
                keep = set(f['numbers'].get('domains') or ())
                inc['plan'] = [dict(c, targets=[t for t in c['targets'] if t in keep])
                               for c in inc['plan'] if c['op'] != 'pause' or keep & set(c['targets'])]
            # Once burned, a quieter day doesn't make it "rejecting" again.
            if not (inc['kind'] == 'burned' and f['kind'] == 'rejecting'):
                inc['kind'] = f['kind']
            inc.update(updated=day, title=f['title'], confidence=f['confidence'],
                       evidence=f['numbers'])
            inc['history'].append((day, f['kind']))
        for key, inc in list(live.items()):
            if key not in seen and gap(inc['updated'], day) >= QUIET.get(inc['kind'], QUIET_DAYS):
                if inc['kind'] == 'cascade':
                    why = 'no new deaths since %s' % inc['evidence'].get('last')
                else:
                    why = 'not seen since %s' % inc['updated']
                inc.update(status='ended', ending=why)
                closed.append(live.pop(key))
    return sorted(closed + list(live.values()), key=lambda i: (i['opened'], i['kind']))


def save(incidents, path):
    db = connect(path)
    with db:
        db.execute('DELETE FROM incidents')
        for n, i in enumerate(incidents, 1):
            db.execute('INSERT INTO incidents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', (
                n, i['kind'], i['scope'], i['target'], 'google',
                i['opened'], i['updated'], i['status'], i['confidence'],
                i['title'], json.dumps({'numbers': i['evidence'],
                                        'history': i['history'],
                                        'ending': i.get('ending')}),
                json.dumps(i['plan']), i.get('report')))
    db.close()


def load(path):
    db = connect(path)
    out = []
    cols = [c[1] for c in db.execute('PRAGMA table_info(incidents)')]
    for row in db.execute('SELECT * FROM incidents ORDER BY id'):
        i = dict(zip(cols, row))
        ev = json.loads(i.pop('evidence') or '{}')
        i['evidence'] = ev.get('numbers') or {}
        i['history'] = ev.get('history') or []
        i['ending'] = ev.get('ending')
        i['plan'] = json.loads(i['plan'] or '[]')
        out.append(i)
    db.close()
    return out
