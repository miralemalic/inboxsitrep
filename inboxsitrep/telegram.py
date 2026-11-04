# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# Telegram: reports to your chat, and questions answered in it.
#
# A bot from @BotFather; the app asks Telegram for new messages (long
# polling), so nothing has to be reachable from the internet and it works
# from a laptop while the app is open.  Only one chat is ever answered: the
# one that sent the pairing code shown in Settings.

import json
import time
import threading
import urllib.parse
import urllib.request

API = 'https://api.telegram.org/bot%s/%s'
LIMIT = 4000            # Telegram takes 4,096 characters a message


def call(token, method, body=None, timeout=30):
    req = urllib.request.Request(API % (token, method), data=json.dumps(body or {}).encode(),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.loads(r.read())
    if not out.get('ok'):
        raise RuntimeError('%s: %s' % (method, out.get('description')))
    return out.get('result')


def send(token, chat, text):
    # Long reports go in pieces, split at line ends.
    while text:
        part = text[:LIMIT]
        if len(text) > LIMIT and '\n' in part:
            part = part.rsplit('\n', 1)[0]
        call(token, 'sendMessage', {'chat_id': chat, 'text': part, 'disable_web_page_preview': True})
        text = text[len(part):].lstrip('\n')


class Bot(threading.Thread):
    # Pairs with the chat that sends the code, then answers its questions.

    def __init__(self, settings, answer, say=lambda m: None):
        super().__init__(daemon=True)
        self.settings = settings        # .cfg['notify']['telegram'], .keep(), .save()
        self.answer = answer
        self.say = say
        self.state = 'off'
        self.offset = 0

    def config(self):
        from .notify import secret
        return secret('telegram.key'), self.settings.cfg.setdefault('notify', {}).setdefault('telegram', {})

    def run(self):
        while True:
            token, tg = self.config()
            if not token:
                self.state = 'off'
                time.sleep(30)
                continue
            try:
                updates = call(token, 'getUpdates', {'offset': self.offset, 'timeout': 50,
                                                     'allowed_updates': ['message']}, timeout=70)
                self.state = 'connected' if tg.get('chat_id') else 'waiting for the pairing code'
                for u in updates or []:
                    self.offset = u['update_id'] + 1
                    self.handle(token, tg, u.get('message') or {})
            except (OSError, RuntimeError, ValueError) as e:
                self.state = 'error: %s' % str(e)[:120]
                time.sleep(30)

    def handle(self, token, tg, msg):
        chat, text = (msg.get('chat') or {}).get('id'), (msg.get('text') or '').strip()
        if not chat or not text:
            return
        if not tg.get('chat_id'):
            code = tg.get('code')
            if code and code in text:
                tg['chat_id'] = chat
                self.settings.save()
                send(token, chat, 'Linked.  Reports will come to this chat.  Ask about any workspace, any time '
                                  'the app is open.')
                self.say('Telegram: linked')
            return
        if chat != tg['chat_id'] or text.startswith('/start'):
            return
        threading.Thread(target=self.reply, args=(token, chat, text), daemon=True).start()

    def reply(self, token, chat, text):
        try:
            send(token, chat, 'Received. Checking the numbers.')
            send(token, chat, self.answer(text))
            self.say('Telegram: answered "%s"' % text[:60])
        except (OSError, RuntimeError) as e:
            self.say('Telegram: could not answer: %s' % str(e)[:120])
