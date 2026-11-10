"""HTTP exact-once admission integrity and identity checks, without providers."""
import copy
import os
import tempfile
import unittest
from unittest.mock import patch

from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError, digest
from icloud_for_agents.http_authorization import PRINCIPAL


NONE = {name: "none" for name in ("mail", "calendar", "contacts", "notes", "reminders")}


class HttpOnceAdmissionTests(unittest.TestCase):
    def test_bound_plan_requires_same_client_local_receipt_and_immutable_contents(self):
        for policy in ("local-receipt", "trusted-client"):
            for service in ("calendar", "notes", "reminders"):
                with self.subTest(policy=policy, service=service), tempfile.TemporaryDirectory() as root:
                    with patch.dict(os.environ, {"ICLOUD_APPROVAL_POLICY": policy, "ICLOUD_MAIL_ACCOUNT": "synthetic@example.invalid"}):
                        store = PlanStore(root)
                        kind = "calendar_change" if service == "calendar" else service + "_change"
                        payload = {"account": "synthetic@example.invalid", "changes": []}
                        if service == "calendar":
                            payload.update(kind="cal", temporary_elevation="calendar-exact-batch-once")
                        else:
                            payload["context"] = {"temporary_elevation": "exact-batch-once"}
                        identity = {"client_id": "a" * 32, "permissions": dict(NONE, **{service: "read"}), "manage_clients": False}
                        context = PRINCIPAL.set(identity)
                        try:
                            prepared = store.prepare(kind, payload)
                            plan_id = prepared["plan_id"]
                            self.assertTrue(prepared["local_receipt_required"])
                            with self.assertRaises(FileNotFoundError):
                                store.consume(plan_id, payload["account"])
                            plan = prepared["review"]
                            store.write_once(store.path(plan_id, ".approved"), {"plan_id": plan_id, "sha256": digest(plan)})
                            # A different full client and a stdio caller cannot
                            # spend this owner's exact temporary authorization.
                            for substitute in (dict(identity, client_id="b" * 32, permissions={s: "full" for s in NONE}), None):
                                swap = PRINCIPAL.set(substitute)
                                try:
                                    with self.assertRaises(SafetyError):
                                        store.consume(plan_id, payload["account"])
                                finally:
                                    PRINCIPAL.reset(swap)
                            original = store.path(plan_id, ".json").read_bytes()
                            altered = copy.deepcopy(plan)
                            del altered["http_once"]
                            store.path(plan_id, ".json").unlink()
                            store.write_once(store.path(plan_id, ".json"), altered)
                            with self.assertRaises(SafetyError):
                                store.consume(plan_id, payload["account"])
                            self.assertFalse(store.path(plan_id, ".started").exists())
                            store.path(plan_id, ".json").write_bytes(original)
                            store.consume(plan_id, payload["account"])
                            self.assertEqual(store.read(store.path(plan_id, ".started"))["authorization"], "local-receipt")
                            with self.assertRaises(SafetyError):
                                store.consume(plan_id, payload["account"])
                        finally:
                            PRINCIPAL.reset(context)

    def test_full_http_and_stdio_temporary_plans_keep_existing_trusted_semantics(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {
                "ICLOUD_APPROVAL_POLICY": "trusted-client", "ICLOUD_MAIL_ACCOUNT": "synthetic@example.invalid"}):
            store = PlanStore(root)
            for identity in (None, {"client_id": "b" * 32, "permissions": {s: "full" for s in NONE}, "manage_clients": False}):
                token = PRINCIPAL.set(identity)
                try:
                    prepared = store.prepare("calendar_change", {"account": "synthetic@example.invalid", "kind": "cal",
                        "temporary_elevation": "calendar-exact-batch-once", "changes": []})
                    self.assertNotIn("http_once", prepared["review"])
                    self.assertFalse(prepared["local_receipt_required"])
                    store.consume(prepared["plan_id"], "synthetic@example.invalid")
                    store.finish(prepared["plan_id"], {"status": "applied"})
                finally:
                    PRINCIPAL.reset(token)
