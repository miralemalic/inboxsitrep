# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# Tell someone: a desktop notification, a line in a log file, Slack (an
# incoming webhook) and Telegram (a bot of your own, see telegram.py).
#
# Secrets never live in the settings file: the Telegram bot token is read
# from its own file next to it, owner-only.

import os
import sys
import re
import json
import shutil
import datetime
import subprocess
import urllib.parse
import urllib.request

LOG = os.path.expanduser('~/.local/share/inboxsitrep/alerts.log')
KEYS = os.path.expanduser('~/.config/inboxsitrep/keys')


def secret(name):
    path = os.path.join(KEYS, name)
    if not os.path.exists(path):
        return ''
    with open(path) as f:
        return f.read().strip()


def desktop(title, body):
    # Linux: notify-send.  macOS: Notification Center.  Windows: none; Slack
    # and Telegram carry the alerts there.
    if shutil.which('notify-send'):
        subprocess.run(['notify-send', '--app-name=Inbox Sitrep', title, body],
                       check=False, timeout=10)
    elif sys.platform == 'darwin':
        quote = lambda t: '"%s"' % (t or '').replace('\\', '').replace('"', "'")[:200]
        subprocess.run(['osascript', '-e', 'display notification %s with title %s' % (quote(body), quote(title))],
                       check=False, timeout=10)


def chat(md, links='slack'):
    # Markdown (the analyst's) to what Slack and Telegram show:
    # *bold*, headings as bold lines, bullets as dots, links their way.
    out = []
    for line in md.splitlines():
        line = re.sub(r'\*\*(.+?)\*\*(?!\*)', r'*\1*', line)
        line = re.sub(r'^#{1,6}\s+(.+?)\s*#*$', r'*\1*', line)
        line = re.sub(r'^(\s*)[-*]\s+', '\\1\u2022 ', line)
        line = re.sub(r'\[([^\]]+)\]\((https?://[^)\s]+)\)',
                      r'<\2|\1>' if links == 'slack' else r'\1 (\2)', line)
        out.append(line)
    text = '\n'.join(out)
    if links == 'slack':
        # Slack treats these three as markup
        text = re.sub(r'&(?!amp;|lt;|gt;)', '&amp;', text)
    return text


def webhook(url, title, body):
    body = chat(body or '')
    text = ('*%s*\n%s' % (title, body) if title else body) if body else title
    req = urllib.request.Request(url, data=json.dumps({'text': text, 'content': text}).encode(),
                                 headers={'Content-Type': 'application/json',
                                          'User-Agent': 'inboxsitrep'})
    urllib.request.urlopen(req, timeout=15).close()


def log(title, body, path=None):
    path = path or LOG
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'a') as f:
        f.write('%s  %s%s\n' % (datetime.datetime.now().isoformat(timespec='seconds'),
                                title, ('  (%s)' % body) if body else ''))


def channels(cfg):
    # Which channels are set up, from the notify part of the settings.
    out = []
    if cfg.get('webhook') or (cfg.get('slack_channel') and secret('slack-bot.key')):
        out.append('slack')
    if (cfg.get('telegram') or {}).get('chat_id') and secret('telegram.key'):
        out.append('telegram')
    return out


def send(cfg, title, body, only=None, say=None):
    # Send to every channel that is set up (or just `only`).  One failing
    # channel doesn't stop the others; failures are reported, not raised.
    cfg = cfg or {}
    failed = {}
    for ch in [only] if only else channels(cfg):
        try:
            if ch == 'slack':
                if cfg.get('slack_channel') and secret('slack-bot.key'):
                    from . import slackbot
                    slackbot.post(secret('slack-bot.key'), cfg['slack_channel'],
                                  '*%s*\n%s' % (title, chat(body or '')) if body else title)
                else:
                    webhook(cfg['webhook'], title, body)
            elif ch == 'telegram':
                from . import telegram
                # Telegram shows *bold* as asterisks: drop the marks the
                # reports put at line starts (a family's "growthmail*" stays).
                text = re.sub(r'(?m)^\*([^*\n]+)\*', r'\1', chat(body or '', links='plain'))
                telegram.send(secret('telegram.key'), cfg['telegram']['chat_id'], '%s\n\n%s' % (title, text))
        except (OSError, KeyError, ValueError) as e:
            failed[ch] = str(e)[:200] or e.__class__.__name__
            if say:
                say('%s failed: %s' % (ch, failed[ch]))
    log(title, '' if not failed else 'failed: %s' % ', '.join(failed))
    return failed


def sender(cfg=None):
    # For watch and live mode: (title, body) to the desktop and every channel.
    cfg = cfg or {}

    def go(title, body=''):
        if cfg.get('desktop', True):
            desktop(title, body)
        send(cfg, title, body)
    return go
