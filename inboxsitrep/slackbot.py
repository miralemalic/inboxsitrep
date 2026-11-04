# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# Ask Inbox Sitrep a question in Slack and get the answer in the thread.
#
# Slack's Socket Mode: the app opens a connection out to Slack, so nothing
# has to be reachable from the internet and it works from a laptop.  It
# runs while the desktop app is open.  Mention the app in a channel
# (@Inbox Sitrep how are my domains doing?) or send it a direct message.  It only
# answers; changes are still made in the app, after a preview.
#
# Two tokens from the Slack app: a bot token (xoxb-, scopes app_mentions:read,
# chat:write, im:history) and an app-level token (xapp-, connections:write).
# The WebSocket client is the few lines Socket Mode needs, to keep Inbox
# Sitrep free of dependencies.

import os
import re
import ssl
import json
import time
import base64
import socket
import struct
import threading
import urllib.parse
import urllib.request

API = 'https://slack.com/api/'

# Everything the Slack app needs, so creating it is one click: Slack opens
# app creation with this filled in (manifest_url()).
MANIFEST = {
    'display_information': {'name': 'Inbox Sitrep', 'background_color': '#151816',
                            'description': 'Watches your cold email infrastructure. Reports what needs a '
                                           'decision and answers questions about it.'},
    'features': {'app_home': {'home_tab_enabled': False, 'messages_tab_enabled': True,
                              'messages_tab_read_only_enabled': False},
                 'bot_user': {'display_name': 'Inbox Sitrep', 'always_online': True}},
    'oauth_config': {'scopes': {'bot': ['app_mentions:read', 'chat:write', 'chat:write.public', 'im:history']}},
    'settings': {'event_subscriptions': {'bot_events': ['app_mention', 'message.im']},
                 'interactivity': {'is_enabled': False}, 'org_deploy_enabled': False,
                 'socket_mode_enabled': True, 'token_rotation_enabled': False},
}


def manifest_url():
    return 'https://api.slack.com/apps?new_app=1&manifest_json=' + urllib.parse.quote(json.dumps(MANIFEST))


def post(token, channel, text):
    # A report to a channel through the bot (chat:write.public: no invite
    # needed for public channels).
    call('chat.postMessage', token, {'channel': channel, 'text': text, 'unfurl_links': False})


def call(method, token, body=None):
    req = urllib.request.Request(API + method, data=json.dumps(body or {}).encode(),
                                 headers={'Authorization': 'Bearer ' + token,
                                          'Content-Type': 'application/json; charset=utf-8'})
    with urllib.request.urlopen(req, timeout=30) as r:
        out = json.loads(r.read())
    if not out.get('ok'):
        raise RuntimeError('%s: %s' % (method, out.get('error')))
    return out


class Socket:
    # A WebSocket client: the handshake, masked frames out, frames in,
    # pings answered.

    def __init__(self, url, conn=None):
        self.buf = b''
        if conn is not None:                    # an open socket, for tests
            self.s = conn
            return
        u = urllib.parse.urlparse(url)
        raw = socket.create_connection((u.hostname, u.port or 443), timeout=30)
        self.s = ssl.create_default_context().wrap_socket(raw, server_hostname=u.hostname)
        key = base64.b64encode(os.urandom(16)).decode()
        path = u.path + ('?' + u.query if u.query else '')
        self.s.sendall(('GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                        'Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n'
                        % (path, u.hostname, key)).encode())
        while b'\r\n\r\n' not in self.buf:
            self.buf += self._chunk()
        head, self.buf = self.buf.split(b'\r\n\r\n', 1)
        if b' 101 ' not in head.split(b'\r\n', 1)[0] + b' ':
            raise OSError('Slack refused the connection: %s' % head[:80])
        self.s.settimeout(120)                  # Slack pings every 30 s or so

    def _chunk(self):
        chunk = self.s.recv(65536)
        if not chunk:
            raise OSError('connection closed')
        return chunk

    def _read(self, n):
        while len(self.buf) < n:
            self.buf += self._chunk()
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv(self):
        # One text message.
        data = b''
        while True:
            b1, b2 = self._read(2)
            n = b2 & 0x7f
            if n == 126:
                n = struct.unpack('>H', self._read(2))[0]
            elif n == 127:
                n = struct.unpack('>Q', self._read(8))[0]
            mask = self._read(4) if b2 & 0x80 else None
            payload = self._read(n)
            if mask:
                payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
            op = b1 & 0x0f
            if op == 9:
                self.send(payload, op=10)
            elif op == 8:
                raise OSError('closed by Slack')
            elif op in (0, 1, 2):
                data += payload
                if b1 & 0x80:
                    return data.decode('utf-8', 'replace')

    def send(self, data, op=1):
        data = data.encode() if isinstance(data, str) else data
        n = len(data)
        head = bytes([0x80 | op])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack('>H', n)
        else:
            head += bytes([0x80 | 127]) + struct.pack('>Q', n)
        mask = os.urandom(4)
        self.s.sendall(head + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(data)))

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


class Bot(threading.Thread):
    # Connected while the app is open and both tokens are saved.

    def __init__(self, tokens, answer, say=lambda m: None):
        super().__init__(daemon=True)
        self.tokens = tokens            # () -> (bot token, app token), read each time
        self.answer = answer            # question -> text
        self.say = say
        self.state = 'off'
        self.seen = set()

    def run(self):
        wait = 5
        while True:
            bot, app = self.tokens()
            if not (bot and app):
                self.state = 'off'
                time.sleep(30)
                continue
            try:
                url = call('apps.connections.open', app)['url']
                ws = Socket(url)
                self.state, wait = 'connected', 5
                self.say('Slack: listening for questions')
                try:
                    while True:
                        if not self.handle(json.loads(ws.recv()), ws, bot):
                            break
                finally:
                    ws.close()
            except (OSError, RuntimeError, ValueError) as e:
                self.state = 'error: %s' % str(e)[:120]
                time.sleep(wait)
                wait = min(wait * 2, 300)

    def handle(self, env, ws, bot):
        # Acknowledge at once (Slack resends after 3 s), answer in a thread.
        if env.get('envelope_id'):
            ws.send(json.dumps({'envelope_id': env['envelope_id']}))
        if env.get('type') == 'disconnect':
            return False
        ev = (env.get('payload') or {}).get('event') or {}
        if env.get('type') != 'events_api' or ev.get('bot_id') or ev.get('subtype'):
            return True
        if not (ev.get('type') == 'app_mention' or (ev.get('type') == 'message' and ev.get('channel_type') == 'im')):
            return True
        key = ev.get('client_msg_id') or ev.get('ts')
        if key in self.seen:
            return True
        self.seen.add(key)
        text = re.sub(r'<@[A-Z0-9]+>', '', ev.get('text') or '').strip()
        if text:
            threading.Thread(target=self.reply, args=(bot, ev['channel'], ev.get('thread_ts') or ev['ts'], text),
                             daemon=True).start()
        return True

    def reply(self, bot, channel, ts, text):
        from .notify import chat
        try:
            call('chat.postMessage', bot, {'channel': channel, 'thread_ts': ts, 'text': 'Received. Checking the numbers.'})
            out = self.answer(text)
            call('chat.postMessage', bot, {'channel': channel, 'thread_ts': ts, 'text': chat(out)})
            self.say('Slack: answered "%s"' % text[:60])
        except (OSError, RuntimeError) as e:
            self.say('Slack: could not answer: %s' % str(e)[:120])
