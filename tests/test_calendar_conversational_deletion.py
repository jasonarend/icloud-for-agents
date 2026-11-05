"""Synthetic tests for exact Calendar deletion under the trusted-client policy."""

from copy import deepcopy
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from icloud_for_agents.approvals import PlanStore, authorization_path, trusted_calendar_deletion
from icloud_for_agents.core import SafetyError
from icloud_for_agents.dav import DAVReader
from icloud_for_agents.dav_changes import DAVChanges, event_content


class CalendarConversationalDeletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = PlanStore(Path(self.temp.name) / 'plans')
        self.account = 'synthetic@example.com'
        self.collection = 'https://caldav.icloud.com/synthetic/'
        self.url = self.collection + 'event.ics'
        self.env = patch.dict(os.environ, {'ICLOUD_APPROVAL_POLICY': 'trusted-client', 'ICLOUD_MAIL_ACCOUNT': self.account})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.value = event_content({'summary': 'Synthetic', 'start': '2026-11-09T16:00:00Z',
                                    'end': '2026-11-09T17:00:00Z', 'timezone': 'UTC',
                                    'recurrence': {'frequency': 'weekly', 'count': 3}})
        self.reader = DAVReader(self.account, 'fake-only', Mock())
        self.reader.known_collection = lambda kind, target: target
        self.reader.transport = Mock(side_effect=lambda method, url, body, depth:
                                     (200, {'etag': '"synthetic"'}, self.value.encode()))
        self.writer = Mock(side_effect=self.write)
        self.editor = DAVChanges(self.reader, self.store, self.writer)

    def write(self, kind, item):
        if item['operation'] == 'delete_occurrence':
            self.value = item['content']
        else:
            self.reader.transport = Mock(return_value=(404, {}, b''))
        return {'url': item['url'], 'operation': item['operation'], 'status': 'applied'}

    def change(self, operation='delete'):
        if operation == 'delete':
            return {'operation': 'delete', 'url': self.url, 'recurrence_scope': 'series', 'irreversible_confirmed': True}
        return {'operation': 'delete_occurrence', 'url': self.url, 'recurrence_scope': 'occurrence',
                'occurrence_start': '2026-11-16T16:00:00Z', 'irreversible_confirmed': True}

    def prepare(self, change=None, **kwargs):
        return self.editor.prepare('cal', self.collection, [change or self.change()], temporary=True,
                                   deletion_authorization='trusted-client', **kwargs)

    def test_exact_approved_series_deletion_uses_client_authority_once(self):
        proposal = self.prepare()
        plan = self.store.proposal(proposal['plan_id'])
        self.assertFalse(proposal['local_receipt_required'])
        self.assertTrue(trusted_calendar_deletion(plan))
        self.assertEqual(authorization_path(plan), ('trusted-client', False))
        self.assertIn('not an independently verified human receipt', plan['payload']['calendar_deletion_authority_effect'])
        self.assertFalse(self.store.path(proposal['plan_id'], '.approved').exists())
        result = self.editor.execute(proposal['plan_id'])
        self.assertEqual(result['status'], 'applied')
        self.assertEqual(result['completed'][0]['verification'], 'series_resource_absent')
        started = self.store.read(self.store.path(proposal['plan_id'], '.started'))
        self.assertEqual(started['authorization'], 'trusted-client-policy; not an independent human receipt')
        with self.assertRaises(SafetyError):
            self.editor.execute(proposal['plan_id'])
        self.writer.assert_called_once()

    def test_exact_occurrence_deletion_binds_provider_proof_and_retains_series(self):
        proof = {'url': self.url, 'collection': self.collection, 'etag': '"synthetic"',
                 'snapshot_sha256': hashlib.sha256(self.value.encode()).hexdigest(),
                 'recurrence_id': '20261116T160000Z'}
        with patch('icloud_for_agents.calendar_occurrence_proof.prove_occurrence', return_value=proof):
            proposal = self.prepare(self.change('delete_occurrence'))
            self.assertFalse(proposal['local_receipt_required'])
            result = self.editor.execute(proposal['plan_id'])
        self.assertEqual(result['status'], 'applied')
        self.assertEqual(result['completed'][0]['verification'], 'reviewed_occurrence_change_readback')
        self.assertIn('EXDATE:20261116T160000Z', self.value)
        self.assertIn('RRULE:', self.value)

    def test_ordinary_event_deletion_requires_verified_absence(self):
        self.value = event_content({'summary': 'Synthetic', 'start': '2026-11-09T16:00:00Z',
                                    'end': '2026-11-09T17:00:00Z'})
        proposal = self.prepare({'operation': 'delete', 'url': self.url, 'irreversible_confirmed': True})
        result = self.editor.execute(proposal['plan_id'])
        self.assertEqual(result['status'], 'applied')
        self.assertEqual(result['completed'][0]['verification'], 'event_resource_absent')

    def test_ordinary_deletion_still_present_is_uncertain(self):
        self.value = event_content({'summary': 'Synthetic', 'start': '2026-11-09T16:00:00Z',
                                    'end': '2026-11-09T17:00:00Z'})
        self.writer.side_effect = None
        self.writer.return_value = {'url': self.url, 'operation': 'delete', 'status': 'applied'}
        proposal = self.prepare({'operation': 'delete', 'url': self.url, 'irreversible_confirmed': True})
        result = self.editor.execute(proposal['plan_id'])
        self.assertEqual(result['status'], 'failed_or_uncertain')
        self.assertEqual(result['diagnostic']['phase'], 'calendar_deletion_readback')
        self.assertFalse(self.store.operation_settled(proposal['plan_id']))

    def test_strict_policy_and_old_or_ordinary_plans_keep_local_receipt(self):
        ordinary = self.editor.prepare('cal', self.collection, [self.change()], temporary=True)
        self.assertTrue(ordinary['local_receipt_required'])
        special = self.prepare()
        with patch.dict(os.environ, {'ICLOUD_APPROVAL_POLICY': 'local-receipt'}):
            self.assertEqual(authorization_path(self.store.proposal(special['plan_id'])), ('local-receipt', True))
            with self.assertRaises(FileNotFoundError):
                self.editor.execute(special['plan_id'])
            with self.assertRaises(SafetyError):
                self.prepare()
        self.writer.assert_not_called()

    def test_invalid_scope_cannot_obtain_client_authority(self):
        change = self.change()
        cases = [('card', [change], True), ('cal', [change], False),
                 ('cal', [change, change], True), ('cal', [dict(change, irreversible_confirmed=False)], True),
                 ('cal', [{'operation': 'update', 'url': self.url, 'fields': {'summary': 'Changed'}}], True)]
        for kind, changes, temporary in cases:
            with self.subTest(kind=kind, changes=changes, temporary=temporary), self.assertRaises(SafetyError):
                self.editor.prepare(kind, self.collection, changes, temporary=temporary, deletion_authorization='trusted-client')
        self.writer.assert_not_called()

    def test_malformed_or_cross_service_marker_never_changes_authorization(self):
        proposal = self.prepare()
        valid = self.store.proposal(proposal['plan_id'])
        variants = []
        for field, value in (('kind', 'contact_change'),):
            plan = deepcopy(valid); plan[field] = value; variants.append(plan)
        for field, value in (('kind', 'card'), ('temporary_elevation', 'other'),
                             ('calendar_deletion_authority', 'other'), ('account', '')):
            plan = deepcopy(valid); plan['payload'][field] = value; variants.append(plan)
        for field, value in (('irreversible_confirmed', False), ('before', None),
                             ('url', 'https://caldav.icloud.com/other/event.ics'), ('content', 'unexpected')):
            plan = deepcopy(valid); plan['payload']['changes'][0][field] = value; variants.append(plan)
        for plan in variants:
            with self.subTest(plan=plan):
                self.assertFalse(trusted_calendar_deletion(plan))
                self.assertEqual(authorization_path(plan), ('local-receipt', True))

    def test_wrong_account_and_changed_snapshot_refuse_before_write(self):
        proposal = self.prepare()
        with patch.dict(os.environ, {'ICLOUD_MAIL_ACCOUNT': 'other@example.com'}), self.assertRaises(SafetyError):
            self.editor.execute(proposal['plan_id'])
        self.reader.transport = Mock(return_value=(200, {'etag': '"changed"'}, self.value.encode()))
        result = self.editor.execute(proposal['plan_id'])
        self.assertEqual(result['status'], 'failed_or_uncertain')
        self.writer.assert_not_called()

    def test_unresolved_prior_write_blocks_client_authorized_deletion(self):
        previous = self.store.prepare('calendar_change', {'account': self.account, 'changes': [{'operation': 'create'}]})
        self.store.consume(previous['plan_id'], self.account)
        self.store.finish(previous['plan_id'], {'status': 'failed_or_uncertain', 'completed': [], 'do_not_retry': True})
        proposal = self.prepare()
        with self.assertRaises(SafetyError):
            self.editor.execute(proposal['plan_id'])
        self.assertFalse(self.store.path(proposal['plan_id'], '.started').exists())
        self.writer.assert_not_called()

    def test_occurrence_authority_rejects_unbound_proof(self):
        proof = {'url': self.url, 'collection': self.collection, 'etag': '"synthetic"',
                 'snapshot_sha256': hashlib.sha256(self.value.encode()).hexdigest(),
                 'recurrence_id': '20261116T160000Z'}
        with patch('icloud_for_agents.calendar_occurrence_proof.prove_occurrence', return_value=proof):
            proposal = self.prepare(self.change('delete_occurrence'))
        valid = self.store.proposal(proposal['plan_id'])
        for field, value in (('url', self.collection + 'other.ics'), ('etag', '"other"'),
                             ('snapshot_sha256', '0' * 64), ('collection', 'https://caldav.icloud.com/other/')):
            plan = deepcopy(valid)
            plan['payload']['changes'][0]['occurrence_proof'][field] = value
            with self.subTest(field=field):
                self.assertFalse(trusted_calendar_deletion(plan))
                self.assertEqual(authorization_path(plan), ('local-receipt', True))
