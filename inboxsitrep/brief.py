# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# The morning brief: what needs a decision, what is being watched, what is
# over.  Short enough to paste into a chat channel.

import collections

from .data import SENT, REPLIES, POSITIVES, BOUNCES, SENDER, rate
from .incidents import gap
from .detect import plural


DECIDE = ('burned', 'cascade', 'listed', 'quiet_spam', 'auth', 'apple',
          'bad_list', 'gmail_gap', 'tenant_block', 'ms_block', 'complaints',
          'receiver', 'content', 'link_listed', 'mailboxes', 'runway',
          'bounce_guard', 'dns', 'throttle', 'settings', 'volume')
# How bad, for sorting and for the bar beside each incident: 3 stops the
# fleet or most of it, 2 costs domains or leads now, 1 is being watched.
SEVERITY = {'tenant_block': 3, 'runway': 3, 'link_listed': 3, 'cascade': 3,
            'bounce_guard': 3, 'auth': 3, 'apple': 2, 'listed': 2, 'burned': 2,
            'ms_block': 2, 'receiver': 2, 'content': 2, 'complaints': 2,
            'quiet_spam': 2, 'gmail_gap': 2, 'mailboxes': 2, 'dns': 2, 'bad_list': 2,
            'throttle': 2, 'volume': 2}


def severity(inc):
    n = SEVERITY.get(inc['kind'], 1)
    if inc['kind'] == 'bounce_guard' and not inc['evidence'].get('paused'):
        n = 1
    return n if inc['status'] == 'open' else 0


NAMES = {'google': 'Gmail', 'microsoft': 'Microsoft', 'apple': 'Apple',
         'other': 'others'}


def listed_domains(data, found):
    # Every domain a blocklist names now: from DNS, or from an open incident.
    # One definition, so every screen says the same number.
    here = set(data.domains())
    last = data.days[-1]
    out = {d for d in here if data.listed_on(d, last)}
    for i in found:
        if i['status'] == 'open' and i['kind'] == 'listed':
            out.update(i['evidence'].get('domains') or [i['target']])
    return out & here


def step_words(op, provider, targets, limit=None, warmup=None):
    # One plan step as a person would say it.
    n = len(targets)
    doms = (targets[0] if n == 1 else '%d domains' % n) if n else 'them'
    who = NAMES.get(provider, provider or '')
    many = lambda noun: '%d %s' % (n, noun) if n != 1 else targets[0]
    return {
        'pause': 'pause %s' % doms + (' and their warmup' if warmup == 'off' else ''),
        'delist': 'request removal from the blocklist for %s and keep %s sending' % (doms, 'it' if n == 1 else 'them'),
        'set_limit': 'send at most %s a day from %s' % (limit, doms)
                     + (' and pause their warmup' if warmup == 'off' else ''),
        'warmup_off': 'pause warmup on %s' % doms,
        'unpause': 'resume %s' % doms,
        'resume': 'resume %s on %s' % (who, doms) if who else 'resume %s' % doms,
        'stop': 'stop sending to %s from %s' % (who, doms) if who else 'stop sending from %s' % doms,
        'slow': 'send half as much to %s from %s' % (who or 'them', doms),
        'suppress_apple': 'stop mailing iCloud addresses',
        'fix_dns': 'fix DNS on %s' % doms,
        'hold_receiver': 'hold mail to %s recipients' % (targets[0] if n else 'its'),
        'review_copy': 'review the copy',
        'remove_link': 'take %s out of the copy' % (targets[0] if n else 'the link'),
        'reconnect': 'reconnect the mailboxes on %s' % doms,
        'scrub': 'verify the leads sent from %s' % doms,
        'check': 'find out why %s stopped' % doms,
        'load_leads': 'load new leads',
        'investigate': 'find what bounced in %s' % (many('campaigns') if n else 'the paused campaigns'),
        'route': 'send Gmail leads from the %d healthiest domains only' % n,
        'change_settings': 'change the settings of %s' % (many('campaigns') if n else 'the campaigns'),
    }.get(op, '%s %s' % (op.replace('_', ' '), doms if n else '')).strip()


def planned_by(found):
    # Domain -> the open incident whose plan first pauses or stops it.  An
    # incident's own plan holds only what no earlier one covered, so this is
    # how a reader finds where the rest went.
    out = {}
    for i in found:
        if i['status'] == 'open':
            for c in i['plan']:
                if c['op'] in ('pause', 'stop'):
                    for t in c['targets']:
                        out.setdefault(t, i.get('id'))
    return out


def concerned(inc):
    # The domains an incident is about.
    ev = inc['evidence']
    for k in ('domains', 'members', 'dead'):
        if isinstance(ev.get(k), list) and ev[k]:
            return ev[k]
    return [inc['target']] if inc['scope'] == 'domain' else []


def plan_line(inc, planned=None):
    ev = inc['evidence']
    if inc['kind'] == 'cascade' and ev.get('members'):
        # The whole family, the dead and the next in line, whatever earlier
        # incidents planned for some of them.
        return '%s (%d refusing, %d next)' % (step_words('stop', 'google', ev['members']),
                                             len(ev.get('dead') or ()), len(ev.get('at_risk') or ()))
    gone = ev.get('gone')
    if not inc['plan'] and gone:
        return ('the %d affected domains are already out of this workspace; '
                'find where their mailboxes went' % len(gone))
    # A plan that grew over several days is still one plan: "pause 19
    # domains", not "pause 3; pause 11; pause 5".
    merged = {}
    for c in inc['plan']:
        key = (c['op'], c.get('provider'))
        merged.setdefault(key, []).extend(t for t in c['targets'] if t not in merged.get(key, ()))
    parts = []
    for (op, prov), targets in merged.items():
        part = step_words(op, prov, targets)
        # A listing plan holds every domain listed over the window; say how
        # many are not listed any more, so the count matches the headline.
        now = inc['evidence'].get('domains')
        if inc['kind'] == 'listed' and op == 'pause' and isinstance(now, list):
            off = len(set(targets) - set(now))
            if off:
                part += ' (%d of them no longer listed)' % off
        parts.append(part)
    note = covered(inc, planned)
    if note:
        parts.append(note)
    return '; '.join(parts) or 'already planned'


def covered(inc, planned):
    # What an earlier incident's plan already covers, and which one.  Only
    # for plans about the domains themselves: a receiver hold or a copy
    # review is not "the rest" of anyone's pause.
    mine = {t for c in inc['plan'] for t in c['targets']}
    if not planned or not all(c['op'] in ('pause', 'stop') for c in inc['plan']):
        return ''
    elsewhere = {d: planned[d] for d in concerned(inc)
                 if d not in mine and planned.get(d) not in (None, inc.get('id'))}
    if not elsewhere:
        return ''
    where = ', '.join('#%s' % n for n in sorted(set(elsewhere.values()))[:4])
    if mine:
        return '%d more already in the plan for %s' % (len(elsewhere), where)
    return 'already in the plan for %s' % where


def fold_listings(group):
    # A family on a blocklist is one story: its domains' own listings, and
    # Gmail refusing the ones that are listed, go under the family's line.
    by_id = {}
    for fam in (i for i in group if i['kind'] == 'listed' and i['scope'] == 'family'):
        doms = set(fam['evidence'].get('domains') or ())
        members = set(fam['evidence'].get('members') or ())
        under = [i for i in group if i is not fam and i['scope'] == 'domain' and (
            (i['kind'] == 'listed' and i['target'] in members) or
            (i['kind'] in ('burned', 'rejecting') and i['target'] in doms and 'listed' in i['title']))]
        if under:
            by_id[id(fam)] = under
    return by_id


def brief(data, found, name=None, fresh=(), today=None):
    # fresh: ids of incidents new since the last report, marked "New:".
    # today: the day of the check, to say when nothing has been sent.
    day = data.days[-1]
    week = data.days[-7:]
    sent = sum(data.fleet(p, week)[SENT] for p in NAMES)
    positives = sum(data.fleet(p, week)[POSITIVES] for p in NAMES)
    by = ['%s %.1f%% replied, %.1f%% refused'
          % (NAMES[p], 100 * rate(t[REPLIES], t[SENT] - t[BOUNCES]), 100 * rate(t[SENDER], t[SENT]))
          for p in ('google', 'microsoft', 'other') for t in [data.fleet(p, week)] if t[SENT]]
    out = ['SITREP: %s, %s' % (name, day) if name else 'SITREP, %s' % day,
           '%d domains.  Last 7 days: %s positive replies (%.1f per 1,000 sent).%s'
           % (len(data.domains()), format(positives, ','), 1000 * positives / max(1, sent),
              '  By recipient: %s.' % '; '.join(by) if by else '')]
    if today:
        import datetime
        quiet = (today - datetime.date.fromisoformat(day)).days
        if quiet > 3:
            out.insert(1, 'Nothing has been sent since %s, %d days ago: the campaigns or the mailboxes have '
                          'stopped.' % (day, quiet))
    open_ = [i for i in found if i['status'] == 'open']
    # Gmail slowing a domain is old news once it refuses it outright.
    refused = {i['target'] for i in open_ if i['kind'] in ('rejecting', 'burned')}
    open_ = [i for i in open_ if not (i['kind'] == 'throttle' and i['target'] in refused)]
    planned = planned_by(found)
    decide = [i for i in open_ if i['kind'] in DECIDE]
    watch = [i for i in open_ if i['kind'] not in DECIDE]
    over = [i for i in found if i['status'] != 'open' and gap(i['updated'], day) <= 2]
    for title, group, plans in (('Needs a decision', decide, True),
                                ('Watch list', watch, True),
                                ('Closed', over, False)):
        if not group:
            continue
        # Forty blocklisted domains are one line, and so are four DNS
        # problems: the reader needs the size of it, then the worst.  The
        # app's incident list folds by the same rules (ui/src/group.js), so
        # both give the same count.
        listed = [i for i in group if i['kind'] == 'listed']
        dns = [i for i in group if i['kind'] == 'dns']
        lines = []
        if len(listed) > 2:
            fams = sorted((i for i in listed if i['scope'] == 'family'),
                          key=lambda i: -len(i['evidence'].get('domains') or ()))
            n = len(listed_domains(data, found))
            worst = ', '.join('%s* %d/%d' % (i['target'], len(i['evidence']['domains']),
                                             len(i['evidence'].get('members') or ()))
                              for i in fams[:4])
            failing = {d for i in listed for d in i['evidence'].get('failing') or ()}
            lines.append('- %d domains on blocklists%s%s.  Plan: request removal and keep them sending%s'
                         % (n, ', across %d %s' % (len(fams), 'family' if len(fams) == 1 else 'families')
                            if fams else '', '; worst: ' + worst if worst else '',
                            '; pause the %d that receivers refuse' % len(failing) if failing else ''))
        else:
            listed = []
        if len(dns) > 3:
            lines.append('- DNS problems: %s.  Plan: fix the records'
                         % '; '.join(i['title'][0].lower() + i['title'][1:] for i in dns))
        else:
            dns = []
        under = fold_listings(group) if not listed else {}
        folded = [i for items in under.values() for i in items]
        # Domains Gmail has stopped taking mail from: one line past three.
        from .detect import families
        fams = families(data.domains())
        dead = [i for i in group if i['kind'] == 'burned' and i['scope'] == 'domain']
        # A family going down one domain at a time is the cascade's story;
        # its dead domains are not told again on their own.
        cascade = {d for i in group if i['kind'] == 'cascade' for d in i['evidence'].get('dead') or ()}
        folded += [i for i in dead if i['target'] in cascade]
        dead = [i for i in dead if i['target'] not in cascade]
        if len(dead) > 3:
            dead = [i for i in dead if i not in folded]
            folded += dead
            if dead:
                # "7 growthmail*, 2 leadpilot*, replyfirstonline.com"
                per = collections.Counter(fams.get(i['target']) for i in dead)
                names = ['%d %s*' % (n, fam) for fam, n in per.most_common() if fam and n > 1]
                names += [i['target'] for i in dead if per[fams.get(i['target'])] == 1 or not fams.get(i['target'])]
                which = ', '.join(names[:4])
                more = len(names) - 4
                lines.append('- Gmail has stopped taking mail from %s (%s%s; %s).  Plan: stop sending '
                             'to Gmail from them; keep 10 probe sends a day'
                             % (plural(len(dead), 'domain'), which, ' and %d more' % more if more > 0 else '',
                                ', '.join('#%s' % i['id'] for i in dead if i.get('id')) or 'see incidents'))
        # A listed family whose Gmail replies are falling is one story too.
        spam = {i['target']: i for i in group if i['kind'] == 'quiet_spam'}
        both = {i['target'] for i in group if i['kind'] == 'listed' and i['scope'] == 'family'} & set(spam)
        folded += [spam[t] for t in both]
        rest = [i for i in group if i not in listed and i not in dns and i not in folded]
        out += ['', '%s (%d)' % (title, len(rest) + len(lines))]
        order = {'tenant_block': 0, 'runway': 1, 'bounce_guard': 2, 'link_listed': 3,
                 'cascade': 4, 'listed': 5, 'burned': 6, 'receiver': 7, 'mailboxes': 8}
        ranked = [((order['listed'] if 'blocklists' in l else order['burned'] if 'stopped taking' in l
                    else 20, ''), l) for l in lines]
        for i in rest:
            # The incident number, so a reader (or the analyst) can open it.
            line = '- %s%s%s' % ('[#%s] ' % i['id'] if i.get('id') else '',
                                 'New: ' if i.get('id') in fresh else '', i['title'])
            if id(i) in under:
                also = under[id(i)]
                gmail = [x for x in also if x['kind'] != 'listed']
                if gmail:
                    line += '; Gmail refuses %d of them' % len(gmail)
                cited = ', '.join('#%s' % x['id'] for x in also if x.get('id'))
                if cited:
                    line += ' (also %s)' % cited
                if plans:
                    doms = {t for x in [i] + also for c in x['plan'] if c['op'] == 'pause'
                            for t in c['targets']}
                    doms |= {x['target'] for x in also}
                    line += '.  Plan: %s' % step_words('pause', None, sorted(doms))
                ranked.append(((order.get(i['kind'], 9), i['opened']), line))
                continue
            if plans and i['kind'] == 'listed' and i['target'] in both:
                q = spam[i['target']]
                line += '; Gmail replies there fell from %.2f%% to %.2f%%%s.  Plan: %s; until then the ' \
                        'family sends to Microsoft and others only' % (
                            100 * q['evidence'].get('before', 0), 100 * q['evidence'].get('after', 0),
                            ' (#%s)' % q['id'] if q.get('id') else '',
                            plan_line(i, planned).replace(' and keep them sending', ''))
            elif plans:
                line += '.  Plan: %s' % plan_line(i, planned)
            elif i.get('ending'):
                line += ' (%s)' % i['ending']
            ranked.append(((order.get(i['kind'], 9), i['opened']), line))
        out += [l for _, l in sorted(ranked, key=lambda r: r[0])]
    if not (decide or watch or over):
        out += ['', 'Nothing needs you today.']
    return '\n'.join(out)
