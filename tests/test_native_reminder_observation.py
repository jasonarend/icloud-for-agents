"""Uncertain reminder observations are read-only and retain the write guard."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.native_reminder_observation import inspect_uncertain_reminder


class Adapter:
    backend_id = 'synthetic-reminders-v1'

    def __init__(self):
        self.before = {'list': {'id': 'list', 'source_id': 'selected', 'writable': True},
                       'item': {'id': 'reminder', 'source_id': 'selected', 'list_id': 'list', 'title': 'Synthetic'}}
        self.calls = 0

    def snapshot(self, item):
        self.calls += 1
        assert item['operation'] == 'update_reminder'
        return copy.deepcopy(self.before)

    def apply(self, item):
        raise AssertionError('Observation must not dispatch a mutation')


class ReminderObservationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = PlanStore(Path(temp.name) / 'runtime')
        self.adapter = Adapter()
        self.account = 'synthetic@example.com'
        env = {'ICLOUD_ENABLE_REMINDERS': '1', 'ICLOUD_MAIL_ACCESS': 'read-only',
               'ICLOUD_APPROVAL_POLICY': 'trusted-client', 'ICLOUD_MAIL_ACCOUNT': self.account,
               'ICLOUD_NATIVE_CONFIG': json.dumps({'reminders': {'source_ids': ['selected'], 'access_mode': 'read-only'}})}
        environment = patch.dict(os.environ, env, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.engine = NativeChanges('reminders', self.account, self.adapter, self.store)
        item = {'operation': 'set_urgent', 'source_id': 'selected', 'id': 'reminder',
                'fields': {'urgent': True}, 'before': copy.deepcopy(self.adapter.before)}
        self.plan = self.store.prepare('reminders_change', {'account': self.account,
            'context': self.engine.context(True), 'changes': [item]})
        self.identity = self.plan['plan_id']
        self.store.consume(self.identity, self.account)
        self.store.finish(self.identity, {'status': 'failed_or_uncertain', 'phase': 'mutation',
            'attempted': [], 'completed': [], 'do_not_retry': True})

    def inspect(self):
        return inspect_uncertain_reminder(self.engine, self.identity)

    def test_observe_unchanged_and_changed_without_releasing_guard(self):
        original = self.store.path(self.identity, '.result').read_bytes()
        result = self.inspect()
        self.assertTrue(result['observed_snapshot_matches_before'])
        self.assertTrue(result['original_uncertainty_retained'])
        self.assertFalse(result['causal_dispatch_proven'])
        self.assertFalse(result['data_mutated'])
        self.assertEqual(result['terminal_response'],'not_recorded')
        self.assertNotIn('Synthetic', json.dumps(result))
        self.adapter.before['item']['title'] = 'Changed'
        self.assertFalse(self.inspect()['observed_snapshot_matches_before'])
        self.assertEqual(original, self.store.path(self.identity, '.result').read_bytes())
        with self.assertRaises(SafetyError):
            self.engine.assert_settled()

    def test_source_backend_and_identity_mismatches_refuse(self):
        self.adapter.backend_id = 'other'
        with self.assertRaises(SafetyError): self.inspect()
        self.adapter.backend_id = 'synthetic-reminders-v1'
        self.adapter.before['item']['id'] = 'other'
        with self.assertRaises(SafetyError): self.inspect()
        self.adapter.before['item']['id'] = 'reminder'
        self.adapter.before['item']['source_id'] = 'foreign'
        with self.assertRaises(SafetyError): self.inspect()
        self.assertFalse(self.store.operation_settled(self.identity))

    def test_access_drift_after_read_refuses(self):
        snapshot = self.adapter.snapshot
        def drift(item):
            result = snapshot(item)
            os.environ['ICLOUD_MAIL_ACCESS'] = 'disabled'
            return result
        self.adapter.snapshot = drift
        with self.assertRaises(SafetyError): self.inspect()
        self.assertFalse(self.store.operation_settled(self.identity))

    def test_child_creation_and_unknown_operations_are_not_treated_as_single_object_edits(self):
        from icloud_for_agents.core import digest
        for operation in ('create_subtask', 'unknown'):
            plan = copy.deepcopy(self.store.read(self.store.path(self.identity, '.json')))
            plan['payload']['changes'][0]['operation'] = operation
            with patch.object(self.store, 'read', side_effect=lambda path: {'sha256': digest(plan)} if str(path).endswith('.started') else plan if str(path).endswith('.json') else {'status': 'failed_or_uncertain', 'phase': 'mutation'}):
                with self.subTest(operation=operation), self.assertRaises(SafetyError): self.inspect()
        self.assertEqual(self.adapter.calls, 0)
