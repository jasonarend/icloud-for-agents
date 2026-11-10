"""Synthetic owner-reviewed client setup through the actual HTTP MCP stack."""
import importlib
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.http_keys import HttpKeys
from icloud_for_agents.http_pairing import HttpPairing
from icloud_for_agents.http_transport import create_app
from icloud_for_agents.owner_review import OwnerReview
from test_http_keys import MemoryKeychain

ACCOUNT = "synthetic@example.invalid"
NONE = {name: "none" for name in ("mail", "calendar", "contacts", "notes", "reminders")}


def make_key(keys, label, *, manage_clients):
    reader, writer = os.pipe()
    try:
        keys.create(label, writer, permissions=NONE, manage_clients=manage_clients, owner_approved=True)
        os.close(writer)
        writer = None
        return os.read(reader, 512).decode("ascii")
    finally:
        os.close(reader)
        if writer is not None:
            os.close(writer)


class PairingProtocolTests(unittest.TestCase):
    def test_http_admin_review_and_pairing_work_when_all_apps_are_disabled(self):
        from icloud_for_agents import server
        try:
            with tempfile.TemporaryDirectory(prefix="http-admin-protocol-") as directory:
                env = {"HOME": directory, "PATH": os.defpath, "ICLOUD_MAIL_ACCOUNT": ACCOUNT,
                       "ICLOUD_MAIL_ACCESS": "disabled", "ICLOUD_APPROVAL_POLICY": "trusted-client",
                       "ICLOUD_RUNTIME_ROOT": str(Path(directory) / "runtime"),
                       **{"ICLOUD_ENABLE_" + name.upper(): "0" for name in NONE}}
                with patch.dict(os.environ, env, clear=True):
                    importlib.reload(server)
                    store = PlanStore()
                    keys = HttpKeys(MemoryKeychain(), store)
                    admin = make_key(keys, "Synthetic administrator", manage_clients=True)
                    restricted = make_key(keys, "Synthetic restricted", manage_clients=False)
                    presented = []
                    server._http_pairing = HttpPairing(ACCOUNT, store, keys,
                        presenter=lambda label, token: presented.append(label))
                    server._owner_reviews = OwnerReview(store, dialog=lambda review, heading: "approved")
                    with TestClient(create_app(server.mcp, keys), base_url="http://127.0.0.1") as client:
                        def call(key, name, args=None):
                            response = client.post("/mcp", headers={"Authorization": "Bearer " + key,
                                "Accept": "application/json, text/event-stream", "Mcp-Protocol-Version": "2025-11-25"},
                                json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": name, "arguments": args or {}}})
                            assert response.status_code == 200
                            result = response.json()
                            assert admin not in str(result) and restricted not in str(result)
                            return result

                        args = {"action": "create", "label": "Synthetic paired client", "permissions": NONE}
                        denied = call(restricted, "http_prepare_client", args)
                        self.assertTrue("error" in denied or denied.get("result", {}).get("isError", False))
                        prepared = call(admin, "http_prepare_client", args)["result"]["structuredContent"]
                        plan_id = prepared["plan_id"]
                        self.assertTrue(prepared["local_receipt_required"])
                        call(admin, "owner_request_review", {"plan_id": plan_id})
                        for _ in range(300):
                            decision = call(admin, "owner_review_status", {"plan_id": plan_id})["result"]["structuredContent"]
                            if decision["status"] != "pending":
                                break
                            time.sleep(.01)
                        self.assertEqual(decision["status"], "approved")
                        started = call(admin, "http_execute_client", {"plan_id": plan_id})["result"]["structuredContent"]
                        self.assertEqual(started["status"], "pending")
                        for _ in range(300):
                            state = call(admin, "http_client_status", {"plan_id": plan_id})["result"]["structuredContent"]
                            if state.get("status") == "completed" and not state.get("working_locally"):
                                break
                            time.sleep(.01)
                        self.assertEqual(state["status"], "completed")
                        self.assertEqual(state["delivery_status"], "window_closed")
                        self.assertFalse(state["connection_verified"])
                        self.assertEqual(presented, ["Synthetic paired client"])

                        def value(response):
                            self.assertNotIn("error", response, response)
                            self.assertFalse(response["result"].get("isError"), response)
                            return response["result"]["structuredContent"]

                        def refused(response):
                            self.assertTrue("error" in response or response.get("result", {}).get("isError", False), response)

                        def approve_as(key, plan_id):
                            value(call(key, "owner_request_review", {"plan_id": plan_id}))
                            for _ in range(300):
                                decision = value(call(key, "owner_review_status", {"plan_id": plan_id}))
                                if decision["status"] != "pending":
                                    break
                                time.sleep(.01)
                            self.assertEqual(decision["status"], "approved")

                        restricted_id = keys.authenticate(restricted.encode())["client_id"]
                        admin_id = keys.authenticate(admin.encode())["client_id"]
                        listing = client.post("/mcp", headers={"Authorization": "Bearer " + restricted,
                            "Accept": "application/json, text/event-stream", "Mcp-Protocol-Version": "2025-11-25"},
                            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}).json()
                        names = {tool["name"] for tool in listing["result"]["tools"]}
                        self.assertEqual(names, {"http_prepare_client", "http_execute_client", "http_client_status",
                                                 "owner_request_review", "owner_review_status"})
                        requested = dict(NONE, notes="read")
                        own_args = {"action": "permissions", "permissions": requested}
                        for action in ("create", "rotate", "revoke", "show", "authorize"):
                            refused(call(restricted, "http_prepare_client", dict(own_args, action=action)))
                        refused(call(restricted, "http_prepare_client", dict(own_args, manage_clients=True)))
                        refused(call(restricted, "http_prepare_client", dict(own_args, client_id=admin_id)))
                        refused(call(restricted, "http_clients"))
                        other = value(call(admin, "http_prepare_client", {
                            "action": "permissions", "client_id": admin_id, "permissions": NONE, "manage_clients": True}))
                        for tool in ("owner_request_review", "owner_review_status", "http_execute_client", "http_client_status"):
                            refused(call(restricted, tool, {"plan_id": other["plan_id"]}))
                        # Even an owner-approved proposal for this key cannot be
                        # used by a nonadmin caller to gain client management.
                        management = value(call(admin, "http_prepare_client", dict(own_args,
                            client_id=restricted_id, manage_clients=True)))
                        approve_as(admin, management["plan_id"])
                        refused(call(restricted, "http_execute_client", {"plan_id": management["plan_id"]}))
                        own = value(call(restricted, "http_prepare_client", own_args))
                        self.assertEqual(own["review"]["payload"]["client_id"], restricted_id)
                        self.assertIs(own["review"]["payload"]["manage_clients"], False)
                        self.assertTrue(own["local_receipt_required"])
                        stale = value(call(restricted, "http_prepare_client", dict(own_args, client_id=restricted_id)))
                        for proposal in (own, stale):
                            refused(call(restricted, "http_execute_client", {"plan_id": proposal["plan_id"]}))
                            approve_as(restricted, proposal["plan_id"])
                        value(call(restricted, "http_execute_client", {"plan_id": own["plan_id"]}))
                        for _ in range(300):
                            state = value(call(restricted, "http_client_status", {"plan_id": own["plan_id"]}))
                            if state.get("status") == "completed" and not state.get("working_locally"):
                                break
                            time.sleep(.01)
                        self.assertEqual(state["status"], "completed")
                        identity = keys.authenticate(restricted.encode())
                        self.assertEqual(identity["permissions"], requested)
                        self.assertIs(identity["manage_clients"], False)
                        refused(call(restricted, "http_execute_client", {"plan_id": own["plan_id"]}))
                        refused(call(restricted, "http_execute_client", {"plan_id": stale["plan_id"]}))
                        self.assertFalse(store.path(stale["plan_id"], ".started").exists())
                        self.assertEqual(presented, ["Synthetic paired client"])
        finally:
            importlib.reload(server)


if __name__ == "__main__":
    unittest.main()
