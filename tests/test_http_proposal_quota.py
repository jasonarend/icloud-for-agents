"""Synthetic admission and recovery checks for self-service HTTP proposals."""

from pathlib import Path
import tempfile
import unittest

from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError
from icloud_for_agents.http_proposal_quota import SLOTS, approval_lock, prepare_self_permission


class SelfPermissionQuotaTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="synthetic-self-proposals-")
        self.addCleanup(self.directory.cleanup)
        self.now = 1000
        self.store = PlanStore(Path(self.directory.name) / "runtime", clock=lambda: self.now)
        self.client = "a" * 32

    def payload(self, client=None):
        return {"account": "synthetic@example.invalid", "action": "permissions",
                "client_id": client or self.client, "manage_clients": False}

    def prepare(self, client=None):
        client = client or self.client
        return prepare_self_permission(self.store, client, self.payload(client))

    def test_flood_is_bounded_and_other_clients_have_separate_slots(self):
        plans = [self.prepare() for _ in range(SLOTS)]
        for _ in range(20):
            with self.assertRaisesRegex(SafetyError, "Too many pending"):
                self.prepare()
        self.assertEqual(len(list(self.store.root.glob("*.json"))), SLOTS)
        self.assertEqual(len({plan["plan_id"] for plan in plans}), SLOTS)
        self.assertNotEqual(self.prepare("b" * 32)["plan_id"], plans[0]["plan_id"])

    def test_only_expired_unreviewed_proposals_are_reclaimed(self):
        plans = [self.prepare() for _ in range(SLOTS)]
        approved = plans[0]
        started = plans[1]
        self.store.write_once(self.store.path(approved["plan_id"], ".approved"),
                              {"plan_id": approved["plan_id"], "sha256": approved["sha256"]})
        self.store.write_once(self.store.path(started["plan_id"], ".started"), {"started": self.now})
        self.now += 1861
        fresh = self.prepare()
        self.assertTrue(self.store.path(fresh["plan_id"], ".json").exists())
        for retained in (approved, started):
            self.assertTrue(self.store.path(retained["plan_id"], ".json").exists())
        self.assertTrue(self.store.path(approved["plan_id"], ".approved").exists())
        self.assertTrue(self.store.path(started["plan_id"], ".started").exists())
        for expired in plans[2:]:
            self.assertFalse(self.store.path(expired["plan_id"], ".json").exists())

    def test_admission_lock_prevents_races_and_invalid_targets_refuse(self):
        with approval_lock(self.store):
            with self.assertRaisesRegex(SafetyError, "busy"):
                self.prepare()
        payload = self.payload()
        payload["client_id"] = "b" * 32
        with self.assertRaises(SafetyError):
            prepare_self_permission(self.store, self.client, payload)


if __name__ == "__main__":
    unittest.main()
