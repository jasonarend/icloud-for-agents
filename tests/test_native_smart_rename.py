"""Exact custom-view renames, shared approvals and retained-error recovery."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from test_native_reminder_smart_lists import Reader, SOURCE, ID
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError, digest
from icloud_for_agents.native_access import require_write
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.native_grants import NativeGrants
from icloud_for_agents.native_protocol import mutation_request
from icloud_for_agents.native_reminders import RemindersCreateAdapter


class Adapter(RemindersCreateAdapter):
    backend_id = 'synthetic-smart-rename'
    def __init__(self):self.reader = Reader();self.calls = []
    def apply(self, item):
        require_write('reminders', item);self.calls.append(item['operation'])
        self.reader.value['title'] = item['fields']['title']
        return {'id': item['id'], 'source_id': item['source_id'], 'saved_locally': True}


class SmartRenameTests(unittest.TestCase):
    def setUp(self):
        env = {'ICLOUD_ENABLE_REMINDERS': '1', 'ICLOUD_MAIL_ACCESS': 'read-only', 'ICLOUD_MAIL_ACCOUNT': 'synthetic@example.com',
               'ICLOUD_APPROVAL_POLICY': 'trusted-client', 'ICLOUD_NATIVE_CONFIG': json.dumps({'reminders': {'source_ids': [SOURCE],
                'access_mode': 'read-only', 'advanced_backend': 'reminderkit', 'helper_path': '/synthetic/helper'}})}
        p = patch.dict(os.environ, env, clear=True);p.start();self.addCleanup(p.stop)
        temp = tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.store = PlanStore(Path(temp.name) / 'runtime')
        self.adapter = Adapter();self.engine = NativeChanges('reminders', 'synthetic@example.com', self.adapter, self.store)

    def change(self, title='Synthetic renamed view'):
        return {'operation': 'rename_smart_list', 'source_id': SOURCE, 'id': ID, 'fields': {'title': title}}

    def scope(self):
        return {'scenario': 'Synthetic exact view rename', 'rules': [{'operation': 'rename_smart_list', 'source_id': SOURCE,
                'container_id': ID, 'fields': ['title'], 'destinations': []}], 'max_batch': 1, 'max_24h': 2, 'expires': None}

    def test_once_preserves_raw_filter_and_read_only_defaults_and_forbids_replay(self):
        old = copy.deepcopy(self.adapter.reader.value)
        with self.assertRaises(SafetyError):self.engine.prepare([self.change()])
        p = self.engine.prepare_once([self.change()]);wire = mutation_request('reminders', p['review']['payload']['changes'][0])
        self.assertEqual(wire['change']['before'], old);self.assertEqual(self.engine.execute(p['plan_id'])['status'], 'applied')
        self.assertEqual(self.adapter.reader.value, dict(old, title='Synthetic renamed view'))
        self.assertEqual(os.environ['ICLOUD_MAIL_ACCESS'], 'read-only');self.engine.assert_settled()
        with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])

    def test_strict_independent_receipt_required_in_isolated_fixture(self):
        os.environ['ICLOUD_APPROVAL_POLICY'] = 'local-receipt';p = self.engine.prepare_once([self.change()])
        with self.assertRaises(FileNotFoundError):self.engine.execute(p['plan_id'])
        # Independent owner fixture only, never a receipt for live data.
        self.store.write_once(self.store.path(p['plan_id'], '.approved'), {'plan_id': p['plan_id'], 'sha256': p['sha256']})
        self.assertEqual(self.engine.execute(p['plan_id'])['status'], 'applied')

    def test_exact_title_fields_and_custom_identity_validation(self):
        for title in ('', ' ', '\n', '\ud800', 'x' * 1001, True):
            with self.assertRaises(SafetyError):self.engine.prepare_once([self.change(title)])
        for change in (dict(self.change(), extra=True), dict(self.change(), id='builtin'),
                       dict(self.change(), source_id=ID), dict(self.change(), fields={'title': 'New', 'filter': {}})):
            with self.assertRaises(SafetyError):self.engine.prepare_once([change])
        with self.assertRaises(SafetyError):self.engine.prepare_once([self.change(self.adapter.reader.value['title'])])
        self.assertEqual(self.adapter.calls, [])

    def test_changed_title_or_raw_filter_refuses_before_started_marker(self):
        p = self.engine.prepare_once([self.change()]);self.adapter.reader.value['definition_json'] = '{"changed":true}'
        with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
        self.assertFalse(self.store.path(p['plan_id'], '.started').exists());self.assertEqual(self.adapter.calls, [])

    def test_unavailable_backend_and_disabled_service_cannot_prepare(self):
        for update in ({'ICLOUD_ENABLE_REMINDERS': '0'}, {'ICLOUD_MAIL_ACCESS': 'disabled'},
                       {'ICLOUD_NATIVE_CONFIG': json.dumps({'reminders': {'source_ids': [SOURCE], 'helper_path': '/synthetic/helper'}})}):
            with patch.dict(os.environ, update), self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])

    def test_scoped_rename_roundtrip_quota_and_revocation(self):
        m = NativeGrants(self.engine);g = m.prepare(self.scope())['plan_id'];m.activate(g)
        for title in ('Synthetic renamed view', 'Synthetic Smart List'):
            p = self.engine.prepare_with_grant(g, [self.change(title)]);self.assertEqual(self.engine.execute(p['plan_id'])['status'], 'applied')
            self.assertEqual(self.store.read(self.store.path(p['plan_id'], '.native-grant-use'))['count'], 1)
        p = self.engine.prepare_with_grant(g, [self.change()])
        with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
        self.assertFalse(self.store.path(p['plan_id'], '.started').exists());m.revoke(g)
        with self.assertRaises(SafetyError):self.engine.prepare_with_grant(g, [self.change()])

    def test_revocation_blocks_an_already_prepared_scoped_plan(self):
        m = NativeGrants(self.engine);g = m.prepare(self.scope())['plan_id'];m.activate(g)
        p = self.engine.prepare_with_grant(g, [self.change()]);m.revoke(g)
        with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
        self.assertEqual(self.adapter.calls, [])

    def test_verification_rejects_any_raw_filter_change(self):
        apply = self.adapter.apply
        def altered(item):
            result = apply(item);self.adapter.reader.value['definition_json'] = '{"changed":true}';return result
        p = self.engine.prepare_once([self.change()])
        with patch.object(self.adapter, 'apply', side_effect=altered):self.assertEqual(self.engine.execute(p['plan_id'])['status'], 'failed_or_uncertain')
        with self.assertRaises(SafetyError):self.engine.assert_settled()

    def test_returned_id_failed_readback_can_be_reconciled_without_replay(self):
        p = self.engine.prepare_once([self.change()])
        with patch.object(self.adapter, 'verify', return_value=False):self.assertEqual(self.engine.execute(p['plan_id'])['status'], 'failed_or_uncertain')
        self.assertEqual(self.engine.reconcile_known_changes(p['plan_id'])['status'], 'applied_after_readback_reconciliation')
        self.engine.assert_settled();self.assertEqual(len(self.adapter.calls), 1)

    def test_retained_terminal_error_expected_state_and_tamper_checks(self):
        from icloud_for_agents.native_reminder_exchange import begin, finish
        from icloud_for_agents.native_reminder_lifecycle import reconcile
        apply = self.adapter.apply
        def uncertain(item):
            apply(item);finish(begin(mutation_request('reminders', item)), {'protocol_version': 1, 'error': 'mutation_failed_or_uncertain', 'mutation_dispatched': True})
            raise SafetyError('Synthetic terminal error')
        p = self.engine.prepare_once([self.change()])
        with patch.object(self.adapter, 'apply', side_effect=uncertain):self.engine.execute(p['plan_id'])
        original = self.store.path(p['plan_id'], '.result').read_bytes();self.assertEqual(self.engine.reconcile_known_changes(p['plan_id'])['status'], 'applied_after_readback_reconciliation')
        self.engine.assert_settled();path = self.store.path(p['plan_id'], '.native-lifecycle-readback');record = self.store.read(path)
        record['observed']['snapshot']['definition_json'] = '{"forged":true}';record['observed_sha256'] = digest(record['observed']);path.write_text(json.dumps(record))
        self.assertFalse(self.store._native_lifecycle_readback_completed(p['plan_id']));self.assertEqual(self.store.path(p['plan_id'], '.result').read_bytes(), original)

    def test_terminal_error_unchanged_review_never_claims_success_or_replays(self):
        from icloud_for_agents.native_reminder_exchange import begin, finish
        from icloud_for_agents.native_reminder_recovery import ReminderObservationRecovery
        def uncertain(item):
            finish(begin(mutation_request('reminders', item)), {'protocol_version': 1, 'error': 'mutation_failed_or_uncertain', 'mutation_dispatched': True})
            raise SafetyError('Synthetic unchanged terminal error')
        p = self.engine.prepare_once([self.change()])
        with patch.object(self.adapter, 'apply', side_effect=uncertain):self.engine.execute(p['plan_id'])
        original = self.store.path(p['plan_id'], '.result').read_bytes();r = ReminderObservationRecovery(self.engine.account, self.store, self.adapter)
        review = r.prepare(p['plan_id']);self.assertTrue(review['review']['payload']['observed']['observed_snapshot_matches_before']);r.execute(review['plan_id'])
        self.engine.assert_settled();self.assertEqual(self.store.path(p['plan_id'], '.result').read_bytes(), original)
        with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
