"""Synthetic owner-review lifecycle; no real windows or account access."""

import json
import fcntl
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError, digest
from icloud_for_agents.owner_review import OwnerReview, service_for
from icloud_for_agents.policy_management import PolicyManager


class OwnerReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = PlanStore(Path(self.temp.name) / 'plans')
        self.env = patch.dict(os.environ, {'ICLOUD_MAIL_ACCOUNT': 'synthetic@example.com',
            'ICLOUD_MAIL_ACCESS': 'read-only', 'ICLOUD_APPROVAL_POLICY': 'local-receipt',
            'ICLOUD_ENABLE_MAIL': '1', 'ICLOUD_ENABLE_CALENDAR': '1', 'ICLOUD_ENABLE_CONTACTS': '1',
            'ICLOUD_ENABLE_NOTES': '1', 'ICLOUD_ENABLE_REMINDERS': '1'}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def proposal(self, kind='calendar_change'):
        return self.store.prepare(kind, {'account': 'synthetic@example.com',
                                         'changes': [{'operation': 'delete', 'url': 'synthetic'}]})

    def wait(self, manager, plan_id):
        for _ in range(200):
            status = manager.status(plan_id)['status']
            if status != 'pending':
                return status
            time.sleep(0.005)
        self.fail('Synthetic owner review did not finish')

    def wait_worker(self, manager, plan_id, scope='service'):
        for _ in range(200):
            with manager.lock:
                status = manager.requests[(scope, plan_id)]['status']
            if status != 'pending':
                return status
            time.sleep(0.005)
        self.fail('Synthetic owner-review worker did not finish')

    def test_local_click_creates_exact_receipt_without_executing_plan(self):
        proposal = self.proposal()
        shown = []
        manager = OwnerReview(self.store, dialog=lambda review, heading: shown.append((review, heading)) or 'approved')
        request = manager.request(proposal['plan_id'])
        self.assertEqual(request['status'], 'pending')
        self.assertEqual(self.wait(manager, proposal['plan_id']), 'approved')
        receipt = self.store.read(self.store.path(proposal['plan_id'], '.approved'))
        self.assertEqual(receipt['sha256'], proposal['sha256'])
        self.assertEqual(receipt['authorization'], 'local-owner-graphical-review')
        self.assertIn(proposal['plan_id'], shown[0][0])
        self.assertIn(proposal['sha256'], shown[0][0])
        self.assertFalse(self.store.path(proposal['plan_id'], '.started').exists())
        self.assertEqual(self.store.consume(proposal['plan_id'], 'synthetic@example.com')['kind'], 'calendar_change')

    def test_receipt_is_not_reported_ready_until_its_admission_lock_is_released(self):
        proposal = self.proposal()
        identity = proposal['plan_id']
        written, release = threading.Event(), threading.Event()
        original = self.store.write_once
        def slow_write(path, value):
            original(path, value)
            if path.name == identity + '.approved':
                written.set()
                if not release.wait(5):
                    raise RuntimeError('Synthetic test did not release the receipt writer')
        manager = OwnerReview(self.store, dialog=lambda review, heading: 'approved')
        other_instance = OwnerReview(self.store, dialog=lambda review, heading: 'approved')
        with patch.object(self.store, 'write_once', side_effect=slow_write):
            try:
                manager.request(identity)
                self.assertTrue(written.wait(5))
                self.assertEqual(manager.status(identity)['status'], 'pending')
                self.assertEqual(manager.request(identity)['status'], 'pending')
                self.assertEqual(other_instance.status(identity)['status'], 'pending')
                self.assertEqual(other_instance.request(identity)['status'], 'pending')
            finally:
                release.set()
            self.assertEqual(self.wait(manager, identity), 'approved')
        self.assertEqual(other_instance.status(identity)['status'], 'approved')
        self.assertEqual(self.store.consume(identity, 'synthetic@example.com')['kind'], 'calendar_change')

    def test_cancelled_started_and_finished_statuses_take_precedence_over_receipt_lock(self):
        manager = OwnerReview(self.store, dialog=lambda review, heading: 'approved')
        for suffix, record, expected in (
                ('.cancelled', lambda p: {'plan_sha256': p['sha256']}, 'cancelled'),
                ('.started', lambda p: {'sha256': p['sha256'], 'account': 'synthetic@example.com'}, 'started_or_uncertain'),
                ('.result', lambda p: {'status': 'applied'}, 'applied')):
            proposal = self.proposal()
            identity = proposal['plan_id']
            self.store.write_once(self.store.path(identity, '.approved'), {'plan_id': identity, 'sha256': proposal['sha256']})
            self.store.write_once(self.store.path(identity, suffix), record(proposal))
            fd = os.open(self.store.root / 'approval-policy.lock', os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.subTest(suffix=suffix):
                    self.assertEqual(manager.status(identity)['status'], expected)
            finally:
                os.close(fd)

    def test_status_and_request_wait_while_receipt_creation_has_not_yet_started(self):
        proposal = self.proposal()
        identity = proposal['plan_id']
        manager = OwnerReview(self.store, dialog=lambda review, heading: 'approved')
        fd = os.open(self.store.root / 'approval-policy.lock', os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertFalse(self.store.path(identity, '.approved').exists())
            self.assertEqual(manager.status(identity)['status'], 'pending')
            self.assertEqual(manager.request(identity)['status'], 'pending')
            self.assertFalse(manager.requests)
        finally:
            os.close(fd)
        self.assertEqual(manager.status(identity)['status'], 'awaiting_local_approval')

    def test_cancel_failure_or_unexpected_result_never_creates_receipt(self):
        for decision in ('declined', None, True, 'APPROVED'):
            proposal = self.proposal()
            manager = OwnerReview(self.store, dialog=lambda review, heading: decision)
            manager.request(proposal['plan_id'])
            expected = 'declined' if decision == 'declined' else 'not_approved'
            self.assertEqual(self.wait(manager, proposal['plan_id']), expected)
            self.assertFalse(self.store.path(proposal['plan_id'], '.approved').exists())

    def test_changed_cancelled_or_expired_plan_cannot_be_approved_after_dialog(self):
        for action in ('changed', 'cancelled', 'expired'):
            proposal = self.proposal()
            entered, release = threading.Event(), threading.Event()
            def dialog(review, heading):
                entered.set()
                release.wait(3)
                return 'approved'
            manager = OwnerReview(self.store, dialog=dialog)
            manager.request(proposal['plan_id'])
            self.assertTrue(entered.wait(3))
            path = self.store.path(proposal['plan_id'], '.json')
            plan = self.store.read(path)
            if action == 'changed':
                plan['payload']['changes'][0]['url'] = 'different'
                path.write_text(json.dumps(plan))
            elif action == 'cancelled':
                self.store.write_once(self.store.path(proposal['plan_id'], '.cancelled'), {'plan_sha256': digest(plan)})
            else:
                plan['expires'] = 0
                path.write_text(json.dumps(plan))
            release.set()
            self.assertEqual(self.wait_worker(manager, proposal['plan_id']), 'not_approved')
            if action in ('cancelled', 'expired'):
                self.assertEqual(manager.status(proposal['plan_id'])['status'], action)
            self.assertFalse(self.store.path(proposal['plan_id'], '.approved').exists())

    def test_wrong_account_disabled_service_or_disabled_mode_never_shows_window(self):
        proposal = self.proposal()
        called = []
        manager = OwnerReview(self.store, dialog=lambda *args: called.append(args) or 'approved')
        for values in ({'ICLOUD_MAIL_ACCOUNT': 'other@example.com'}, {'ICLOUD_ENABLE_CALENDAR': '0'},
                       {'ICLOUD_MAIL_ACCESS': 'disabled'}):
            with self.subTest(values=values), patch.dict(os.environ, values), self.assertRaises(SafetyError):
                manager.request(proposal['plan_id'])
        self.assertEqual(called, [])

    def test_client_and_native_review_remain_available_when_mail_mode_is_disabled(self):
        with patch.dict(os.environ, {'ICLOUD_MAIL_ACCESS': 'disabled', 'ICLOUD_ENABLE_MAIL': '0',
                                     'ICLOUD_ENABLE_NOTES': '1'}):
            for kind in ('http_client', 'notes_change'):
                with self.subTest(kind=kind):
                    proposal = self.store.prepare(kind, {'account': 'synthetic@example.com'})
                    manager = OwnerReview(self.store, dialog=lambda review, heading: 'approved')
                    manager.request(proposal['plan_id'])
                    self.assertEqual(self.wait(manager, proposal['plan_id']), 'approved')

    def test_client_review_remains_available_when_every_service_is_disabled(self):
        with patch.dict(os.environ, {'ICLOUD_MAIL_ACCESS': 'disabled', 'ICLOUD_ENABLE_MAIL': '0',
                                     'ICLOUD_ENABLE_CALENDAR': '0', 'ICLOUD_ENABLE_CONTACTS': '0',
                                     'ICLOUD_ENABLE_NOTES': '0', 'ICLOUD_ENABLE_REMINDERS': '0'}):
            proposal = self.store.prepare('http_client', {'account': 'synthetic@example.com', 'action': 'revoke'})
            manager = OwnerReview(self.store, dialog=lambda review, heading: 'approved')
            manager.request(proposal['plan_id'])
            self.assertEqual(self.wait(manager, proposal['plan_id']), 'approved')

    def test_policy_change_during_review_does_not_create_a_receipt(self):
        proposal = self.store.prepare('send', {'account': 'synthetic@example.com', 'changes': []})
        entered, release = threading.Event(), threading.Event()
        def dialog(review, heading):
            entered.set(); release.wait(3); return 'approved'
        manager = OwnerReview(self.store, dialog=dialog)
        manager.request(proposal['plan_id'])
        self.assertTrue(entered.wait(3))
        with patch.dict(os.environ, {'ICLOUD_APPROVAL_POLICY': 'trusted-client'}):
            release.set()
            self.assertEqual(self.wait_worker(manager, proposal['plan_id']), 'not_approved')
            self.assertEqual(manager.status(proposal['plan_id'])['status'], 'awaiting_client_authorized_execution')
        self.assertFalse(self.store.path(proposal['plan_id'], '.approved').exists())

    def test_one_pending_window_is_reused_and_other_plans_wait(self):
        first, second = self.proposal(), self.proposal()
        entered, release = threading.Event(), threading.Event()
        def dialog(review, heading):
            entered.set(); release.wait(3); return 'declined'
        manager = OwnerReview(self.store, dialog=dialog)
        one = manager.request(first['plan_id'])
        self.assertTrue(entered.wait(3))
        self.assertEqual(manager.request(first['plan_id'])['request_id'], one['request_id'])
        with self.assertRaises(SafetyError):
            manager.request(second['plan_id'])
        release.set()
        self.assertEqual(self.wait(manager, first['plan_id']), 'declined')

    def test_every_plan_kind_maps_to_its_own_service(self):
        self.assertEqual(service_for('send'), 'mail')
        self.assertEqual(service_for('trash'), 'mail')
        self.assertEqual(service_for('calendar_change'), 'calendar')
        self.assertEqual(service_for('contact_change'), 'contacts')
        for kind in ('notes_change', 'notes_access', 'notes_grant', 'notes_recovery', 'notes_move_recovery', 'notes_edit_recovery'):
            self.assertEqual(service_for(kind), 'notes')
        for kind in ('reminders_change', 'reminders_access', 'reminders_grant', 'reminders_observation_recovery'):
            self.assertEqual(service_for(kind), 'reminders')
        with self.assertRaises(SafetyError):
            service_for('other')

    def test_managed_policy_review_writes_receipt_but_does_not_apply_policy(self):
        manager = PolicyManager(Path(self.temp.name) / 'policy')
        policy = {'account': 'synthetic@example.com', 'operations': ['mark_read'],
                  'source_folders': ['Synthetic'], 'destinations': {}, 'max_batch': 2,
                  'max_24h': 5, 'expires': None, 'protect_starred': True}
        manager.initialize_offline(policy)
        replacement = dict(policy, max_batch=1)
        proposal = manager.preview_change(replacement)
        plan_id = proposal['proposal']['plan_id']
        reviews = OwnerReview(self.store, dialog=lambda review, heading: 'approved')
        with patch('icloud_for_agents.managed_policy.context', return_value=(manager, None, None)):
            reviews.request(plan_id, 'managed-policy')
            for _ in range(200):
                status = reviews.status(plan_id, 'managed-policy')['status']
                if status != 'pending':
                    break
                time.sleep(0.005)
            self.assertEqual(status, 'approved')
            receipt = manager.store.read(manager.store.path(plan_id, '.approved'))
            self.assertEqual(receipt['sha256'], proposal['sha256'])
            self.assertEqual(receipt['authorization'], 'local-owner-graphical-review')
            self.assertEqual(manager.current()['policy'], policy)
            self.assertFalse(manager.store.path(plan_id, '.started').exists())

    def test_managed_policy_revocation_during_review_cannot_approve(self):
        manager = PolicyManager(Path(self.temp.name) / 'policy')
        policy = {'account': 'synthetic@example.com', 'operations': ['mark_read'],
                  'source_folders': ['Synthetic'], 'destinations': {}, 'max_batch': 2,
                  'max_24h': 5, 'expires': None, 'protect_starred': True}
        manager.initialize_offline(policy)
        proposal = manager.preview_change(dict(policy, max_batch=1))
        plan_id = proposal['proposal']['plan_id']
        entered, release = threading.Event(), threading.Event()
        def dialog(review, heading):
            entered.set(); release.wait(3); return 'approved'
        reviews = OwnerReview(self.store, dialog=dialog)
        with patch('icloud_for_agents.managed_policy.context', return_value=(manager, None, None)):
            reviews.request(plan_id, 'managed-policy')
            self.assertTrue(entered.wait(3))
            manager.revoke(plan_id)
            release.set()
            self.assertEqual(self.wait_worker(reviews, plan_id, 'managed-policy'), 'not_approved')
            self.assertEqual(reviews.status(plan_id, 'managed-policy')['status'], 'revoked')
            self.assertFalse(manager.store.path(plan_id, '.approved').exists())
