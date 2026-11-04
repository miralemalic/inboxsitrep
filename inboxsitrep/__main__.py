#!/usr/bin/env python3
# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# inboxsitrep sim --db demo.db              a made-up fleet with real-world failures
# inboxsitrep demo --dir try                three made-up workspaces, and the app on them
# inboxsitrep import export.csv --db f.db   any sequencer's lead export
# inboxsitrep collect --db fleet.db         read a PlusVibe workspace (read-only)
# inboxsitrep dns --db fleet.db             SPF, DKIM, DMARC, MX and SURBL for every domain
# inboxsitrep check --db fleet.db           find incidents, day by day
# inboxsitrep report --db fleet.db [--ai]   write them up
# inboxsitrep brief --db fleet.db           what needs a decision today
# inboxsitrep serve --db fleet.db           the dashboard
# inboxsitrep watch                         collect, check and report every fleet in the config
# inboxsitrep live                          bounces as they happen, alerts within minutes
# inboxsitrep schedule                      run the watch twice a day, live mode always
# inboxsitrep ask "why did growthmail die?"  the analyst answers, from the tools
# inboxsitrep mcp --db fleet.db             the tools, for Claude Code, Codex or Cursor
# inboxsitrep app                           the desktop app
# inboxsitrep score                         how well all this does on 20 fleets

import os
import sys
import argparse
import datetime

from . import __version__, sim, score, incidents, report
from . import data as store


def need_fleet(path):
    # sqlite would happily create an empty file and fail later; say it now.
    if not os.path.exists(path):
        sys.exit('inboxsitrep: no fleet database at %s.  Make one with `inboxsitrep '
                 'sim`, `inboxsitrep import` or `inboxsitrep collect`.' % path)
    if not store.load(path).days:
        sys.exit('inboxsitrep: %s has no sending data in it.' % path)


def cmd_sim(args):
    fleet = sim.SCENARIOS[args.scenario](sim.Fleet(args.seed))
    for _ in range(args.days):
        fleet.step()
    if args.export:
        print('%d leads -> %s' % (sim.export(fleet, args.export), args.export))
    sim.add_campaigns(fleet, args.seed)
    store.save(fleet.data, args.db, meta={
        'source': 'simulated', 'seed': args.seed, 'scenario': args.scenario,
        'days': args.days, 'truth': fleet.truth})
    found = incidents.track(fleet.data)
    incidents.save(found, args.db)
    print('%d domains, %d days -> %s; %d incidents'
          % (len(fleet.doms), args.days, args.db, len(found)))


def cmd_demo(args):
    # Three workspaces with their own trouble, and a settings file to open
    # the app on, apart from your own.
    import json
    out = os.path.abspath(os.path.expanduser(args.dir))
    os.makedirs(out, exist_ok=True)
    fleets = []
    for name, fleet in sim.demo():
        path = os.path.join(out, name.lower().replace(' ', '-') + '.db')
        if os.path.exists(path):
            os.remove(path)         # a fresh demo: no changes or reports left from the last one
        store.save(fleet.data, path, meta={'source': 'simulated', 'workspace': name, 'truth': fleet.truth})
        incidents.save(incidents.track(fleet.data), path)
        fleets.append({'name': name, 'sequencer': 'sim', 'db': path})
        print('%-20s %d domains, %d campaigns -> %s' % (name, len(fleet.doms), len(fleet.data.campaigns), path))
    config = os.path.join(out, 'config.json')
    with open(config, 'w') as f:
        # No analyst: a check would otherwise spend Claude usage on made-up workspaces.
        json.dump({'fleets': fleets, 'analyst': False, 'notify': {'desktop': False, 'webhook': ''}}, f, indent=1)
    print('\ninboxsitrep app --config %s' % config)


def cmd_import(args):
    from . import csvimport
    if not os.path.exists(args.file):
        sys.exit('inboxsitrep: no file at %s' % args.file)
    try:
        data, cols = csvimport.read(args.file, lookup=not args.no_mx)
    except ValueError as e:
        sys.exit('inboxsitrep: %s.  It looks for columns named like: sender '
                 '(email_acc_name, sender email, from email), recipient (email, '
                 'lead email), status, and send date (last_sent_at, last '
                 'contacted).' % e)
    store.save(data, args.db, meta={'source': 'import', 'file': os.path.basename(args.file)})
    found = incidents.track(data)
    incidents.save(found, args.db)
    print('%d domains, %d days -> %s; %d incidents  (columns: %s)' % (
        len(data.domains()), len(data.days), args.db, len(found),
        ', '.join('%s=%s' % kv for kv in sorted(cols.items()))))


def cmd_collect(args):
    say = lambda m: print('  ' + m)
    if args.sequencer == 'plusvibe':
        from . import plusvibe
        key = os.environ.get('PLUSVIBE_KEY')
        if not key or not args.workspace:
            sys.exit('set PLUSVIBE_KEY and pass --workspace (the workspace id)')
        data = plusvibe.collect(plusvibe.Client(key, args.workspace, verbose=args.verbose),
                                days=args.days, say=say)
    elif args.sequencer == 'instantly':
        from . import instantly
        key = os.environ.get('INSTANTLY_KEY') or sys.exit('set INSTANTLY_KEY')
        data = instantly.collect(instantly.Client(key), days=args.days, say=say)
    else:
        from . import smartlead
        key = os.environ.get('SMARTLEAD_KEY') or sys.exit('set SMARTLEAD_KEY')
        old = store.load(args.db) if os.path.exists(args.db) else None
        data = smartlead.mailboxes(smartlead.Client(key), old, say=say)
    if os.path.exists(args.db):
        store.carry(store.load(args.db), data)
    store.save(data, args.db, meta={
        'source': args.sequencer, 'workspace': args.workspace, 'days': args.days,
        'collected': datetime.datetime.now().isoformat(timespec='seconds')})
    print('saved %s' % args.db)


def cmd_dns(args):
    from . import dns
    need_fleet(args.db)
    data = store.load(args.db)
    doms = args.domain or None
    dns.check_all(data, doms, say=lambda m: print('  ' + m))
    meta = {k: store.get_meta(args.db, k) for k in ('source', 'workspace', 'truth')}
    store.save(data, args.db, meta={k: v for k, v in meta.items() if v is not None})
    found = incidents.track(store.reclassify(data))
    incidents.save(found, args.db)
    for i in found:
        if i['kind'] in ('dns', 'listed') and i['status'] == 'open':
            print('%-8s %s' % (i['kind'], i['title']))


def cmd_check(args):
    need_fleet(args.db)
    data = store.reclassify(store.load(args.db))
    found = incidents.track(data)
    incidents.save(found, args.db)
    for i in found:
        print('%s  %-10s %-8s %s' % (i['opened'], i['kind'], i['status'], i['title']))
    print('%d incidents over %d days' % (len(found), len(data.days)))


def cmd_report(args):
    need_fleet(args.db)
    data = store.load(args.db)
    found = incidents.load(args.db)
    if args.id:
        found = [i for i in found if i['id'] == args.id]
    for i in found:
        text = (report.ai(i, data) if args.ai else None) or report.plain(i, data)
        print(text)
        print()


def cmd_brief(args):
    from . import brief, agent
    need_fleet(args.db)
    data = store.reclassify(store.load(args.db))
    # The incidents saved by the last check carry their numbers; without
    # any, find them now.
    found = incidents.load(args.db) or incidents.track(data)
    name = store.get_meta(args.db, 'workspace')
    if args.ai:
        text, how = agent.briefing(data, found, name, model=args.model)
        if how != 'analyst':
            print('(analyst unavailable: %s)' % how, file=sys.stderr)
        print(text)
    else:
        print(brief.brief(data, found, name))


def cmd_ask(args):
    from . import agent
    for db in args.db:
        need_fleet(db)
    text, err = agent.ask(args.question, args.db, model=args.model)
    if err:
        sys.exit('inboxsitrep: %s' % err)
    print(text)


def cmd_mcp(args):
    from . import mcp, app
    paths = args.db
    if not paths:
        # The desktop app's workspaces, so `inboxsitrep mcp` needs no setup.
        paths = [f['db'] for f in app.Settings(args.config).cfg['fleets'] if os.path.exists(f['db'])]
    # With no fleets the server still starts; its tools say what to do.
    mcp.serve(paths)


def cmd_serve(args):
    from . import web, watch
    dbs = list(args.db or [])
    if args.config:
        dbs += [f['db'] for f in watch.load(args.config).get('fleets', [])
                if os.path.exists(f['db'])]
    if not dbs:
        sys.exit('inboxsitrep: give --db (one or more) or --config')
    for db in dbs:
        need_fleet(db)
    try:
        web.serve(dbs, args.port, secret=args.hook_secret, host=args.host)
    except OSError as e:
        sys.exit('inboxsitrep: port %d is busy (%s).  Try --port %d.'
                 % (args.port, e.strerror, args.port + 1))


def cmd_watch(args):
    from . import watch
    watch.run(watch.load(args.config), only=args.fleet)


def cmd_live(args):
    from . import watch
    try:
        watch.live_loop(watch.load(args.config), every=args.every, once=args.once)
    except KeyboardInterrupt:
        pass


def cmd_schedule(args):
    from . import watch
    watch.load(args.config)
    units = watch.schedule(args.config, times=args.times.split(','), every=args.every,
                           port=args.port)
    for u in units:
        print('wrote ' + u)
    cmds = [['systemctl', '--user', 'daemon-reload'],
            ['systemctl', '--user', 'enable', '--now', 'inboxsitrep-watch.timer']]
    if args.live:
        cmds.append(['systemctl', '--user', 'enable', '--now', 'inboxsitrep-live.service'])
    if args.dashboard:
        cmds.append(['systemctl', '--user', 'enable', '--now', 'inboxsitrep-dashboard.service'])
    if not args.enable:
        print('\nto turn it on:')
        for c in cmds:
            print('  ' + ' '.join(c))
        return
    import subprocess
    for c in cmds:
        subprocess.run(c, check=True)
    print('on.  `systemctl --user list-timers inboxsitrep*` shows the next run.')


def cmd_app(args):
    from . import app
    if args.install:
        print('added to your applications: %s' % app.install_desktop())
        return
    app.main(args.config, check=not args.no_check, window=not args.no_window, port=args.port)


def cmd_why(args):
    from . import rootcause, dns
    need_fleet(args.db)
    data = store.reclassify(store.load(args.db))
    if args.refresh or not any('registrar' in r or 'ns' in r for r in data.dns.values()):
        print('checking DNS and registrations (a few minutes for a big fleet)')
        dns.check_all(data, say=lambda m: print('  ' + m))
        meta = {k: store.get_meta(args.db, k) for k in ('source', 'workspace', 'collected')}
        store.save(data, args.db, meta={k: v for k, v in meta.items() if v is not None})
    print(rootcause.report(data, incidents.load(args.db) or incidents.track(data)))


def cmd_evalset(args):
    from . import evalset
    plugin = args.plugin or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'plugin')
    cfg = evalset.build(plugin=plugin)
    print('\nfleets and settings: %s\ncases: %s/evals\nrun:\n  INBOXSITREP_CONFIG=%s claude plugin eval %s '
          '--mocks off --allow-tools %s --runs 1' % (cfg, plugin, cfg, plugin, ' '.join(evalset.TOOLS)))


def cmd_score(args):
    score.scorecard(seeds=range(1, args.fleets + 1), days=args.days)


def main(argv=None):
    p = argparse.ArgumentParser(prog='inboxsitrep', description='Watch a cold '
                                'email sending fleet like a deliverability '
                                'engineer would.')
    p.add_argument('--version', action='version', version=__version__)
    sub = p.add_subparsers(dest='cmd', required=True)

    s = sub.add_parser('sim', help='simulate a fleet')
    s.add_argument('--db', default='demo.db')
    s.add_argument('--seed', type=int, default=1)
    s.add_argument('--days', type=int, default=45)
    s.add_argument('--scenario', default='standard', choices=sorted(sim.SCENARIOS))
    s.add_argument('--export', metavar='CSV', help='also write it as a lead export')
    s.set_defaults(func=cmd_sim)

    dm = sub.add_parser('demo', help='three simulated workspaces to try the app on')
    dm.add_argument('--dir', default='inboxsitrep-demo')
    dm.set_defaults(func=cmd_demo)

    i = sub.add_parser('import', help='read a lead export from any sequencer')
    i.add_argument('file')
    i.add_argument('--db', required=True)
    i.add_argument('--no-mx', action='store_true', help='don\'t look up who '
                   'hosts each recipient (when the file has no provider column)')
    i.set_defaults(func=cmd_import)

    c = sub.add_parser('collect', help='read a workspace (read-only)')
    c.add_argument('--db', required=True)
    c.add_argument('--sequencer', default='plusvibe', choices=('plusvibe', 'instantly', 'smartlead'),
                   help='key from PLUSVIBE_KEY, INSTANTLY_KEY or SMARTLEAD_KEY')
    c.add_argument('--workspace', help='workspace id (PlusVibe)')
    c.add_argument('--days', type=int, default=21)
    c.add_argument('-v', '--verbose', action='store_true')
    c.set_defaults(func=cmd_collect)

    d = sub.add_parser('dns', help='check SPF, DKIM, DMARC, MX and SURBL (DNS over HTTPS)')
    d.add_argument('--db', required=True)
    d.add_argument('domain', nargs='*', help='only these domains')
    d.set_defaults(func=cmd_dns)

    k = sub.add_parser('check', help='find incidents')
    k.add_argument('--db', required=True)
    k.set_defaults(func=cmd_check)

    r = sub.add_parser('report', help='write incidents up')
    r.add_argument('--db', required=True)
    r.add_argument('--id', type=int)
    r.add_argument('--ai', action='store_true', help='have Claude rewrite them '
                   'and read the bounce text (uses the claude command)')
    r.set_defaults(func=cmd_report)

    b = sub.add_parser('brief', help='the morning brief, short enough to paste')
    b.add_argument('--db', required=True)
    b.add_argument('--ai', action='store_true', help='the analyst writes it (your claude login)')
    b.add_argument('--model', help='e.g. sonnet, opus, haiku')
    b.set_defaults(func=cmd_brief)

    a = sub.add_parser('ask', help='ask the analyst about a fleet')
    a.add_argument('question')
    a.add_argument('--db', action='append', required=True)
    a.add_argument('--model')
    a.set_defaults(func=cmd_ask)

    m = sub.add_parser('mcp', help='the tools over stdio, for Claude Code, Codex, Cursor')
    m.add_argument('--db', action='append', help='a fleet database; default: every workspace in the app')
    m.add_argument('--config', help='default ~/.config/inboxsitrep/config.json')
    m.set_defaults(func=cmd_mcp)

    v = sub.add_parser('serve', help='the dashboard')
    v.add_argument('--db', action='append', help='a fleet database; give several to switch between them')
    v.add_argument('--config', help='serve every fleet in a watch config')
    v.add_argument('--port', type=int, default=8008)
    v.add_argument('--host', default='127.0.0.1')
    v.add_argument('--hook-secret', help='accept sequencer webhooks on POST /hook/<secret>')
    v.set_defaults(func=cmd_serve)

    w = sub.add_parser('watch', help='collect, check and report every fleet in the config')
    w.add_argument('--config', help='default ~/.config/inboxsitrep/config.json')
    w.add_argument('--fleet', help='only this one')
    w.set_defaults(func=cmd_watch)

    lv = sub.add_parser('live', help='poll for bounces and replies, alert within minutes')
    lv.add_argument('--config')
    lv.add_argument('--every', type=int, default=15, help='minutes between looks')
    lv.add_argument('--once', action='store_true')
    lv.set_defaults(func=cmd_live)

    sh = sub.add_parser('schedule', help='systemd timer for the watch; live mode and dashboard')
    sh.add_argument('--config')
    sh.add_argument('--times', default='07:13,15:13', help='when the watch runs (local time)')
    sh.add_argument('--every', type=int, default=15)
    sh.add_argument('--port', type=int, default=8008)
    sh.add_argument('--live', action='store_true', help='also run live mode')
    sh.add_argument('--dashboard', action='store_true', help='also keep the dashboard up')
    sh.add_argument('--enable', action='store_true', help='turn it on now')
    sh.set_defaults(func=cmd_schedule)

    ap = sub.add_parser('app', help='the desktop app: a window that stands watch while open')
    ap.add_argument('--config', help='default ~/.config/inboxsitrep/config.json')
    ap.add_argument('--no-check', action='store_true', help='open without checking anything')
    ap.add_argument('--no-window', action='store_true', help='serve only; open the printed link')
    ap.add_argument('--port', type=int)
    ap.add_argument('--install', action='store_true', help='add it to your applications menu')
    ap.set_defaults(func=cmd_app)

    wl = sub.add_parser('why-listed', help='what the blocklisted domains have in common')
    wl.add_argument('--db', required=True)
    wl.add_argument('--refresh', action='store_true', help='run the DNS and registration check first')
    wl.set_defaults(func=cmd_why)

    ev = sub.add_parser('evalset', help="build the analyst's exam: planted-problem fleets and graded cases")
    ev.add_argument('--plugin', help='the Claude Code plugin folder to write cases into')
    ev.set_defaults(func=cmd_evalset)

    sc = sub.add_parser('score', help='score against simulated fleets')
    sc.add_argument('--fleets', type=int, default=20)
    sc.add_argument('--days', type=int, default=score.HORIZON)
    sc.set_defaults(func=cmd_score)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == '__main__':
    sys.exit(main())
