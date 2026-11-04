"""Synthetic reconciliation of one returned series reschedule with uncertain readback."""

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError
from icloud_for_agents.dav import DAVReader
from icloud_for_agents.dav_changes import DAVChanges, event_content


class CalendarSeriesReadbackRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = PlanStore(Path(self.directory.name) / 'plans')
        self.account = 'synthetic@example.com'
        self.collection = 'https://caldav.icloud.com/synthetic/'
        self.url = self.collection + 'event.ics'
        self.env = patch.dict(os.environ, {'ICLOUD_APPROVAL_POLICY': 'trusted-client', 'ICLOUD_MAIL_ACCOUNT': self.account})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.before = event_content({'summary': 'Synthetic', 'start': '2026-11-02T15:00:00Z',
                                     'end': '2026-11-02T16:00:00Z', 'timezone': 'UTC',
                                     'recurrence': {'frequency': 'weekly', 'count': 3}})
        self.transport = Mock(return_value=(200, {'etag': '"before"'}, self.before.encode()))
        self.reader = DAVReader(self.account, 'fake-only', self.transport)
        self.reader.known_collection = Mock(side_effect=lambda kind, url: url)
        self.writer = Mock(return_value={'url': self.url, 'operation': 'update', 'status': 'applied'})
        self.editor = DAVChanges(self.reader, self.store, self.writer)
        fields = {'start': '2026-11-09T16:00:00Z', 'end': '2026-11-09T17:00:00Z', 'timezone': 'UTC',
                  'recurrence': {'frequency': 'weekly', 'count': 4}}
        proposal = self.editor.prepare('cal', self.collection, [{'operation': 'update', 'url': self.url,
                                       'recurrence_scope': 'series', 'fields': fields}])
        self.plan_id = proposal['plan_id']
        self.item = proposal['review']['payload']['changes'][0]
        self.transport.side_effect = [(200, {'etag': '"before"'}, self.before.encode()),
                                      (200, {'etag': '"after"'}, self.item['content'].replace('COUNT=4', 'COUNT=5').encode())]
        result = self.editor.execute(self.plan_id)
        self.assertEqual(result['status'], 'failed_or_uncertain')
        self.transport.side_effect = None
        self.transport.return_value = (200, {'etag': '"after"'}, self.item['content'].encode())
        self.transport.reset_mock()
        self.writer.reset_mock()

    def recover(self):
        return self.store.reconcile_calendar_series_update_readback(self.plan_id, self.editor)

    def test_verified_readback_settles_without_mutation_or_replay(self):
        originals = {suffix: self.store.path(self.plan_id, suffix).read_bytes() for suffix in ('.json', '.started', '.result')}
        result = self.recover()
        self.assertEqual(result['status'], 'calendar_series_update_verified_after_readback')
        self.assertTrue(self.store.operation_settled(self.plan_id))
        self.assertEqual(self.store.status(self.plan_id)['original_status'], 'failed_or_uncertain')
        self.writer.assert_not_called()
        self.transport.assert_called_once_with('GET', self.url, '', 0)
        for suffix, content in originals.items():
            self.assertEqual(self.store.path(self.plan_id, suffix).read_bytes(), content)
        with self.assertRaises(SafetyError):
            self.store.consume(self.plan_id, self.account)
        fresh = self.store.prepare('calendar_change', {'account': self.account, 'changes': []})
        self.store.consume(fresh['plan_id'], self.account)

    def test_missing_wrong_or_weak_observation_does_not_settle(self):
        for response in ((404, {}, b''), (200, {'etag': 'W/"weak"'}, self.item['content'].encode()),
                         (200, {'etag': '"other"'}, self.item['content'].replace('COUNT=4', 'COUNT=5').encode())):
            self.transport.return_value = response
            with self.subTest(response=response), self.assertRaises(SafetyError):
                self.recover()
            self.assertFalse(self.store.operation_settled(self.plan_id))
        self.writer.assert_not_called()

    def test_wrong_phase_or_unreturned_write_does_not_settle(self):
        path = self.store.path(self.plan_id, '.result')
        original = self.store.read(path)
        variants = []
        for phase in ('conditional_write', 'identity_preflight'):
            value = deepcopy(original)
            value['diagnostic']['phase'] = phase
            variants.append(value)
        value = deepcopy(original)
        value['completed'] = []
        variants.append(value)
        value = deepcopy(original)
        value['completed'][0]['operation'] = 'create'
        variants.append(value)
        for value in variants:
            path.write_text(json.dumps(value))
            with self.subTest(value=value), self.assertRaises(SafetyError):
                self.recover()
        path.write_text(json.dumps(original))
        self.assertFalse(self.store.operation_settled(self.plan_id))

    def test_tampered_observation_or_original_invalidates_record(self):
        self.recover()
        path = self.store.path(self.plan_id, '.calendar-series-readback')
        original = self.store.read(path)
        for key, value in (('uid', 'other'), ('url', self.collection + 'other.ics'), ('original_sha256', '0' * 64)):
            record = deepcopy(original)
            record[key] = value
            path.write_text(json.dumps(record))
            with self.subTest(key=key):
                self.assertFalse(self.store.operation_settled(self.plan_id))
        path.write_text(json.dumps(original))
        result = self.store.path(self.plan_id, '.result')
        saved = self.store.read(result)
        result.write_text(json.dumps(dict(saved, modified=True)))
        self.assertFalse(self.store.operation_settled(self.plan_id))

    def test_wrong_account_or_missing_resource_does_not_write(self):
        with patch.dict(os.environ, {'ICLOUD_MAIL_ACCOUNT': 'other@example.com'}), self.assertRaises(SafetyError):
            self.recover()
        self.transport.assert_not_called()
        self.writer.assert_not_called()
