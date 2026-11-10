"""Server-owned per-client policy over real in-process Streamable HTTP."""
from concurrent.futures import ThreadPoolExecutor
import asyncio
import importlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastmcp import FastMCP
from starlette.testclient import TestClient

from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError, digest
from icloud_for_agents.http_authorization import READ_TOOLS, WRITE_TOOLS, SHARED, CLIENT_MANAGEMENT, ClientPermissions, validate_permissions
from icloud_for_agents.http_transport import create_app


NONE = {name: "none" for name in ("mail", "calendar", "contacts", "notes", "reminders")}


class PermissionsTests(unittest.TestCase):
    def test_every_registered_application_tool_has_explicit_permission_classification(self):
        from icloud_for_agents import server
        try:
            with patch.dict(os.environ, {"ICLOUD_ENABLE_" + name.upper(): "1" for name in NONE} | {"ICLOUD_ENABLE_POLICY_MANAGEMENT": "1"}):
                importlib.reload(server)
                tools = asyncio.run(server.mcp.list_tools())
                known = READ_TOOLS | WRITE_TOOLS | SHARED | CLIENT_MANAGEMENT
                self.assertEqual({tool.name for tool in tools} - known, set())
                self.assertFalse(READ_TOOLS & WRITE_TOOLS)
                for tool in tools:
                    if tool.name in READ_TOOLS:
                        self.assertTrue(tool.annotations.read_only_hint, tool.name)
        finally:
            importlib.reload(server)

    def test_policy_is_complete_and_strict(self):
        for bad in (None, {}, dict(NONE, mail=True), dict(NONE, extra="full"), dict(NONE, mail="write")):
            with self.subTest(bad=bad), self.assertRaises(SafetyError):
                validate_permissions(bad)

    def test_read_only_calendar_once_does_not_offer_trusted_client_deletion(self):
        permissions = ClientPermissions()
        policy = dict(NONE, calendar="read")
        self.assertTrue(permissions.allowed("calendar_prepare_once", policy, {}))
        self.assertFalse(permissions.allowed("calendar_prepare_once", policy, {"deletion_authorization": "trusted-client"}))

    def test_real_http_filters_and_enforces_scope_and_shared_targets(self):
        with tempfile.TemporaryDirectory(prefix="http-permissions-") as directory:
            with patch.dict(os.environ, {"ICLOUD_RUNTIME_ROOT": directory, "ICLOUD_MAIL_ACCOUNT": "synthetic@example.invalid"}):
                store = PlanStore()
                calendar = store.prepare("calendar_change", {"account": "synthetic@example.invalid"})["plan_id"]
                contact = store.prepare("contact_change", {"account": "synthetic@example.invalid"})["plan_id"]
                mail = store.prepare("send", {"account": "synthetic@example.invalid"})["plan_id"]
                other = store.prepare("send", {"account": "other@example.invalid"})["plan_id"]
                admin = store.prepare("http_client", {"account": "synthetic@example.invalid"})["plan_id"]
                notes_access = store.prepare("notes_access", {"account": "synthetic@example.invalid", "operation": "request_os_access"})["plan_id"]
                reminders_access = store.prepare("reminders_access", {"account": "synthetic@example.invalid", "operation": "request_os_access"})["plan_id"]
                notes_write = store.prepare("notes_change", {"account": "synthetic@example.invalid"})["plan_id"]
                managed = PlanStore(store.root / "managed-policy")
                managed.ready()
                managed.write_once(managed.root / "binding.json", {"account": "synthetic@example.invalid"})
                managed_id = "f" * 32
                managed.write_once(managed.path(managed_id, ".json"), {"kind": "policy-change"})
                server = FastMCP("Synthetic scoped server")
                calls = []

                def register(name):
                    def action(plan_id: str = "", scope: str = "service") -> str:
                        calls.append(name)
                        return name
                    action.__name__ = name
                    server.tool()(action)

                for name in ("email_list_folders", "email_archive", "calendar_list", "dav_execute_approved",
                             "owner_request_review", "owner_review_status", "email_plan_status", "email_unclassified",
                             "http_prepare_client", "notes_prepare_os_access", "notes_request_os_access",
                             "reminders_prepare_os_access", "reminders_request_os_access", "policy_execute_approved"):
                    register(name)

                class Keys:
                    def __init__(self):
                        self.values = {
                            b"reader": {"client_id": "reader", "permissions": dict(NONE, mail="read"), "manage_clients": False},
                            b"full": {"client_id": "full", "permissions": {name: "full" for name in NONE}, "manage_clients": False},
                            b"admin": {"client_id": "admin", "permissions": dict(NONE), "manage_clients": True},
                            b"notes-reader": {"client_id": "notes-reader", "permissions": dict(NONE, notes="read"), "manage_clients": False},
                            b"mixed": {"client_id": "mixed", "permissions": dict(NONE, calendar="read", contacts="full"), "manage_clients": False},
                        }
                    def authenticate(self, token):
                        return self.values.get(token)

                keys = Keys()
                with TestClient(create_app(server, keys), base_url="http://127.0.0.1") as client:
                    def call(token, name, args=None):
                        response = client.post("/mcp", headers={"Authorization": "Bearer " + token,
                            "Accept": "application/json, text/event-stream", "Mcp-Protocol-Version": "2025-11-25"},
                            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args or {}}})
                        self.assertEqual(response.headers.get("cache-control"), "no-store")
                        return response.status_code, response.json() if response.status_code == 200 else {}
                    def success(result):
                        return result[0] == 200 and "error" not in result[1] and not result[1].get("result", {}).get("isError", False)
                    self.assertTrue(success(call("reader", "email_list_folders")))
                    for name in ("email_archive", "calendar_list", "email_unclassified", "http_prepare_client"):
                        self.assertFalse(success(call("reader", name)))
                    self.assertTrue(success(call("full", "email_archive")))
                    self.assertFalse(success(call("full", "email_unclassified")))
                    self.assertFalse(success(call("full", "http_prepare_client")))
                    self.assertTrue(success(call("admin", "http_prepare_client")))
                    self.assertFalse(success(call("reader", "owner_review_status", {"plan_id": calendar})))
                    self.assertTrue(success(call("reader", "owner_review_status", {"plan_id": mail})))
                    self.assertFalse(success(call("reader", "owner_request_review", {"plan_id": mail})))
                    self.assertFalse(success(call("full", "email_plan_status", {"plan_id": calendar})))
                    self.assertFalse(success(call("full", "email_plan_status", {"plan_id": other})))
                    self.assertTrue(success(call("full", "dav_execute_approved", {"plan_id": calendar})))
                    self.assertTrue(success(call("mixed", "dav_execute_approved", {"plan_id": contact})))
                    self.assertFalse(success(call("mixed", "dav_execute_approved", {"plan_id": calendar})))
                    self.assertTrue(success(call("full", "policy_execute_approved", {"plan_id": managed_id})))
                    self.assertTrue(success(call("full", "owner_request_review", {"plan_id": managed_id, "scope": "managed-policy"})))
                    self.assertFalse(success(call("reader", "dav_execute_approved", {"plan_id": calendar})))
                    self.assertFalse(success(call("full", "owner_request_review", {"plan_id": admin})))
                    self.assertTrue(success(call("admin", "owner_request_review", {"plan_id": admin})))
                    self.assertTrue(success(call("notes-reader", "notes_prepare_os_access")))
                    self.assertTrue(success(call("notes-reader", "owner_request_review", {"plan_id": notes_access})))
                    self.assertFalse(success(call("notes-reader", "owner_request_review", {"plan_id": notes_write})))
                    self.assertFalse(success(call("notes-reader", "owner_request_review", {"plan_id": reminders_access})))
                    self.assertFalse(success(call("notes-reader", "notes_request_os_access", {"plan_id": notes_access})))
                    self.assertFalse(success(call("notes-reader", "reminders_request_os_access", {"plan_id": notes_access})))
                    plan = store.proposal(notes_access)
                    store.write_once(store.path(notes_access, ".approved"), {"plan_id": notes_access, "sha256": digest(plan)})
                    self.assertTrue(success(call("notes-reader", "notes_request_os_access", {"plan_id": notes_access})))
                    self.assertFalse(success(call("notes-reader", "notes_request_os_access", {"plan_id": reminders_access})))
                    def concurrent(index):
                        token = "reader" if index % 2 else "full"
                        return token, success(call(token, "email_archive"))
                    with ThreadPoolExecutor(max_workers=4) as pool:
                        for token, allowed in pool.map(concurrent, range(20)):
                            self.assertEqual(allowed, token == "full")
                    keys.values.pop(b"full")
                    self.assertEqual(call("full", "email_archive")[0], 401)
                    listing = client.post("/mcp", headers={"Authorization": "Bearer reader", "Accept": "application/json, text/event-stream",
                        "Mcp-Protocol-Version": "2025-11-25"}, json={"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}).json()
                    names = {tool["name"] for tool in listing["result"]["tools"]}
                    self.assertIn("email_list_folders", names)
                    self.assertNotIn("email_archive", names)
                    self.assertNotIn("calendar_list", names)


if __name__ == "__main__":
    unittest.main()
