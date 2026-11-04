# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# What the detectors look at, and how it is kept on disk.
#
# Everything is counted by the day the mail was SENT, not the day a bounce
# notice arrived.  PlusVibe's own daily chart books bounces on arrival, which
# smears one bad day across three and can make yesterday look better than
# the day before.  Cohorts by send day don't lie that way.

import json
import bisect
import sqlite3
import collections


# sent, replies, positives, bounces, sender_bounces
SENT, REPLIES, POSITIVES, BOUNCES, SENDER = range(5)

SCHEMA = '''
CREATE TABLE IF NOT EXISTS cells (
    day TEXT, domain TEXT, provider TEXT,
    sent INTEGER, replies INTEGER, positives INTEGER,
    bounces INTEGER, sender_bounces INTEGER,
    PRIMARY KEY (day, domain, provider));
CREATE TABLE IF NOT EXISTS kinds (
    day TEXT, domain TEXT, kind TEXT, n INTEGER,
    PRIMARY KEY (day, domain, kind));
CREATE TABLE IF NOT EXISTS samples (
    domain TEXT, kind TEXT, message TEXT,
    PRIMARY KEY (domain, kind));
CREATE TABLE IF NOT EXISTS domains (
    domain TEXT PRIMARY KEY, workspace TEXT, mailboxes INTEGER,
    google_health REAL, microsoft_health REAL,
    listed TEXT, auth TEXT);
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY, kind TEXT, scope TEXT, target TEXT,
    provider TEXT, opened TEXT, updated TEXT, status TEXT,
    confidence TEXT, title TEXT, evidence TEXT, plan TEXT,
    report TEXT);
CREATE TABLE IF NOT EXISTS bounces (
    day TEXT, domain TEXT, provider TEXT, sender INTEGER, message TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS blockers (
    day TEXT, domain TEXT, receiver TEXT, n INTEGER,
    PRIMARY KEY (day, domain, receiver));
CREATE TABLE IF NOT EXISTS links (
    day TEXT, domain TEXT, link TEXT, n INTEGER,
    PRIMARY KEY (day, domain, link));
CREATE TABLE IF NOT EXISTS accounts (
    domain TEXT, state TEXT, n INTEGER, PRIMARY KEY (domain, state));
CREATE TABLE IF NOT EXISTS hosts (domain TEXT PRIMARY KEY, host TEXT);
CREATE TABLE IF NOT EXISTS history (
    day TEXT, domain TEXT, google REAL, microsoft REAL,
    PRIMARY KEY (day, domain));
CREATE TABLE IF NOT EXISTS campaigns (id TEXT PRIMARY KEY, body TEXT);
CREATE TABLE IF NOT EXISTS fresh (day TEXT PRIMARY KEY, n INTEGER);
CREATE TABLE IF NOT EXISTS dns (domain TEXT PRIMARY KEY, body TEXT);
CREATE TABLE IF NOT EXISTS events (
    key TEXT PRIMARY KEY, ts TEXT, source TEXT, kind TEXT, domain TEXT,
    provider TEXT, detail TEXT, text TEXT);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
CREATE TABLE IF NOT EXISTS raw (key TEXT PRIMARY KEY, body TEXT);
'''


class Data:
    # All counts for one fleet.  Detectors read this and nothing else, so the
    # simulator and the PlusVibe collector only have to fill it the same way.

    def __init__(self):
        self.days = []
        self.cells = collections.defaultdict(dict)    # (dom, prov) -> {day: row}
        self.kinds = collections.defaultdict(collections.Counter)  # (dom, day)
        self.samples = {}                              # (dom, kind) -> text
        self.listed = {}                               # dom -> (list, since)
        self.auth = {}                                 # dom -> (missing, since, until)
        self.health = {}                               # dom -> (google, microsoft)
        self.workspace = {}
        self.mailboxes = {}
        self.bounces = []       # (day, dom, prov, sender, masked text), if kept
        self.blockers = collections.defaultdict(collections.Counter)  # (dom, day) -> receiver
        self.links = collections.defaultdict(collections.Counter)     # (dom, day) -> link domain
        self.accounts = {}      # dom -> {'active': n, 'down': n, 'warmup_off': n, ...}
        self.host = {}          # dom -> who hosts its mailboxes: google, microsoft, other
        self.history = collections.defaultdict(dict)  # dom -> {day: (google, microsoft)}
        self.campaigns = {}     # id -> what the sequencer says about it today
        self.fresh = {}         # day -> new leads contacted
        self.dns = {}           # dom -> what its DNS said, see dns.py
        self.raw = {}           # what a collector keeps to fetch only what changed next time

    def listed_on(self, dom, day):
        # The blocklist naming dom as of `day`.  Flags carry dates so that
        # replaying old days can't see what was only found out later.
        hit = self.listed.get(dom)
        return hit[0] if hit and hit[1] <= day else None

    def auth_missing(self, dom, day):
        hit = self.auth.get(dom)
        if hit and hit[1] <= day and not (hit[2] and day >= hit[2]):
            return hit[0]
        return None

    def domains(self):
        seen = {dom for dom, _ in self.cells}
        seen.update(self.workspace)
        return sorted(seen)

    def add_day(self, day):
        if day not in self.days:
            bisect.insort(self.days, day)

    def add(self, day, dom, prov, row):
        self.add_day(day)
        old = self.cells[dom, prov].get(day)
        if old:
            row = [a + b for a, b in zip(old, row)]
        self.cells[dom, prov][day] = list(row)

    def row(self, dom, prov, day):
        return self.cells.get((dom, prov), {}).get(day) or [0, 0, 0, 0, 0]

    def total(self, dom, prov, days):
        out = [0, 0, 0, 0, 0]
        series = self.cells.get((dom, prov), {})
        for day in days:
            r = series.get(day)
            if r:
                for i in range(5):
                    out[i] += r[i]
        return out

    def fleet(self, prov, days, doms=None):
        out = [0, 0, 0, 0, 0]
        for dom in doms or self.domains():
            r = self.total(dom, prov, days)
            for i in range(5):
                out[i] += r[i]
        return out

    def note_bounce(self, day, dom, text):
        # Everything the detectors want from one bounce's text.
        from .bounces import kind_of, receiver_of, link_named, REFUSALS
        k = kind_of(text, dom)
        self.kinds[dom, day][k] += 1
        if text:
            self.samples.setdefault((dom, k), text[:600])
        if k in REFUSALS:
            who = receiver_of(text)
            if who:
                self.blockers[dom, day][who] += 1
        link = link_named(text, dom) if k in ('surbl_ip', 'spamhaus_ip') else None
        if link:
            self.links[dom, day][link] += 1
        return k

    def window(self, end, n):
        # The n days ending at `end`, inclusive, oldest first.
        i = self.days.index(end) + 1
        return self.days[max(0, i - n):i]


def connect(path):
    # The watch, live mode and the dashboard share one file; wait for each
    # other rather than fail.
    db = sqlite3.connect(path, timeout=60)
    db.executescript(SCHEMA)
    return db


def save(data, path, meta=None):
    db = connect(path)
    with db:
        db.execute('DELETE FROM cells')
        db.execute('DELETE FROM kinds')
        db.execute('DELETE FROM samples')
        db.execute('DELETE FROM domains')
        for table in ('bounces', 'blockers', 'links', 'accounts', 'hosts',
                      'history', 'campaigns', 'fresh', 'dns', 'raw'):
            db.execute('DELETE FROM ' + table)
        db.executemany('INSERT INTO bounces VALUES (?,?,?,?,?)', data.bounces)
        for table, counts in (('blockers', data.blockers), ('links', data.links)):
            db.executemany('INSERT INTO %s VALUES (?,?,?,?)' % table,
                           [(day, dom, k, n) for (dom, day), c in counts.items()
                            for k, n in c.items()])
        db.executemany('INSERT INTO accounts VALUES (?,?,?)',
                       [(dom, k, n) for dom, c in data.accounts.items()
                        for k, n in c.items()])
        db.executemany('INSERT INTO hosts VALUES (?,?)', data.host.items())
        db.executemany('INSERT INTO history VALUES (?,?,?,?)',
                       [(day, dom, g, m) for dom, h in data.history.items()
                        for day, (g, m) in h.items()])
        db.executemany('INSERT INTO campaigns VALUES (?,?)',
                       [(k, json.dumps(v)) for k, v in data.campaigns.items()])
        db.executemany('INSERT INTO fresh VALUES (?,?)', data.fresh.items())
        db.executemany('INSERT INTO dns VALUES (?,?)',
                       [(k, json.dumps(v)) for k, v in data.dns.items()])
        db.executemany('INSERT INTO raw VALUES (?,?)',
                       [(k, json.dumps(v)) for k, v in data.raw.items()])
        for (dom, prov), series in data.cells.items():
            db.executemany('INSERT INTO cells VALUES (?,?,?,?,?,?,?,?)',
                           [(day, dom, prov) + tuple(r)
                            for day, r in series.items()])
        db.executemany('INSERT INTO kinds VALUES (?,?,?,?)',
                       [(day, dom, k, n)
                        for (dom, day), c in data.kinds.items()
                        for k, n in c.items()])
        db.executemany('INSERT INTO samples VALUES (?,?,?)',
                       [(dom, k, text)
                        for (dom, k), text in data.samples.items()])
        for dom in data.domains():
            g, m = data.health.get(dom, (None, None))
            db.execute('INSERT INTO domains VALUES (?,?,?,?,?,?,?)',
                       (dom, data.workspace.get(dom), data.mailboxes.get(dom),
                        g, m, json.dumps(data.listed.get(dom)),
                        json.dumps(data.auth.get(dom))))
        for key, value in (meta or {}).items():
            db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',
                       (key, json.dumps(value)))
    db.close()


def load(path):
    db = connect(path)
    data = Data()
    days = set()
    for day, dom, prov, *r in db.execute('SELECT * FROM cells'):
        data.cells[dom, prov][day] = list(r)
        days.add(day)
    data.days = sorted(days)
    for day, dom, k, n in db.execute('SELECT * FROM kinds'):
        data.kinds[dom, day][k] = n
    for dom, k, text in db.execute('SELECT * FROM samples'):
        data.samples[dom, k] = text
    data.bounces = db.execute('SELECT * FROM bounces').fetchall()
    for table, counts in (('blockers', data.blockers), ('links', data.links)):
        for day, dom, k, n in db.execute('SELECT * FROM ' + table):
            counts[dom, day][k] = n
    for dom, k, n in db.execute('SELECT * FROM accounts'):
        data.accounts.setdefault(dom, {})[k] = n
    data.host = dict(db.execute('SELECT * FROM hosts'))
    for day, dom, g, m in db.execute('SELECT * FROM history'):
        data.history[dom][day] = (g, m)
    data.campaigns = {k: json.loads(v) for k, v in db.execute('SELECT * FROM campaigns')}
    data.fresh = dict(db.execute('SELECT * FROM fresh'))
    data.dns = {k: json.loads(v) for k, v in db.execute('SELECT * FROM dns')}
    data.raw = {k: json.loads(v) for k, v in db.execute('SELECT * FROM raw')}
    for dom, ws, boxes, g, m, listed, auth in db.execute('SELECT * FROM domains'):
        data.workspace[dom] = ws
        data.mailboxes[dom] = boxes
        if g is not None or m is not None:
            data.health[dom] = (g, m)
        listed, auth = json.loads(listed or 'null'), json.loads(auth or 'null')
        if listed:
            data.listed[dom] = tuple(listed)
        if auth:
            data.auth[dom] = tuple(auth)
    db.close()
    return data


def reclassify(data):
    # Name every kept bounce again with today's rules.  The kinds counted
    # when the data was collected are thrown away.
    if not data.bounces:
        return data
    data.kinds.clear()
    data.samples.clear()
    data.blockers.clear()
    data.links.clear()
    for day, dom, prov, sender, text in data.bounces:
        data.note_bounce(day, dom, text)
    return data


HISTORY_DAYS = 90


def carry(old, new):
    # What only builds up over time (warmup scores, DNS checks) comes over
    # from the last collection into the new one.  Warmup history is kept
    # for 90 days.
    import datetime
    cut = (datetime.date.today() - datetime.timedelta(days=HISTORY_DAYS)).isoformat()
    for dom, h in old.history.items():
        for day, v in h.items():
            if day >= cut:
                new.history[dom].setdefault(day, v)
    for dom, v in old.dns.items():
        new.dns.setdefault(dom, v)
    # A listing the DNS check found stays until the next DNS check says
    # otherwise; collections in between must not drop it.
    for dom, v in old.listed.items():
        if (new.dns.get(dom) or {}).get('surbl') != []:
            new.listed.setdefault(dom, v)
    return new


def save_meta(path, values):
    db = connect(path)
    with db:
        for key, value in values.items():
            db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, json.dumps(value)))
    db.close()


def get_meta(path, key, default=None):
    db = connect(path)
    row = db.execute('SELECT value FROM meta WHERE key = ?', (key,)).fetchone()
    db.close()
    return json.loads(row[0]) if row else default


def rate(n, d):
    return n / d if d else 0.0
