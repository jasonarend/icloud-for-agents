"""Synthetic regressions for defects found in the public-release review."""
import importlib
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from icloud_for_agents import core, server
from icloud_for_agents.dav_changes import contact_content, event_content
from icloud_for_agents.cleanup_access import CleanupAccess
from icloud_for_agents.service import MailService
from test_mail_safety import FakeIMAP


class ContactReplacementTests(unittest.TestCase):
    card = ('BEGIN:VCARD\r\nVERSION:3.0\r\nUID:synthetic\r\nFN:Example\r\n'
            'item1.EMAIL;TYPE=HOME:old@example.com\r\nitem2.TEL;TYPE=CELL:123\r\n'
            'item3.email:other@example.com\r\nX-CUSTOM:retain\r\nEND:VCARD\r\n')

    def test_replace_grouped_addresses_and_phones(self):
        result = contact_content({'emails': ['new@example.com'], 'phones': ['456']}, self.card)
        for old in ('old@example.com', 'other@example.com', '123'):
            self.assertNotIn(old, result)
        for retained in ('EMAIL:new@example.com', 'TEL:456', 'UID:synthetic', 'X-CUSTOM:retain'):
            self.assertIn(retained, result)

    def test_empty_lists_clear_grouped_properties(self):
        result = contact_content({'emails': [], 'phones': []}, self.card)
        self.assertNotIn('EMAIL', result.upper())
        self.assertNotIn('TEL', result.upper())

    def test_unedited_grouped_properties_are_retained(self):
        result = contact_content({'note': 'Updated'}, self.card)
        self.assertIn('item1.EMAIL;TYPE=HOME:old@example.com', result)
        self.assertIn('item2.TEL;TYPE=CELL:123', result)


class EventDurationTests(unittest.TestCase):
    event = ('BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:synthetic\r\n'
             'DTSTART:20261021T100000Z\r\nDURATION:PT1H\r\nSUMMARY:Before\r\n'
             'BEGIN:VALARM\r\nACTION:DISPLAY\r\nTRIGGER:-PT10M\r\n'
             'DURATION:PT5M\r\nREPEAT:2\r\nEND:VALARM\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n')

    def test_explicit_end_replaces_event_duration_and_preserves_alarm(self):
        result = event_content({'start': '2026-10-21T12:00:00Z',
                                'end': '2026-10-21T14:00:00Z'}, self.event)
        self.assertNotIn('DURATION:PT1H', result)
        self.assertIn('DTEND:20261021T140000Z', result)
        self.assertIn('DURATION:PT5M', result)

    def test_summary_only_keeps_duration_without_adding_end(self):
        result = event_content({'summary': 'After'}, self.event)
        self.assertIn('DURATION:PT1H', result)
        self.assertNotIn('DTEND', result)


class ReadStateVerificationTests(unittest.TestCase):
    def test_protected_batch_refuses_new_star_seen_on_final_snapshot(self):
        client = FakeIMAP((1,))
        client.boxes['INBOX'][1][b'FLAGS'] = ()
        reviewed = core.snapshot(client, 'INBOX', 71, [1])
        client.boxes['INBOX'][1][b'FLAGS'] = (b'\\Flagged',)
        with self.assertRaises(core.SafetyError):
            core.set_seen(client, 'INBOX', 71, [1], True, expected_snapshot=reviewed)
        self.assertFalse(any(c[0] == 'add_flags' for c in client.calls))
        self.assertNotIn(b'\\Seen', client.boxes['INBOX'][1][b'FLAGS'])

    def test_standing_read_rechecks_protection_at_action_time(self):
        from unittest.mock import Mock
        client = FakeIMAP((1,))
        client.boxes['INBOX'][1][b'FLAGS'] = ()
        access = Mock()
        def run(operation, folder, validity, uids, destination, reviewed, action):
            client.boxes['INBOX'][1][b'FLAGS'] = (b'\\Flagged',)
            return action()
        access.run.side_effect = run
        service = MailService('synthetic@example.com', 'synthetic-only', connector=lambda: client)
        with patch('icloud_for_agents.service.cleanup_access', return_value=access):
            with self.assertRaises(core.SafetyError):
                service.set_seen(folder='INBOX', uidvalidity=71, uids=[1], seen=True)
        self.assertNotIn(b'\\Seen', client.boxes['INBOX'][1][b'FLAGS'])

    def test_missing_or_wrong_final_flags_fail_both_directions(self):
        for seen in (True, False):
            for observed in ({}, {1: () if seen else (b'\\Seen',)}):
                with self.subTest(seen=seen, observed=observed):
                    client = FakeIMAP((1,))
                    with patch.object(client, 'get_flags', return_value=observed):
                        with self.assertRaises(core.SafetyError):
                            core.set_seen(client, 'INBOX', 71, [1], seen)

    def test_success_verifies_requested_state_and_preserves_other_flags(self):
        client = FakeIMAP((1,))
        for seen in (True, False):
            result = core.set_seen(client, 'INBOX', 71, [1], seen)
            self.assertEqual('\\Seen' in result['flags_after']['1'], seen)
            self.assertIn('\\Flagged', result['flags_after']['1'])
            self.assertIn('custom', result['flags_after']['1'])

    def test_failed_verification_records_uncertain_cleanup_and_blocks_followup(self):
        with tempfile.TemporaryDirectory() as tmp:
            account = 'synthetic@example.com'
            access = CleanupAccess('1' * 32, account, root=tmp, clock=lambda: 1000)
            grant = dict(grant_id='1' * 32, account=account, operations=['mark_read'],
                         source_folders=['INBOX'], destinations={}, max_batch=2, max_24h=10,
                         issued=999, expires=2000, rules_reference='Synthetic rules')
            access.authority.write_once(access.authority.path(access.grant_id, '.json'), grant)
            client = FakeIMAP((1, 2))
            for row in client.boxes['INBOX'].values():
                row[b'FLAGS'] = ()
            service = MailService(account, 'synthetic-only', connector=lambda: client)
            with patch('icloud_for_agents.service.cleanup_access', return_value=access):
                with patch.object(client, 'get_flags', return_value={}):
                    with self.assertRaises(core.SafetyError):
                        service.set_seen(folder='INBOX', uidvalidity=71, uids=[1], seen=True)
                results = list(access.audit.root.glob('*.result'))
                self.assertEqual(len(results), 1)
                self.assertEqual(access.audit.read(results[0])['status'], 'uncertain')
                with self.assertRaises(core.SafetyError):
                    service.set_seen(folder='INBOX', uidvalidity=71, uids=[2], seen=True)
                self.assertNotIn(b'\\Seen', client.boxes['INBOX'][2][b'FLAGS'])


class MoveCompletionTests(unittest.TestCase):
    def test_fallback_does_not_claim_success_when_deleted_flag_is_cleared(self):
        client = FakeIMAP((1,), capabilities=('UIDPLUS',))
        reviewed = core.snapshot(client, 'INBOX', 71, [1])
        expunge = client.uid_expunge
        def competing_client(uids):
            client.boxes['INBOX'][1][b'FLAGS'] = (b'\\Flagged', b'custom')
            expunge(uids)
        with patch.object(client, 'uid_expunge', side_effect=competing_client):
            with self.assertRaisesRegex(core.SafetyError, 'Source removal unconfirmed'):
                core.safe_move(client, 'INBOX', 71, [1], 'Retained', reviewed)
        self.assertIn(1, client.boxes['INBOX'])
        self.assertIn(10001, client.boxes['Retained'])

    def test_native_move_also_requires_source_absence(self):
        client = FakeIMAP((1,))
        reviewed = core.snapshot(client, 'INBOX', 71, [1])
        with patch.object(client, 'move', side_effect=client.copy):
            with self.assertRaisesRegex(core.SafetyError, 'Source removal unconfirmed'):
                core.safe_move(client, 'INBOX', 71, [1], 'Retained', reviewed)


class SharedDescriptorTests(unittest.TestCase):
    def test_mail_and_dav_share_one_handoff_in_either_order(self):
        for dav_first in (True, False):
            with self.subTest(dav_first=dav_first):
                importlib.reload(server)
                read_fd, write_fd = os.pipe()
                os.write(write_fd, json.dumps({'email': 'synthetic@example.com',
                                             'app_password': 'synthetic-only'}).encode())
                os.close(write_fd)
                try:
                    with patch.dict(os.environ, {'ICLOUD_SECRET_FD': str(read_fd),
                                    'ICLOUD_ENABLE_MAIL': '1', 'ICLOUD_ENABLE_CALENDAR': '1'}, clear=True):
                        with patch('icloud_for_agents.credentials.subprocess.run',
                                   side_effect=AssertionError('No Keychain lookup')):
                            first, second = ((server.dav_owner, server.service) if dav_first
                                             else (server.service, server.dav_owner))
                            one, two = first(), second()
                            self.assertEqual(one.account, two.account)
                            self.assertEqual(one._password, two._password)
                            self.assertNotIn('ICLOUD_SECRET_FD', os.environ)
                            if dav_first:
                                self.assertIsNotNone(server._dav_owner)
                finally:
                    try:
                        os.close(read_fd)
                    except OSError:
                        pass
                    importlib.reload(server)
