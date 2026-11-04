# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# The standing watch: collect every fleet in the config (read-only), check
# DNS when it is stale, find incidents, write the sitrep, and tell someone
# about what is new since the last run.  A systemd timer runs it twice a
# day; live mode runs between.
#
# ~/.config/inboxsitrep/config.json:
#   {"fleets": [{"name": "Main", "sequencer": "plusvibe", "workspace": "<id>",
#                "key_file": "~/.config/inboxsitrep/main.key",
#                "db": "~/fleets/main.db", "live": true}],
#    "notify": {"desktop": true, "webhook": null},
#    "sitreps": "~/.local/share/inboxsitrep/sitreps"}
# sequencer is plusvibe, instantly, smartlead (with "export": a lead CSV)
# or csv (an export only).  Keys come from key_file or key_env, never the
# config itself.

import os
import sys
import json
import time
import datetime

from . import brief, csvimport, dns, incidents, notify, live
from . import data as store


CONFIG = os.path.expanduser('~/.config/inboxsitrep/config.json')
DNS_EVERY = 7       # days between DNS checks of the whole fleet


def path(p):
    return os.path.expanduser(p) if p else p


def load(cfg_path=None):
    cfg_path = path(cfg_path) or CONFIG
    if not os.path.exists(cfg_path):
        sys.exit('inboxsitrep: no config at %s (see `inboxsitrep watch --help`)' % cfg_path)
    with open(cfg_path) as f:
        cfg = json.load(f)
    from . import rules
    for problem in rules.load(rules.path_for(cfg_path)):
        print('inboxsitrep: rules.ini: %s' % problem, file=sys.stderr)
    for f in cfg.get('fleets', []):
        f['db'] = path(f['db'])
    return cfg


def key(fleet):
    if fleet.get('key_file'):
        with open(path(fleet['key_file'])) as f:
            return f.read().strip()
    if fleet.get('key_env'):
        return os.environ.get(fleet['key_env'], '')
    return ''


def client(fleet):
    seq = fleet.get('sequencer', 'csv')
    if seq == 'plusvibe':
        from . import plusvibe
        return plusvibe.Client(key(fleet), fleet['workspace'])
    if seq == 'instantly':
        from . import instantly
        return instantly.Client(key(fleet))
    if seq == 'smartlead':
        from . import smartlead
        return smartlead.Client(key(fleet))
    if seq == 'emailbison':
        from . import emailbison
        if not fleet.get('base_url'):
            raise ValueError('EmailBison needs your instance address, e.g. https://send.example.com')
        return emailbison.Client(key(fleet), fleet['base_url'])
    return None


# One small read per sequencer, to tell a working key from a wrong one
# before the first check.
PROBES = {'plusvibe': ('/account/list', {'skip': 0, 'limit': 1}),
          'instantly': ('/api/v2/accounts', {'limit': 1}),
          'emailbison': ('/api/sender-emails', {'page': 1, 'per_page': 1}),
          'smartlead': ('/email-accounts/', {'offset': 0, 'limit': 1})}
NAMES = {'plusvibe': 'PlusVibe', 'instantly': 'Instantly', 'emailbison': 'EmailBison', 'smartlead': 'Smartlead'}


def probe(fleet):
    seq = fleet.get('sequencer')
    if seq not in PROBES:
        return 'Nothing to test for %s.' % seq
    if not key(fleet):
        raise ValueError('no API key saved for %s' % fleet['name'])
    path, params = PROBES[seq]
    try:
        client(fleet).get(path, **params)
    except (OSError, RuntimeError) as e:
        code = getattr(e, 'code', None)
        if code and 400 <= code < 500:
            try:
                said = e.read()[:300].decode('utf-8', 'replace').strip()
                said = json.loads(said).get('message') or said
            except (OSError, AttributeError, ValueError):
                said = ''
            raise ValueError('%s refused it: %s.  Check the key and the workspace id'
                             % (NAMES[seq], str(said).rstrip('.') or 'error %s' % code))
        raise ValueError('could not read the workspace: %s' % (str(e)[:160] or e.__class__.__name__))
    return 'The key works: %s answered.' % fleet['name']


def gather(fleet, say):
    seq = fleet.get('sequencer', 'csv')
    days = fleet.get('days', 31)
    api = client(fleet)
    if seq == 'plusvibe':
        from . import plusvibe
        prior = store.load(fleet['db']) if os.path.exists(fleet['db']) else None
        return plusvibe.collect(api, days=days, say=say, prior=prior)
    if seq == 'instantly':
        from . import instantly
        return instantly.collect(api, days=days, say=say)
    if seq == 'emailbison':
        from . import emailbison
        return emailbison.collect(api, days=days, say=say)
    data = None
    if seq == 'sim' and os.path.exists(fleet['db']):
        # A simulated fleet has nothing to collect; it is checked as it is.
        say('simulated fleet, nothing to collect')
        return store.load(fleet['db'])
    if seq == 'sim':
        # The first look: 45 days of a fleet with the failures seen in the
        # field planted in it.
        from . import sim
        say('simulating 45 days of a fleet with planted problems')
        f = sim.standard(sim.Fleet(1))
        for _ in range(45):
            f.step()
        sim.add_campaigns(f)
        return f.data
    if fleet.get('export'):
        data, _ = csvimport.read(path(fleet['export']))
    elif os.path.exists(fleet['db']):
        data = store.load(fleet['db'])
    if seq == 'smartlead':
        from . import smartlead
        data = smartlead.mailboxes(api, data, say=say)
    if data is None:
        raise ValueError('nothing to read for %s' % fleet['name'])
    return data


def stale(data):
    checked = [r.get('checked') for r in data.dns.values() if r.get('checked')]
    if not checked:
        return True
    oldest = datetime.date.fromisoformat(min(checked))
    return (datetime.date.today() - oldest).days >= DNS_EVERY


SITREPS = '~/.local/share/inboxsitrep/sitreps'


def sitrep_file(cfg, fleet, text):
    where = path(cfg.get('sitreps') or SITREPS)
    os.makedirs(where, exist_ok=True)
    slug = fleet['name'].lower().replace(' ', '-')
    name = os.path.join(where, '%s-%s.md' % (slug, datetime.datetime.now().strftime('%Y-%m-%d-%H%M')))
    with open(name, 'w') as f:
        f.write(text + '\n')
    return name


def watch_one(cfg, fleet, send, say, report=False, outbox=None):
    # outbox: a list to put the message in instead of sending it, so a run
    # over several workspaces sends one message.
    t0 = time.time()
    data = gather(fleet, say)
    if fleet.get('sequencer') != 'sim':
        for dom in data.domains():
            data.workspace[dom] = fleet['name']
        if os.path.exists(fleet['db']):
            store.carry(store.load(fleet['db']), data)
        if data.days and stale(data):
            say('checking DNS')
            dns.check_all(data, say=say)
    # New since the last check; for a report, new since the last report.
    def keys(name):
        return set(map(tuple, store.get_meta(fleet['db'], name, []) or [])) \
            if os.path.exists(fleet['db']) else set()
    before = keys('open_keys')
    checked = os.path.exists(fleet['db']) and store.get_meta(fleet['db'], 'open_keys') is not None
    since = (keys('report_keys') or before) if report else before
    say('looking for trouble')
    meta = {'workspace': fleet['name'],
            'collected': datetime.datetime.now().isoformat(timespec='seconds')}
    if fleet.get('sequencer') != 'sim':
        meta['source'] = fleet.get('sequencer', 'csv')
    store.save(data, fleet['db'], meta=meta)
    found = incidents.track(store.reclassify(data))
    incidents.save(found, fleet['db'])
    found = incidents.load(fleet['db'])          # numbered, so the report can cite them
    now_open = {(i['kind'], i['target']) for i in found if i['status'] == 'open'}
    new = [i for i in found if i['status'] == 'open' and (i['kind'], i['target']) not in before
           and i['kind'] in brief.DECIDE]
    # On a first check everything would be "new": mark nothing.
    fresh = {i['id'] for i in found if i['status'] == 'open' and (i['kind'], i['target']) not in since} \
        if checked else set()
    day = datetime.date.today() if fleet.get('sequencer') != 'sim' else None
    text, how = brief.brief(data, found, fleet['name'], fresh, day), 'plain'
    if cfg.get('analyst'):
        from . import agent
        say('the analyst is writing the briefing')
        text, how = agent.briefing(data, found, fleet['name'], model=cfg.get('model'), fresh=fresh, today=day)
    if report:
        store.save_meta(fleet['db'], {'report_keys': sorted(now_open), 'fresh_ids': sorted(fresh)})
    store.save_meta(fleet['db'], {'open_keys': sorted(now_open), 'briefing': {
        'text': text, 'how': how, 'at': datetime.datetime.now().isoformat(timespec='minutes'),
        'new': [i['title'] for i in new]}})
    sitrep_file(cfg, fleet, text)
    if new:
        title = ('%s: %s' % (fleet['name'], new[0]['title']) if len(new) == 1 else
                 '%s: %d new problems need a decision' % (fleet['name'], len(new)))
        notify.log(title, '')
        if (cfg.get('notify') or {}).get('desktop', True):
            notify.desktop(title, '; '.join(i['title'] for i in new[1:4]))
    ncfg = cfg.get('notify') or {}
    # At a report time the app sends one report for every workspace once
    # they are all checked (Checker.digest), not one message each.
    if notify.channels(ncfg) and not report and new and cfg.get('between') == 'new':
        subject = ('Inbox Sitrep, %s: %s' % (fleet['name'], new[0]['title']) if len(new) == 1 else
                   'Inbox Sitrep, %s: %d new problems' % (fleet['name'], len(new)) if new else
                   'Inbox Sitrep, %s: report' % fleet['name'])
        if outbox is not None:
            outbox.append((fleet['name'], subject, text, len(new)))
        else:
            say('sending the report to %s' % ', '.join(notify.channels(ncfg)))
            notify.send(ncfg, subject, text, say=say)
    store.save_meta(fleet['db'], {'took': round(time.time() - t0)})
    say('done: %d incidents, %d new, %.0f s' % (len(found), len(new), time.time() - t0))
    return found, new


def run(cfg, only=None, say=print):
    send = notify.sender(cfg.get('notify'))
    for fleet in cfg.get('fleets', []):
        if only and fleet['name'] != only:
            continue
        try:
            watch_one(cfg, fleet, send, lambda m, n=fleet['name']: say('%s: %s' % (n, m)))
        except (OSError, RuntimeError, ValueError) as e:
            say('%s: failed: %s' % (fleet['name'], e))
            send('inboxsitrep could not read %s' % fleet['name'], str(e)[:200])


def live_loop(cfg, every=15, once=False, say=print):
    send = notify.sender(cfg.get('notify'))
    fleets = [f for f in cfg.get('fleets', []) if f.get('live') and f.get('sequencer') == 'plusvibe']
    if not fleets:
        sys.exit('inboxsitrep: no fleet in the config has "live": true (PlusVibe only, for now)')
    while True:
        for fleet in fleets:
            if not os.path.exists(fleet['db']):
                store.connect(fleet['db']).close()
            live.cycle(client(fleet), fleet['db'],
                       lambda t, b, n=fleet['name']: send('%s: %s' % (n, t), b),
                       lambda m, n=fleet['name']: say('%s %s: %s' % (
                           datetime.datetime.now().strftime('%H:%M'), n, m)))
        if once:
            return
        time.sleep(every * 60)


UNIT = '''[Unit]
Description=inboxsitrep %(what)s

[Service]
Type=%(type)s
Environment=PYTHONPATH=%(path)s
Environment=PYTHONUNBUFFERED=1
ExecStart=%(python)s -m inboxsitrep %(args)s
%(extra)s
'''
TIMER = '''[Unit]
Description=inboxsitrep watch, %(times)s

[Timer]
%(calendar)s
Persistent=true

[Install]
WantedBy=timers.target
'''


def schedule(cfg_path, times=('07:13', '15:13'), every=15, port=8008):
    # systemd user units: the watch on a timer (Persistent, so a laptop
    # that slept through 07:13 catches up when it wakes), live mode and the
    # dashboard as services.
    where = os.path.expanduser('~/.config/systemd/user')
    os.makedirs(where, exist_ok=True)
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg_path = os.path.abspath(path(cfg_path) or CONFIG)
    common = {'path': here, 'python': sys.executable}
    units = {
        'inboxsitrep-watch.service': UNIT % dict(common, what='watch', type='oneshot',
                                                 args='watch --config ' + cfg_path, extra=''),
        'inboxsitrep-watch.timer': TIMER % {
            'times': ' and '.join(times),
            'calendar': '\n'.join('OnCalendar=*-*-* %s:00' % t for t in times)},
        'inboxsitrep-live.service': UNIT % dict(
            common, what='live mode', type='simple',
            args='live --config %s --every %d' % (cfg_path, every),
            extra='Restart=on-failure\nRestartSec=120\n\n[Install]\nWantedBy=default.target'),
        'inboxsitrep-dashboard.service': UNIT % dict(
            common, what='dashboard', type='simple',
            args='serve --config %s --port %d' % (cfg_path, port),
            extra='Restart=on-failure\nRestartSec=30\n\n[Install]\nWantedBy=default.target'),
    }
    for name, text in units.items():
        with open(os.path.join(where, name), 'w') as f:
            f.write(text)
    return [os.path.join(where, n) for n in units]
