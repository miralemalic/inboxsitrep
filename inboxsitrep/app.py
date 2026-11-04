# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# The desktop app: open it and it stands watch until you close it.
#
# One process.  The dashboard is served on a loopback port picked at
# random and shown in a window of its own; a checker thread collects every
# workspace in the settings when the app opens and again every few hours,
# has the analyst write the briefing, and sends it to Slack.  Close the
# window and all of it stops.  Settings and keys live in
# ~/.config/inboxsitrep, data in ~/.local/share/inboxsitrep.

import os
import re
import sys
import json
import time
import socket
import secrets
import datetime
import threading
import http.server

from . import watch, notify, web, slackbot, telegram
from . import data as store

HOME = os.path.expanduser('~/.config/inboxsitrep')
DATA = os.path.expanduser('~/.local/share/inboxsitrep')
QUICK = 15         # minutes between quick sweeps for urgent bounces
DEFAULTS = {'fleets': [], 'every_hours': 6, 'alerts': True, 'analyst': True, 'model': 'sonnet',
            'notify': {'desktop': True, 'webhook': ''}, 'between': 'urgent',
            'report_at': ['08:00', '17:00'], 'weekly': {'day': 5, 'at': '08:00'}}
LATE = 3           # hours after a report time that the report still goes out
SEQUENCERS = ('plusvibe', 'instantly', 'emailbison', 'smartlead', 'sim')


def slug(name):
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-') or 'fleet'


class Settings:

    def __init__(self, path=None):
        # INBOXSITREP_CONFIG points everything (app, MCP server, plugin) at
        # another settings file, e.g. a demo fleet.
        self.path = os.path.expanduser(path or os.environ.get('INBOXSITREP_CONFIG')
                                       or os.path.join(HOME, 'config.json'))
        # Started by the Claude Code plugin with no workspaces set up (a new
        # user, or the plugin's own exam in a sealed home): use the exam
        # fleets that ship next to the plugin, if they were built.
        exam = os.path.join(os.environ.get('INBOXSITREP_PLUGIN', ''), 'evals', 'fleets', 'config.json')
        if not path and not os.path.exists(self.path) and os.path.exists(exam):
            self.path = exam
        self.lock = threading.Lock()
        self.cfg = json.loads(json.dumps(DEFAULTS))     # a copy: the defaults hold lists
        if os.path.exists(self.path):
            with open(self.path) as f:
                self.cfg.update(json.load(f))
        # The thresholds, from rules.ini beside this file (written the first time).
        from . import rules
        self.rules_path = rules.path_for(self.path)
        self.rules_problems = rules.load(self.rules_path)
        for f in self.cfg['fleets']:
            f['db'] = os.path.expanduser(f.get('db') or os.path.join(DATA, slug(f['name']) + '.db'))
        if 'between' not in self.cfg:
            # Settings from before the daily and weekly reports: reports at
            # 08:00 and 17:00, the weekly on Saturday morning, urgent alerts only.
            self.cfg['between'] = 'urgent'
            self.cfg.pop('slack_on', None)
            if not self.cfg.get('report_at'):
                self.cfg['report_at'] = list(DEFAULTS['report_at'])
            self.cfg.setdefault('weekly', dict(DEFAULTS['weekly']))

    def save(self):
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        tmp = self.path + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(self.cfg, f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def public(self):
        # What the window may see: everything but the keys.
        from . import notify
        out = json.loads(json.dumps(self.cfg))
        for f in out['fleets']:
            f['has_key'] = bool(f.pop('key_file', None) or f.get('key_env'))
            f.pop('db', None)
        n = out.setdefault('notify', {})
        n['has_telegram_key'] = bool(notify.secret('telegram.key'))
        n['has_slack_bot'] = bool(notify.secret('slack-bot.key') and notify.secret('slack-app.key'))
        out['autostart'] = os.path.exists(AUTOSTART)
        return out

    def update(self, body):
        # From the settings page.  A key arrives once and goes straight to
        # its own file; it is never sent back.
        times = report_times(body['report_at']) if 'report_at' in body else None
        if 'weekly' in body:
            w = body['weekly'] or {}
            day = w.get('day')
            if day is not None and day not in range(7):
                raise ValueError('the weekly report day is Monday to Sunday')
            at = report_times(w.get('at') or '17:00')
            body = dict(body, weekly={'day': day, 'at': at[0] if at else '17:00'})
        with self.lock:
            if times is not None:
                self.cfg['report_at'] = times
            for k in ('every_hours', 'analyst', 'model', 'between', 'alerts', 'weekly'):
                if k in body:
                    self.cfg[k] = body[k]
            if 'webhook' in body:
                self.cfg['notify']['webhook'] = body['webhook'].strip()
            if 'slack_channel' in body:
                self.cfg['notify']['slack_channel'] = body['slack_channel'].strip()
            for field, name in (('slack_bot', 'slack-bot.key'), ('slack_app', 'slack-app.key')):
                if body.get(field):
                    self.keep(name, body[field])
            if body.get('telegram_token') or body.get('telegram_unlink'):
                # A new bot, or a new chat: pair again with a fresh code.
                if body.get('telegram_token'):
                    self.keep('telegram.key', body['telegram_token'])
                self.cfg['notify']['telegram'] = {'code': '%06d' % secrets.randbelow(10 ** 6), 'chat_id': None}
            if 'desktop' in body:
                self.cfg['notify']['desktop'] = bool(body['desktop'])
            if 'autostart' in body:
                autostart(bool(body['autostart']))
            for f in body.get('fleets', ()):
                self.put_fleet(f)
            for name in body.get('remove', ()):
                self.cfg['fleets'] = [f for f in self.cfg['fleets'] if f['name'] != name]
            self.save()

    def keep(self, name, value):
        keys = notify.KEYS          # one place for every secret
        os.makedirs(keys, mode=0o700, exist_ok=True)
        path = os.path.join(keys, name)
        with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as k:
            k.write(value.strip() + '\n')
        return path

    def put_fleet(self, f):
        name = (f.get('name') or '').strip()
        old = next((x for x in self.cfg['fleets'] if x['name'] == name), None)
        if not name or (old is None and f.get('sequencer') not in SEQUENCERS):
            raise ValueError('a workspace needs a name and a sequencer')
        new = old or {'name': name, 'db': os.path.join(DATA, slug(name) + '.db')}
        new['sequencer'] = f.get('sequencer') or new.get('sequencer')
        if f.get('workspace') is not None:
            new['workspace'] = f['workspace'].strip()
        if f.get('key'):
            new['key_file'] = self.keep(slug(name) + '.key', f['key'])
        if f.get('base_url') is not None:
            new['base_url'] = f['base_url'].strip().rstrip('/')
        if 'writes' in f:
            # Changes allowed for this workspace; each one is still
            # previewed and approved.
            new['writes'] = bool(f['writes'])
        if old is None:
            self.cfg['fleets'].append(new)


def report_times(value):
    # "8:00, 17:30" or ['08:00'] -> ['08:00', '17:30']
    items = value.replace(';', ',').split(',') if isinstance(value, str) else value or []
    out = set()
    for t in items:
        m = re.fullmatch(r'\s*(\d{1,2})[:.]?(\d{2})?\s*', str(t))
        if not str(t).strip():
            continue
        if not m or int(m.group(1)) > 23 or int(m.group(2) or 0) > 59:
            raise ValueError('report times look like 08:00 or 17:30, not "%s"' % str(t).strip())
        out.add('%02d:%s' % (int(m.group(1)), m.group(2) or '00'))
    return sorted(out)


class Stopped(Exception):
    # Raised inside a check when the person presses Stop.  The check ends at
    # its next progress line; nothing half-collected is saved.
    pass


class Checker(threading.Thread):
    # Checks every workspace when the app opens and then every few hours.
    # One at a time; each says what it is doing so the window can show it.

    def __init__(self, settings, fleets):
        super().__init__(daemon=True)
        self.settings = settings
        self.fleets = fleets            # the dashboard's list; grows as databases appear
        self.wake = threading.Event()
        self.halt = threading.Event()
        self.only = None
        self.report = False
        self.failed = {}
        self.outbox = []
        self.swept = {}
        self.reported_file = os.path.join(state_dir(settings), 'reports.json')
        try:
            with open(self.reported_file) as f:
                self.reported = json.load(f)
        except (OSError, ValueError):
            self.reported = {}
        self.state = {'busy': False, 'fleet': None, 'step': '', 'log': [], 'next': None,
                      'started': None, 'queued': False, 'done': 0, 'total': 0}
        self.logfile = os.path.join(state_dir(settings), 'app.log')

    def say(self, msg):
        if self.halt.is_set() and self.state['busy'] and self.state['fleet']:
            raise Stopped()
        stamp = datetime.datetime.now().strftime('%H:%M:%S')
        msg = msg.strip()
        self.state['step'] = msg
        line = '%s  %s%s' % (stamp, self.state['fleet'] + ': ' if self.state['fleet'] else '', msg)
        self.state['log'] = (self.state['log'] + [line])[-60:]
        os.makedirs(DATA, exist_ok=True)
        if os.path.exists(self.logfile) and os.path.getsize(self.logfile) > 5 << 20:
            # keep the last megabyte
            with open(self.logfile, 'rb') as f:
                f.seek(-(1 << 20), 2)
                tail = f.read()
            with open(self.logfile, 'wb') as f:
                f.write(tail[tail.find(b'\n') + 1:])
        with open(self.logfile, 'a') as f:
            f.write(datetime.date.today().isoformat() + ' ' + line + '\n')

    def stop(self):
        self.halt.set()
        self.only = None
        self.state['queued'] = False

    def now(self, fleet=None):
        # "Check now" checks now, due or not; '*' means every workspace.
        # Pressed during a check, it runs as soon as that one ends.
        self.only = fleet or '*'
        self.state['queued'] = self.state['busy']
        self.wake.set()

    def due(self, f):
        if not os.path.exists(f['db']):
            return True
        last = store.get_meta(f['db'], 'collected')
        if not last:
            return True
        age = datetime.datetime.now() - datetime.datetime.fromisoformat(last)
        return age.total_seconds() >= float(self.settings.cfg['every_hours']) * 3600

    def lead(self, cfg):
        # How long checking every workspace took last time, so a report run
        # starts that much early and the 08:00 report arrives near 08:00.
        return datetime.timedelta(seconds=min(sum(took(f) for f in cfg['fleets']), 2 * 3600))

    def slot(self, cfg, now=None):
        # (time, day) of a report that is due and not sent yet, or None;
        # ('weekly', day) on the weekly report's day.  An app opened hours
        # after a report time skips it rather than send a morning report at
        # night.
        now = now or datetime.datetime.now()
        lead = self.lead(cfg)
        w = cfg.get('weekly') or {}
        if w.get('day') is not None and w.get('at'):
            for day in (now.date(), now.date() + datetime.timedelta(days=1)):
                if day.weekday() == int(w['day']):
                    at = datetime.datetime.combine(day, datetime.time.fromisoformat(w['at']))
                    if at - lead <= now < at + datetime.timedelta(hours=LATE) \
                            and self.reported.get('weekly') != day.isoformat():
                        return 'weekly', day.isoformat()
        for t in cfg.get('report_at') or ():
            for day in (now.date(), now.date() + datetime.timedelta(days=1)):
                at = datetime.datetime.combine(day, datetime.time.fromisoformat(t))
                if at - lead <= now < at + datetime.timedelta(hours=LATE) \
                        and self.reported.get(t) != day.isoformat():
                    return t, day.isoformat()
        return None

    def until_report(self, cfg, now=None):
        # Seconds to the start of the next report run.
        now = now or datetime.datetime.now()
        lead = self.lead(cfg)
        waits = []
        for t in cfg.get('report_at') or ():
            at = datetime.datetime.combine(now.date(), datetime.time.fromisoformat(t)) - lead
            while at <= now:
                at += datetime.timedelta(days=1)
            waits.append((at - now).total_seconds())
        w = cfg.get('weekly') or {}
        if w.get('day') is not None and w.get('at'):
            at = datetime.datetime.combine(now.date(), datetime.time.fromisoformat(w['at'])) - lead
            while at <= now or at.weekday() != int(w['day']):
                at += datetime.timedelta(days=1)
            waits.append((at - now).total_seconds())
        return min(waits) if waits else None

    def run(self):
        # Nothing that goes wrong in one round may end the watch for good.
        while True:
            try:
                self.round()
            except Exception as e:
                self.state.update(busy=False, fleet=None)
                self.say('the watch hit an error and carries on: %s' % e)
                time.sleep(30)

    def round(self):
        # One pass: what is due, checked one workspace at a time, then the report.
        cfg = self.settings.cfg
        forced, self.only = self.only, None
        due = self.slot(cfg)
        slot = due and due[0]
        if due:
            # Report time: check everything afresh and send the report,
            # whether or not anything new turned up.
            forced = '*'
            self.reported[slot] = due[1]
            if slot == 'weekly':
                # the weekly report stands in for a daily one due at the same time
                at = datetime.time.fromisoformat(cfg['weekly']['at'])
                for t in cfg.get('report_at') or ():
                    gap = abs(datetime.datetime.combine(datetime.date.today(), datetime.time.fromisoformat(t))
                              - datetime.datetime.combine(datetime.date.today(), at))
                    if gap <= datetime.timedelta(hours=LATE):
                        self.reported[t] = due[1]
            try:
                os.makedirs(DATA, exist_ok=True)
                with open(self.reported_file, 'w') as f:
                    json.dump(self.reported, f)
            except OSError:
                pass
        self.report = bool(slot)
        self.failed = {}
        self.outbox = []
        self.state['queued'] = False
        todo = [f for f in cfg['fleets'] if forced in ('*', f['name'])
                or (forced is None and self.due(f))]
        # Busy from the first workspace to the last, with how long each
        # took last time, so the window can show one bar and the time left.
        self.state.update(total=len(todo), done=0, busy=bool(todo), ran=0,
                          run_started=datetime.datetime.now().isoformat(timespec='seconds'),
                          expect=[took(f) for f in todo])
        self.halt.clear()
        for f in todo:
            self.state.update(fleet=f['name'], started=datetime.datetime.now().isoformat(timespec='seconds'))
            t0 = time.time()
            try:
                self.check(cfg, f)
            except Stopped:
                self.state['fleet'] = None      # so saying so doesn't stop again
                self.say('%s: stopped; the last complete check is still shown' % f['name'])
                break
            except Exception as e:      # keep watching whatever one fleet does
                self.say('failed: %s' % e)
                self.failed[f['name']] = str(e)[:160] or e.__class__.__name__
            self.state.update(done=self.state['done'] + 1, ran=self.state['ran'] + time.time() - t0)
        self.state.update(busy=False, fleet=None)
        stopped = self.halt.is_set()
        self.send_outbox(cfg)
        self.halt.clear()
        if slot and todo and not stopped:
            self.digest(cfg, slot)
        if forced and not todo:
            self.say('nothing to check')
        if not todo and cfg.get('alerts', True):
            self.quick(cfg)
        self.report = False
        self.state['next'] = self.next_due(cfg)
        wait = self.until_report(cfg)
        self.state['next_report'] = ((datetime.datetime.now() + datetime.timedelta(seconds=wait))
                                     .isoformat(timespec='minutes') if wait is not None else None)
        if not self.only:
            self.wake.wait(timeout=min(QUICK * 60, wait + 1 if wait is not None else QUICK * 60))
        self.wake.clear()

    def next_due(self, cfg):
        times = []
        for f in cfg['fleets']:
            last = os.path.exists(f['db']) and store.get_meta(f['db'], 'collected')
            if not last:
                return datetime.datetime.now().isoformat(timespec='minutes')
            times.append(datetime.datetime.fromisoformat(last)
                         + datetime.timedelta(hours=float(cfg['every_hours'])))
        return min(times).isoformat(timespec='minutes') if times else None

    def quick(self, cfg):
        # Between full checks: only the bounces since the last look, read
        # in two light requests, for the signatures that can't wait hours
        # (a tenant block, Gmail starting to refuse a domain, a blocklist
        # naming one).  PlusVibe only, for now.
        from . import live
        for f in cfg['fleets']:
            if f.get('sequencer') != 'plusvibe' or not os.path.exists(f['db']):
                continue
            if time.time() - self.swept.get(f['name'], 0) < QUICK * 60 - 5:
                continue
            self.swept[f['name']] = time.time()
            try:
                live.cycle(watch.client(f), f['db'],
                           notify=lambda t, b, n=f['name']: self.alert(cfg, n, t, b),
                           say=lambda m: None)
            except (OSError, RuntimeError, ValueError) as e:
                self.state['quick_error'] = str(e)[:200]

    def send_outbox(self, cfg):
        # What this run found, in one message however many workspaces it
        # covered.
        box, self.outbox = self.outbox, []
        n = cfg.get('notify') or {}
        if not box or not notify.channels(n):
            return
        if len(box) == 1:
            title, body = box[0][1], box[0][2]
        else:
            title = 'New problems · ' + ', '.join('%s (%d)' % (b[0], b[3]) for b in box if b[3])
            body = '\n\n---\n\n'.join(b[2] for b in box)
        self.say('sending %s to %s' % (title, ', '.join(notify.channels(n))))
        notify.send(n, title, body, say=self.say)

    def digest(self, cfg, slot):
        # The daily SITREP, or on its day the weekly one, for every
        # workspace in one message.
        from . import reports
        if slot == 'weekly':
            title, text = reports.weekly(cfg)
        else:
            title, text = reports.daily(cfg, today_live=web.today_numbers)
        n = cfg.get('notify') or {}
        if not text or not notify.channels(n):
            return
        if self.failed:
            text = '\n'.join('%s: this check failed (%s); its numbers are from the last good one.' % kv
                              for kv in self.failed.items()) + '\n\n' + text
        self.say('sending the %s to %s' % (title, ', '.join(notify.channels(n))))
        notify.send(n, title, text, say=self.say)

    def alert(self, cfg, name, title, body):
        self.say('ALERT %s: %s' % (name, title))
        n = cfg.get('notify') or {}
        if n.get('desktop', True):
            notify.desktop('%s: %s' % (name, title), body)
        notify.send(n, 'ALERT · %s · %s' % (name, title), body)

    def check(self, cfg, f):
        os.makedirs(os.path.dirname(f['db']), exist_ok=True)
        send = notify.sender(dict(cfg['notify'], desktop=cfg['notify'].get('desktop', True)))
        watch.watch_one(cfg, f, send, self.say, report=self.report, outbox=self.outbox)
        if not any(x.path == f['db'] for x in self.fleets) and os.path.exists(f['db']):
            self.fleets.append(web.Fleet(f['db']))


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def answer(settings, question):
    # A question from Slack, answered by the analyst over every workspace;
    # without Claude Code, the latest reports.
    from . import agent
    paths = [f['db'] for f in settings.cfg['fleets'] if os.path.exists(f['db'])]
    if not paths:
        return 'No workspace has been checked yet.'
    text, err = agent.ask(question, paths, model=settings.cfg.get('model'))
    if text:
        return text
    reports = [(store.get_meta(p, 'briefing') or {}).get('text') for p in paths]
    return ('The analyst could not answer (%s).  The latest reports:\n\n' % err) + '\n\n'.join(r for r in reports if r)


def took(fleet):
    # Seconds the workspace's last check took; five minutes if never checked.
    return (os.path.exists(fleet['db']) and store.get_meta(fleet['db'], 'took')) or 300


def state_dir(settings):
    # Where the app keeps its log, its lock and which reports went out: the
    # usual place for the usual settings, beside any other settings file, so
    # a demo never writes into the real log or marks a real report sent.
    if os.path.abspath(settings.path) == os.path.abspath(os.path.join(HOME, 'config.json')):
        return DATA
    return os.path.dirname(os.path.abspath(settings.path))


def lock_file(settings):
    return os.path.join(state_dir(settings), 'app.lock')


def running(path):
    # The address of an app already open on these settings, if one answers.
    import urllib.request
    try:
        with open(path) as f:
            info = json.load(f)
        # (no os.kill(pid, 0): on Windows that sends the process a Ctrl-C)
        urllib.request.urlopen(info['url'].replace('/?t=', '/api/app?t='), timeout=3).close()
        return info['url']
    except (OSError, ValueError, KeyError, TypeError):
        return None


def main(cfg_path=None, check=True, window=True, port=None):
    settings = Settings(cfg_path)
    lock = lock_file(settings)
    there = running(lock)
    if there:
        # Already open: another window onto that app, not a second app that
        # checks, alerts and suggests changes from older code.
        if not window:
            print('inboxsitrep is already running: %s' % there, flush=True)
            return
        from .window import open_window
        open_window(there)
        return
    fleets = [web.Fleet(f['db']) for f in settings.cfg['fleets'] if os.path.exists(f['db'])]
    checker = Checker(settings, fleets)
    token = secrets.token_urlsafe(18)
    port = port or free_port()
    tg = telegram.Bot(settings, lambda q: answer(settings, q), say=checker.say)
    bot = slackbot.Bot(lambda: (notify.secret('slack-bot.key'), notify.secret('slack-app.key')),
                       lambda q: answer(settings, q), say=checker.say)
    server = http.server.ThreadingHTTPServer(
        ('127.0.0.1', port), web.handler(fleets, app={'settings': settings, 'checker': checker, 'fleets': fleets,
                                                      'token': token, 'port': port, 'pending': {}, 'bot': bot,
                                                      'telegram': tg}))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    if check:
        checker.start()
        bot.start()
        tg.start()
    url = 'http://127.0.0.1:%d/?t=%s' % (port, token)
    os.makedirs(DATA, exist_ok=True)
    with open(os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as f:
        json.dump({'pid': os.getpid(), 'url': url}, f)

    def unlock():
        try:
            with open(lock) as f:
                if json.load(f).get('pid') == os.getpid():
                    os.remove(lock)
        except (OSError, ValueError):
            pass
    import atexit
    atexit.register(unlock)
    if not window:
        print('inboxsitrep: %s  (Ctrl-C to stop)' % url, flush=True)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return
    from .window import open_window
    if not open_window(url):
        # A browser window we can't wait on: stay up until interrupted.
        print('inboxsitrep is running at %s  (Ctrl-C to stop)' % url, file=sys.stderr)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


DESKTOP = '''[Desktop Entry]
Type=Application
Name=Inbox Sitrep
Comment=Watches your cold email infrastructure
Exec=env PYTHONPATH=%(path)s %(python)s -m inboxsitrep app
Icon=%(icon)s
Terminal=false
Categories=Office;
StartupWMClass=inboxsitrep
'''
# macOS: a login item.
PLIST = '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.inboxsitrep.app</string>
<key>ProgramArguments</key><array><string>%(python)s</string><string>-m</string><string>inboxsitrep</string><string>app</string></array>
<key>EnvironmentVariables</key><dict><key>PYTHONPATH</key><string>%(path)s</string></dict>
<key>RunAtLoad</key><true/>
</dict></plist>
'''
# Windows: a script the Start menu and the Startup folder can run.
CMD = '@set PYTHONPATH=%(path)s\r\n@start "" "%(python)s" -m inboxsitrep app\r\n'


def launcher_python():
    if sys.platform == 'win32':
        quiet = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')      # no console window
        return quiet if os.path.exists(quiet) else sys.executable
    if sys.platform == 'darwin':
        return sys.executable
    # Linux: the system Python has the GTK bindings for the window; a
    # virtualenv usually does not.
    return '/usr/bin/python3' if os.path.exists('/usr/bin/python3') else sys.executable


def entry(python=None, kind=None):
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'icon.svg')
    template = {'darwin': PLIST, 'win32': CMD}.get(kind or sys.platform, DESKTOP)
    return template % {'path': here, 'python': python or launcher_python(), 'icon': icon}


def startup_folder():
    return os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'Microsoft', 'Windows', 'Start Menu',
                        'Programs')


def autostart_path():
    if sys.platform == 'darwin':
        return os.path.expanduser('~/Library/LaunchAgents/com.inboxsitrep.app.plist')
    if sys.platform == 'win32':
        return os.path.join(startup_folder(), 'Startup', 'Inbox Sitrep.cmd')
    return os.path.expanduser('~/.config/autostart/inboxsitrep.desktop')


AUTOSTART = autostart_path()


def install_desktop(python=None):
    # Inbox Sitrep in the applications menu (Linux), ~/Applications (macOS)
    # or the Start menu (Windows).
    if sys.platform == 'darwin':
        app = os.path.expanduser('~/Applications/Inbox Sitrep.app/Contents')
        os.makedirs(os.path.join(app, 'MacOS'), exist_ok=True)
        run = os.path.join(app, 'MacOS', 'Inbox Sitrep')
        with open(run, 'w') as f:
            f.write('#!/bin/sh\nexport PYTHONPATH="%s"\nexec "%s" -m inboxsitrep app\n'
                    % (os.path.dirname(os.path.dirname(os.path.abspath(__file__))), python or launcher_python()))
        os.chmod(run, 0o755)
        with open(os.path.join(app, 'Info.plist'), 'w') as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict>'
                    '<key>CFBundleName</key><string>Inbox Sitrep</string>'
                    '<key>CFBundleExecutable</key><string>Inbox Sitrep</string>'
                    '<key>CFBundleIdentifier</key><string>com.inboxsitrep.app</string>'
                    '<key>CFBundlePackageType</key><string>APPL</string></dict></plist>')
        return os.path.dirname(app)
    if sys.platform == 'win32':
        path = os.path.join(startup_folder(), 'Inbox Sitrep.cmd')
    else:
        path = os.path.join(os.path.expanduser('~/.local/share/applications'), 'inboxsitrep.desktop')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(entry(python))
    return path


def autostart(on):
    # Open the app when you log in: freedesktop autostart on Linux, a login
    # item on macOS, the Startup folder on Windows.
    if not on:
        if os.path.exists(AUTOSTART):
            os.remove(AUTOSTART)
        return
    os.makedirs(os.path.dirname(AUTOSTART), exist_ok=True)
    with open(AUTOSTART, 'w') as f:
        f.write(entry())
