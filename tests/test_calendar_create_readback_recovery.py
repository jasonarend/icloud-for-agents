"""Synthetic-only recovery of one returned recurring create with failed readback."""

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError, digest
from icloud_for_agents.dav import DAVReader
from icloud_for_agents.dav_changes import DAVChanges


class CalendarCreateReadbackRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = PlanStore(Path(self.directory.name) / 'plans')
        self.account = 'synthetic@example.com'
        self.collection = 'https://caldav.icloud.com/synthetic/'
        self.env = patch.dict(os.environ, {'ICLOUD_APPROVAL_POLICY': 'trusted-client', 'ICLOUD_MAIL_ACCOUNT': self.account})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.transport = Mock()
        self.reader = DAVReader(self.account, 'fake-only', self.transport)
        self.reader.known_collection = Mock(side_effect=lambda kind, url: url)
        self.writer = Mock()
        self.editor = DAVChanges(self.reader, self.store, self.writer)
        fields = {'summary': 'Synthetic recurrence', 'start': '2026-11-09T16:00:00Z',
                  'end': '2026-11-09T17:00:00Z', 'timezone': 'UTC',
                  'recurrence': {'frequency': 'weekly', 'count': 3}}
        proposal = self.editor.prepare('cal', self.collection, [{'operation': 'create', 'fields': fields}])
        self.plan_id = proposal['plan_id']
        self.item = proposal['review']['payload']['changes'][0]
        self.writer.return_value = {'url': self.item['url'], 'operation': 'create', 'status': 'applied'}
        self.transport.return_value = (200, {'etag': '"synthetic"'}, self.item['content'].replace('COUNT=3', 'COUNT=4').encode())
        result = self.editor.execute(self.plan_id)
        self.assertEqual(result['status'], 'failed_or_uncertain')
        self.assertEqual(result['diagnostic']['phase'], 'recurring_readback')
        self.transport.return_value = (200, {'etag': '"observed"'}, self.item['content'].encode())
        self.transport.reset_mock()
        self.writer.reset_mock()

    def recover(self):
        return self.store.reconcile_calendar_create_readback(self.plan_id, self.editor)

    def rewrite(self, path, value):
        path.write_text(json.dumps(value), encoding='utf-8')

    def test_live_verified_readback_settles_admission_without_replay_or_mutation(self):
        originals = {suffix: self.store.path(self.plan_id, suffix).read_bytes() for suffix in ('.json', '.started', '.result')}
        self.assertFalse(self.store.operation_settled(self.plan_id))
        result = self.recover()
        self.assertEqual(result['status'], 'calendar_create_verified_after_readback')
        self.assertTrue(result['original_uncertainty_retained'])
        self.assertTrue(self.store.operation_settled(self.plan_id))
        self.assertEqual(self.store.status(self.plan_id)['original_status'], 'failed_or_uncertain')
        self.transport.assert_called_once_with('GET', self.item['url'], '', 0)
        self.writer.assert_not_called()
        for suffix, value in originals.items():
            self.assertEqual(self.store.path(self.plan_id, suffix).read_bytes(), value)
        with self.assertRaises(SafetyError):
            self.store.consume(self.plan_id, self.account)
        fresh = self.store.prepare('calendar_change', {'account': self.account, 'changes': []})
        self.store.consume(fresh['plan_id'], self.account)

    def test_record_binds_full_observation_uid_and_all_original_records(self):
        self.recover()
        record = self.store.read(self.store.path(self.plan_id, '.calendar-create-readback'))
        self.assertEqual(record['original_id'], self.plan_id)
        self.assertEqual(record['url'], self.item['url'])
        self.assertIn('UID:' + record['uid'] + '\r\n', self.item['content'])
        self.assertEqual(record['observation']['etag'], '"observed"')
        self.assertEqual(record['verification'], 'full_reviewed_recurring_create_readback')
        self.assertFalse(record['mutation_attempted'])
        for suffix, key in (('.json', 'original_sha256'), ('.started', 'started_sha256'), ('.result', 'original_result_sha256')):
            self.assertEqual(record[key], digest(self.store.read(self.store.path(self.plan_id, suffix))))
        with self.assertRaises(FileExistsError):
            self.recover()

    def test_missing_mismatched_and_weak_etag_readbacks_do_not_settle(self):
        for response in ((404, {}, b''), (200, {'etag': 'W/"weak"'}, self.item['content'].encode()),
                         (200, {'etag': '"changed"'}, self.item['content'].replace('COUNT=3', 'COUNT=5').encode())):
            self.transport.return_value = response
            with self.subTest(response=response), self.assertRaises(SafetyError):
                self.recover()
            self.assertFalse(self.store.operation_settled(self.plan_id))
            self.assertFalse(self.store.path(self.plan_id, '.calendar-create-readback').exists())
        self.writer.assert_not_called()

    def test_readback_timeout_cannot_create_a_record(self):
        self.transport.side_effect = TimeoutError('synthetic timeout')
        with self.assertRaises(TimeoutError):
            self.recover()
        self.assertFalse(self.store.operation_settled(self.plan_id))
        self.writer.assert_not_called()

    def test_wrong_original_phase_write_type_and_incomplete_write_refuse(self):
        path = self.store.path(self.plan_id, '.result')
        original = self.store.read(path)
        variants = []
        for phase in ('conditional_write', 'occurrence_readback', 'identity_preflight'):
            value = deepcopy(original)
            value['diagnostic']['phase'] = phase
            variants.append(value)
        value = deepcopy(original)
        value['completed'] = []
        variants.append(value)
        value = deepcopy(original)
        value['completed'][0]['operation'] = 'update'
        variants.append(value)
        value = deepcopy(original)
        value['completed'][0]['url'] = self.collection + 'other.ics'
        variants.append(value)
        value = deepcopy(original)
        value['completed'].append(deepcopy(value['completed'][0]))
        variants.append(value)
        for variant in variants:
            self.rewrite(path, variant)
            with self.subTest(variant=variant), self.assertRaises(SafetyError):
                self.recover()
        self.rewrite(path, original)
        self.transport.assert_not_called()

    def test_wrong_account_store_or_caller_evidence_refuse(self):
        with patch.dict(os.environ, {'ICLOUD_MAIL_ACCOUNT': 'other@example.com'}), self.assertRaises(SafetyError):
            self.recover()
        self.reader.account = 'other@example.com'
        with self.assertRaises(SafetyError):
            self.recover()
        self.reader.account = self.account
        other = DAVChanges(self.reader, PlanStore(Path(self.directory.name) / 'other'), Mock())
        with self.assertRaises(SafetyError):
            self.store.reconcile_calendar_create_readback(self.plan_id, other)
        with self.assertRaises(SafetyError):
            self.store.reconcile_calendar_create_readback(self.plan_id, {'all_fields_verified': True})
        self.transport.assert_not_called()

    def test_tampered_durable_observation_does_not_settle(self):
        self.recover()
        path = self.store.path(self.plan_id, '.calendar-create-readback')
        original = self.store.read(path)
        variants = []
        for key, value in (('uid', 'other'), ('account', 'other@example.com'), ('url', self.collection + 'other.ics'),
                           ('original_sha256', '0' * 64), ('started_sha256', '0' * 64), ('observed_at', 0)):
            variant = deepcopy(original)
            variant[key] = value
            variants.append(variant)
        variant = deepcopy(original)
        variant['observation']['etag'] = '"different"'
        variants.append(variant)
        variant = deepcopy(original)
        variant['observation']['content'] = variant['observation']['content'].replace('COUNT=3', 'COUNT=4')
        variants.append(variant)
        for variant in variants:
            self.rewrite(path, variant)
            with self.subTest(variant=variant):
                self.assertFalse(self.store.operation_settled(self.plan_id))
                fresh = self.store.prepare('calendar_change', {'account': self.account, 'changes': []})
                with self.assertRaises(SafetyError):
                    self.store.consume(fresh['plan_id'], self.account)
        self.rewrite(path, original)

    def test_tampered_original_records_invalidate_observation(self):
        self.recover()
        for suffix in ('.json', '.started', '.result'):
            path = self.store.path(self.plan_id, suffix)
            original = self.store.read(path)
            changed = deepcopy(original)
            changed['synthetic_change'] = True
            self.rewrite(path, changed)
            self.assertFalse(self.store.operation_settled(self.plan_id))
            self.rewrite(path, original)

    def test_nonrecurring_create_or_wrong_kind_cannot_use_recovery(self):
        path = self.store.path(self.plan_id, '.json')
        original = self.store.read(path)
        for variant in (dict(original, kind='contact_change'), deepcopy(original)):
            if variant['kind'] == 'calendar_change':
                del variant['payload']['changes'][0]['fields']['recurrence']
            self.rewrite(path, variant)
            with self.assertRaises(SafetyError):
                self.recover()
        self.rewrite(path, original)


if __name__ == '__main__':
    unittest.main()
