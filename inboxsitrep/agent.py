# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# The analyst: Claude, briefed with deliverability doctrine, reading the
# fleet through inboxsitrep's own read-only tools.
#
# It is not trained in the machine-learning sense.  It is briefed: the
# doctrine below is what a deliverability engineer learned on a real fleet,
# written down, and the detectors hand it measured findings to reason from.
# The numbers always come from the detectors; the analyst explains, connects
# and recommends, and it can only recommend what the plans already say.
#
# It runs on whatever the `claude` command is logged in with, so a Claude
# subscription works and no API key is needed.  Without `claude`, or when
# the subscription is out of messages, the plain brief is used instead.

import os
import sys
import json
import shutil
import tempfile
import subprocess

from . import brief as plain_brief

DOCTRINE = '''You are the deliverability analyst for a cold email operation: its sending
domains, mailboxes and campaigns.  The person reading you runs that operation, is
busy, and must be able to act on what you say.

How you write: an intelligence brief.  Clear, concise, professional.
- Bottom line first, one or two sentences.
- Then the actions, most urgent first, each with the numbers that justify it.
- Short declarative sentences.  Name the domains, families and receivers.
- Numbers exactly as the tools give them.  Never invent one.
- Assessments carry a confidence: confirmed, likely, possible.  State what is
  unknown and what would settle it.
- A light military register where it fits (bottom line, assessment, watch,
  stand down, contact), never theatre: no codewords, no call signs, no drama.
- No filler ("it's worth noting", "crucial", "robust", "delve", "in summary"), no
  emoji, no markdown tables.

Doctrine (field lessons; apply them):
- Count by send date.  Bounce notices arrive days late.
- Stop Gmail, not the domain.  A domain Gmail refuses still delivers to Microsoft.
- Gmail refusing 30%+ for 1-2 days may be an episode; 3+ days, or a blocklist
  naming the domain, is a burn.  Episodes clear in 2-4 days.
- Look-alike domains die together.  When two of a family die in a week, the
  rest of the family is at risk.
- Quiet spam: Gmail replies fall while Microsoft replies hold and nothing
  bounces.  Bounce monitors never see it.
- A listing counts only when the bounce or DNS names the domain itself.  A
  listed IP is the provider's problem.
- Microsoft 5.7.237 "outbound email has been blocked" is the sender's own
  365 tenant shut down.  No receiver saw that mail.  It is not a Gmail problem.
- One receiver (Spectrum, Mimecast, Sophos) refusing many domains at once has
  judged the sender, not one domain.  Pulling domains changes nothing.
- Many healthy domains bouncing on addresses that do not exist is one bad
  list.  Clean the list; leave the domains alone.
- A blocklisted link in the copy makes every domain look listed.
- The common "pull a domain past 3-5% bounce" rule causes friendly fire.
- Leads running out and campaigns paused by the sequencer are as costly as
  burned domains: nothing is being sent.

Rules of engagement:
- You are read-only.  You cannot change anything.  Recommend; the operator acts.
- Recommend only what the incident plans say, or what follows directly from the
  evidence.  Mark anything beyond that as your assessment.
- Use the tools to check before you claim.  If the tools do not show it, say so.
- An assessment of cause needs two independent pieces of evidence.  With less,
  write "Cause unknown" and name the one check that would settle it.'''

BRIEF_TASK = '''Write the briefing for this check, from the evidence below.

Format, nothing else:
SITREP: <fleet>, <date>
Bottom line: <one or two sentences>
1. <most urgent: what, how big, since when, what to do>  (at most 5 items, worst first)
Watch: <one line, or omit>
Assessment: <one or two sentences on what connects the findings, or "Cause unknown" and the check that would settle it>

Items marked "New:" in the evidence appeared since the last report; say so
("New: ...") on those items.

Keep it under 170 words.  Every number must appear in the evidence.'''


def claude():
    # A desktop launcher may start us with a shorter PATH than a terminal.
    found = shutil.which('claude')
    for path in ('~/.npm-global/bin/claude', '~/.local/bin/claude', '~/.claude/local/claude',
                 '/usr/local/bin/claude', '/opt/homebrew/bin/claude'):
        path = os.path.expanduser(path)
        if not found and os.access(path, os.X_OK):
            found = path
    return found


def run(prompt, system, mcp=None, model=None, timeout=240):
    # One headless Claude Code call.  No built-in tools, no settings, no
    # other MCP servers, an empty working folder: only what we hand it.
    cmd = [claude(), '-p', '--output-format', 'json', '--no-session-persistence',
           '--setting-sources', '', '--strict-mcp-config', '--tools', '',
           '--system-prompt', system]
    if model:
        cmd += ['--model', model]
    with tempfile.TemporaryDirectory() as tmp:
        if mcp:
            path = os.path.join(tmp, 'mcp.json')
            with open(path, 'w') as f:
                json.dump({'mcpServers': {'inboxsitrep': {
                    'command': sys.executable, 'args': ['-m', 'inboxsitrep', 'mcp'] + mcp,
                    'env': {'PYTHONPATH': os.path.dirname(os.path.dirname(os.path.abspath(__file__)))}}}}, f)
            cmd += ['--mcp-config', path, '--allowedTools', 'mcp__inboxsitrep']
        try:
            r = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                               timeout=timeout, cwd=tmp)
        except subprocess.TimeoutExpired:
            return None, 'Claude took too long'
    try:
        out = json.loads(r.stdout)
    except ValueError:
        return None, (r.stderr or r.stdout or 'no answer').strip()[:300]
    if out.get('is_error'):
        return None, str(out.get('result') or out.get('subtype'))[:300]
    return (out.get('result') or '').strip(), None


def evidence(data, found, name, fresh=(), today=None):
    from . import report
    text = [plain_brief.brief(data, found, name, fresh, today), '', 'Details of open incidents:']
    open_ = [i for i in found if i['status'] == 'open' and i['kind'] in plain_brief.DECIDE]
    planned = plain_brief.planned_by(found)
    for i in open_[:12]:
        ev = {k: v for k, v in i['evidence'].items() if not isinstance(v, list) or len(v) <= 12}
        text.append('- [%s] %s | since %s | %s confidence | evidence %s | plan: %s'
                    % (i['kind'], i['title'], i['opened'], i['confidence'],
                       json.dumps(ev, default=str)[:600], plain_brief.plan_line(i, planned)))
        want = report.RELEVANT.get(i['kind'])
        doms = i['evidence'].get('domains')
        doms = doms if isinstance(doms, list) else [i['target']]
        for (d, k), t in sorted(data.samples.items()):
            if want and k in want and d in doms:
                text.append('    receiver said: %s' % t[:240])
                break
    if any(i['kind'] == 'listed' and i['status'] == 'open' for i in found) and data.dns:
        from . import rootcause
        text += ['', 'What the blocklisted domains have in common:', rootcause.report(data, found, top=4)]
    return '\n'.join(text)


def briefing(data, found, name, model=None, fresh=(), today=None):
    # The analyst's briefing, or the plain one if Claude can't be reached.
    fallback = plain_brief.brief(data, found, name, fresh, today)
    if not claude():
        return fallback, 'plain'
    text, err = run(BRIEF_TASK + '\n\nEvidence:\n' + evidence(data, found, name, fresh, today),
                    DOCTRINE, model=model)
    return (text, 'analyst') if text else (fallback, 'plain: ' + (err or ''))


INSTRUCT = '''You turn an operator's request into changes to their cold email workspace.
Reply with one JSON object and nothing else:
{"changes": [{"op": "...", "targets": ["..."], ...}], "note": "<one plain sentence: what these changes do>"}
If the request needs something these operations can't do, reply
{"changes": [], "note": "<what can't be done here, and how to do it by hand>"}

Operations:
%s

Targets are sending domains from the context, or a family written as its name and *
(growthmail* means every domain whose name starts with growthmail).  Never invent a
domain.  Do exactly what was asked, no more: a request about warmup alone changes
warmup alone.'''


def instruct(text, context, model=None):
    # A change in the operator's words -> ([changes], note, error).  The
    # changes still go through actions.clean() and a preview the operator
    # approves; this only translates.
    from .actions import OPS
    if not claude():
        return [], '', 'Claude Code is not installed, so changes can\'t be asked for in words'
    system = INSTRUCT % '\n'.join('- %s: %s' % kv for kv in OPS.items())
    out, err = run('Context:\n%s\n\nRequest: %s' % (context, text), system, model=model, timeout=120)
    if err:
        return [], '', err
    try:
        body = json.loads(out[out.index('{'):out.rindex('}') + 1])
    except ValueError:
        return [], '', 'the analyst did not answer in the expected form'
    changes = [c for c in body.get('changes') or [] if isinstance(c, dict)]
    return changes, str(body.get('note') or '').strip(), None


def ask(question, paths, history=(), model=None):
    # A question from the operator, answered with the tools.
    if not claude():
        return None, 'The claude command is not installed, so there is no analyst to ask.'
    convo = ''.join('Operator: %s\nYou: %s\n\n' % (q, a) for q, a in history[-4:])
    prompt = (convo + 'Operator: ' + question + '\n\nAnswer from the tools.  Lead with '
              'the answer.  Under 200 words unless asked for detail.')
    args = []
    for path in paths:
        args += ['--db', path]
    return run(prompt, DOCTRINE, mcp=args, model=model, timeout=300)
