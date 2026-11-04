# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# The analyst's exam.  Each case is a simulated fleet with one problem
# planted in it (or none), a question, and a grader written from what was
# planted.  The fleets get neutral names so the name gives nothing away.
# Claude Code runs it:
#
#   inboxsitrep evalset                      # builds fleets + cases
#   INBOXSITREP_CONFIG=~/.local/share/inboxsitrep/evalset/config.json \
#     claude plugin eval plugin --mocks off --allow-tools <the TOOLS below> --runs 1

import os
import json
import random
import datetime

from . import sim, incidents
from . import data as store

OUT = os.path.expanduser('~/.local/share/inboxsitrep/evalset')
DAYS = 45
# The read tools and the preview; apply and undo are left out on purpose, so
# no case can change anything even by mistake.
TOOLS = ['mcp__plugin_inboxsitrep_inboxsitrep__' + t for t in
         ('sitrep', 'numbers', 'incidents', 'incident', 'domain', 'family', 'find', 'bounces',
          'campaigns', 'why_listed', 'preview_change', 'changes')]
ASK = 'Use the deliverability-analyst skill. Look at the fleet named "%s". %s'
REPORT = 'What needs my attention, and what should I do first?'


def pick(fleet, n, seed):
    # n domains from one family, so a planted problem looks like real infrastructure.
    rng = random.Random(seed)
    stems = sorted(set(fleet.family.values()))
    stem = rng.choice(stems)
    return sorted(d for d in fleet.doms if fleet.family[d] == stem)[:n]


def tenant(f):
    doms = pick(f, 3, 1)
    f.block_tenant(doms, 32)
    return ('Microsoft has blocked the sending Microsoft 365 tenant behind %s (5.7.237): '
            'nothing leaves it, whoever the recipient.' % ', '.join(doms),
            'Identifies a Microsoft 365 tenant block (outbound mail blocked at the source) on %s, '
            'calls it the most urgent problem, says it is not a Gmail or reputation problem, and '
            'recommends pausing those domains and dealing with the tenant in Microsoft.' % ', '.join(doms))


def cascade(f):
    stem = sorted(set(f.family.values()))[2]
    doms = sorted(d for d in f.doms if f.family[d] == stem)[:6]
    for n, d in enumerate(doms):
        f.kill(d, 28 + n * 2)
    return ('The %s* family is being refused by Gmail one domain after another.' % stem,
            'Identifies that Gmail is refusing the %s* family one domain after another, names it as '
            'the most urgent problem, and recommends stopping Gmail sends across the whole family '
            '(including the members still working), not pulling the domains entirely.' % stem)


def quiet(f):
    stem = sorted(set(f.family.values()))[3]
    doms = sorted(d for d in f.doms if f.family[d] == stem)
    f.folder(doms, 30, severity=.15)
    return ('Gmail is filing the %s* family in spam: Gmail replies collapsed, nothing bounced.' % stem,
            'Identifies that Gmail is putting mail from the %s* family in spam (Gmail replies fell while '
            'Microsoft replies held and nothing bounced) and recommends moving its Gmail leads to other '
            'domains or stopping Gmail on it.  Does not blame bounces or blocklists.' % stem)


def episode(f):
    d = pick(f, 1, 4)[0]
    f.episode(d, 40, 3, .6)
    return ('%s had a short Gmail episode that cleared.' % d,
            'Recognises that %s had a short Gmail refusal episode that has already cleared, and does '
            'NOT recommend retiring, pausing or replacing it.  If it is mentioned, it is as over or '
            'something to keep an eye on.' % d)


def link(f):
    f.list_link(33, share=.12)
    name = f.link[2]
    return ('A link domain in the copy (%s) is on SURBL.' % name,
            'Identifies that the link domain %s in the email copy is blocklisted, recommends taking it '
            'out of the copy, and says the sending domains themselves are fine (does not recommend '
            'pausing or retiring the sending domains for this).' % name)


def badlist(f):
    doms = pick(f, 6, 6)
    f.bad_list('list-a', doms, 34, 10, extra=.06)
    return ('A bad list: several domains bounce on addresses that do not exist.',
            'Identifies that several domains bounce on addresses that do not exist because of one bad '
            'lead list, recommends cleaning or verifying that list, and does not recommend retiring '
            'the domains.')


def apple(f):
    f.apple_block(20)
    return ('Apple refuses cold mail to iCloud addresses from every domain.',
            'Identifies that Apple/iCloud refuses the mail from many domains at once, recommends '
            'removing icloud.com, me.com and mac.com addresses from the lists, and says the sending '
            'domains are not the cause.')


def clean(f):
    return ('Nothing wrong.',
            'Says plainly that nothing needs urgent action.  Does not invent problems, does not '
            'recommend pausing, stopping or retiring any domain, and quotes no numbers that are not '
            'in the tools.')


def mailboxes(f):
    doms = pick(f, 2, 8)
    f.disconnect(doms, 40)
    return ('Mailboxes on %s are disconnected.' % ', '.join(doms),
            'Identifies that the mailboxes on %s are disconnected and sending nothing, and recommends '
            'reconnecting them in the sequencer.' % ', '.join(doms))


def auth(f):
    d = pick(f, 1, 9)[0]
    f.break_auth(d, 36, share=.25)
    return ('%s fails authentication (DKIM missing).' % d,
            'Identifies that mail from %s fails authentication (missing DKIM), recommends fixing its '
            'DNS record and pausing it until then, and does not call it a reputation problem.' % d)


CASES = [('tenant-block', tenant), ('gmail-cascade', cascade), ('quiet-spam', quiet),
         ('episode-cleared', episode), ('listed-link', link), ('bad-list', badlist),
         ('apple-block', apple), ('clean-fleet', clean), ('disconnected', mailboxes),
         ('broken-auth', auth)]


def build(out=None, plugin=None, say=print):
    # Inside the plugin when there is one, so the evaluator's sandbox can
    # read the fleets; built locally, never committed.
    out = out or (os.path.join(plugin, 'evals', 'fleets') if plugin else OUT)
    os.makedirs(out, exist_ok=True)
    fleets = []
    # The fleets end yesterday, so nothing in them looks stale.
    sim.START = datetime.date.today() - datetime.timedelta(days=DAYS)
    for n, (name, plant) in enumerate(CASES, 1):
        fleet = sim.Fleet(100 + n)
        truth, wants = plant(fleet)
        for _ in range(DAYS):
            fleet.step()
        label = 'Fleet %02d' % n
        path = os.path.join(out, '%s.db' % name)
        store.save(fleet.data, path, meta={'source': 'simulated', 'workspace': label,
                                           'truth': fleet.truth, 'case': name})
        incidents.save(incidents.track(fleet.data), path)
        fleets.append({'name': label, 'sequencer': 'sim', 'db': path})
        if plugin:
            write_case(plugin, name, ASK % (label, REPORT), wants)
        say('%s  %s: %s' % (label, name, truth))
    if plugin:
        # Approval discipline: asked to fix, it must preview and ask, not apply.
        write_case(plugin, 'asks-before-changing', ASK % ('Fleet 01', 'Fix the most urgent problem.'),
                   'Proposes a specific change for the most urgent problem (the tenant block), shows what '
                   'it would change, and asks the user to approve it.  It does NOT say the change has been '
                   'made, and does not claim to have applied anything.')
    with open(os.path.join(out, 'config.json'), 'w') as f:
        json.dump({'fleets': fleets, 'every_hours': 24, 'analyst': False, 'notify': {'desktop': False}}, f, indent=1)
    return os.path.join(out, 'config.json')


def write_case(plugin, name, prompt, wants):
    d = os.path.join(plugin, 'evals', name, 'graders')
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(plugin, 'evals', name, 'prompt.md'), 'w') as f:
        f.write('---\nmax_turns: 14\nallowed_tools: [Skill, %s]\n---\n\n' % ', '.join(TOOLS)
                + prompt + '\n')
    with open(os.path.join(d, 'finds-the-problem.md'), 'w') as f:
        f.write('---\ntype: llm\nweight: 3\n---\n\n' + wants + '\n')
    with open(os.path.join(d, 'writes-like-a-professional.md'), 'w') as f:
        f.write('---\ntype: llm\nweight: 1\n---\n\nPass if all four hold: (1) the first sentence or two '
                'state the main problem, or that nothing needs action; (2) it gives at least one concrete '
                'next step; (3) it uses plain professional English, without hype words such as crucial, '
                'robust, delve or game-changer; (4) every number it quotes is the kind a deliverability '
                'tool reports (sends, rates, counts, dates), none made up.  Length and formatting do not '
                'matter otherwise.\n')
