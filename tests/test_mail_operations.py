"""Ordinary-Mail crash/replay/recovery tests with synthetic IMAP only."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError, digest
from icloud_for_agents.mail_operations import MailOperations
from icloud_for_agents.service import MailService
from test_mail_safety import FakeIMAP


ACCOUNT = "synthetic@example.invalid"


class RecoveryIMAP(FakeIMAP):
    @staticmethod
    def row(n):
        header = f"From: sender@example.invalid\r\nSubject: Synthetic {n}\r\nMessage-ID: <{n}@invalid>\r\n\r\n".encode()
        raw = header + b"Synthetic body\r\n"
        return {b"FLAGS": (b"\\Flagged", b"custom"), b"RFC822.SIZE": len(raw),
                b"BODY[HEADER.FIELDS (FROM TO CC SUBJECT DATE MESSAGE-ID)]<0>": header,
                b"SYNTHETIC_RAW": raw}

    def fetch(self, uids, fields):
        result = super().fetch(uids, fields)
        for row in result.values():
            raw = row.pop(b"SYNTHETIC_RAW")
            if any(str(field).startswith("BODY.PEEK[]") for field in fields):
                for key in list(row):
                    if key.startswith(b"BODY["):
                        row.pop(key)
                row[b"BODY[]<0>"] = raw
        return result


class MailOperationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mail-operation-tests-")
        self.addCleanup(self.temp.cleanup)
        self.store = PlanStore(Path(self.temp.name) / "runtime")
        self.client = RecoveryIMAP(capabilities=("UIDPLUS",))
        self.service = MailService(ACCOUNT, "synthetic", store=self.store, connector=lambda: self.client)
        self.environment = patch.dict(os.environ, {"ICLOUD_MAIL_ACCOUNT": ACCOUNT,
                  "ICLOUD_APPROVAL_POLICY": "local-receipt", "ICLOUD_ENABLE_POLICY_MANAGEMENT": "0"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def approve(self, prepared):
        plan = prepared["review"]
        self.store.write_once(self.store.path(prepared["plan_id"], ".approved"),
                              {"plan_id": prepared["plan_id"], "sha256": digest(plan)})
        return prepared["plan_id"]

    def failed_copy(self, identity="a" * 32, operation="archive", after_delete=False):
        if operation == "restore":
            self.client.boxes["Bin"][1] = self.client.boxes["INBOX"].pop(1)
        if after_delete:
            original = self.client.uid_expunge
            with patch.object(self.client, "uid_expunge", side_effect=RuntimeError("Synthetic lost response")):
                with self.assertRaises(SafetyError):
                    if operation == "archive":
                        self.service.archive("INBOX", 71, [1], identity)
                    else:
                        self.service.restore(73, [1], "INBOX", identity)
        else:
            original_fetch = self.client.fetch
            destination = "Retained" if operation == "archive" else "INBOX"

            def fail_destination(uids, fields):
                if self.client.selected == destination and any(n >= 10000 for n in uids):
                    raise RuntimeError("Synthetic destination read interruption")
                return original_fetch(uids, fields)

            with patch.object(self.client, "fetch", side_effect=fail_destination), self.assertRaises(SafetyError):
                if operation == "archive":
                    self.service.archive("INBOX", 71, [1], identity)
                else:
                    self.service.restore(73, [1], "INBOX", identity)
        return identity

    def test_success_is_durable_and_existing_id_never_dispatches_again(self):
        identity = "a" * 32
        result = self.service.archive("INBOX", 71, [1], identity)
        self.assertEqual(result["operation_id"], identity)
        self.assertEqual(self.service.operations.status(identity)["status"], "completed")
        before = copy.deepcopy(self.client.calls)
        cached = self.service.archive("INBOX", 71, [1], identity)
        self.assertEqual(cached["status"], "completed")
        self.assertEqual(self.client.calls, before)
        with self.assertRaises(SafetyError):
            self.service.archive("INBOX", 71, [2], identity)

    def test_ordinary_read_state_and_restore_have_journals(self):
        result = self.service.set_seen(folder="INBOX", uidvalidity=71, uids=[1], seen=True)
        self.assertEqual(self.service.operations.status(result["operation_id"])["status"], "completed")
        self.client.boxes["Bin"][4] = self.client.boxes["INBOX"].pop(2)
        result = self.service.restore(73, [4], "INBOX")
        self.assertEqual(self.service.operations.status(result["operation_id"])["status"], "completed")

    def test_copy_mapping_is_recorded_before_destination_read_failure(self):
        identity = self.failed_copy()
        mapping = self.service.operations.phase(identity, "mapping")
        self.assertEqual(mapping["uid_mapping"], {"1": 10001})
        self.assertEqual(mapping["method"], "UID COPY")
        self.assertIn(1, self.client.boxes["INBOX"])
        self.assertIn(10001, self.client.boxes["Retained"])
        with self.assertRaisesRegex(SafetyError, "prior Mail operation"):
            self.service.set_seen(folder="INBOX", uidvalidity=71, uids=[2], seen=True)

    def test_observation_retains_uncertainty_and_never_replays(self):
        identity = self.failed_copy()
        copies = sum(call[0] == "copy" for call in self.client.calls)
        observed = self.service.observe_operation(identity)
        self.assertFalse(observed["observation"]["desired_state_observed"])
        self.assertFalse(observed["observation"]["causal_dispatch_proven"])
        self.assertEqual(self.service.operations.status(identity)["status"], "failed_or_uncertain")
        self.assertEqual(copies, sum(call[0] == "copy" for call in self.client.calls))

    def test_exact_gui_receipt_required_even_under_trusted_client_policy(self):
        identity = self.failed_copy()
        prepared = self.service.prepare_operation_recovery(identity, "accept_uncertainty")
        self.assertTrue(prepared["local_receipt_required"])
        with patch.dict(os.environ, {"ICLOUD_APPROVAL_POLICY": "trusted-client"}), self.assertRaises(FileNotFoundError):
            self.service.execute_operation_recovery(prepared["plan_id"])
        self.assertFalse(self.store.path(prepared["plan_id"], ".started").exists())

    def test_reviewed_acceptance_retains_original_failure_and_allows_fresh_work(self):
        identity = self.failed_copy()
        original = self.service.operations.store.path(identity, ".result").read_bytes()
        prepared = self.service.prepare_operation_recovery(identity, "accept_uncertainty")
        result = self.service.execute_operation_recovery(self.approve(prepared))
        self.assertEqual(result["recovery_status"], "owner_accepted_uncertainty")
        self.assertEqual(self.service.operations.store.path(identity, ".result").read_bytes(), original)
        self.assertTrue(self.store.operation_settled(prepared["plan_id"]))
        self.assertEqual(self.service.operations.status(identity)["original_status"], "failed_or_uncertain")
        self.service.set_seen(folder="INBOX", uidvalidity=71, uids=[2], seen=True)

    def test_changed_observation_refuses_before_receipt_consumption(self):
        identity = self.failed_copy()
        prepared = self.service.prepare_operation_recovery(identity, "accept_uncertainty")
        self.approve(prepared)
        self.client.boxes["INBOX"][1][b"FLAGS"] += (b"new-flag",)
        with self.assertRaisesRegex(SafetyError, "observation changed"):
            self.service.execute_operation_recovery(prepared["plan_id"])
        self.assertFalse(self.store.path(prepared["plan_id"], ".started").exists())

    def test_reviewed_copy_completion_preserves_destination_without_another_copy(self):
        identity = self.failed_copy()
        retained = copy.deepcopy(self.client.boxes["Retained"][10001])
        prepared = self.service.prepare_operation_recovery(identity, "complete_verified_copy")
        before = sum(call[0] == "copy" for call in self.client.calls)
        result = self.service.execute_operation_recovery(self.approve(prepared))
        self.assertEqual(result["recovery_status"], "verified_copy_completion")
        self.assertNotIn(1, self.client.boxes["INBOX"])
        self.assertEqual(retained, self.client.boxes["Retained"][10001])
        self.assertEqual(before, sum(call[0] == "copy" for call in self.client.calls))
        with self.assertRaises(SafetyError):
            self.service.execute_operation_recovery(prepared["plan_id"])

    def test_restore_copy_completion_and_deleted_source_are_supported(self):
        identity = self.failed_copy(operation="restore", after_delete=True)
        self.assertIn(b"\\Deleted", self.client.boxes["Bin"][1][b"FLAGS"])
        prepared = self.service.prepare_operation_recovery(identity, "complete_verified_copy")
        result = self.service.execute_operation_recovery(self.approve(prepared))
        self.assertEqual(result["recovery_status"], "verified_copy_completion")
        self.assertNotIn(1, self.client.boxes["Bin"])
        self.assertIn(10001, self.client.boxes["INBOX"])

    def test_unknown_copy_mapping_cannot_be_guessed_or_replayed(self):
        original = self.client.copy

        def lost(uids, destination):
            original(uids, destination)
            raise RuntimeError("Synthetic response lost after copy")

        with patch.object(self.client, "copy", side_effect=lost), self.assertRaises(SafetyError):
            self.service.archive("INBOX", 71, [1], "a" * 32)
        observed = self.service.observe_operation("a" * 32)
        self.assertTrue(observed["observation"]["destination_identity_unknown"])
        with self.assertRaisesRegex(SafetyError, "recorded exact COPY"):
            self.service.prepare_operation_recovery("a" * 32, "complete_verified_copy")

    def test_changed_destination_and_large_bodies_refuse_completion(self):
        identity = self.failed_copy()
        self.client.boxes["Retained"][10001][b"SYNTHETIC_RAW"] += b"changed"
        with self.assertRaises(SafetyError):
            self.service.prepare_operation_recovery(identity, "complete_verified_copy")
        self.assertIn(1, self.client.boxes["INBOX"])

    def test_recovery_receipt_and_outcome_must_bind_settlement(self):
        identity = self.failed_copy()
        prepared = self.service.prepare_operation_recovery(identity, "accept_uncertainty")
        self.service.execute_operation_recovery(self.approve(prepared))
        self.store.path(prepared["plan_id"], ".approved").unlink()
        with self.assertRaises(FileNotFoundError):
            self.service.operations.status(identity)

    def test_operation_account_and_pagination_boundaries(self):
        result = self.service.set_seen(folder="INBOX", uidvalidity=71, uids=[1], seen=True)
        other = MailOperations("different@example.invalid", self.store)
        with self.assertRaises(FileNotFoundError):
            other.status(result["operation_id"])
        page = self.service.operations.list(limit=1, outstanding_only=False)
        self.assertEqual(len(page["operations"]), 1)
        self.assertTrue(self.service.operations.list()["done"])
        for bad in (0, 101, True):
            with self.assertRaises(SafetyError):
                self.service.operations.list(limit=bad)

    def test_uncertain_ordinary_operation_blocks_cleanup_managed_and_approved_writes(self):
        from icloud_for_agents.cleanup_access import CleanupAccess
        from icloud_for_agents import managed_policy
        self.failed_copy()
        cleanup = CleanupAccess('a' * 32, ACCOUNT, root=self.store.root)
        with self.assertRaisesRegex(SafetyError, 'prior Mail operation'):
            cleanup.run('mark_read', 'INBOX', 71, [2], None, [], lambda: self.fail('Must not execute'))
        with patch.object(managed_policy, 'context', side_effect=AssertionError('Must not reach policy admission')):
            with self.assertRaisesRegex(SafetyError, 'prior Mail operation'):
                managed_policy.execute(ACCOUNT, self.store, 'a' * 32, 'hash', 'synthetic', lambda: self.service)
        proposal = self.service.prepare_trash('INBOX', 71, [2], 'Synthetic review')
        with self.assertRaisesRegex(SafetyError, 'prior Mail operation'):
            self.service.execute(self.approve(proposal))
        self.assertFalse(self.store.path(proposal['plan_id'], '.started').exists())

    def test_unconfigured_cleanup_uncertainty_blocks_ordinary_write(self):
        self.store.ready()
        cleanup = PlanStore(self.store.root / 'cleanup-audit')
        cleanup.write_once(cleanup.path('c' * 32, '.started'), {'account': ACCOUNT})
        with self.assertRaisesRegex(SafetyError, 'Standing cleanup remains uncertain'):
            self.service.set_seen(folder='INBOX', uidvalidity=71, uids=[1], seen=True)
        cleanup.write_once(cleanup.path('c' * 32, '.result'), {'status': 'completed'})
        self.service.set_seen(folder='INBOX', uidvalidity=71, uids=[1], seen=True)

    def test_unfinished_approved_mail_write_blocks_ordinary_but_other_apps_do_not(self):
        proposal = self.service.prepare_trash('INBOX', 71, [1], 'Synthetic')
        self.store.write_once(self.store.path(proposal['plan_id'], '.started'), {'account': ACCOUNT})
        with self.assertRaisesRegex(SafetyError, 'approved Mail operation'):
            self.service.set_seen(folder='INBOX', uidvalidity=71, uids=[2], seen=True)

    def test_partial_source_removal_can_be_completed_without_copying_again(self):
        original = self.client.uid_expunge

        def partial(uids):
            original([uids[0]])
            raise RuntimeError('Synthetic interruption during bounded source removal')

        with patch.object(self.client, 'uid_expunge', side_effect=partial), self.assertRaises(SafetyError):
            self.service.archive('INBOX', 71, [1, 2], 'a' * 32)
        self.assertNotIn(1, self.client.boxes['INBOX'])
        self.assertIn(2, self.client.boxes['INBOX'])
        prepared = self.service.prepare_operation_recovery('a' * 32, 'complete_verified_copy')
        result = self.service.execute_operation_recovery(self.approve(prepared))
        self.assertEqual(result['recovery_status'], 'verified_copy_completion')
        self.assertNotIn(2, self.client.boxes['INBOX'])
        self.assertEqual(set(self.client.boxes['Retained']), {10001, 10002})

    def test_later_exact_review_resolves_failed_recovery_without_erasing_failure(self):
        from icloud_for_agents.managed_policy import mail_guard
        identity = self.failed_copy()
        prepared = self.service.prepare_operation_recovery(identity, 'complete_verified_copy')
        with patch('icloud_for_agents.reconcile.complete_mail_copy', side_effect=RuntimeError('Synthetic interruption')):
            result = self.service.execute_operation_recovery(self.approve(prepared))
        self.assertEqual(result['status'], 'failed_or_uncertain')
        original = self.store.path(prepared['plan_id'], '.result').read_bytes()
        with self.assertRaisesRegex(SafetyError, 'Mail recovery remains uncertain'):
            with mail_guard(self.store, ACCOUNT):
                self.fail('Unreviewed recovery must block managed execution')
        accepted = self.service.prepare_operation_recovery(identity, 'accept_uncertainty')
        with self.assertRaisesRegex(SafetyError, 'Mail recovery remains uncertain'):
            with mail_guard(self.store, ACCOUNT):
                self.fail('A prepared proposal is not an owner-approved settlement')
        self.service.execute_operation_recovery(self.approve(accepted))
        self.assertTrue(self.store.operation_settled(prepared['plan_id']))
        with mail_guard(self.store, ACCOUNT):
            pass
        self.assertEqual(original, self.store.path(prepared['plan_id'], '.result').read_bytes())


if __name__ == "__main__":
    unittest.main()
