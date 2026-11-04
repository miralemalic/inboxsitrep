# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# Turn what the detectors saw into changes a person can read before saying
# yes: which domains stop sending to whom, for how long, and why.  Every
# change has an opposite, so anything approved can be undone.
#
# The rules of thumb behind the plans:
#   - Stop Gmail, not the domain.  A domain Gmail refuses still delivers to
#     Microsoft; on the real fleet Microsoft showed zero refusals even from
#     domains Gmail had killed.
#   - Keep a trickle of Gmail sends on a stopped domain, so we see when it
#     heals instead of guessing.  Episodes heal in days.
#   - Fix what is broken where it is broken: a bad list is cleaned, not the
#     domains that sent to it; missing DNS records are added, not the domain
#     retired.

OPPOSITE = {'stop': 'resume', 'resume': 'stop',
            'pause': 'unpause', 'unpause': 'pause',
            'slow': 'restore', 'restore': 'slow'}


def change(op, targets, why, provider=None):
    c = {'op': op, 'targets': sorted(targets), 'why': why}
    if provider:
        c['provider'] = provider
    return c


def undo(changes):
    out = []
    for c in reversed(changes):
        if c['op'] in OPPOSITE:
            out.append(dict(c, op=OPPOSITE[c['op']], why='undo: ' + c['why']))
    return out


class Operator:
    # Holds what has been changed and why, so later days can be judged
    # against it (resume a domain stopped for an episode; unpause one whose
    # DNS was fixed).  `state` mirrors the sequencer: who is stopped, who is
    # paused.

    def __init__(self):
        self.off = {'google': {}, 'microsoft': {}}   # provider -> domain -> reason
        self.google_off = self.off['google']
        self.slowed = {}
        self.paused = {}
        self.delisted = set()
        self.said = set()            # one-off advice already given
        self.apple_off = False
        self.scrubbed = set()

    def plan(self, f):
        k, target, n = f['kind'], f['target'], f['numbers']
        if k in ('rejecting', 'burned'):
            todo = [target] if target not in self.google_off else []
            why = ('Gmail refuses %.0f%%; keep %s probe sends a day and '
                   'resume when it takes mail again' % (n['rate'] * 100, 10)
                   if k == 'rejecting' else
                   'dead at Gmail; keep sending to Microsoft and others')
            return [change('stop', todo, why, 'google')] if todo else []
        if k == 'cleared':
            if self.google_off.get(target) in ('rejecting', 'burned'):
                return [change('resume', [target],
                               'Gmail takes its mail again', 'google')]
            return []
        if k in ('cascade', 'quiet_spam'):
            todo = [d for d in n['members'] if d not in self.google_off]
            why = ('Gmail is refusing this family one domain after another; protect the rest'
                   if k == 'cascade' else
                   'Gmail files this mail as spam; send their Gmail leads '
                   'from healthier domains')
            return [change('stop', todo, why, 'google')] if todo else []
        if k == 'listed':
            # Pause what receivers refuse; get the rest off the list and
            # keep it sending.
            doms = n.get('domains') or [target]
            bad = set(n['failing']) if 'failing' in n else set(doms)
            out = []
            todo = [d for d in doms if d in bad and d not in self.paused]
            if todo:
                out.append(change('pause', todo, 'blocklisted and receivers refuse it'))
            rest = [d for d in doms if d not in bad and d not in self.delisted]
            if rest:
                self.delisted.update(rest)
                out.append(change('delist', rest, 'on a blocklist but still delivering: request removal '
                                  'and keep it sending'))
            return out
        if k == 'tenant_block':
            todo = [d for d in n['domains'] if d not in self.paused]
            if not todo:
                # Already out of the workspace, or already paused.
                return []
            return [change('pause', todo, 'Microsoft blocks this tenant\'s '
                           'outbound mail: every send fails at the source.  '
                           'Check Restricted entities in Microsoft 365 Defender '
                           'and fix the cause before asking for release')]
        if k == 'auth':
            out = []
            if ('fix_dns', target) not in self.said:
                self.said.add(('fix_dns', target))
                out.append(change('fix_dns', [target], 'add the missing %s record'
                                  % (n.get('missing') or 'authentication')))
            if target not in self.paused:
                out.append(change('pause', [target], 'until DNS is fixed'))
            return out
        if k == 'apple':
            if self.apple_off:
                return []
            return [change('suppress_apple', [], 'drop icloud.com, me.com '
                           'and mac.com addresses before upload')]
        if k == 'bad_list':
            todo = [d for d in n['domains'] if d not in self.scrubbed]
            if not todo:
                return []
            return [change('scrub', todo, 'clean the list these domains '
                           'send to; leave the domains alone')]
        if k == 'gmail_gap':
            return [change('route', n['healthy'], 'send Gmail leads only '
                           'from mailboxes Google still scores 80+', 'google')]
        if k == 'ms_block':
            doms = n.get('domains') or [target]
            todo = [d for d in doms if d not in self.off['microsoft']]
            return [change('stop', todo, 'Microsoft refuses it; keep sending '
                           'to Gmail and others', 'microsoft')] if todo else []
        if k == 'warmup_drop':
            todo = [d for d in n['members'] if d not in self.google_off]
            return [change('stop', todo, 'Google\'s warmup score says its mail '
                           'lands in spam; send its Gmail leads from healthier '
                           'domains until the score is back over 70',
                           'google')] if todo else []
        if k == 'throttle':
            doms = n.get('domains') or [target]
            todo = [d for d in doms if d not in self.slowed and d not in self.google_off]
            return [change('slow', todo, 'halve Gmail sends for three days, '
                           'spread over the whole day', 'google')] if todo else []
        if k == 'complaints':
            doms = n.get('domains') or [target]
            todo = [d for d in doms if d not in self.paused]
            return [change('pause', todo, 'recipients are reporting it; check '
                           'targeting and the opt-out line before it sends '
                           'again')] if todo else []
        # The rest is advice for a person, given once per thing.
        once = {
            'receiver': ('hold_receiver', [target], 'stop mailing %s-hosted '
                         'recipients from these domains and ask %s to lift it'
                         % (target, target)),
            'content': ('review_copy', [], 'spam filters object to the message: '
                        'cut links and tracking, change the wording, test it'),
            'link_listed': ('remove_link', [target], 'take %s out of the copy '
                            'and the signature, or replace it' % target),
            'mailboxes': ('reconnect', n.get('domains') or [], 'reconnect them in '
                          'the sequencer, or take them out of rotation'),
            'stalled': ('check', n.get('domains') or [target], 'find out why: '
                        'out of leads, a paused campaign, or mailboxes moved'),
            'runway': ('load_leads', [], 'load new leads: about %s days of new '
                       'contacts left' % n.get('days')),
            'bounce_guard': ('investigate', n.get('paused') or [], 'find what '
                             'bounced before resuming; resuming as is trips it '
                             'again'),
            'dns': ('fix_dns', n.get('domains') or [], 'fix the record: %s'
                    % n.get('issue', '').replace('_', ' ')),
            'volume': ('set_limit', n.get('domains') or [], 'cap each mailbox at %d a day' % n.get('cap', 30)),
            'settings': ('change_settings', n.get('campaigns') or [],
                         'turn off open tracking' if target == 'tracking' else
                         'send only to verified addresses'),
        }
        if k in once:
            op, targets, why = once[k]
            key = (k, target, tuple(targets))
            if key in self.said:
                return []
            self.said.add(key)
            c = change(op, targets, why)
            if k == 'settings':
                c['setting'] = target
            if k == 'volume':
                c['limit'] = n.get('cap', 30)
            return [c]
        return []

    def recover(self, findings, data):
        # Changes that should end because the reason for them ended.
        out = []
        for d, why in list(self.paused.items()):
            if why == 'auth' and not data.auth_missing(d, data.days[-1]):
                out.append(change('unpause', [d], 'DNS fixed'))
        back = []
        for d, why in list(self.google_off.items()):
            h = data.health.get(d)
            if why == 'warmup_drop' and h and h[0] is not None and h[0] >= 70:
                back.append(d)
        if back:
            out.append(change('resume', back, 'warmup score back over 70', 'google'))
        return out

    def held(self):
        # Domains we stopped or paused ourselves: quiet on purpose.
        return set(self.paused) | set(self.google_off)

    def record(self, c, reason):
        op = c['op']
        off = self.off.get(c.get('provider'), self.google_off)
        for d in c['targets']:
            if op == 'stop':
                off[d] = reason
            elif op == 'resume':
                off.pop(d, None)
            elif op == 'slow':
                self.slowed[d] = reason
            elif op == 'restore':
                self.slowed.pop(d, None)
            elif op == 'pause':
                self.paused[d] = reason
            elif op == 'unpause':
                self.paused.pop(d, None)
            elif op == 'scrub':
                self.scrubbed.add(d)
        if op == 'suppress_apple':
            self.apple_off = True

    def step(self, findings, data):
        # Everything to do today, in order, already recorded as done.
        todo = []
        for f in findings:
            for c in self.plan(f):
                self.record(c, f['kind'])
                todo.append(c)
        for c in self.recover(findings, data):
            self.record(c, 'recover')
            todo.append(c)
        return todo
