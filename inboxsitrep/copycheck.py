# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# What is wrong with a campaign's copy before a spam filter or a recipient
# finds it.  Read from the sequences the sequencer already returns, so it
# costs no extra requests; only the findings are kept.
#
# Two kinds of finding.  Broken: a sentence some recipients will see
# mangled (a merge field glued to the next word, spintax or conditions that
# don't close).  Risky: what filters hold against cold email (links and
# images in the first email, an open-tracking pixel, words spam filters
# score, a long first email).

import re
import functools

# A merge field followed straight by a letter: "{{company_name}}for the
# right number".  Checked on the raw template, never inside rendered
# options: options that rightly start with a space look like defects once
# trimmed.  On one real fleet this hit about 1 email in 156.
RUN_ON = re.compile(r'\{\{(?!random\b)[a-z_]+\}\}(?=[A-Za-z0-9])', re.I)
# The same defect one level in: a merge field followed by a spintax block
# whose option starts with a letter.  Only the options that directly follow
# a field are looked at.
RUN_ON_SPIN = re.compile(r'\{\{(?!random\b)[a-z_]+\}\}\{\{random\|([^{}]*)\}\}', re.I)
LINK = re.compile(r'https?://([\w.-]+)', re.I)
TAG = re.compile(r'<[^>]+>')
LIQUID = re.compile(r'\{%.*?%\}', re.S)
FIELD = re.compile(r'\{\{(?!random\b)[a-z_]+\}\}', re.I)

# Words and phrases content filters score in cold email.  Short on purpose:
# a long list flags every honest sentence.
SPAMMY = ['for free', 'free trial', 'guarantee', 'guaranteed', 'risk-free', 'no obligation', 'act now', 'limited time',
          'click here', 'buy now', 'order now', 'urgent', 'winner', 'congratulations', '100%',
          'cash', 'earn money', 'make money', 'double your', 'no cost', 'special promotion',
          'once in a lifetime', 'exclusive deal', 'lowest price', 'best price', 'call now',
          'apply now', 'miracle', '$$$', '!!!']
SPAMMY_RX = re.compile(r'(?<![\w-])(' + '|'.join(re.escape(w) for w in SPAMMY) + r')(?![\w-])', re.I)
LONG = 125           # words in a first email before it reads like a newsletter
SHORTENERS = re.compile(r'\b(bit\.ly|tinyurl\.com|t\.co|goo\.gl|ow\.ly|rebrand\.ly|cutt\.ly|is\.gd|buff\.ly)\b', re.I)
SHOUT = re.compile(r'\b[A-Z]{4,}\b')


def first_option(text):
    # One rendering: the first branch of every condition, the first option
    # of every spintax block, merge fields as a placeholder.  Enough to
    # count words.
    for _ in range(30):
        new = re.sub(r'\{%\s*if\b[^%]*%\}((?:(?!\{%\s*(?:if|else|endif)\b).)*?)'
                     r'(?:\{%\s*else\s*%\}(?:(?!\{%\s*(?:if|else|endif)\b).)*?)?\{%\s*endif\s*%\}',
                     r'\1', text, flags=re.S)
        if new == text:
            break
        text = new
    text = FIELD.sub('X', LIQUID.sub('', text))
    for _ in range(50):
        # innermost {{name|a|b}}: spintax takes its first option, a fallback
        # its default
        new = re.sub(r'\{\{(\w+)\|([^{}|]*)(?:\|([^{}]*))?\}\}',
                     lambda m: m.group(2) if m.group(1) == 'random' else (m.group(3) or 'X').split('|')[0],
                     text)
        if new == text:
            break
        text = new
    return text


@functools.lru_cache(maxsize=2048)
def plain(html):
    return re.sub(r'\s+', ' ', TAG.sub(' ', (html or '').replace('&nbsp;', ' '))).strip()


def snippet(text, at, width=45):
    s = plain(text[max(0, at - width * 2):at + width * 2])
    return s[:160]


@functools.lru_cache(maxsize=2048)
def words_in(body):
    return len(plain(first_option(body)).split())


@functools.lru_cache(maxsize=4096)
def spam_in(text):
    return tuple(sorted({m.group(1).lower() for m in SPAMMY_RX.finditer(plain(text))}))


def balanced(text):
    # Spintax and conditions that open and close.  An unclosed block shows
    # the recipient raw template.
    if text.count('{{') != text.count('}}'):
        return 'spintax or a merge field does not close ({{ %d, }} %d)' % (text.count('{{'), text.count('}}'))
    ifs, ends = len(re.findall(r'\{%\s*if\b', text)), len(re.findall(r'\{%\s*endif\s*%\}', text))
    if ifs != ends:
        return 'conditions do not close ({%% if %%} %d, {%% endif %%} %d)' % (ifs, ends)
    return None


def switched_on(value):
    # PlusVibe says 1 and 0; other sequencers true or "yes".
    return str(value).lower() in ('1', 'true', 'yes')


def check(sequences, campaign=None):
    # [{level, kind, where, what, example}], broken first.
    out = []
    campaign = campaign or {}
    for step in sequences or []:
        n = step.get('step')
        for var in step.get('variations') or []:
            where = 'step %s, variation %s' % (n, var.get('variation') or var.get('name') or '?')
            for part in ('subject', 'body'):
                text = var.get(part) or ''
                if not text:
                    continue
                hits = list(RUN_ON.finditer(text)) + [
                    m for m in RUN_ON_SPIN.finditer(text)
                    if any(o[:1].isalnum() for o in m.group(1).split('|'))]
                if hits:
                    out.append({'level': 'broken', 'kind': 'run_on', 'where': where + ', ' + part,
                                'what': '%d merge field%s run into the next word' % (len(hits), 's' if len(hits) > 1 else ''),
                                'example': snippet(text, hits[0].end())})
                bad = balanced(text)
                if bad:
                    out.append({'level': 'broken', 'kind': 'unbalanced', 'where': where + ', ' + part,
                                'what': bad, 'example': ''})
            body = var.get('body') or ''
            if str(n) == '1':
                links = sorted({m.group(1).lower() for m in LINK.finditer(body)})
                if links:
                    out.append({'level': 'risky', 'kind': 'links', 'where': where,
                                'what': 'links in the first email: %s' % ', '.join(links[:4]),
                                'example': ''})
                if '<img' in body.lower():
                    out.append({'level': 'risky', 'kind': 'images', 'where': where,
                                'what': 'an image in the first email', 'example': ''})
                words = words_in(body)
                if words > LONG:
                    out.append({'level': 'risky', 'kind': 'long', 'where': where,
                                'what': 'first email is about %d words' % words, 'example': ''})
            if SHORTENERS.search(body):
                out.append({'level': 'risky', 'kind': 'shortener', 'where': where,
                            'what': 'a link shortener (filters treat them as spam)', 'example': ''})
            subject = plain(first_option(var.get('subject') or ''))
            if SHOUT.search(subject) or '!' in subject:
                out.append({'level': 'risky', 'kind': 'subject', 'where': where,
                            'what': 'subject shouts (capitals or "!"): %s' % subject[:60], 'example': ''})
            spam = sorted(set(spam_in(var.get('subject') or '')) | set(spam_in(body)))
            if spam:
                out.append({'level': 'risky', 'kind': 'words', 'where': where,
                            'what': 'words filters score: %s' % ', '.join(spam[:6]), 'example': ''})
    first = next((st for st in sequences or [] if str(st.get('step')) == '1'), None)
    if first:
        vs = first.get('variations') or []
        if len(vs) <= 1 and not any('{{random|' in (v.get('body') or '') for v in vs):
            out.append({'level': 'risky', 'kind': 'identical', 'where': 'step 1',
                        'what': 'one version of the first email, no spintax: everyone gets the same text',
                        'example': ''})
    if switched_on(campaign.get('is_emailopened_tracking')):
        out.append({'level': 'risky', 'kind': 'tracking', 'where': 'campaign settings',
                    'what': 'open tracking is on: a tracking pixel in every email', 'example': ''})
    return sorted(out, key=lambda i: (i['level'] != 'broken', i['kind'], i['where']))


# Merge fields the sequencer fills from the sending account or the clock,
# not from the lead.
NOT_LEAD = ('random', 'fallback')
NOT_LEAD_PREFIX = ('pipl_', 'sender_', 'account_', 'signature', 'unsubscribe', 'sending_')


def fields(sequences):
    # (merge fields the copy uses, the ones it guards with a condition or a
    # fallback so an empty value is handled)
    text = ' '.join((v.get('subject') or '') + ' ' + (v.get('body') or '')
                    for st in sequences or [] for v in st.get('variations') or [])
    used = set(re.findall(r'\{\{\s*([a-z_][a-z0-9_]*)\s*[|}]', text, re.I))
    used |= set(re.findall(r'\{\{\s*fallback\|\s*([a-z_][a-z0-9_]*)', text, re.I))
    guarded = set(re.findall(r'\{%\s*if\s+([a-z_][a-z0-9_]*)', text, re.I))
    guarded |= set(re.findall(r'\{\{\s*fallback\|\s*([a-z_][a-z0-9_]*)', text, re.I))
    used = {u for u in used if u.lower() not in NOT_LEAD and not u.lower().startswith(NOT_LEAD_PREFIX)}
    return used, guarded


def mapping(sequences, sample, details):
    # Merge fields that are not mapped to the campaign's leads, or are empty
    # on many of them with nothing in the copy to catch it: the sentence
    # some recipients get with a hole in it.  sample: lead records as the
    # lead list returns them (standard fields); details: a few full leads,
    # custom fields included (lead_data).
    used, guarded = fields(sequences)
    out = []
    known = set()
    for lead in list(sample) + list(details):
        known |= {k for k, v in lead.items() if v not in (None, '')} | set(lead)
    for f in sorted(used):
        names = (f, 'custom_' + f)
        if details and not any(n in known for n in names):
            out.append({'level': 'broken' if f not in guarded else 'risky', 'kind': 'unmapped',
                        'where': 'merge fields',
                        'what': '{{%s}} is not on this campaign\'s leads: %s' % (
                            f, 'every email gets it empty' if f not in guarded else 'every email takes the fallback'),
                        'example': ''})
            continue
        pool = [l for l in (sample if any(f in l for l in sample) else details)]
        if not pool or f in guarded:
            continue
        empty = sum(1 for l in pool if not str(l.get(f) or l.get('custom_' + f) or '').strip())
        if empty >= max(1, .05 * len(pool)):
            out.append({'level': 'broken', 'kind': 'empty_field', 'where': 'merge fields',
                        'what': '{{%s}} is empty for %d%% of the leads read and the copy has no fallback'
                                % (f, round(100 * empty / len(pool))), 'example': ''})
    return out


def summary(issues, keep=25):
    # What is stored with the campaign: counts and the first findings.
    return {'broken': sum(1 for i in issues if i['level'] == 'broken'),
            'risky': sum(1 for i in issues if i['level'] == 'risky'),
            'issues': issues[:keep]}
