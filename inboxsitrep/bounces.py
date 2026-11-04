# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# Name the few kinds of bounce text the detectors care about.  For a full
# diagnosis of every bounce (whose problem it is, what to do), use
# bouncesitrep; this only needs to spot the signatures below.

import re


KINDS = [
    # Gmail refusing on the sending domain's reputation.
    ('gmail_reputation', r'low reputation of the sending domain'),
    ('surbl', r'surbl'),
    ('spamhaus', r'spamhaus'),
    # Apple's policy block: every iCloud address, whatever domain sends.
    ('apple_cs01', r'\[cs01\]|ht204137'),
    # Receivers deferring or refusing over their users' complaints: Yahoo
    # and AOL's TSS04, smaller hosts' "too many complaints from domain".
    ('complaints', r'\[?tss0[1-9]\]?|user complaints|too many complaints'),
    # Gmail slowing a sender down before it blocks it: 421 4.7.28 "unusual
    # rate of unsolicited mail".  The last warning before the reputation
    # block, and the cheapest one to act on.
    ('throttle', r'4\.7\.28|unusual rate of unsolicited|rate[- ]limit|'
                 r'too many (messages|connections|emails) from|sending rate'),
    # Authentication: Gmail's 5.7.26/5.7.27, and Microsoft's 5.7.515, which
    # since November 2025 refuses consumer mail from any domain sending them
    # 5,000 a day without passing, aligned SPF, DKIM and DMARC.
    ('auth', r'5\.7\.2[67]|5\.7\.515|unauthenticated|dmarc polic|does not pass dmarc|'
             r"required authentication level|spf (check )?fail|dkim (check )?fail"),
    # Microsoft stopping the SENDER's own Microsoft 365 tenant: nothing
    # leaves, whoever it is addressed to.  Found on a real fleet: 3,601 of
    # these over a week on 13 domains, all "terms of service".
    ('tenant_block', r"outbound email has been blocked|5\.7\.237|5\.7\.705|5\.7\.750|"
                     r'tenant has exceeded'),
    # A big ISP refusing on reputation (Spectrum's AUP codes, "poor
    # reputation of a domain used in message transfer").
    ('isp_block', r'spectrum\.net|aup#|poor reputation of a domain|charter\.net'),
    # Corporate gateways refusing on content or reputation.  Before the
    # Microsoft codes: gateway message ids are random enough to contain one.
    ('gateway_block', r'mimecast|proofpoint|barracuda|messagelabs|ironport|'
                      r'sophos|trend ?micro|forcepoint|cloudmark'),
    # Microsoft's reputation block wears a "mailbox unavailable" mask; the
    # S-code gives it away.  5.7.708 and 5.7.511 are its plainer cousins.
    ('microsoft_block', r'\bs(2017062302|314\d)\b|5\.7\.708|5\.7\.511|banned sender|'
                        r'bad outbound sender'),
    # Mailboxes that only take mail from inside their company.  They exist;
    # they will never take yours.  Suppress, don't retry.
    ('restricted', r'restrictedtorecipientspermission|sendernotauthenticatedfor|'
                   r'external senders aren|unifiedgroupagent'),
    ('ip_block', r'your ip has been blocked|mbrbl'),
    ('mailbox_full', r'5\.2\.2|over ?quota|quotaexceeded|mailbox (is )?full|'
                     r'insufficient storage|out of storage'),
    ('user_unknown', r'5\.1\.1|5\.1\.10|5\.1\.351|does not exist|user unknown|'
                     r'no such user|unknown (user|recipient)|recipient not found|'
                     r'address rejected|invalid recipient|mailbox (is )?disabled|'
                     r'account (is )?disabled|554\.30|recipientnotfound|'
                     r'mailbox not found|mailbox unavailable'),
    ('loop', r'hop count exceeded|mail loop'),
    # A spam filter judging the message, not the sender: the copy, its
    # links or its attachments.  Fixed in the copy, not by moving domains.
    ('spam_filter', r'classified as spam|detected (your message )?as spam|'
                    r'high probability of spam|spam content|looks like spam|'
                    r'considered (as )?spam|content rejected'),
    ('expired', r'message expired|5\.4\.300|4\.4\.7'),
    ('policy', r'5\.7\.1\b|access to submit messages|rejected due to .*polic|'
               r'rejectmessage|organi[sz]ation policy'),
]
COMPILED = [(k, re.compile(p, re.I)) for k, p in KINDS]
# Who refused, when the bounce says.  One receiver refusing mail from many
# of your domains is one problem with that receiver: on a real fleet,
# Spectrum refused 2,557 messages from dozens of domains in three weeks.
RECEIVERS = [
    ('Spectrum', r'spectrum\.net|charter\.net|aup#|\brr\.com\b'),
    ('Mimecast', r'mimecast'),
    ('Proofpoint', r'proofpoint|pphosted'),
    ('Barracuda', r'barracuda'),
    ('Sophos', r'sophos|xgemail'),
    ('Trend Micro', r'trend ?micro|\btmes\b'),
    ('Symantec', r'messagelabs|symantec'),
    ('Cisco', r'ironport'),
    ('Forcepoint', r'forcepoint|mailcontrol'),
    ('Cloudmark', r'cloudmark'),
    ('Yahoo', r'yahoo|\baol\b|tss0[1-9]'),
    ('AT&T', r'att\.net|sbcglobal|bellsouth'),
    ('Comcast', r'comcast'),
    ('Cox', r'cox\.net'),
]
RECEIVERS_RX = [(n, re.compile(p, re.I)) for n, p in RECEIVERS]
# Refusals of the sender rather than of the address: when a sequencer
# doesn't say which side a bounce was on, the text does.
SENDER_KINDS = ('gmail_reputation', 'surbl', 'spamhaus', 'auth', 'tenant_block',
                'microsoft_block', 'gateway_block', 'isp_block', 'apple_cs01', 'policy',
                'ip_block', 'throttle', 'complaints', 'spam_filter')

# The kinds that are one receiver's verdict on the sender, worth naming.
REFUSALS = ('isp_block', 'gateway_block', 'complaints', 'spam_filter', 'policy',
            'ip_block', 'spamhaus_ip', 'surbl_ip')
URL = re.compile(r'url in (?:this|the) (?:e-?mail|message) ?\(\s*([\w\s.-]+?)\s*\)', re.I)
EMAIL = re.compile(r'[\w.+-]+@[\w-]+(\.[\w-]+)+')


def kind_of(text, dom=None):
    # With the sending domain given, a blocklist bounce only counts against
    # it when it names it.  Lists of IPs (zen, PBL, a shared Microsoft
    # outbound address) are someone else's problem: those become *_ip.
    text = text or ''
    for k, rx in COMPILED:
        if rx.search(text):
            if k in ('surbl', 'spamhaus') and dom:
                squashed = text.lower().replace(' ', '')
                if dom.lower() not in squashed:
                    return k + '_ip'
            return k
    return 'other'


def receiver_of(text):
    for name, rx in RECEIVERS_RX:
        if rx.search(text or ''):
            return name
    return None


def link_named(text, dom=None):
    # The domain a URL blocklist bounce names, when it isn't the sender's:
    # a link or tracking domain in the copy, shared by every domain that
    # sends it.
    for got in URL.findall(text or ''):
        got = got.replace(' ', '').lower().strip('.')
        if got and got != (dom or '').lower():
            return got
    return None


def mask(text):
    # Bounce text quotes the recipient's address; keep the reason, not them.
    return EMAIL.sub('<email>', text or '')
