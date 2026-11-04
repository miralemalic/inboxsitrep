# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# Write an incident up for the person who has to act on it: what we see,
# why we believe it, what to do, how sure we are.
#
# Every report can be written without a model.  With ai=True, Claude
# rewrites it in plainer words and reads the raw bounce messages, which is
# the one part that really needs reading: receivers explain themselves in
# a dozen formats.  Claude gets the evidence and nothing else: no tools, no
# settings files, no MCP servers, an empty working folder.  It runs on
# whatever subscription the `claude` command is logged in with (--bare
# would skip that login, so it isn't used).  The numbers come from the
# detectors, not the model.

import shutil
import tempfile
import subprocess

from .data import SENT, SENDER


WHY = {
    'burned': 'For the first day or two a dead domain and a passing Gmail '
              'episode look the same.  Episodes fall back to normal while '
              'volume holds; burns stay pinned, and a blocklist usually '
              'names the domain.  This one has stayed down.',
    'rejecting': 'Gmail refuses mail on the sending domain\'s reputation.  '
                 'Usually it clears in two to four days.  If it stays pinned '
                 'for three days, or a blocklist names the domain, it is a '
                 'burn.  Stopping Gmail sends now costs little: Microsoft '
                 'keeps receiving, and ten probe sends a day show when '
                 'Gmail takes mail again.',
    'cascade': 'Gmail judges look-alike domains as one sender.  In a real '
               'workspace nine of thirty died one after another, first one a '
               'day, then two, and pulling the dead ones did not stop it.  '
               'The live members are next.',
    'quiet_spam': 'Nothing bounces, so bounce monitors never see this.  '
                  'Gmail recipients stop replying while Microsoft '
                  'recipients on the same domains reply as before, which '
                  'rules out the copy and the list.',
    'listed': 'A blocklist is something to get off, not a reason to retire '
              'a domain that still delivers.  Only receivers that check this '
              'list refuse the mail; the rest take it.  Request removal and '
              'keep the domain sending; pause it only where receivers refuse '
              'it, and fix what put it there.',
    'tenant_block': 'Microsoft has stopped the sending tenant itself, so '
                    'nothing leaves it, whoever it is addressed to.  This is '
                    'not any receiver\'s verdict and no amount of warming '
                    'helps: the tenant has to be released by Microsoft once '
                    'the cause is dealt with.',
    'auth': 'Receivers refuse unauthenticated mail outright.  This is a '
            'DNS fix, not a reputation problem: add the record, test, '
            'resume.',
    'apple': 'Apple refuses cold mail with [CS01] whatever domain sends '
             'it, evenly across every sender.  It is not the domains, it is '
             'the addresses.',
    'bad_list': 'Several domains bouncing at the same high rate, all on '
                'addresses that do not exist, is one bad list.  The domains '
                'are fine; retiring them throws away healthy '
                'infrastructure.',
    'gmail_gap': 'Gmail recipients reply far less than Microsoft ones across '
                 'the workspace while almost nothing bounces: Gmail is filing '
                 'the mail as spam.  That is reputation at Google, not '
                 'burned domains, so culling domains will not fix it.',
    'ms_block': 'Microsoft, as a receiver, refuses the domain outright (S3150, '
                '5.7.708 and kin).  Its blocks are rarer than Gmail\'s and '
                'slower to lift.  Gmail and the rest still take the mail, so '
                'stop Microsoft recipients on this domain only.',
    'throttle': 'Gmail is rate-limiting the domain for an unusual rate of '
                'unsolicited mail (421 4.7.28).  It is a warning, not the '
                'verdict.  Slowed down now, a domain usually keeps its '
                'reputation; pushed through it, it gets the reputation block.',
    'complaints': 'Receivers are refusing the mail because their users report '
                  'it.  Spam reports are what Gmail and Yahoo\'s 0.3% '
                  'bulk-sender line measures, and they follow the domain.  '
                  'Check who is being mailed and that opting out is easy.',
    'receiver': 'One receiver refuses mail from many of your domains at once.  '
                'It has made up its mind about the sender, not about any one '
                'domain, so pulling domains changes nothing.  Hold its '
                'recipients and ask it to lift the block.',
    'content': 'Spam filters are judging the message, not the sender, across '
               'many domains.  That points at the copy: its links, tracking, '
               'wording or attachments.',
    'link_listed': 'A link domain in the copy is on a blocklist.  Every domain '
                   'that sends the copy carries it, so every domain looks listed '
                   'when only the link is.  Take it out; the domains are fine.',
    'warmup_drop': 'The warmup score is the share of warmup mail that lands in '
                   'the inbox at Google.  When it falls, campaign mail from '
                   'the same mailboxes is landing in spam too, usually days '
                   'before replies show it.',
    'mailboxes': 'Mailboxes whose connection broke send nothing and raise no '
                 'alarm: campaigns just get slower.',
    'stalled': 'These domains were sending and stopped while their mailboxes '
               'are still here.  Out of leads, a paused campaign, mailboxes '
               'moved: the capacity is idle either way.',
    'runway': 'Active campaigns are close to running out of new leads.  A '
              'workspace with nothing to send costs as much as one with '
              'nothing working.',
    'bounce_guard': 'PlusVibe pauses a campaign when its bounce rate passes the '
                    'limit.  The cause is usually another incident (a tenant '
                    'block, a bad list); resuming as is trips it again.',
    'volume': 'Past about 50 cold sends a day per mailbox, replies fall and bounces climb: in 2026 '
              'field data, 20-49 a day replied at 5.7% and 100+ at 1.4%.  More mailboxes at a lower '
              'limit send the same volume and keep the domains.',
    'settings': 'Settings the sequencer applies to every email in the campaign.  '
                'Open tracking adds a pixel and a tracking link domain that '
                'filters weigh against cold mail; risky addresses bounce more.',
    'dns': 'Gmail and Yahoo refuse bulk mail without SPF, DKIM and DMARC since '
           'February 2024, and an SPF record that doesn\'t name the provider '
           'sending the mail is the same as none.',
}

PROVIDER = {'google': 'Gmail', 'microsoft': 'Microsoft'}

SYSTEM = ('You write incident reports for the person who runs a cold email '
          'sending fleet.  Plain words, short sentences, no hype, no '
          'markdown tables.  Keep every number exactly as given and add '
          'none.  Do not recommend anything that is not in the plan.  When '
          'bounce messages are included, say in one or two sentences what '
          'the receivers are saying, in everyday words.  Output the report '
          'and nothing else: no notes about the rewrite.')


def series(data, dom, end, n=10):
    lines = []
    for day in data.window(end, n):
        r = data.row(dom, 'google', day)
        if r[SENT]:
            lines.append('%s  %5d Gmail sends  %5d refused  (%.0f%%)'
                         % (day, r[SENT], r[SENDER], 100.0 * r[SENDER] / r[SENT]))
    return lines


def plain(inc, data=None):
    out = ['# %s' % inc['title'], '',
           '%s %s, opened %s, last seen %s, %s confidence, %s.'
           % (inc['scope'], inc['target'] if len(inc['target']) < 60 else
              '(%d domains)' % len(inc['target'].split(',')),
              inc['opened'], inc['updated'], inc['confidence'], inc['status'])]
    if inc.get('ending'):
        out += ['', inc['ending'] + '.']
    out += ['', '## What we see', '']
    for k, v in sorted(inc['evidence'].items()):
        if isinstance(v, list):
            v = ', '.join(map(str, v[:12])) + (' ...' if len(v) > 12 else '')
        out.append('- %s: %s' % (k.replace('_', ' '), v))
    if data is not None and inc['scope'] == 'domain' and inc['kind'] in ('burned', 'rejecting'):
        out += ['', '```'] + series(data, inc['target'], inc['updated']) + ['```']
    out += ['', '## Why we think so', '', WHY.get(inc['kind'], '')]
    out += ['', '## What to do', '']
    for c in inc['plan'] or []:
        who = c['targets'][:8]
        out.append('- %s%s %s: %s' % (
            c['op'].replace('_', ' '),
            ' ' + PROVIDER.get(c['provider'], c['provider']) if c.get('provider') else '',
            ', '.join(who) + (' and %d more' % (len(c['targets']) - 8)
                              if len(c['targets']) > 8 else ''),
            c['why']))
    if not inc['plan']:
        out.append('- nothing new: what this needs is already in place')
    return '\n'.join(out) + '\n'


# The bounce kinds that speak to each incident, shown to Claude first.
RELEVANT = {
    'listed': ('spamhaus', 'surbl'), 'burned': ('gmail_reputation',),
    'rejecting': ('gmail_reputation',), 'episode': ('gmail_reputation',),
    'cascade': ('gmail_reputation',), 'auth': ('auth',),
    'tenant_block': ('tenant_block',),
    'apple': ('apple_cs01',), 'bad_list': ('user_unknown',),
    'ms_block': ('microsoft_block',), 'throttle': ('throttle',),
    'complaints': ('complaints',), 'receiver': ('isp_block', 'gateway_block', 'policy'),
    'content': ('spam_filter',), 'link_listed': ('surbl_ip', 'spamhaus_ip'),
    'warmup_drop': (), 'mailboxes': (), 'stalled': (), 'runway': (),
    'bounce_guard': (), 'dns': (), 'gmail_gap': (), 'quiet_spam': (),
}


def bounce_samples(data, inc, n=6):
    ev = inc['evidence']
    if inc['scope'] == 'domain':
        doms = [inc['target']]
    else:
        doms = ev.get('domains') or ev.get('dead') or ev.get('members') or []
    want = RELEVANT.get(inc['kind'], ())
    hits = [(kind not in want, dom, kind, text)
            for (dom, kind), text in data.samples.items()
            if dom in doms or (not doms and kind in want)]
    return ['%s [%s]: %s' % (dom, kind, text[:400])
            for _, dom, kind, text in sorted(hits)[:n]]


def ai(inc, data=None, timeout=240):
    if not shutil.which('claude'):
        return None
    text = plain(inc, data)
    samples = bounce_samples(data, inc) if data is not None else []
    if samples:
        text += '\n## Bounce messages (raw)\n\n' + '\n'.join(samples) + '\n'
    prompt = ('Rewrite this incident report under the same four headings '
              '(title, What we see, Why we think so, What to do).\n\n' + text)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            r = subprocess.run(['claude', '-p', '--tools', '',
                                '--setting-sources', '', '--strict-mcp-config',
                                '--no-session-persistence',
                                '--system-prompt', SYSTEM],
                               input=prompt, capture_output=True, text=True,
                               cwd=tmp, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            return None
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None
