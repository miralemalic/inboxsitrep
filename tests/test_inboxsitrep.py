# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

import os
import sys
import json
import unittest
from unittest import mock
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from inboxsitrep import bounces, detect, incidents, plan, plusvibe, score, sim
from inboxsitrep import data as store
from inboxsitrep import app as app_module, notify, watch

# Nothing a test does may read or land in the real settings, keys, data or
# reports.
SCRATCH = tempfile.TemporaryDirectory()
notify.LOG = os.path.join(SCRATCH.name, 'alerts.log')
notify.KEYS = os.path.join(SCRATCH.name, 'keys')
watch.SITREPS = os.path.join(SCRATCH.name, 'sitreps')
app_module.HOME = os.path.join(SCRATCH.name, 'config')
app_module.DATA = os.path.join(SCRATCH.name, 'data')
from inboxsitrep.data import Data, SENT, SENDER


def run_fleet(fleet, days):
    for _ in range(days):
        fleet.step()
    return fleet


class Bounces(unittest.TestCase):

    def test_kinds_from_real_wording(self):
        cases = {
            'gmail_reputation': '550 5.7.1 Gmail has detected that this message is '
                                'likely suspicious due to the very low reputation '
                                'of the sending domain',
            'surbl': '550 5.7.1 A URL in this email (x.com) is listed on '
                     'https://www.surbl.org/lists',
            'spamhaus': '550 An URL in this email ( x . com ) is listed by Spamhaus DBL',
            'apple_cs01': '550 5.7.1 [CS01] Message rejected due to local policy',
            'auth': '550-5.7.26 Gmail requires all senders to authenticate',
            'gateway_block': '554 Email rejected due to security policies - '
                             'https://community.mimecast.com/docs/DOC-1369#554',
            'microsoft_block': 'Requested action not taken: mailbox unavailable '
                               '(S2017062302). [DM3PEPF000265BE.namprd04]',
            'user_unknown': '550 5.1.1 The email account that you tried to reach '
                            'does not exist.',
            'mailbox_full': '552 5.2.2 The recipient\'s mailbox is full',
            'expired': '5.4.300 Message expired -> 451 4.7.5 try again later',
        }
        for kind, text in cases.items():
            self.assertEqual(bounces.kind_of(text), kind, text)
        # Microsoft's high-volume rule, rejecting since November 2025.
        self.assertEqual(bounces.kind_of("550 5.7.515 Access denied, sending domain acme.com doesn't meet "
                                         "the required authentication level"), 'auth')

    def test_gateway_ids_do_not_look_like_microsoft_codes(self):
        text = '554 rejected by mimecast [xs3141Kq.us653]'
        self.assertEqual(bounces.kind_of(text), 'gateway_block')

    def test_blocklist_counts_only_when_it_names_the_domain(self):
        named = ('550 An URL in this email ( growthmailai . com ) is listed by '
                 'Spamhaus DBL')
        ip = '554 Service unavailable; Client host [40.107.1.2] blocked using zen.spamhaus.org'
        self.assertEqual(bounces.kind_of(named, 'growthmailai.com'), 'spamhaus')
        self.assertEqual(bounces.kind_of(ip, 'growthmailai.com'), 'spamhaus_ip')

    def test_recipient_addresses_are_masked(self):
        text = '550 5.1.1 <jane.doe@acme.com>: user unknown'
        self.assertEqual(bounces.mask(text), '550 5.1.1 <<email>>: user unknown')


class Families(unittest.TestCase):

    def test_lookalikes_group_and_strangers_do_not(self):
        doms = ['growthmailads.com', 'growthmailpro.com', 'growthmailscale.co',
                'hvproptech.com', 'example.org']
        fams = detect.families(doms)
        self.assertEqual(fams['growthmailads.com'], 'growthmail')
        self.assertEqual(fams['growthmailscale.co'], 'growthmail')
        self.assertNotIn('hvproptech.com', fams)
        self.assertNotIn('example.org', fams)


class Statistics(unittest.TestCase):

    def test_binomial_tail(self):
        self.assertAlmostEqual(detect.below(0, 100, .05), .95 ** 100, places=6)
        self.assertEqual(detect.below(5, 0, .5), 1.0)
        self.assertAlmostEqual(detect.below(100, 100, .3), 1.0, places=9)


class Detectors(unittest.TestCase):

    def test_dead_domain_is_seen_the_day_gmail_refuses_it(self):
        fleet = sim.Fleet(3)
        dom = fleet.doms[5]
        fleet.kill(dom, 20)
        run_fleet(fleet, 21)
        day = fleet.data.days[-1]
        found = [f for f in detect.google_blocks(fleet.data, day) if f['target'] == dom]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['kind'], 'rejecting')
        # Microsoft keeps taking its mail
        self.assertEqual(fleet.data.row(dom, 'microsoft', day)[SENDER], 0)

    def test_dead_domain_turns_burned_after_three_days(self):
        fleet = sim.Fleet(3)
        dom = fleet.doms[5]
        fleet.kill(dom, 20)
        run_fleet(fleet, 23)
        found = [f for f in detect.google_blocks(fleet.data, fleet.data.days[-1])
                 if f['target'] == dom]
        self.assertEqual(found[0]['kind'], 'burned')

    def test_episode_is_called_an_episode_once_it_clears(self):
        fleet = sim.Fleet(4)
        dom = fleet.doms[9]
        fleet.episode(dom, 20, 2, .7)
        run_fleet(fleet, 30)
        found = [i for i in incidents.track(fleet.data) if i['target'] == dom]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['kind'], 'episode')
        self.assertEqual(found[0]['status'], 'cleared')

    def test_quiet_spam_is_caught_though_nothing_bounces(self):
        fleet = sim.Fleet(5)
        stem = sorted(set(fleet.family.values()))[0]
        doms = sorted(d for d, s in fleet.family.items() if s == stem)
        fleet.folder(doms, 25, severity=.15)
        run_fleet(fleet, 32)
        found = detect.quiet_spam(fleet.data, fleet.data.days[-1],
                                  detect.families(fleet.doms))
        self.assertTrue(any(f['target'] == stem for f in found))

    def test_apple_block_is_one_fleet_incident(self):
        fleet = sim.Fleet(6)
        fleet.apple_block(10)
        run_fleet(fleet, 12)
        found = detect.apple(fleet.data, fleet.data.days[-1])
        self.assertEqual([f['target'] for f in found], ['apple'])

    def test_bad_list_names_the_list_not_the_domains(self):
        fleet = sim.Fleet(7)
        fleet.bad_list('x', fleet.doms[:6], 10, 10, extra=.06)
        run_fleet(fleet, 14)
        found = detect.bad_lists(fleet.data, fleet.data.days[-1])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['target'], 'bad list')
        self.assertTrue(set(found[0]['numbers']['domains']) <= set(fleet.doms[:6]))

    def test_healthy_fleets_raise_nothing_that_stops_mail(self):
        for seed in (1, 2, 3):
            fleet, acted = score.run(seed, 'inboxsitrep', 60, scenario='clean')
            stopped = [k for k in acted if k[0] in ('stop', 'pause', 'retire')]
            self.assertEqual(stopped, [], seed)

    def test_tenant_block_is_seen_even_after_the_mailboxes_are_gone(self):
        fleet = run_fleet(sim.Fleet(9), 10)
        day = fleet.data.days[-1]
        fleet.data.kinds['gone.com', day]['tenant_block'] += 40
        found = detect.tenant_blocks(fleet.data, day)
        self.assertEqual(found[0]['numbers']['gone'], ['gone.com'])
        self.assertEqual(found[0]['numbers']['domains'], [])

    def test_tenant_blocks_are_not_gmail_refusing(self):
        fleet = run_fleet(sim.Fleet(9), 10)
        day, dom = fleet.data.days[-1], fleet.doms[0]
        row = fleet.data.row(dom, 'google', day)
        row[SENDER] = row[SENT]
        fleet.data.cells[dom, 'google'][day] = row
        fleet.data.kinds[dom, day]['tenant_block'] = row[SENT]
        self.assertEqual(detect.gmail_rate(fleet.data, dom, day), 0)

    def test_flags_carry_dates(self):
        d = Data()
        d.listed['x.com'] = ('surbl', '2026-09-10')
        self.assertIsNone(d.listed_on('x.com', '2026-09-09'))
        self.assertEqual(d.listed_on('x.com', '2026-09-10'), 'surbl')
        d.auth['y.com'] = ('dkim', '2026-09-01', '2026-09-05')
        self.assertEqual(d.auth_missing('y.com', '2026-09-03'), 'dkim')
        self.assertIsNone(d.auth_missing('y.com', '2026-09-05'))


class Plans(unittest.TestCase):

    def test_gmail_trouble_stops_gmail_not_the_domain(self):
        op = plan.Operator()
        f = detect.finding('rejecting', 'domain', 'a.com', '2026-09-01', 'medium',
                           't', rate=.8, days=1, since='2026-09-01')
        changes = op.step([f], Data())
        self.assertEqual([(c['op'], c.get('provider')) for c in changes],
                         [('stop', 'google')])

    def test_cleared_domains_come_back(self):
        op = plan.Operator()
        f = detect.finding('rejecting', 'domain', 'a.com', 'd', 'medium', 't',
                           rate=.8, days=1, since='d')
        op.step([f], Data())
        back = op.step([detect.finding('cleared', 'domain', 'a.com', 'd', 'medium',
                                       't', rate=0)], Data())
        self.assertEqual(back[0]['op'], 'resume')

    def test_undo_reverses_in_order(self):
        changes = [plan.change('stop', ['a.com'], 'x', 'google'),
                   plan.change('pause', ['b.com'], 'y')]
        self.assertEqual([c['op'] for c in plan.undo(changes)], ['unpause', 'resume'])


class Collector(unittest.TestCase):

    def test_balance_matches_both_margins(self):
        guess = {('a', 'google'): 50, ('a', 'microsoft'): 50,
                 ('b', 'google'): 10, ('b', 'microsoft'): 90}
        split = plusvibe.balance(guess, {'a': 100, 'b': 100},
                                 {'google': 120, 'microsoft': 80})
        self.assertAlmostEqual(split['a', 'google'] + split['b', 'google'], 120, 1)
        self.assertAlmostEqual(split['a', 'google'] + split['a', 'microsoft'], 100, 1)

    def test_replies_and_bounces_of_deleted_campaigns_come_from_the_mailboxes(self):
        # gone.com's campaigns were deleted with their leads; kept.com's leads
        # are all there and stay the record, whatever the mailboxes say.
        totals = {('gone.com', '2026-10-06'): [300, 6, 2, 13, 1], ('kept.com', '2026-10-06'): [300, 6, 2, 13, 1]}
        leads = {('kept.com', 'google', '2026-10-06'): [4, 1, 9, 0], ('kept.com', 'microsoft', '2026-10-06'): [1, 1, 3, 1]}
        by_prov = {('google', '2026-10-06'): 300, ('microsoft', '2026-10-06'): 300}
        data = Data()
        plusvibe.build(data, totals, by_prov, leads, lambda dom: {'google': .5, 'microsoft': .5})
        gone = [sum(data.total('gone.com', p, ['2026-10-06'])[k] for p in ('google', 'microsoft')) for k in range(5)]
        kept = [sum(data.total('kept.com', p, ['2026-10-06'])[k] for p in ('google', 'microsoft')) for k in range(5)]
        self.assertEqual(gone, [300, 6, 2, 13, 1])
        self.assertEqual(kept, [300, 5, 2, 12, 1])

    def test_apple_addresses_are_their_own_provider(self):
        self.assertEqual(plusvibe.provider_of('REGULAR_ACCOUNT', 'x@icloud.com'), 'apple')
        self.assertEqual(plusvibe.provider_of('GOOGLE_WORKSPACE', 'x@acme.com'), 'google')

    def test_client_can_only_read(self):
        api = plusvibe.Client('k', 'w')
        self.assertTrue(hasattr(api, 'get'))
        for verb in ('post', 'put', 'patch', 'delete'):
            self.assertFalse(hasattr(api, verb))


class Storage(unittest.TestCase):

    def test_round_trip(self):
        fleet = run_fleet(sim.standard(sim.Fleet(8)), 40)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'f.db')
            store.save(fleet.data, path)
            back = store.load(path)
        self.assertEqual(back.days, fleet.data.days)
        dom = fleet.doms[0]
        self.assertEqual(back.total(dom, 'google', back.days),
                         fleet.data.total(dom, 'google', fleet.data.days))
        self.assertEqual(back.listed, fleet.data.listed)


class Import(unittest.TestCase):

    def test_an_export_comes_back_with_the_same_dead_domain(self):
        from inboxsitrep import csvimport
        fleet = sim.Fleet(10, families=3, size=(4, 6))
        dom = fleet.doms[2]
        fleet.kill(dom, 18, listed_after=1)
        run_fleet(fleet, 24)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'export.csv')
            sim.export(fleet, path)
            data, cols = csvimport.read(path, lookup=False)
        self.assertEqual(cols['sender'], 'email_acc_name')
        found = {(i['kind'], i['target']) for i in incidents.track(data)}
        self.assertIn(('burned', dom), found)
        self.assertIn(('listed', dom), found)

    def test_columns_are_found_by_common_names(self):
        from inboxsitrep import csvimport
        cols, missing = csvimport.find_columns(
            ['Lead Email', 'Sender Email', 'Lead Status', 'Last Contacted', 'Bounce Reason'])
        self.assertEqual(missing, [])
        self.assertEqual(cols['bounce'], 'Bounce Reason')


def quiet_fleet(days=21, doms=('a.com', 'b.com', 'c.com', 'd.com', 'e.com', 'f.com'),
                per_day=300, start='2026-10-01'):
    import datetime
    data = Data()
    first = datetime.date.fromisoformat(start)
    for t in range(days):
        day = (first + datetime.timedelta(days=t)).isoformat()
        for d in doms:
            data.add(day, d, 'google', [per_day // 2, 3, 1, 2, 0])
            data.add(day, d, 'microsoft', [per_day // 2, 4, 1, 2, 0])
            data.mailboxes[d] = 100
    return data


class NewThreats(unittest.TestCase):

    def test_complaints_throttle_and_content_from_real_wording(self):
        cases = {
            'complaints': '421 4.7.0 [TSS04] Messages from 40.107.1.2 temporarily '
                          'deferred due to unexpected volume or user complaints',
            'throttle': '421-4.7.28 Gmail has detected an unusual rate of unsolicited '
                        'mail originating from your domain',
            'spam_filter': '550 5.7.1 The mail server detected your message as spam '
                           'and has prevented delivery.',
        }
        for kind, text in cases.items():
            self.assertEqual(bounces.kind_of(text), kind, text)
        text = 'Recipient address rejected: Too many complaints from domain x.com'
        self.assertEqual(bounces.kind_of(text, 'x.com'), 'complaints')

    def test_receivers_are_named_and_rejectmessage_is_not_trend_micro(self):
        self.assertEqual(bounces.receiver_of('<x> sender rejected AUP#In-1310 '
                                             'https://www.spectrum.net/support'), 'Spectrum')
        self.assertEqual(bounces.receiver_of('5.7.352 Mimecast detected message as spam'), 'Mimecast')
        self.assertIsNone(bounces.receiver_of('TRANSPORT.RULES.RejectMessage; the '
                                              'message was rejected by organization policy'))

    def test_link_domain_is_named_only_when_it_is_not_the_sender(self):
        text = '550 A URL in this email ( trk-mail . com ) is listed by Spamhaus DBL'
        self.assertEqual(bounces.link_named(text, 'acme.com'), 'trk-mail.com')
        self.assertIsNone(bounces.link_named(text, 'trk-mail.com'))

    def test_one_receiver_refusing_many_domains_is_one_incident(self):
        data = quiet_fleet()
        day = data.days[-1]
        for d in ('a.com', 'b.com', 'c.com', 'd.com', 'e.com'):
            for _ in range(12):
                data.note_bounce(day, d, '<x> sender rejected AUP#In-1310 spectrum.net')
        found = [f for f in detect.run(data, day) if f['kind'] == 'receiver']
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['target'], 'Spectrum')
        self.assertEqual(len(found[0]['numbers']['domains']), 5)

    def test_spam_filter_refusals_across_the_fleet_point_at_the_copy(self):
        data = quiet_fleet()
        day = data.days[-1]
        for d in ('a.com', 'b.com', 'c.com', 'd.com', 'e.com'):
            for _ in range(8):
                data.note_bounce(day, d, '550 High probability of spam')
        kinds = {f['kind'] for f in detect.run(data, day)}
        self.assertIn('content', kinds)

    def test_a_listed_link_is_not_mistaken_for_listed_domains(self):
        fleet = sim.Fleet(5)
        fleet.list_link(20, share=.12)
        run_fleet(fleet, 26)
        found = incidents.track(fleet.data)
        kinds = {i['kind'] for i in found}
        self.assertIn('link_listed', kinds)
        self.assertNotIn('listed', kinds)
        self.assertFalse([c for i in found for c in i['plan'] if c['op'] == 'pause'])

    def test_microsoft_block_stops_microsoft_and_nothing_else(self):
        fleet = sim.Fleet(6)
        dom = fleet.doms[4]
        fleet.block_microsoft(dom, 20, share=.6)
        run_fleet(fleet, 25)
        inc = [i for i in incidents.track(fleet.data) if i['kind'] == 'ms_block']
        self.assertEqual([i['target'] for i in inc], [dom])
        self.assertEqual({(c['op'], c.get('provider')) for c in inc[0]['plan']},
                         {('stop', 'microsoft')})

    def test_gmail_throttle_slowed_in_time_never_becomes_a_block(self):
        fleet = sim.Fleet(7)
        dom = fleet.doms[3]
        fleet.throttle(dom, 20, share=.15)
        op = plan.Operator()
        fams = detect.families(fleet.doms)
        for t in range(30):
            day = fleet.step()
            for c in op.step(detect.run(fleet.data, day, fams, op.held()), fleet.data):
                fleet.apply(c)
        self.assertIsNone(fleet.fail[dom].get('dead'))
        self.assertIn(dom, fleet.slowed)

    def test_falling_warmup_score_is_reported(self):
        fleet = sim.Fleet(8)
        doms = fleet.doms[:2]
        fleet.folder(doms, 15, severity=.1)
        run_fleet(fleet, 24)
        found = {(f['kind'], f['target']) for f in detect.run(fleet.data, fleet.data.days[-1], {})}
        for d in doms:
            self.assertIn(('warmup_drop', d), found)

    def test_disconnected_mailboxes_are_reported_on_the_last_day_only(self):
        fleet = sim.Fleet(9)
        fleet.disconnect(fleet.doms[:2], 10)
        run_fleet(fleet, 15)
        last = fleet.data.days[-1]
        self.assertIn('mailboxes', {f['kind'] for f in detect.run(fleet.data, last)})
        self.assertNotIn('mailboxes', {f['kind'] for f in detect.run(fleet.data, fleet.data.days[-3])})

    def test_runway_warns_before_new_leads_run_out(self):
        data = quiet_fleet()
        day = data.days[-1]
        data.campaigns = {'1': {'name': 'Roofers TX', 'status': 'ACTIVE',
                                'lead_count': 10000, 'lead_contacted_count': 8200},
                          '2': {'name': 'Old', 'status': 'COMPLETED',
                                'lead_count': 5000, 'lead_contacted_count': 0}}
        data.fresh = {d: 600 for d in data.days}
        found = [f for f in detect.run(data, day) if f['kind'] == 'runway']
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['numbers']['left'], 1800)
        data.fresh = {d: 100 for d in data.days}
        self.assertFalse([f for f in detect.run(data, day) if f['kind'] == 'runway'])

    def test_bounce_guard_pauses_are_reported(self):
        data = quiet_fleet()
        day = data.days[-1]
        data.campaigns = {'1': {'name': 'A', 'status': 'PAUSED',
                                'last_paused_at_bounced': day + 'T18:09:24Z'}}
        found = [f for f in detect.run(data, day) if f['kind'] == 'bounce_guard']
        self.assertEqual(found[0]['numbers']['paused'], ['A'])

    def test_weekends_are_not_stalls_but_a_quiet_domain_is(self):
        import datetime
        data = quiet_fleet(days=28, start='2026-10-01')
        for (dom, prov), series in data.cells.items():
            for day in series:
                if datetime.date.fromisoformat(day).weekday() >= 5:
                    series[day] = [0, 0, 0, 0, 0]
        last = [d for d in data.days if datetime.date.fromisoformat(d).weekday() < 5][-1]
        self.assertFalse([f for f in detect.run(data, last) if f['kind'] == 'stalled'])
        prev = data.days[data.days.index(last) - 1]
        for day in (prev, last):
            for prov in ('google', 'microsoft'):
                data.cells['c.com', prov][day] = [0, 0, 0, 0, 0]
        data.days = [d for d in data.days if d <= last]
        found = [f for f in detect.run(data, last) if f['kind'] == 'stalled']
        self.assertEqual(found[0]['target'], 'c.com')


class Dns(unittest.TestCase):
    # A fake DNS, so the tests never touch the network.
    ZONE = {
        ('acme.com', 'MX'): [(15, '10 park-mx.above.com.')],
        ('acme.com', 'TXT'): [(16, '"v=spf1 ip6:fdcf:abda:4154::/48 -all"')],
        ('good.com', 'MX'): [(15, '1 aspmx.l.google.com.')],
        ('good.com', 'TXT'): [(16, '"v=spf1 include:_spf.google.com ~all"')],
        ('_dmarc.good.com', 'TXT'): [(16, '"v=DMARC1; p=none"')],
        ('google._domainkey.good.com', 'TXT'): [(16, '"v=DKIM1; k=rsa; p=MII"')],
        ('_spf.google.com', 'TXT'): [(16, '"v=spf1 include:_netblocks.google.com ~all"')],
        ('ms.com', 'MX'): [(15, '0 ms-com.mail.protection.outlook.com.')],
        ('ms.com', 'TXT'): [(16, '"v=spf1 include:a.com include:b.com include:c.com '
                                 'include:d.com include:e.com include:f.com a mx '
                                 'include:g.com include:h.com include:i.com -all"')],
    }

    def setUp(self):
        from inboxsitrep import dns
        self.dns = dns
        self.real = (dns.query, dns.rdap, dns.redirect)
        dns.query = lambda name, kind, doh=None: self.ZONE.get((name, kind), [])
        dns.rdap = lambda dom: {'created': '2026-08-14', 'expires': '2027-08-14', 'registrar': 'Namecheap'}
        dns.redirect = lambda dom: 'main.com'

    def tearDown(self):
        self.dns.query, self.dns.rdap, self.dns.redirect = self.real

    def test_parked_domain_with_a_parking_spf_is_caught(self):
        r = self.dns.check('acme.com')
        self.assertIn('parked', r['issues'])
        self.assertIn('no_dmarc', r['issues'])

    def test_a_domain_set_up_properly_has_no_issues(self):
        self.assertEqual(self.dns.check('good.com')['issues'], [])

    def test_spf_without_the_sending_provider_and_too_many_lookups(self):
        r = self.dns.check('ms.com')
        self.assertIn('spf_misses_host', r['issues'])
        self.assertIn('spf_lookups', r['issues'])
        self.assertIn('no_dkim', r['issues'])


class Tools(unittest.TestCase):

    def test_every_tool_answers_on_a_fleet_with_every_kind_of_incident(self):
        from inboxsitrep import mcp, agent
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'f.db')
            fleet = run_fleet(sim.standard(sim.Fleet(4)), 45)
            store.save(fleet.data, path, meta={'source': 'simulated', 'workspace': 'Demo'})
            found = incidents.track(fleet.data)
            incidents.save(found, path)
            f = mcp.Fleets([path])
            self.assertIn('SITREP: Demo', mcp.t_sitrep(f))
            for i in mcp.t_incidents(f, status='all'):
                self.assertIn('report', mcp.t_incident(f, i['id']))
            self.assertIn('last_14_days', mcp.t_domain(f, fleet.doms[0]))
            for what in ('listed', 'dns', 'down', 'refused'):
                self.assertIn('count', mcp.t_find(f, what))
            self.assertIn('Details of open incidents', agent.evidence(fleet.data, found, 'Demo'))


class Live(unittest.TestCase):

    def test_webhooks_from_three_sequencers_become_the_same_event(self):
        from inboxsitrep import live
        pv = {'webhook_event': 'BOUNCED_EMAIL', 'sender_email': 'ann@acme.com',
              'lead_email': 'bob@gmail.com', 'bounce_type': 'SENDER',
              'msg': '550 5.7.1 very low reputation of the sending domain',
              'date': '2026-10-30T14:50:56.668+00:00'}
        ins = {'event_type': 'email_bounced', 'email_account': 'ann@acme.com',
               'lead_email': 'bob@gmail.com', 'timestamp': '2026-10-30T14:50:56Z'}
        sl = {'event_type': 'EMAIL_BOUNCED', 'from_email': 'ann@acme.com',
              'lead': {'email': 'bob@outlook.com'}, 'timestamp': '2026-10-30T14:50:56Z'}
        a, b, c = (live.normalize(x) for x in (pv, ins, sl))
        self.assertEqual((a['kind'], a['domain'], a['provider'], a['detail']),
                         ('bounce', 'acme.com', 'google', 'gmail_reputation'))
        self.assertEqual((b['kind'], b['domain']), ('bounce', 'acme.com'))
        self.assertEqual(c['provider'], 'microsoft')
        self.assertNotIn('bob', json.dumps([a, b, c]))
        self.assertIsNone(live.normalize({'event_type': 'email_opened'}))

    def test_a_burst_of_tenant_blocks_raises_one_alert(self):
        from inboxsitrep import live
        text = "5.7.237 outbound email has been blocked due to sending activity"
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'f.db')
            evs = [live.event('2026-10-30 14:%02d:00' % i, 'poll', 'bounce', 'x@acme.com',
                              'l%d@b.com' % i, text) for i in range(12)]
            evs += [live.event('2026-10-30 14:%02d:30' % i, 'poll', 'bounce', 'x@ok.com',
                               'm%d@b.com' % i, text) for i in range(3)]
            self.assertEqual(live.store(path, evs + evs), 15)
            got = live.alerts(path, at='2026-10-30 15:00:00')
            self.assertEqual([(a['detail'], a['hits']) for a in got], [('tenant_block', [('acme.com', 12)])])
            self.assertFalse(live.alerts(path, at='2026-10-30 19:00:00'))

    def test_alerts_are_urgent_once_a_day_per_domain_and_capped(self):
        from inboxsitrep import live
        from inboxsitrep import data as store_
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'f.db')
            store.save(Data(), path)
            sent = []
            waves = [[('a.com', 30)], [('a.com', 40), ('b.com', 25)], [('c.com', 25)], [('d.com', 25)], [('e.com', 25)]]
            for wave in waves:
                with mock.patch.object(live, 'poll', lambda *a, **k: []), \
                        mock.patch.object(live, 'alerts', lambda p, w=wave: [{'detail': 'gmail_reputation', 'hits': w, 'hours': 2}]):
                    live.cycle(None, path, notify=lambda t, b: sent.append(t), say=lambda m: None)
            # a.com once, b.com once (alone), c.com, then the cap
            self.assertEqual(len(sent), 3)
            self.assertIn('a.com', sent[0])
            self.assertEqual(sent[1], 'Gmail started refusing mail from b.com')
            self.assertTrue(store_.get_meta(path, 'alerted'))


class Instantly(unittest.TestCase):

    def test_collect_reads_daily_totals_and_leads(self):
        from inboxsitrep import instantly

        class Fake:
            calls = 0

            def pages(self, path, body=None, **params):
                if path == '/api/v2/accounts':
                    return iter([{'email': 'a@acme.com', 'status': 1, 'provider_code': 2,
                                  'stat_warmup_score': 90},
                                 {'email': 'b@acme.com', 'status': -1, 'provider_code': 2}])
                if body['filter'] == 'FILTER_VAL_BOUNCED':
                    return iter([{'email': 'x@gmail.com', 'esp_code': 1,
                                  'last_contacted_from': 'a@acme.com',
                                  'timestamp_last_contact': '2026-10-20T10:00:00Z'}])
                return iter([{'email': 'y@corp.com', 'esp_code': 2, 'lt_interest_status': 1,
                              'last_contacted_from': 'a@acme.com',
                              'timestamp_last_contact': '2026-10-20T11:00:00Z'}])

            def get(self, path, **params):
                return [{'date': '2026-10-20', 'email_account': 'a@acme.com', 'sent': 100,
                         'bounced': 1, 'replies': 2, 'replies_automatic': 1,
                         'new_leads_contacted': 40}]

        import datetime
        data = instantly.collect(Fake(), days=21, today=datetime.date(2026, 10, 25), say=lambda m: None)
        self.assertEqual(data.accounts['acme.com'], {'active': 1, 'down': 1})
        self.assertEqual(data.host['acme.com'], 'google')
        sent = sum(data.row('acme.com', p, '2026-10-20')[SENT] for p in ('google', 'microsoft'))
        self.assertEqual(sent, 100)
        self.assertEqual(data.row('acme.com', 'microsoft', '2026-10-20')[2], 1)
        self.assertEqual(data.fresh['2026-10-20'], 40)

    def test_the_client_refuses_to_write(self):
        from inboxsitrep import instantly
        with self.assertRaises(ValueError):
            instantly.Client('k').request('/api/v2/campaigns', body={'name': 'x'})


class Dashboard(unittest.TestCase):

    def test_every_page_of_the_api_answers(self):
        import threading
        import http.client
        import http.server
        from inboxsitrep import web
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'demo.db')
            fleet = run_fleet(sim.standard(sim.Fleet(2)), 40)
            store.save(fleet.data, path, meta={'source': 'simulated'})
            incidents.save(incidents.track(fleet.data), path)
            server = http.server.ThreadingHTTPServer(
                ('127.0.0.1', 0), web.handler([web.Fleet(path)], secret='s3'))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            port = server.server_address[1]

            def get(url, method='GET', body=None):
                c = http.client.HTTPConnection('127.0.0.1', port, timeout=20)
                c.request(method, url, body=body)
                r = c.getresponse()
                return r.status, json.loads(r.read() or b'null')

            code, summary = get('/api/summary')
            self.assertEqual(code, 200)
            self.assertTrue(summary['decide'])
            code, m = get('/api/map')
            self.assertEqual(len(m['families'][0]['rows'][0]['s']), len(m['days']))
            first = summary['decide'][0]['id']
            self.assertEqual(get('/api/incidents/%d' % first)[0], 200)
            self.assertEqual(get('/api/domains/' + fleet.doms[0])[0], 200)
            self.assertEqual(get('/api/fleet')[0], 200)
            self.assertEqual(get('/hook/wrong', 'POST', b'{}')[0], 403)
            code, r = get('/hook/s3', 'POST', json.dumps(
                {'webhook_event': 'BOUNCED_EMAIL', 'sender_email': 'a@' + fleet.doms[0],
                 'lead_email': 'b@gmail.com', 'msg': 'x', 'date': '2026-10-30T10:00:00Z'}).encode())
            self.assertEqual(r['events'], 1)
            self.assertEqual(len(get('/api/feed')[1]), 1)
            server.shutdown()


class FakePlusVibe:
    # Just enough of PlusVibe's API to collect from, with a clock.
    calls = 0

    def __init__(self):
        self.bounced = [{'_id': 'b1', 'email': 'x@gmail.com', 'mx': 'GOOGLE_WORKSPACE',
                         'email_acc_name': 'a@acme.com', 'last_sent_at': '2026-10-20T10:00:00Z',
                         'modified_at': '2026-10-20T12:00:00Z', 'bounce_type': 'SENDER',
                         'bounce_msg': '550 5.7.1 very low reputation of the sending domain'}]
        self.replied = []

    def get(self, path, **p):
        self.calls += 1
        if path == '/account/list':
            return {'accounts': [{'email': 'a@acme.com', 'status': 'ACTIVE', 'provider': 'GOOGLE_WORKSPACE',
                                  'payload': {'cmps': [{'id': 'c1'}]}}] if p['skip'] == 0 else []}
        if path == '/account/email-stats/bulk':
            days = [d for d in ('2026-10-19', '2026-10-20', '2026-10-21')
                    if p['start_date'] <= d <= p['end_date']]
            return {'accounts': [{'email': 'a@acme.com', 'chart': [
                {'date': d, 'total_sent_count': 100, 'total_bounce_count': 1} for d in days]}]
                if p['page'] == 1 else [], 'has_more': False}
        if path == '/account/email-stats':
            return {'chart': []}
        if path == '/campaign/list-all':
            return [{'id': 'c1', 'status': 'ACTIVE', 'last_lead_sent': '2026-10-21'}] if p['skip'] == 0 else []
        if path == '/lead/workspace-leads':
            if 'campaign_id' in p:
                return [{'email': 'z@gmail.com', 'mx': 'GOOGLE_WORKSPACE'}]
            if p['page'] > 1:
                return []
            return self.bounced if p['status'] == 'BOUNCED' else self.replied
        raise AssertionError(path)


class Collect(unittest.TestCase):

    def test_a_second_collection_counts_nothing_twice_and_adds_what_is_new(self):
        import datetime
        api = FakePlusVibe()
        today = datetime.date(2026, 10, 22)
        first = plusvibe.collect(api, days=10, today=today, say=lambda m: None)
        self.assertEqual(first.row('acme.com', 'google', '2026-10-20')[3], 1)
        api.replied = [{'_id': 'r1', 'email': 'y@gmail.com', 'mx': 'GOOGLE_WORKSPACE',
                        'email_acc_name': 'a@acme.com', 'last_sent_at': '2026-10-21T10:00:00Z',
                        'modified_at': '2026-10-21T15:00:00Z', 'label': 'INTERESTED'}]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'f.db')
            store.save(first, path)
            second = plusvibe.collect(api, days=10, today=today, say=lambda m: None,
                                      prior=store.load(path))
        self.assertEqual(second.row('acme.com', 'google', '2026-10-20')[3], 1)
        self.assertEqual(second.row('acme.com', 'google', '2026-10-21')[2], 1)
        self.assertEqual(len(second.bounces), 1)
        self.assertEqual(sum(second.kinds['acme.com', '2026-10-20'].values()), 1)


class Changes(unittest.TestCase):

    class Read:
        def get(self, path, **p):
            if path == '/account/list':
                if p['skip']:
                    return {'accounts': []}
                return {'accounts': [
                    {'id': 'a1', 'email': 'x@bad.com', 'payload': {'daily_limit': 2, 'cmps': [{'id': 'c1'}]}},
                    {'id': 'a2', 'email': 'y@bad.com', 'payload': {'daily_limit': 5, 'cmps': [{'id': 'c1'}]}},
                    {'id': 'a3', 'email': 'z@good.com', 'payload': {'daily_limit': 2}}]}
            if path == '/blocklist/list':
                return [{'value': 'icloud.com'}] if not p['skip'] else []
            raise AssertionError(path)

    class Write:
        def __init__(self):
            self.sent = []

        def send(self, method, path, body):
            self.sent.append((method, path, body))
            return {'status': 'success'}

    def data(self):
        d = Data()
        d.campaigns = {'c1': {'name': 'Roofers TX', 'status': 'ACTIVE'}}
        return d

    def test_pausing_a_domain_previews_then_undoes_to_each_mailbox_limit(self):
        from inboxsitrep import actions
        act = actions.prepare({'op': 'pause', 'targets': ['bad.com']}, self.data(), self.Read(), 'Main')
        self.assertEqual(act['steps'][0]['body'], {'ids': ['a1', 'a2'], 'daily_limit': 0})
        self.assertEqual(sorted((s['body']['daily_limit'], tuple(s['body']['ids'])) for s in act['undo']),
                         [(2, ('a1',)), (5, ('a2',))])
        self.assertTrue(act['big'])          # 2 of 3 mailboxes
        w = self.Write()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'f.db')
            self.assertEqual(actions.apply(act, w, path, say=lambda m: None), 'applied')
            self.assertEqual(len(w.sent), 1)
            self.assertEqual(actions.history(path)[0]['status'], 'applied')
            actions.undo(path, act['id'], w, say=lambda m: None)
            self.assertEqual(len(w.sent), 3)
            self.assertEqual(actions.history(path)[0]['status'], 'undone')
            with self.assertRaises(ValueError):
                actions.undo(path, act['id'], w, say=lambda m: None)

    def test_a_pause_is_read_back(self):
        # The fake workspace never changes, so both mailboxes still show a
        # limit after the pause; the status stays 'applied' so Undo shows.
        from inboxsitrep import actions
        act = actions.prepare({'op': 'pause', 'targets': ['bad.com']}, self.data(), self.Read(), 'Main')
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(actions, 'SETTLE', 0):
            path = os.path.join(tmp, 'f.db')
            status = actions.apply(act, self.Write(), path, say=lambda m: None, read=self.Read(), seq='plusvibe')
            self.assertEqual(status, 'applied')
            self.assertEqual(actions.history(path)[0]['checked'], '2 mailboxes do not show it yet; look again in a minute')
        api = InstantlyChanges.Api()
        act = actions.instantly_prepare({'op': 'pause', 'targets': ['acme.com']}, api, 'Main')
        api.pages = lambda path, **p: iter([{'email': 'a@acme.com', 'daily_limit': 0},
                                            {'email': 'b@acme.com', 'daily_limit': 0}])
        with mock.patch.object(actions, 'SETTLE', 0):
            self.assertEqual(actions.verify(act, api, 'instantly'), 0)

    def test_undo_is_there_while_the_read_back_is_still_running(self):
        from inboxsitrep import actions
        act = actions.prepare({'op': 'pause', 'targets': ['bad.com']}, self.data(), self.Read(), 'Main')
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'f.db')
            seen = []

            def slow(act, read, seq):
                seen.append(actions.history(path)[0]['status'])
                raise OSError('the app was closed')
            with mock.patch.object(actions, 'verify', slow):
                actions.apply(act, self.Write(), path, say=lambda m: None, read=self.Read(), seq='plusvibe')
            self.assertEqual(seen, ['applied'])
            self.assertTrue(actions.history(path)[0]['undo'])

    def test_a_step_already_applied_shows_as_done_until_undone(self):
        from inboxsitrep import actions
        plan = [{'op': 'pause', 'targets': ['a.com', 'b.com']}, {'op': 'suppress_apple', 'targets': []}]
        past = [{'id': 'x', 'at': '2026-11-03T15:55', 'status': 'applied',
                 'change': {'op': 'pause', 'targets': ['a.com', 'b.com', 'c.com']}},
                {'id': 'y', 'at': '2026-11-03T15:40', 'status': 'undone',
                 'change': {'op': 'suppress_apple', 'targets': []}}]
        self.assertEqual(actions.applied(plan, past), [{'id': 'x', 'at': '2026-11-03T15:55'}, None])

    def test_warmup_too_touches_only_warmed_mailboxes_and_undo_turns_those_back_on(self):
        from inboxsitrep import actions
        read = self.Read()
        real = read.get

        def get(path, **p):
            out = real(path, **p)
            if path == '/account/list' and out['accounts']:
                out['accounts'][1]['warmup_status'] = 'INACTIVE'      # y@bad.com: warmup already off
            return out
        read.get = get
        act = actions.prepare({'op': 'pause', 'targets': ['bad.com'], 'warmup': 'off'}, self.data(), read, 'Main')
        warm = [s for s in act['steps'] if s['path'] == '/account/bulk-update-warmup']
        self.assertEqual(warm[0]['body'], {'ids': ['a1'], 'warmup_status': 'INACTIVE'})
        self.assertIn({'ids': ['a1'], 'warmup_status': 'ACTIVE'}, [u['body'] for u in act['undo']])
        self.assertIn('warmup paused too (1 had warmup off already)', act['lines'][0])
        act = actions.prepare({'op': 'set_limit', 'targets': ['bad.com'], 'limit': 10}, self.data(), self.Read(), 'Main')
        self.assertEqual(act['steps'][0]['body'], {'ids': ['a1', 'a2'], 'daily_limit': 10})
        self.assertTrue(act['lines'][0].startswith('Daily limit to 10 on 2 mailboxes'))
        act = actions.prepare({'op': 'warmup_off', 'targets': ['bad.com']}, self.data(), self.Read(), 'Main')
        self.assertEqual([s['path'] for s in act['steps']], ['/account/bulk-update-warmup'])

    def test_a_change_asked_for_is_held_to_the_list_and_the_workspace(self):
        from inboxsitrep import actions
        doms = ['growthmaila.com', 'growthmailb.com', 'other.com']
        c = actions.clean({'op': 'pause', 'targets': ['growthmail*'], 'warmup': 'off'}, doms, 'plusvibe')
        self.assertEqual(c['targets'], ['growthmaila.com', 'growthmailb.com'])
        self.assertEqual(c['warmup'], 'off')
        for bad in ({'op': 'delete_everything'}, {'op': 'pause', 'targets': ['notmine.com']},
                    {'op': 'set_limit', 'targets': ['other.com'], 'limit': 9999},
                    {'op': 'change_settings', 'targets': ['X'], 'setting': 'tracking'}):
            with self.assertRaises(ValueError):
                actions.clean(bad, doms, 'instantly')
        self.assertEqual(actions.clean({'op': 'hold_receiver', 'targets': ['spectrum']}, doms, 'plusvibe')['targets'],
                         ['Spectrum'])

    def test_blocklist_adds_only_what_is_missing_and_undo_removes_only_that(self):
        from inboxsitrep import actions
        act = actions.prepare({'op': 'suppress_apple', 'targets': []}, self.data(), self.Read(), 'Main')
        self.assertEqual(act['steps'][0]['body'], {'entries': ['me.com', 'mac.com']})
        self.assertEqual(act['undo'][0]['body'], {'entries': ['me.com', 'mac.com']})

    def test_stopping_one_provider_is_manual_and_names_the_campaigns(self):
        from inboxsitrep import actions
        act = actions.prepare({'op': 'stop', 'targets': ['bad.com'], 'provider': 'google'},
                              self.data(), self.Read(), 'Main')
        self.assertFalse(act['steps'])
        self.assertIn('Roofers TX', act['manual'])

    def test_the_writer_refuses_anything_off_its_list(self):
        from inboxsitrep import actions
        with self.assertRaises(ValueError):
            actions.Writer('k', 'w').send('POST', '/account/bulk-delete', {})


class Notify(unittest.TestCase):

    def test_slack_and_telegram_send_and_one_failure_does_not_stop_the_other(self):
        from inboxsitrep import notify
        sent = []

        def broken(*a, **k):
            raise OSError('no network')
        from inboxsitrep import telegram
        cfg = {'webhook': 'https://hooks.slack.com/x', 'telegram': {'chat_id': 42}}
        with mock.patch.object(notify, 'webhook', lambda url, t, b: sent.append(('slack', t))), \
                mock.patch.object(notify, 'secret', lambda name: 'TOKEN'), \
                mock.patch.object(telegram, 'send', broken):
            failed = notify.send(cfg, 'Subject', 'Body')
        self.assertEqual(sent, [('slack', 'Subject')])
        self.assertEqual(list(failed), ['telegram'])
        self.assertEqual(notify.channels({'webhook': 'https://hooks.slack.com/x'}), ['slack'])


class FakeBison:
    calls = 0

    def __init__(self):
        self.sent = []

    def pages(self, path, **p):
        if path == '/api/sender-emails':
            return iter([{'id': 1, 'email': 'a@acme.com', 'status': 'Connected', 'daily_limit': 30, 'tags': [{'name': 'Google'}]},
                         {'id': 2, 'email': 'b@acme.com', 'status': 'Not connected', 'daily_limit': 20, 'tags': []},
                         {'id': 3, 'email': 'c@other.com', 'status': 'Connected', 'daily_limit': 30, 'tags': []}])
        if path == '/api/warmup/sender-emails':
            return iter([{'email': 'a@acme.com', 'warmup_score': 91}])
        if path == '/api/replies' and p.get('folder') == 'bounced':
            return iter([{'sender_email_id': 1, 'date_received': '2026-10-20T10:00:00Z',
                          'text_body': 'Delivery to bob@gmail.com failed: 550 5.7.1 very low reputation of the sending domain'}])
        if path == '/api/replies':
            return iter([{'sender_email_id': 1, 'from_email_address': 'jane@outlook.com'}])
        if path == '/api/blacklisted-domains':
            return iter([{'id': 9, 'domain': 'icloud.com'}])
        raise AssertionError(path)

    def get(self, path, **p):
        days = [['2026-10-20T00:00:00Z', 100]]
        return {'data': [{'label': 'Sent', 'dates': days}, {'label': 'Bounced', 'dates': [['2026-10-20', 1]]},
                         {'label': 'Replied', 'dates': [['2026-10-20', 2]]}, {'label': 'Interested', 'dates': [['2026-10-20', 1]]}]}

    def request(self, method, path, params=None, body=None):
        self.sent.append((method, path, body))
        return {'data': {'success': True}}


class EmailBison(unittest.TestCase):

    def test_collect_reads_mailboxes_days_and_bounce_text(self):
        import datetime
        from inboxsitrep import emailbison
        data = emailbison.collect(FakeBison(), days=21, today=datetime.date(2026, 10, 25), say=lambda m: None,
                                  lookup=False)
        self.assertEqual(data.accounts['acme.com'], {'active': 1, 'down': 1})
        self.assertEqual(data.host['acme.com'], 'google')
        self.assertEqual(data.health['acme.com'][0], 91.0)
        sent = sum(data.row('acme.com', p, '2026-10-20')[SENT] for p in ('google', 'microsoft', 'other'))
        self.assertEqual(sent, 100)
        self.assertEqual(data.row('acme.com', 'google', '2026-10-20')[SENDER], 1)
        self.assertEqual(data.kinds['acme.com', '2026-10-20']['gmail_reputation'], 1)
        self.assertNotIn('bob@gmail.com', json.dumps(data.bounces))

    def test_pause_and_blocklist_preview_with_exact_undo(self):
        from inboxsitrep import actions
        api = FakeBison()
        act = actions.bison_prepare({'op': 'pause', 'targets': ['acme.com']}, api, 'Main')
        self.assertEqual(act['steps'][0]['body'], {'sender_email_ids': [1, 2], 'daily_limit': 0})
        self.assertEqual(sorted(s['body']['daily_limit'] for s in act['undo']), [20, 30])
        act = actions.bison_prepare({'op': 'suppress_apple', 'targets': []}, api, 'Main')
        self.assertEqual([s['body']['domain'] for s in act['steps']], ['me.com', 'mac.com'])
        w = actions.BisonWriter(api)
        with self.assertRaises(ValueError):
            w.send('DELETE', '/api/sender-emails/1', {})


class InstantlyChanges(unittest.TestCase):

    class Api:
        def __init__(self):
            self.sent = []

        def pages(self, path, body=None, **p):
            if path == '/api/v2/accounts':
                return iter([{'email': 'a@acme.com', 'daily_limit': 40}, {'email': 'b@acme.com', 'daily_limit': 25},
                             {'email': 'c@ok.com', 'daily_limit': 40}])
            if path == '/api/v2/block-lists-entries':
                return iter([{'id': 'x1', 'bl_value': 'charter.net'}])
            raise AssertionError(path)

        def write(self, method, path, body):
            self.sent.append((method, path, body))
            return {}

    def test_warmup_off_disables_only_warming_accounts(self):
        from inboxsitrep import actions
        api = self.Api()
        real = api.pages
        api.pages = lambda path, body=None, **p: iter(
            [dict(a, warmup_status=1 if a['email'] != 'b@acme.com' else 0) for a in real(path)]) \
            if path == '/api/v2/accounts' else real(path)
        act = actions.instantly_prepare({'op': 'pause', 'targets': ['acme.com'], 'warmup': 'off'}, api, 'Main')
        self.assertEqual(act['steps'][-1]['body'], {'emails': ['a@acme.com']})
        self.assertEqual(act['undo'][-1]['path'], '/api/v2/accounts/warmup/enable')
        w = actions.InstantlyWriter(api)
        w.send('POST', '/api/v2/accounts/warmup/disable', {'emails': ['a@acme.com']})
        self.assertEqual(api.sent[-1], ('POST', '/api/v2/accounts/warmup/disable', {'emails': ['a@acme.com']}))

    def test_pause_restores_each_limit_and_blocklist_undo_finds_the_entry(self):
        from inboxsitrep import actions
        api = self.Api()
        act = actions.instantly_prepare({'op': 'pause', 'targets': ['acme.com']}, api, 'Main')
        self.assertEqual([s['body'] for s in act['undo']],
                         [{'email': 'a@acme.com', 'daily_limit': 40}, {'email': 'b@acme.com', 'daily_limit': 25}])
        w = actions.InstantlyWriter(api)
        w.send(*[act['steps'][0][k] for k in ('method', 'path', 'body')])
        self.assertEqual(api.sent[-1], ('PATCH', '/api/v2/accounts/a%40acme.com', {'daily_limit': 0}))
        act = actions.instantly_prepare({'op': 'hold_receiver', 'targets': ['Spectrum']}, api, 'Main')
        self.assertNotIn('charter.net', [s['body']['bl_value'] for s in act['steps']])
        w.send('DELETE', '/api/v2/block-lists-entries/{id}', {'bl_value': 'charter.net'})
        self.assertEqual(api.sent[-1][1], '/api/v2/block-lists-entries/x1')
        with self.assertRaises(ValueError):
            w.send('DELETE', '/api/v2/accounts/{email}', {'email': 'a@acme.com'})


class WhyListed(unittest.TestCase):

    def test_a_shared_registration_batch_stands_out_and_a_safe_trait_shows_too(self):
        from inboxsitrep import rootcause
        data = Data()
        data.days = ['2026-10-30']
        for n in range(60):
            dom = 'd%02d.com' % n
            batch = n < 30
            data.workspace[dom] = 'x'
            data.dns[dom] = {'registrar': 'Cheapreg' if batch else 'Otherreg',
                             'created': '2026-08-14' if batch else '2025-0%d-01' % (1 + n % 9),
                             'ns': ['ns1.cheapreg.com'] if batch else ['a.ns.cloudflare.com'],
                             'mx': ['1 aspmx.l.google.com.']}
        listed = {'d%02d.com' % n for n in range(28)} | {'d45.com'}
        hits = rootcause.analyze(data, listed=listed)
        risk = [h for h in hits if h['kind'] == 'risk']
        safe = {(h['trait'], h['value']) for h in hits if h['kind'] == 'protects'}
        # registrar, month and nameservers pick out the same 30 domains: one finding
        self.assertEqual([(h['trait'], h['value']) for h in risk], [('registrar', 'Cheapreg')])
        self.assertIn('registered in 2026-08', risk[0]['also'])
        self.assertIn(('registrar', 'Otherreg'), safe)


class QuickSweep(unittest.TestCase):

    def test_a_tenant_block_burst_alerts_once_between_checks(self):
        import datetime
        from inboxsitrep import app, watch
        now = datetime.datetime.now(datetime.timezone.utc)

        class Fake:
            def get(self, path, **p):
                if p.get('status') != 'BOUNCED' or p.get('page', 1) > 1:
                    return []
                return [{'_id': str(i), 'email': 'x%d@corp.com' % i, 'email_acc_name': 'a@acme.com',
                         'modified_at': (now - datetime.timedelta(minutes=i)).isoformat(),
                         'bounce_msg': '5.7.237 outbound email has been blocked'} for i in range(12)]

        class Settings:
            cfg = {}

        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, 'f.db')
            store.connect(db).close()
            Settings.cfg = {'fleets': [{'name': 'Main', 'sequencer': 'plusvibe', 'db': db}],
                            'every_hours': 6, 'notify': {'desktop': False}}
            Settings.path = os.path.join(tmp, 'config.json')
            alerts = []
            real = watch.client
            watch.client = lambda f: Fake()
            try:
                c = app.Checker(Settings(), [])
                c.alert = lambda cfg, name, title, body: alerts.append(title)
                c.quick(Settings.cfg)
                c.quick(Settings.cfg)          # within 15 minutes: nothing new
            finally:
                watch.client = real
        self.assertEqual(len(alerts), 1)
        self.assertIn('acme.com', alerts[0])


class AppServer(unittest.TestCase):

    def test_token_host_and_secrets_and_a_simulated_change(self):
        import threading
        import http.client
        import http.server
        from inboxsitrep import app, web
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, 'demo.db')
            fleet = run_fleet(sim.standard(sim.Fleet(2)), 40)
            store.save(fleet.data, db, meta={'source': 'simulated', 'workspace': 'Demo'})
            incidents.save(incidents.track(fleet.data), db)
            cfg = os.path.join(tmp, 'config.json')
            with open(cfg, 'w') as f:
                json.dump({'fleets': [{'name': 'Demo', 'sequencer': 'sim', 'db': db}]}, f)
            from inboxsitrep import notify
            real_home, real_keys = app.HOME, notify.KEYS
            app.HOME, notify.KEYS = tmp, os.path.join(tmp, 'keys')
            try:
                settings = app.Settings(cfg)
                fleets = [web.Fleet(db)]
                server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), None)
                port = server.server_address[1]
                server.RequestHandlerClass = web.handler(fleets, app={
                    'settings': settings, 'checker': app.Checker(settings, fleets), 'fleets': fleets,
                    'token': 'tok', 'port': port, 'pending': {}})
                threading.Thread(target=server.serve_forever, daemon=True).start()

                def call(path, method='GET', body=None, token='tok', host=None):
                    c = http.client.HTTPConnection('127.0.0.1', port, timeout=20)
                    h = {'X-Token': token, 'Content-Type': 'application/json'}
                    if host:
                        h['Host'] = host
                    c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
                    r = c.getresponse()
                    return r.status, json.loads(r.read() or b'null')

                self.assertEqual(call('/api/summary', token='wrong')[0], 403)
                self.assertEqual(call('/api/summary', host='evil.example:%d' % port)[0], 403)
                self.assertEqual(call('/api/summary')[0], 200)
                code, state = call('/api/app/settings', 'POST', {
                    'fleets': [{'name': 'Main', 'sequencer': 'plusvibe', 'workspace': 'w1', 'key': 'SECRET-KEY'}],
                    'telegram_token': '123:TG-SECRET'})
                self.assertEqual(code, 200)
                self.assertNotIn('SECRET', json.dumps(state))
                self.assertTrue(state['settings']['notify']['has_telegram_key'])
                self.assertEqual(len(state['settings']['notify']['telegram']['code']), 6)
                inc = next(i for i in fleets[0].incidents if i['plan'])
                code, act = call('/api/app/preview', 'POST', {'fleet': 'Demo', 'incident': inc['id'], 'step': 0})
                self.assertEqual(code, 200)
                code, r = call('/api/app/apply', 'POST', {'fleet': 'Demo', 'id': act['id']})
                self.assertEqual(r['status'], 'applied (simulated)')
                server.shutdown()
            finally:
                app.HOME, notify.KEYS = real_home, real_keys


class Brief(unittest.TestCase):

    def inc(self, id, kind, target, title, plan=(), scope='domain', **ev):
        return {'id': id, 'kind': kind, 'scope': scope, 'target': target, 'title': title,
                'status': 'open', 'opened': '2026-10-01', 'updated': '2026-10-02',
                'plan': list(plan), 'evidence': ev}

    def test_a_listed_family_is_one_line_and_plans_read_as_actions(self):
        from inboxsitrep import brief
        fleet = sim.Fleet(3)
        run_fleet(fleet, 3)
        pause = lambda *t: {'op': 'pause', 'targets': list(t)}
        found = [
            self.inc(1, 'listed', 'lp', "3 of the lp* family's 18 domains are on a blocklist",
                     [pause('lp3.com')], scope='family', domains=['lp1.com', 'lp2.com', 'lp3.com'],
                     members=['lp%d.com' % n for n in range(1, 19)]),
            self.inc(2, 'listed', 'lp1.com', 'lp1.com is on SURBL', [pause('lp1.com')]),
            self.inc(3, 'burned', 'lp2.com', 'Gmail has stopped taking mail from lp2.com (listed on SURBL)'),
            self.inc(4, 'apple', 'apple', 'Apple is refusing 79% of mail to iCloud addresses',
                     [{'op': 'suppress_apple', 'targets': []}]),
            self.inc(5, 'burned', 'x.com', 'Gmail has stopped taking mail from x.com',
                     [{'op': 'stop', 'targets': ['x.com'], 'provider': 'google'}]),
        ]
        text = brief.brief(fleet.data, found, 'Main')
        self.assertIn('Needs a decision (3)', text)
        self.assertIn("[#1] 3 of the lp* family's 18 domains are on a blocklist; Gmail refuses 1 of them "
                      "(also #2, #3).  Plan: pause 3 domains", text)
        self.assertNotIn('[#2]', text)
        self.assertIn('Plan: stop mailing iCloud addresses', text)
        self.assertIn('Plan: stop sending to Gmail from x.com', text)
        self.assertEqual(brief.plan_line(self.inc(6, 'burned', 'y.com', 'y')), 'already planned')
        self.assertIn('[#5] New: Gmail has stopped taking mail from x.com',
                      brief.brief(fleet.data, found, 'Main', fresh={5}))

    def test_a_plan_says_which_incident_already_covers_the_rest(self):
        from inboxsitrep import brief
        pause = lambda *t: {'op': 'pause', 'targets': list(t)}
        found = [self.inc(1, 'listed', 'a.com', 'a.com is on SURBL', [pause('a.com')]),
                 self.inc(2, 'tenant_block', 'tenant', 'Microsoft blocked 2 domains', [pause('c.com')],
                          scope='fleet', domains=['a.com', 'c.com']),
                 self.inc(3, 'burned', 'a.com', 'Gmail has stopped taking mail from a.com'),
                 self.inc(4, 'receiver', 'Spectrum', 'Spectrum refused 9', [{'op': 'hold_receiver', 'targets': ['Spectrum']}],
                          scope='fleet', domains=['a.com'])]
        planned = brief.planned_by(found)
        self.assertEqual(brief.plan_line(found[1], planned), 'pause c.com; 1 more already in the plan for #1')
        self.assertEqual(brief.plan_line(found[2], planned), 'already in the plan for #1')
        self.assertEqual(brief.plan_line(found[3], planned), 'hold mail to Spectrum recipients')

    def test_four_dead_domains_are_one_line_like_the_app_shows_them(self):
        from inboxsitrep import brief
        fleet = sim.Fleet(3)
        run_fleet(fleet, 3)
        fam = sorted(fleet.doms)[:3]
        found = [self.inc(n, 'burned', d, 'Gmail has stopped taking mail from %s' % d)
                 for n, d in enumerate(fam + ['lonely.com'], 1)]
        text = brief.brief(fleet.data, found, 'Main')
        self.assertIn('Needs a decision (1)', text)
        self.assertIn('Gmail has stopped taking mail from 4 domains (', text)
        self.assertIn('lonely.com; #1, #2, #3, #4)', text)


class Demo(unittest.TestCase):
    # The three-workspace demo, read as its user would.

    @classmethod
    def setUpClass(cls):
        cls.fleets = dict(sim.demo(days=45))

    def test_thirds_of_each_infrastructure_and_twelve_campaigns(self):
        import collections
        for name, fleet in self.fleets.items():
            hosts = collections.Counter(fleet.data.host.values())
            self.assertEqual(set(hosts), {'google', 'microsoft', 'other'})
            self.assertGreater(min(hosts.values()), len(fleet.doms) / 5, name)
            self.assertEqual(len(fleet.data.campaigns), 12)

    def test_spare_domains_last_as_long_as_the_losses_allow(self):
        from inboxsitrep import checks
        for name, fleet in self.fleets.items():
            data = fleet.data
            r = checks.reserve(data, incidents.track(data), data.days[-1])
            self.assertEqual(r['spare'], sim.SPARES[name], name)
        north = self.fleets['Northwind Outbound'].data
        r = checks.reserve(north, incidents.track(north), north.days[-1])
        self.assertGreaterEqual(r['lost'], 10)          # the cascade and the tenant block
        self.assertTrue(r['buy'] > 0 and r['weeks'] < 4)

    def test_a_cascade_is_told_once_and_campaigns_running_dry_are_named(self):
        from inboxsitrep import brief
        data = self.fleets['Northwind Outbound'].data
        text = brief.brief(data, incidents.track(data))
        self.assertIn('one domain after another', text)
        self.assertNotIn('Gmail has stopped taking mail from', text)
        self.assertRegex(text, r'\d+ campaigns run out of new leads within 2 days \(')
        self.assertNotIn('track opens', text)          # PlusVibe says 0, not "no"

    def test_a_throttle_turned_refusal_is_not_told_twice(self):
        from inboxsitrep import brief
        data = self.fleets['Atlas Growth'].data
        text = brief.brief(data, incidents.track(data))
        self.assertNotIn('slowing down mail from brightreachmail.com', text)
        self.assertIn('until then the family sends to Microsoft and others only', text)
        self.assertIn('1 of 12 active campaigns track opens', text)

    def test_the_daily_report_gives_each_workspace_a_status(self):
        import datetime
        from inboxsitrep import reports
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {'fleets': []}
            for name, fleet in self.fleets.items():
                db = os.path.join(tmp, name + '.db')
                store.save(fleet.data, db, meta={'workspace': name})
                incidents.save(incidents.track(fleet.data), db)
                cfg['fleets'].append({'name': name, 'db': db})
            title, text = reports.daily(cfg, now=datetime.datetime.combine(
                datetime.date.fromisoformat(fleet.data.days[-1]) + datetime.timedelta(days=1),
                datetime.time(8)))
        # What a phone shows first: which workspaces need you.
        self.assertIn('RED: Northwind Outbound', title)
        self.assertIn('AMBER: Atlas Growth, Summit Pipeline', title)
        self.assertTrue(text.startswith('3 workspaces: 1 RED, 2 AMBER.'))
        self.assertIn('*Northwind Outbound* · RED\nYesterday (', text)
        self.assertEqual(text.count('Microsoft has blocked outbound mail'), 1)     # each problem once


class Rules(unittest.TestCase):

    def tearDown(self):
        from inboxsitrep import rules
        with tempfile.TemporaryDirectory() as tmp:
            rules.load(os.path.join(tmp, 'rules.ini'))         # back to the defaults

    def test_the_rules_file_changes_the_verdicts_and_their_words(self):
        from inboxsitrep import rules, verdicts
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'rules.ini')
            self.assertEqual(rules.load(path), [])
            self.assertIn('stop_refused_percent = 4', open(path).read())     # written with the defaults
            self.assertEqual(verdicts.REFUSED, .04)
            text = open(path).read().replace('stop_refused_percent = 4', 'stop_refused_percent = 3')
            text = text.replace('loser_times = 0.5', 'loser_times = nonsense').replace('protect = ', 'protect = acme.com, brand*')
            open(path, 'w').write(text)
            problems = rules.load(path)
            self.assertAlmostEqual(verdicts.REFUSED, .03)
            self.assertEqual(verdicts.LOSER, .5)                   # a bad value keeps its default
            self.assertEqual(len(problems), 1)
            self.assertIn('loser_times', problems[0])
            self.assertIn('over 3% of its mail', verdicts.said(verdicts.VERDICTS['burned'][1]))
            self.assertTrue(rules.protected('acme.com'))
            self.assertTrue(rules.protected('brandmail.io', 'brand'))
            self.assertFalse(rules.protected('other.com'))


class StopButton(unittest.TestCase):

    def test_stop_ends_the_check_and_the_watch_carries_on(self):
        # Stop pressed mid-check: the round ends, nothing escapes to kill the
        # watch thread (saying "stopped" once stopped it a second time).
        with tempfile.TemporaryDirectory() as tmp:
            settings = app_module.Settings(os.path.join(tmp, 'c.json'))
            settings.cfg.update(fleets=[{'name': 'A', 'sequencer': 'sim', 'db': os.path.join(tmp, 'a.db')}],
                                report_at=[], weekly={'day': None, 'at': '08:00'}, alerts=False)
            c = app_module.Checker(settings, [])

            def check(cfg, f):
                c.halt.set()
                c.say('reading')
            c.check = check
            c.only = '*'
            c.wake.set()
            c.round()
            self.assertFalse(c.state['busy'])
            self.assertTrue(any('stopped' in line for line in c.state['log']))


class Chat(unittest.TestCase):

    def test_reports_go_through_the_slack_bot_when_a_channel_is_set(self):
        import urllib.parse
        from inboxsitrep import notify, slackbot
        posted = []
        cfg = {'slack_channel': '#deliverability', 'webhook': 'https://hooks.slack.com/x'}
        with mock.patch.object(notify, 'secret', lambda name: 'xoxb-1' if name == 'slack-bot.key' else ''), \
                mock.patch.object(slackbot, 'post', lambda token, ch, text: posted.append((ch, text))), \
                mock.patch.object(notify, 'webhook', lambda *a: posted.append('webhook')):
            self.assertEqual(notify.channels(cfg), ['slack'])
            notify.send(cfg, 'Title', 'Body')
        self.assertEqual(posted, [('#deliverability', '*Title*\nBody')])
        got = json.loads(urllib.parse.unquote(slackbot.manifest_url().split('manifest_json=')[1]))
        self.assertTrue(got['settings']['socket_mode_enabled'])
        self.assertIn('chat:write.public', got['oauth_config']['scopes']['bot'])

    def test_markdown_becomes_slack_and_telegram_markup(self):
        from inboxsitrep import notify
        md = '## Bottom line\nPause **x.com** now.\n- AT&T refused 3, [docs](https://e.com/a)'
        self.assertEqual(notify.chat(md), '*Bottom line*\nPause *x.com* now.\n'
                                          '\u2022 AT&amp;T refused 3, <https://e.com/a|docs>')
        self.assertEqual(notify.chat(md, links='plain').splitlines()[-1],
                         '\u2022 AT&T refused 3, docs (https://e.com/a)')


class ReportTimes(unittest.TestCase):

    def test_times_are_read_loosely_and_a_late_app_skips_a_passed_report(self):
        import datetime
        from inboxsitrep import app
        self.assertEqual(app.report_times('8:00, 17.30;8'), ['08:00', '17:30'])
        self.assertEqual(app.report_times(''), [])
        with self.assertRaises(ValueError):
            app.report_times('25:00')
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(app, 'DATA', tmp):
            c = app.Checker(app.Settings(os.path.join(tmp, 'c.json')), [])
            cfg = {'report_at': ['08:00', '17:00']}
            day = datetime.date(2026, 11, 3)
            at = lambda h, m=0: datetime.datetime.combine(day, datetime.time(h, m))
            cfg['fleets'] = []                                  # no checks to start early for
            self.assertIsNone(c.slot(cfg, at(7, 59)))
            self.assertEqual(c.slot(cfg, at(8, 5)), ('08:00', day.isoformat()))
            c.reported['08:00'] = day.isoformat()
            self.assertIsNone(c.slot(cfg, at(8, 20)))           # sent already
            self.assertIsNone(c.slot(cfg, at(16, 0)))           # 08:00 too long ago
            self.assertEqual(c.until_report(cfg, at(16, 0)), 3600)
            self.assertEqual(c.until_report(cfg, at(18, 0)), 14 * 3600)
            # Checks that took 20 minutes start 20 minutes early, even across midnight.
            db = os.path.join(tmp, 'w.db')
            store.save(Data(), db, meta={'took': 1200})
            cfg['fleets'] = [{'name': 'W', 'db': db}]
            c.reported.clear()
            self.assertEqual(c.slot(cfg, at(7, 41)), ('08:00', day.isoformat()))
            self.assertEqual(c.until_report(cfg, at(7, 0)), 40 * 60)
            late = {'report_at': ['00:10'], 'fleets': cfg['fleets']}
            tomorrow = (day + datetime.timedelta(days=1)).isoformat()
            self.assertEqual(c.slot(late, at(23, 55)), ('00:10', tomorrow))
            # New problems from a run over several workspaces: one message.
            c.outbox = [('A', 'Inbox Sitrep, A: 2 new problems', 'Report: A', 2),
                        ('B', 'Inbox Sitrep, B: x', 'Report: B', 1)]
            with mock.patch('inboxsitrep.notify.send') as send:
                c.send_outbox({'notify': {'webhook': 'https://hooks.example/x'}})
            self.assertEqual(send.call_count, 1)
            self.assertEqual(send.call_args[0][1], 'New problems · A (2), B (1)')
            # One message for every workspace at a report time: the daily
            # SITREP, and on its day the weekly one.
            cfg = {'report_at': ['08:00'], 'notify': {'webhook': 'https://hooks.example/x'}, 'fleets': [],
                   'weekly': {'day': 4, 'at': '17:00'}}
            for name in ('A', 'B'):
                fleet = run_fleet(sim.Fleet(3), 10)
                db = os.path.join(tmp, name + '.db')
                store.save(fleet.data, db, meta={'workspace': name})
                incidents.save(incidents.track(fleet.data), db)
                cfg['fleets'].append({'name': name, 'db': db})
            with mock.patch('inboxsitrep.notify.send') as send:
                c.digest(cfg, '08:00')
            self.assertEqual(send.call_count, 1)
            title, body = send.call_args[0][1:3]
            self.assertTrue(title.startswith('SITREP · '))
            self.assertTrue(body.startswith('2 workspaces: '))
            c.failed = {'B': 'HTTP Error 502'}
            with mock.patch('inboxsitrep.notify.send') as send:
                c.digest(cfg, 'weekly')
            title, body = send.call_args[0][1:3]
            self.assertTrue(title.startswith('Weekly SITREP'))
            self.assertTrue(body.startswith('B: this check failed (HTTP Error 502)'))
            c.failed = {}
            friday = datetime.datetime(2026, 11, 6, 17, 5)
            c.reported.clear()
            self.assertEqual(c.slot(cfg, friday), ('weekly', '2026-11-06'))
            target = os.path.join(tmp, 'autostart', 'inboxsitrep.desktop')
            with mock.patch.object(app, 'AUTOSTART', target):
                app.autostart(True)
                with open(target) as e:
                    self.assertIn('inboxsitrep', e.read())
                app.autostart(False)
                self.assertFalse(os.path.exists(target))


class InWords(unittest.TestCase):

    def test_a_change_said_in_words_is_checked_previewed_and_applied(self):
        from inboxsitrep import app, web
        with tempfile.TemporaryDirectory() as tmp:
            fleet = sim.Fleet(3)
            run_fleet(fleet, 3)
            db = os.path.join(tmp, 'demo.db')
            store.save(fleet.data, db, meta={'source': 'simulated', 'workspace': 'Demo'})
            settings = app.Settings(os.path.join(tmp, 'config.json'))
            settings.cfg['fleets'] = [{'name': 'Demo', 'sequencer': 'sim', 'db': db}]
            ctx = {'fleets': [web.Fleet(db)], 'settings': settings, 'pending': {},
                   'checker': type('C', (), {'say': staticmethod(lambda m: None)})()}
            fam = detect.families(fleet.data.domains())
            name = sorted(set(fam.values()))[0]
            said = ([{'op': 'pause', 'targets': [name + '*'], 'warmup': 'off'}], 'Pauses them and their warmup.', None)
            with mock.patch('inboxsitrep.agent.instruct', return_value=said) as asked:
                act = web.app_post(ctx, 'instruct', {'fleet': 'Demo', 'text': 'pause them, no warmup either'})
            self.assertIn('Workspace: Demo on sim', asked.call_args[0][1])
            self.assertEqual(act['understood'], 'Pauses them and their warmup.')
            self.assertEqual(act['change']['warmup'], 'off')
            self.assertEqual(len(act['change']['targets']), sum(1 for v in fam.values() if v == name))
            self.assertEqual(web.change_post(ctx, 'apply', {'fleet': 'Demo', 'id': act['id']})['status'],
                             'applied (simulated)')
            with mock.patch('inboxsitrep.agent.instruct',
                            return_value=([{'op': 'pause', 'targets': ['madeup.com']}], '', None)):
                with self.assertRaises(ValueError):
                    web.change_post(ctx, 'instruct', {'fleet': 'Demo', 'text': 'pause madeup.com'})
            act = web.change_post(ctx, 'custom', {'fleet': 'Demo', 'change': {
                'op': 'set_limit', 'targets': [name + '*'], 'limit': 5}})
            self.assertIn('send at most 5 a day', act['lines'][0])


class Log(unittest.TestCase):

    def test_the_log_page_reads_both_logs_newest_first_with_kinds(self):
        from inboxsitrep import app, web
        with tempfile.TemporaryDirectory() as tmp:
            applog = os.path.join(tmp, 'app.log')
            with open(applog, 'w') as f:
                f.write('2026-11-03 15:41:12  North Fleet: applying: Pause 700 mailboxes\n'
                        '2026-11-03 15:41:13  North Fleet: pause warmup on 500 mailboxes\n'
                        '2026-11-03 14:33:43  South: done: 8 incidents, 5 new, 488 s\n'
                        '2026-11-03 14:30:00  South: failed: HTTP Error 502\n'
                        'not a log line\n')
            with open(notify.LOG, 'w') as f:
                f.write('2026-11-03T08:00:05  Inbox Sitrep, 08:00 report: 2 workspaces\n')
            settings = app.Settings(os.path.join(tmp, 'c.json'))
            settings.cfg['fleets'] = [{'name': 'North Fleet'}, {'name': 'South'}]
            checker = type('C', (), {'logfile': applog})()
            rows = web.app_log({'settings': settings, 'checker': checker})
            self.assertEqual([r['kind'] for r in rows], ['change', 'change', 'check', 'problem', 'report'])
            self.assertEqual(rows[0]['who'], 'North Fleet')
            self.assertEqual(rows[1]['text'], 'applying: Pause 700 mailboxes')
            os.remove(notify.LOG)


class Platforms(unittest.TestCase):

    def test_a_launcher_for_each_system(self):
        from inboxsitrep import app
        self.assertIn('Exec=env PYTHONPATH=', app.entry('/py', 'linux'))
        self.assertIn('<string>inboxsitrep</string><string>app</string>', app.entry('/py', 'darwin'))
        self.assertIn('"/py" -m inboxsitrep app\r\n', app.entry('/py', 'win32'))


class FirstLook(unittest.TestCase):

    def test_allowing_changes_for_a_workspace_is_saved(self):
        from inboxsitrep import app
        with tempfile.TemporaryDirectory() as tmp:
            settings = app.Settings(os.path.join(tmp, 'config.json'))
            settings.update({'fleets': [{'name': 'Main', 'sequencer': 'plusvibe', 'workspace': 'w'}]})
            settings.update({'fleets': [{'name': 'Main', 'writes': True}]})
            self.assertTrue(app.Settings(os.path.join(tmp, 'config.json')).cfg['fleets'][0]['writes'])
            settings.update({'fleets': [{'name': 'Main', 'writes': False}]})
            self.assertFalse(settings.public()['fleets'][0]['writes'])

    def test_a_wrong_key_says_what_the_sequencer_said(self):
        import io
        import urllib.error

        class Refuses:
            def get(self, path, **p):
                raise urllib.error.HTTPError('https://x', 400, 'Bad Request', {},
                                             io.BytesIO(b'{"message": "Invalid Api key"}'))
        f = {'name': 'Main', 'sequencer': 'plusvibe', 'workspace': 'w', 'key_env': 'INBOXSITREP_TEST_KEY'}
        with mock.patch.dict(os.environ, {'INBOXSITREP_TEST_KEY': 'k'}), \
                mock.patch.object(watch, 'client', lambda fleet: Refuses()):
            with self.assertRaises(ValueError) as e:
                watch.probe(f)
        self.assertEqual(str(e.exception), 'PlusVibe refused it: Invalid Api key.  Check the key and the workspace id')

    def test_a_simulated_workspace_is_built_and_checked_on_its_first_check(self):
        from inboxsitrep import app
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(app, 'DATA', tmp):
            settings = app.Settings(os.path.join(tmp, 'config.json'))
            settings.update({'fleets': [{'name': 'Demo', 'sequencer': 'sim'}], 'analyst': False,
                             'desktop': False})
            c = app.Checker(settings, [])
            f = settings.cfg['fleets'][0]
            with mock.patch('inboxsitrep.notify.desktop'):
                c.check(settings.cfg, f)
            self.assertTrue(os.path.exists(f['db']))
            self.assertTrue(any(i['status'] == 'open' for i in incidents.load(f['db'])))
            self.assertEqual(len(c.fleets), 1)


class Carry(unittest.TestCase):

    def test_first_incremental_run_on_an_old_database_counts_bounces_once(self):
        import datetime
        api = FakePlusVibe()
        today = datetime.date(2026, 10, 22)
        old = plusvibe.collect(api, days=10, today=today, say=lambda m: None)
        old.raw = {}                       # a database from before incremental collection
        again = plusvibe.collect(api, days=10, today=today, say=lambda m: None, prior=old)
        self.assertEqual(len(again.bounces), 1)
        self.assertEqual(sum(again.kinds['acme.com', '2026-10-20'].values()), 1)

    def test_dns_listings_survive_a_collection_until_dns_says_clean(self):
        old, new = Data(), Data()
        old.listed = {'a.com': ('surbl', '2026-10-30'), 'b.com': ('surbl', '2026-10-30')}
        new.dns = {'b.com': {'surbl': []}}
        store.carry(old, new)
        self.assertIn('a.com', new.listed)
        self.assertNotIn('b.com', new.listed)

    def test_what_is_kept_between_runs_is_pruned_to_the_window(self):
        import datetime
        api = FakePlusVibe()
        today = datetime.date(2026, 10, 22)
        old = plusvibe.collect(api, days=10, today=today, say=lambda m: None)
        self.assertTrue(old.raw['seen'])
        old.raw['seen'] = sorted(old.raw['seen']) + ['b:gone-long-ago']    # the 0.3.0 list form
        again = plusvibe.collect(api, days=10, today=today, say=lambda m: None, prior=old)
        self.assertIsInstance(again.raw['seen'], dict)
        self.assertEqual(sum(again.kinds['acme.com', '2026-10-20'].values()), 1)
        later = plusvibe.collect(api, days=10, today=today + datetime.timedelta(days=30),
                                 say=lambda m: None, prior=again)
        self.assertNotIn('b:gone-long-ago', later.raw['seen'])
        old, new = Data(), Data()
        old.history['a.com'] = {'2020-01-01': 90, datetime.date.today().isoformat(): 95}
        store.carry(old, new)
        self.assertEqual(list(new.history['a.com']), [datetime.date.today().isoformat()])


class Verdicts(unittest.TestCase):

    def fleet(self):
        # Ten ordinary domains making 10 positives per 1,000, plus the cases.
        import datetime
        data = Data()
        days = [(datetime.date(2026, 10, 20) + datetime.timedelta(days=n)).isoformat() for n in range(14)]
        for day in days:
            for n in range(10):
                data.add(day, 'ok%d.com' % n, 'google', [100, 4, 1, 1, 0])
            data.add(day, 'young.com', 'google', [50, 2, 1, 0, 0])               # 700 sends: bounces only
            data.add(day, 'tiny.com', 'google', [20, 1, 0, 0, 0])                # 280 sends: nothing
            data.add(day, 'copy.com', 'google', [100, 4, 1, 7, 6])               # spam filters refuse the message
            data.kinds['copy.com', day]['spam_filter'] = 5
            data.add(day, 'quiet.com', 'google', [100, 0, 0, 0, 0])              # sends that go nowhere
            data.add(day, 'gblock.com', 'google', [60, 0, 0, 30, 30])            # Gmail refuses, Microsoft takes it
            data.add(day, 'gblock.com', 'microsoft', [40, 2, 1, 0, 0])
            data.add(day, 'mute.com', 'google', [100, 0, 1, 1, 0])               # positives but no one replies
            data.add(day, 'badlist.com', 'google', [100, 4, 1, 6, 0])            # 6% bounced, none on us
            data.add(day, 'listed.com', 'google', [100, 4, 1, 2, 2])             # named on lists, still earning
            data.add(day, 'dead.com', 'google', [100, 1, 0, 2, 2])               # named on lists, earning nothing
            data.kinds['badlist.com', day]['user_unknown'] = 5
            data.kinds['listed.com', day]['spamhaus'] = 3
            data.kinds['dead.com', day]['spamhaus'] = 3
        return data, days

    def test_each_domain_gets_one_verdict_from_its_own_numbers(self):
        from inboxsitrep import verdicts
        data, days = self.fleet()
        v = verdicts.domains(data, days)
        self.assertEqual(v['ok0.com']['verdict'], 'healthy')
        self.assertEqual(v['young.com']['verdict'], 'early')
        self.assertEqual(v['tiny.com']['verdict'], 'not_enough_data')
        self.assertEqual(v['copy.com']['verdict'], 'copy')
        self.assertEqual(v['quiet.com']['verdict'], 'silent')
        self.assertEqual(v['gblock.com']['verdict'], 'gmail_blocked')
        self.assertEqual(v['mute.com']['verdict'], 'underperforming')       # under 1% replies
        self.assertEqual(v['badlist.com']['verdict'], 'bad_list')
        # On a list is something to get off, not a reason to stop a domain that earns.
        self.assertEqual(v['listed.com']['verdict'], 'listed')
        self.assertTrue(v['listed.com']['delist'][0].startswith('Spamhaus DBL'))
        self.assertEqual(v['dead.com']['verdict'], 'listed_low')
        self.assertEqual(v['badlist.com']['buckets'], {'list': 50})    # its latest 1,000 sends: 10 days
        self.assertEqual(v['ok0.com']['fleet_per_1000'], 9.0)          # 224 of 24,780

    def test_mailboxes_pushed_past_fifty_a_day_get_a_cap(self):
        import datetime
        from inboxsitrep import checks, plan
        data = Data()
        days = [(datetime.date(2026, 10, 1) + datetime.timedelta(days=i)).isoformat() for i in range(5)]
        for day in days:
            data.add(day, 'hot.com', 'google', [800, 8, 2, 20, 2])      # 10 mailboxes: 80 a day each
            data.add(day, 'calm.com', 'google', [300, 6, 2, 6, 0])      # 10 mailboxes: 30 a day each
        data.mailboxes = {'hot.com': 10, 'calm.com': 10}
        found = checks.volume(data, days[-1])
        self.assertEqual(found[0]['numbers']['domains'], ['hot.com'])
        steps = plan.Operator().plan(found[0])
        self.assertEqual((steps[0]['op'], steps[0]['targets'], steps[0]['limit']), ('set_limit', ['hot.com'], 30))

    def test_stop_is_for_domains_receivers_refuse_not_for_bad_lists(self):
        # 2,000 sends each; [replies, positives, bounces, refused for the sender].
        import datetime
        from inboxsitrep import verdicts
        data = Data()
        days = [(datetime.date(2026, 10, 1) + datetime.timedelta(days=i)).isoformat() for i in range(10)]
        cases = {'refused.com': [5, 0, 10, 9],      # 5% bounced, 4.5% refused, earns nothing: stop
                 'earning.com': [5, 2, 10, 9],      # the same refusals, still earning: rest it
                 'both.com': [5, 2, 12, 6],         # 6% bounced, 3% refused: list and domain
                 'list.com': [5, 2, 12, 2],         # 6% bounced, 1% refused: the list
                 'fine.com': [5, 2, 4, 2]}          # 2% bounced: fine
        for day in days:
            for dom, (rep, pos, bnc, snd) in cases.items():
                data.add(day, dom, 'google', [200, rep, pos, bnc, snd])
        v = verdicts.domains(data, days)
        self.assertEqual({k: v[k]['verdict'] for k in cases},
                         {'refused.com': 'burned', 'earning.com': 'refused', 'both.com': 'mixed',
                          'list.com': 'bad_list', 'fine.com': 'healthy'})
        data.raw = {'unlabeled': ['refused.com']}       # positives deleted with the campaign
        self.assertIsNone(verdicts.domains(data, days)['refused.com']['per_1000'])

    def test_a_verdict_is_on_the_latest_sends_whatever_the_range(self):
        # Clean for a fortnight, then 7% bounces from the receiver for five
        # days: a 14-day average would call it fine.
        import datetime
        from inboxsitrep import verdicts
        data = Data()
        days = [(datetime.date(2026, 10, 1) + datetime.timedelta(days=i)).isoformat() for i in range(19)]
        for n, day in enumerate(days):
            data.add(day, 'turned.com', 'google', [200, 4, 2, 2 if n < 14 else 14, 0 if n < 14 else 7])
            data.add(day, 'steady.com', 'google', [200, 4, 2, 2, 0])
        for span in (5, 14, 19):
            v = verdicts.domains(data, days[-span:])
            self.assertNotIn(v['turned.com']['verdict'], verdicts.GOOD, span)
            self.assertEqual(v['turned.com']['judged']['from'], days[-5])
            self.assertEqual(v['steady.com']['verdict'], 'healthy')
            self.assertEqual(v['turned.com']['sent'], 200 * span)       # the numbers still cover the range

    def test_campaigns_are_judged_on_replies_and_bounces_and_luck_is_not_a_winner(self):
        from inboxsitrep import verdicts
        data = Data()
        data.campaigns = {
            'a': {'camp_name': 'A', 'status': 'ACTIVE', 'sent_count': 10000, 'replied_count': 1200},
            'b': {'camp_name': 'B', 'status': 'ACTIVE', 'sent_count': 10000, 'replied_count': 300},
            'c': {'camp_name': 'C', 'status': 'ACTIVE', 'sent_count': 10000, 'replied_count': 50},
            'd': {'camp_name': 'D', 'status': 'ACTIVE', 'sent_count': 400, 'replied_count': 40},
            'e': {'camp_name': 'E', 'status': 'ACTIVE', 'sent_count': 1000, 'replied_count': 30,
                  'bounced_count': 60, 'sender_bounced_count': 5},
            # many replies tagged positive, but bouncing past its limit: not a winner
            'f': {'camp_name': 'F', 'status': 'ACTIVE', 'sent_count': 10000, 'replied_count': 100,
                  'positive_reply_count': 500, 'bounced_count': 620, 'sender_bounced_count': 380, 'bounce_rate_limit': 5},
        }
        got = {r['name']: r['verdict'] for r in verdicts.campaigns(data)}
        self.assertEqual(got, {'A': 'winner', 'B': 'iterate', 'C': 'loser', 'D': 'too_early', 'E': 'bad_list',
                               'F': 'bounces_high'})
        data.campaigns = {'x': {'camp_name': 'X', 'sent_count': 100000, 'replied_count': 20},
                          'y': {'camp_name': 'Y', 'sent_count': 1000, 'replied_count': 1}}
        self.assertEqual({r['name']: r['verdict'] for r in verdicts.campaigns(data)}['Y'], 'iterate')

    def test_ranges_and_the_guard_on_a_healthy_domain(self):
        from inboxsitrep import web
        data, days = self.fleet()
        self.assertEqual(web.pick_days(data, {'range': ['7']}), days[-7:])
        self.assertEqual(web.pick_days(data, {'from': [days[2]], 'to': [days[4]]}), days[2:5])
        fleet = type('F', (), {'data': data})()
        act = {'change': {'op': 'pause', 'targets': ['ok0.com', 'dead.com']}}
        web.guard(act, fleet)
        self.assertEqual(act['healthy'], ['ok0.com'])
        self.assertEqual([v['verdict'] for v in act['verdicts']], ['healthy', 'listed_low'])


class MergeFields(unittest.TestCase):

    def test_a_field_the_leads_do_not_have_or_often_leave_empty_is_caught(self):
        from inboxsitrep import copycheck
        seq = [{'step': 1, 'variations': [{'subject': 'For {{CompanyName}}', 'body':
            'Hi {{first_name}}, {% if city != blank %}in {{city}}{% endif %} we buy {{service}} firms. '
            'Good {{pipl_time_of_day}}.'}]}]
        sample = [{'first_name': 'Ann' if n % 4 else '', 'city': ''} for n in range(20)]
        details = [{'first_name': 'Ann', 'custom_service': 'roofing'}]
        got = {i['kind']: i for i in copycheck.mapping(seq, sample, details)}
        self.assertEqual(sorted(got), ['empty_field', 'unmapped'])
        self.assertIn('{{CompanyName}}', got['unmapped']['what'])
        self.assertIn('{{first_name}} is empty for 25%', got['empty_field']['what'])
        # {{service}} resolves to custom_service; {{city}} is guarded; pipl_ is the sequencer's own


class Listings(unittest.TestCase):

    def test_listed_domains_still_delivering_get_delisted_not_paused(self):
        import datetime
        from inboxsitrep import plan, brief
        op = plan.Operator()
        f = {'kind': 'listed', 'scope': 'family', 'target': 'x', 'title': 't',
             'numbers': {'domains': ['a.com', 'b.com', 'c.com'], 'failing': ['c.com']}}
        got = op.plan(f)
        self.assertEqual([(c['op'], c['targets']) for c in got], [('pause', ['c.com']), ('delist', ['a.com', 'b.com'])])
        data = Data()
        data.add('2026-10-16', 'a.com', 'google', [10, 0, 0, 0, 0])
        text = brief.brief(data, [], 'W', today=datetime.date(2026, 11, 3))
        self.assertIn('Nothing has been sent since 2026-10-16, 18 days ago', text)


class SlackQuestions(unittest.TestCase):

    def test_frames_acks_and_one_answer_per_question(self):
        import socket, struct
        from inboxsitrep import slackbot
        a, b = socket.socketpair()
        ws = slackbot.Socket(None, conn=a)

        def frame(text, op=1):
            data = text.encode()
            return bytes([0x80 | op, len(data)]) + data if len(data) < 126 else \
                bytes([0x80 | op, 126]) + struct.pack('>H', len(data)) + data
        env = {'envelope_id': 'e1', 'type': 'events_api', 'payload': {'event': {
            'type': 'app_mention', 'text': '<@U123> how is North doing?', 'channel': 'C1', 'ts': '1.0'}}}
        b.sendall(frame('ping', op=9) + frame(json.dumps(env)))
        got = json.loads(ws.recv())                    # the ping is answered on the way
        self.assertEqual(got['envelope_id'], 'e1')

        def read_frame():
            h = b.recv(2)
            n = h[1] & 0x7f
            if n == 126:
                n = struct.unpack('>H', b.recv(2))[0]
            mask = b.recv(4)
            data = b''
            while len(data) < n:
                data += b.recv(n - len(data))
            return h[0] & 0x0f, bytes(c ^ mask[i % 4] for i, c in enumerate(data))
        self.assertEqual(read_frame(), (10, b'ping'))     # pong
        asked = []
        bot = slackbot.Bot(lambda: ('xoxb', 'xapp'), lambda q: 'fine')
        bot.reply = lambda tok, ch, ts, text: asked.append((ch, ts, text))
        with mock.patch.object(slackbot.threading, 'Thread', lambda target, args, daemon: type('T', (), {'start': lambda self: target(*args)})()):
            self.assertTrue(bot.handle(got, ws, 'xoxb'))
            bot.handle(got, ws, 'xoxb')                     # Slack sending it again
        self.assertEqual(read_frame(), (1, b'{"envelope_id": "e1"}'))
        self.assertEqual(asked, [('C1', '1.0', 'how is North doing?')])
        self.assertFalse(bot.handle({'type': 'disconnect'}, ws, 'xoxb'))
        a.close(), b.close()


class TelegramQuestions(unittest.TestCase):

    def test_only_the_chat_that_sends_the_code_is_linked_and_answered(self):
        from inboxsitrep import telegram
        tg = {'code': '123456', 'chat_id': None}
        settings = type('S', (), {'cfg': {'notify': {'telegram': tg}}, 'save': lambda self: None})()
        sent, asked = [], []
        bot = telegram.Bot(settings, lambda q: 'fine')
        bot.reply = lambda token, chat, text: asked.append((chat, text))
        with mock.patch.object(telegram, 'send', lambda token, chat, text: sent.append((chat, text))):
            bot.handle('T', tg, {'chat': {'id': 7}, 'text': 'hello'})            # no code: ignored
            bot.handle('T', tg, {'chat': {'id': 9}, 'text': '/start 123456'})     # the code: linked
            with mock.patch.object(telegram.threading, 'Thread',
                                   lambda target, args, daemon: type('T', (), {'start': lambda self: target(*args)})()):
                bot.handle('T', tg, {'chat': {'id': 7}, 'text': 'how are my domains?'})   # someone else
                bot.handle('T', tg, {'chat': {'id': 9}, 'text': 'how are my domains?'})
        self.assertEqual(tg['chat_id'], 9)
        self.assertEqual(sent[0][0], 9)
        self.assertEqual(asked, [(9, 'how are my domains?')])

    def test_long_reports_go_in_pieces(self):
        from inboxsitrep import telegram
        parts = []
        with mock.patch.object(telegram, 'call', lambda token, method, body: parts.append(body['text'])):
            telegram.send('T', 1, ('line ' * 20 + '\n') * 100)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= telegram.LIMIT for p in parts))


class CopyCheck(unittest.TestCase):

    def test_broken_sentences_and_what_filters_hold_against_the_copy(self):
        from inboxsitrep import copycheck
        body = ('<p>{% if first_name != blank %}Hi {{first_name}}{% else %}Hi there{% endif %},</p>'
                '<p>Would you consider selling {{company_name}}{{random|for the right number| this year}}? '
                'It is at no cost to you. See https://example.com/x</p>')
        seq = [{'step': 1, 'variations': [{'variation': 'A', 'subject': 'Question about {{company_name}}', 'body': body}]},
               {'step': 2, 'variations': [{'variation': 'A', 'subject': '', 'body': '{{random|a|b}'}]}]
        issues = copycheck.check(seq, {'is_emailopened_tracking': 'yes'})
        kinds = [(i['level'], i['kind']) for i in issues]
        self.assertEqual(kinds[:2], [('broken', 'run_on'), ('broken', 'unbalanced')])
        self.assertIn('selling X for the right number', copycheck.plain(copycheck.first_option(body)).replace('Xfor', 'X for'))
        self.assertIn(('risky', 'links'), kinds)
        self.assertIn(('risky', 'words'), kinds)
        self.assertIn(('risky', 'tracking'), kinds)
        self.assertEqual(copycheck.plain(copycheck.first_option('{% if a %}Yes{% else %}No{% endif %} {{fallback|first_name|there}}')),
                         'Yes there')
        # "feel free" is not a filter word; one version for everyone is a risk
        self.assertEqual([i['kind'] for i in copycheck.check(
            [{'step': 1, 'variations': [{'body': 'Feel free to reply, {{first_name}}.'}]}])], ['identical'])
        self.assertEqual([i['kind'] for i in copycheck.check(
            [{'step': 1, 'variations': [{'subject': 'FREE AUDIT!', 'body': 'See bit.ly/x {{random|a|b}}'}]}])],
            ['shortener', 'subject'])


class TenantBlock(unittest.TestCase):

    def test_a_stray_block_bounce_then_normal_sending_is_not_a_block(self):
        import datetime
        data = Data()
        days = [(datetime.date(2026, 10, 20) + datetime.timedelta(days=n)).isoformat() for n in range(14)]
        for n, day in enumerate(days):
            data.add(day, 'stray.com', 'google', [200, 8, 2, 4, 0])
            data.add(day, 'other.com', 'google', [200, 8, 2, 4, 0])
            if n == 7:
                data.kinds['stray.com', day]['tenant_block'] = 1
            if n >= 11:                                   # every send fails at the source
                data.add(day, 'blocked.com', 'google', [50, 0, 0, 50, 50])
                data.kinds['blocked.com', day]['tenant_block'] = 50
        found = detect.tenant_blocks(data, days[-1])
        self.assertEqual(found[0]['numbers']['domains'], ['blocked.com'])
        self.assertEqual(found[0]['numbers']['recovered'], ['stray.com'])
        self.assertIn('1 more domain with a stray block is sending normally', found[0]['title'])


class Score(unittest.TestCase):

    def test_inboxsitrep_keeps_more_replies_than_the_bounce_rule(self):
        # Not "wastes fewer leads": a rule that pulls the whole fleet wastes
        # nothing, because it sends nothing.
        for seed in (11, 12):
            ow = score.judge(*score.run(seed, 'inboxsitrep'))
            rule = score.judge(*score.run(seed, 'bounce rule'))
            self.assertGreater(ow['positives'], rule['positives'])
            self.assertLess(ow['wasted'], score.judge(*score.run(seed, 'nothing'))['wasted'])
            self.assertEqual(ow['pulled'], 0)


if __name__ == '__main__':
    unittest.main()
