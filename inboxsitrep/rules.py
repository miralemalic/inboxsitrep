# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# The rules Inbox Sitrep judges by, in a file you can edit: rules.ini beside
# the settings file (~/.config/inboxsitrep/rules.ini).  It is written with the
# defaults the first time; change a number, and the next check uses it.  A
# line you delete goes back to its default.

import os
import configparser

from . import verdicts, checks

# section, name, default, (module, constant), scale to the constant, what it means
RULES = [
    ('domains', 'bounce_floor', 500, (verdicts, 'BOUNCE_FLOOR'), 1,
     'Sends before a domain\'s bounces are judged.'),
    ('domains', 'reply_floor', 1000, (verdicts, 'FLOOR'), 1,
     'Sends before replies are judged, for domains and campaigns.'),
    ('domains', 'stop_refused_percent', 4, (verdicts, 'REFUSED'), .01,
     'Share of sends refused because of the sender at which a domain is Refused,\n'
     'or Burned when it also earns little.'),
    ('domains', 'high_bounce_percent', 5, (verdicts, 'HIGH_BOUNCE'), .01,
     'Total bounce rate that says something is wrong with the list or the domain.'),
    ('domains', 'list_and_domain_percent', 2, (verdicts, 'MIXED'), .01,
     'Refused share, inside a high bounce rate, that makes it "list and domain"\n'
     'rather than just a bad list.'),
    ('domains', 'protect', '', None, None,
     'Domains or families (name*) to protect: changing them always asks twice.\n'
     'Separate with commas, e.g.  protect = acme.com, acmemail*'),
    ('campaigns', 'winner_times', 2.0, (verdicts, 'WINNER'), 1,
     'A winner replies this many times the rest of the workspace\'s rate.'),
    ('campaigns', 'loser_times', 0.5, (verdicts, 'LOSER'), 1,
     'A loser replies at this fraction of the rest of the workspace or less.'),
    ('campaigns', 'min_reply_percent', 0, (verdicts, 'MIN_REPLY'), .01,
     'Reply rate under which a campaign is a loser whatever the workspace does.\n'
     '0 turns it off.'),
    ('campaigns', 'bounce_limit_percent', 5, (verdicts, 'CAMPAIGN_BOUNCE'), .01,
     'Bounce rate over which a campaign is flagged, when it sets no limit of its own.'),
    ('reserve', 'cover_days', 30, (checks, 'COVER_DAYS'), 1,
     'Keep enough warmed spare domains to cover this many days of losses.'),
    ('reserve', 'min_reserve_percent', 10, (checks, 'RESERVE_SHARE'), .01,
     'And never fewer spares than this share of the domains sending.'),
    ('volume', 'max_sends_per_mailbox', 50, (checks, 'PER_MAILBOX'), 1,
     'Cold sends a day per mailbox past which a domain is flagged.'),
    ('volume', 'cap_to', 30, (checks, 'CAP'), 1,
     'The daily limit per mailbox it then recommends.'),
    ('leads', 'warn_days', 5, (checks, 'RUNWAY_DAYS'), 1,
     'Warn when a workspace\'s new leads run out sooner than this.'),
    ('leads', 'campaign_dry_days', 2, (checks, 'DRY_DAYS'), 1,
     'And when a single campaign runs dry sooner than this.'),
]
PROTECT = []


def template():
    out = ['# Inbox Sitrep rules.  Change a number and save; the next check uses it.',
           '# Delete a line to go back to its default.', '']
    section = None
    for sec, name, default, _, _, what in RULES:
        if sec != section:
            out += ['[%s]' % sec, '']
            section = sec
        out += ['# ' + line for line in what.split('\n')]
        out += ['%s = %s' % (name, default), '']
    return '\n'.join(out)


def path_for(settings_path):
    return os.path.join(os.path.dirname(os.path.abspath(settings_path)), 'rules.ini')


def load(path):
    # Defaults first, then whatever the file says.  Returns what it could
    # not read, so the app can say so instead of failing.
    if not os.path.exists(path):
        try:
            with open(path, 'w') as f:
                f.write(template())
        except OSError:
            pass
    ini = configparser.ConfigParser(inline_comment_prefixes=('#', ';'))
    problems = []
    try:
        ini.read(path)
    except configparser.Error as e:
        problems.append(str(e).split('\n')[0])
    for sec, name, default, where, scale, _ in RULES:
        raw = ini.get(sec, name, fallback=None)
        if where is None:
            PROTECT[:] = [x.strip().lower() for x in (raw or '').split(',') if x.strip()]
            continue
        value = default
        if raw not in (None, ''):
            try:
                value = float(raw)
                if value < 0:
                    raise ValueError
            except ValueError:
                problems.append('%s %s = %r is not a number; using %s' % (sec, name, raw, default))
                value = default
        module, const = where
        n = value * scale
        setattr(module, const, int(n) if scale == 1 and float(n).is_integer() and isinstance(default, int) else n)
    return problems


def protected(domain, family=None):
    for p in PROTECT:
        if p.endswith('*') and (family == p[:-1] or domain.startswith(p[:-1])):
            return True
        if domain == p:
            return True
    return False
