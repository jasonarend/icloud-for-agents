"""Owner-reviewed client-key setup using only synthetic credentials."""
import os
import fcntl
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError, digest
from icloud_for_agents.http_keys import HttpKeys
from icloud_for_agents.http_pairing import HttpPairing, _atomic_record
from icloud_for_agents.http_secret_dialog import show_client_key
from test_http_keys import MemoryKeychain


PERMISSIONS = {"mail": "read", "calendar": "none", "contacts": "none", "notes": "none", "reminders": "none"}
ACCOUNT = "synthetic@example.invalid"


class HttpPairingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="http-pairing-")
        self.addCleanup(self.directory.cleanup)
        self.store = PlanStore(Path(self.directory.name) / "runtime")
        self.backend = MemoryKeychain()
        self.keys = HttpKeys(self.backend, self.store)
        self.presented = []
        self.pairing = HttpPairing(ACCOUNT, self.store, self.keys,
                                   presenter=lambda label, token: self.presented.append((label, token)))

    def approve(self, proposal):
        plan_id = proposal["plan_id"]
        plan = self.store.proposal(plan_id)
        self.store.write_once(self.store.path(plan_id, ".approved"), {"plan_id": plan_id, "sha256": digest(plan)})
        return plan_id

    def finish(self, plan_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = self.pairing.status(plan_id)
            if state.get("status") in {"completed", "failed_or_uncertain"} and not state.get("working_locally"):
                return state
            time.sleep(0.01)
        self.fail("Synthetic client action did not finish")

    def create(self):
        proposal = self.pairing.prepare("create", label="Synthetic client", permissions=PERMISSIONS)
        plan_id = self.approve(proposal)
        self.pairing.request(plan_id)
        return proposal, self.finish(plan_id)

    def test_exact_local_receipt_is_required_even_in_trusted_client_mode(self):
        with patch.dict(os.environ, {"ICLOUD_APPROVAL_POLICY": "trusted-client", "ICLOUD_MAIL_ACCOUNT": ACCOUNT}):
            proposal = self.pairing.prepare("create", label="Synthetic", permissions=PERMISSIONS)
            self.assertTrue(proposal["local_receipt_required"])
            with self.assertRaisesRegex(SafetyError, "owner must approve"):
                self.pairing.request(proposal["plan_id"])
            self.assertEqual(self.backend.items, {})
            self.pairing.request(self.approve(proposal))
            self.assertEqual(self.finish(proposal["plan_id"])["status"], "completed")

    def test_key_is_shown_locally_but_never_returned_or_persisted_in_plan(self):
        proposal, result = self.create()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["delivery_status"], "window_closed")
        self.assertFalse(result["result"]["connection_verified"] if "result" in result else result["connection_verified"])
        self.assertEqual(len(self.presented), 1)
        token = self.presented[0][1]
        self.assertIsNotNone(self.keys.authenticate(token))
        for path in self.store.root.iterdir():
            if path.is_file():
                self.assertNotIn(token, path.read_bytes())
        self.assertNotIn(token.decode(), str(proposal))
        self.assertNotIn(token.decode(), str(result))
        with self.assertRaises(SafetyError):
            self.pairing.request(proposal["plan_id"])

    def test_changed_client_requires_a_new_review(self):
        proposal, _ = self.create()
        client_id = proposal["review"]["payload"]["client_id"]
        review = self.pairing.prepare("revoke", client_id=client_id)
        self.approve(review)
        changed = dict(PERMISSIONS, mail="full")
        self.keys.update_permissions(client_id, changed, owner_approved=True)
        with self.assertRaisesRegex(SafetyError, "changed since review"):
            self.pairing.request(review["plan_id"])
        self.assertEqual(self.keys.status(client_id)["permissions"], changed)

    def test_reviewed_clients_can_be_found_and_revoked_without_a_secret(self):
        proposal, _ = self.create()
        client_id = proposal["review"]["payload"]["client_id"]
        listing = self.pairing.clients()
        self.assertEqual(listing["clients"][0]["client_id"], client_id)
        self.assertEqual(listing["clients"][0]["credential"]["status"], "active")
        self.assertNotIn(self.presented[0][1].decode(), str(listing))
        review = self.pairing.prepare("revoke", client_id=client_id)
        self.pairing.request(self.approve(review))
        self.assertEqual(self.finish(review["plan_id"])["status"], "completed")
        self.assertEqual(self.pairing.clients()["clients"][0]["credential"]["status"], "inactive")

    def test_inventory_ignores_partial_unindexed_plans_and_admissions(self):
        proposal = self.pairing.prepare("create", label="Synthetic", permissions=PERMISSIONS)
        self.approve(proposal)
        started = self.store.path(proposal["plan_id"], ".started")
        started.write_text("{")
        started.chmod(0o600)
        unrelated = self.store.path("a" * 32, ".json")
        unrelated.write_text("{")
        unrelated.chmod(0o600)
        self.assertEqual(self.pairing.clients(), {"clients": [], "next_cursor": None, "done": True})

    def test_other_process_cannot_treat_an_unfinished_creation_as_inactive(self):
        proposal = self.pairing.prepare("create", label="Synthetic", permissions=PERMISSIONS)
        plan_id = self.approve(proposal)
        plan = self.store.consume(plan_id, ACCOUNT)
        _atomic_record(self.store, self.store.path(plan_id, ".http-client-index"),
                       {"plan_sha256": digest(plan), "client_id": plan["payload"]["client_id"]})
        other = HttpPairing(ACCOUNT, self.store, self.keys, presenter=lambda *args: None)
        self.assertEqual(other.clients()["clients"][0]["credential"]["status"], "pending_or_interrupted")
        self.assertEqual(other.status(plan_id)["credential_observation"]["status"], "not_observed_present")

    def test_race_after_consumption_cannot_apply_stale_permissions(self):
        proposal, _ = self.create()
        client_id = proposal["review"]["payload"]["client_id"]
        requested = dict(PERMISSIONS, mail="full")
        review = self.pairing.prepare("permissions", client_id=client_id, permissions=requested)
        self.approve(review)
        started, release = threading.Event(), threading.Event()
        perform = self.pairing._perform

        def delayed(plan):
            started.set()
            release.wait(3)
            perform(plan)

        with patch.object(self.pairing, "_perform", side_effect=delayed):
            self.pairing.request(review["plan_id"])
            self.assertTrue(started.wait(3))
            changed = dict(PERMISSIONS, contacts="read")
            self.keys.update_permissions(client_id, changed, owner_approved=True)
            release.set()
            self.assertEqual(self.finish(review["plan_id"])["status"], "failed_or_uncertain")
        self.assertEqual(self.keys.status(client_id)["permissions"], changed)

    def test_status_never_reads_a_partially_written_result(self):
        proposal = self.pairing.prepare("create", label="Synthetic", permissions=PERMISSIONS)
        plan_id = self.approve(proposal)
        entered, release = threading.Event(), threading.Event()
        original_link = os.link

        def delayed_link(source, target, **kwargs):
            if str(target).endswith(".result"):
                entered.set()
                release.wait(3)
            return original_link(source, target, **kwargs)

        with patch("icloud_for_agents.http_pairing.os.link", side_effect=delayed_link):
            try:
                self.pairing.request(plan_id)
                self.assertTrue(entered.wait(3))
                self.assertEqual(self.pairing.status(plan_id)["status"], "pending")
                self.assertFalse(self.store.path(plan_id, ".result").exists())
            finally:
                release.set()
            self.assertEqual(self.finish(plan_id)["status"], "completed")

    def test_status_waits_for_an_in_progress_owner_receipt(self):
        proposal = self.pairing.prepare("create", label="Synthetic", permissions=PERMISSIONS)
        plan_id = proposal["plan_id"]
        lock = os.open(self.store.root / "approval-policy.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            receipt = self.store.path(plan_id, ".approved")
            receipt.write_text("{")
            receipt.chmod(0o600)
            self.assertEqual(self.pairing.status(plan_id), {"plan_id": plan_id, "status": "pending"})
        finally:
            os.close(lock)

    def test_key_window_does_not_put_secret_in_process_arguments_or_result(self):
        token = b"ika_" + b"a" * 32 + b"." + b"b" * 43
        with patch("icloud_for_agents.http_secret_dialog.sys.platform", "darwin"), \
             patch("icloud_for_agents.http_secret_dialog.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="CLOSED\n")) as run:
            self.assertEqual(show_client_key("Synthetic client", token), "window_closed")
            self.assertNotIn(token.decode(), str(run.call_args.args))
            self.assertIn(token.decode(), run.call_args.kwargs["input"])
            self.assertNotIn(token.decode(), str(run.call_args.kwargs["env"]))


if __name__ == "__main__":
    unittest.main()
