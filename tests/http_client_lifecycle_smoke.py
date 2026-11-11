"""Reviewed permissions, rotation and revocation through real loopback MCP.

Production HTTP middleware, owner review, key lifecycle and Mail execution are
used with synthetic providers and an in-memory Keychain. No actual Muse app,
Apple account, native dialog, real Keychain or external network is involved.
"""
import asyncio
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ("mail", "calendar", "contacts", "notes", "reminders")
TOKENS = {identity: "ika_" + char * 32 + "." + char * 43
          for identity, char in (("read", "a"), ("other", "d"), ("admin", "e"))}


class ClientLifecycleTests(unittest.TestCase):
    def test_reviewed_scope_changes_rotation_and_revocation_on_existing_connection(self):
        with tempfile.TemporaryDirectory(prefix="icloud-http-client-lifecycle-") as directory:
            home = Path(directory)
            audit, delivery = home / "audit.json", home / "owner-delivery"
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            env = {"HOME": directory, "PATH": os.defpath,
                   "PYTHONPATH": os.pathsep.join((str(ROOT / "src"), str(ROOT / "tests"))),
                   "ICLOUD_RUNTIME_ROOT": str(home / "runtime"), "BRIDGE_TEST_AUDIT": str(audit),
                   "BRIDGE_TEST_REAL_KEYS": "1", "BRIDGE_TEST_KEY_DELIVERY": str(delivery),
                   "FASTMCP_CHECK_FOR_UPDATES": "off", "ICLOUD_MAIL_ACCOUNT": "synthetic@example.invalid",
                   "ICLOUD_MAIL_ACCESS": "write-enabled", "ICLOUD_HTTP_TRANSPORT": "1",
                   "ICLOUD_APPROVAL_POLICY": "local-receipt",
                   "ICLOUD_NATIVE_CONFIG": json.dumps({name: {"access_mode": "read-only", "source_ids": ["selected"]}
                                                       for name in ("notes", "reminders")}),
                   **{"ICLOUD_ENABLE_" + name.upper(): "1" for name in SERVICES}}
            process = subprocess.Popen([sys.executable, str(ROOT / "tests/http_services_server.py"), str(port)],
                                       cwd=home, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            rotated = None
            try:
                def status(token=None):
                    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                    try:
                        headers = {"Authorization": "Bearer " + token} if token else {}
                        connection.request("GET", "/mcp", headers=headers)
                        return connection.getresponse().status
                    finally:
                        connection.close()

                deadline = time.monotonic() + 20
                while True:
                    try:
                        self.assertEqual(status(), 401)
                        break
                    except OSError:
                        if process.poll() is not None or time.monotonic() >= deadline:
                            self.fail("Synthetic lifecycle server failed to start")
                        time.sleep(.05)

                def client(token):
                    return Client(StreamableHttpTransport(f"http://127.0.0.1:{port}/mcp",
                        headers={"Authorization": "Bearer " + token}))

                def value(result):
                    self.assertFalse(result.is_error, result)
                    result = result.structured_content or json.loads(result.content[0].text)
                    for token in TOKENS.values():
                        self.assertNotIn(token, json.dumps(result))
                    return result

                async def call(c, tool, args=None):
                    return await c.call_tool_mcp(tool, args or {})

                async def refused(c, tool, args=None):
                    self.assertTrue((await call(c, tool, args)).is_error, tool)

                async def approve(c, plan_id):
                    value(await call(c, "owner_request_review", {"plan_id": plan_id}))
                    for _ in range(300):
                        result = value(await call(c, "owner_review_status", {"plan_id": plan_id}))
                        if result["status"] != "pending":
                            break
                        await asyncio.sleep(.01)
                    self.assertEqual(result["status"], "approved")

                async def execute_client(c, plan_id):
                    value(await call(c, "http_execute_client", {"plan_id": plan_id}))
                    for _ in range(300):
                        result = value(await call(c, "http_client_status", {"plan_id": plan_id}))
                        if result.get("status") == "completed" and not result.get("working_locally"):
                            return result
                        await asyncio.sleep(.01)
                    self.fail("Synthetic client lifecycle did not complete")

                async def permissions(c, requested):
                    prepared = value(await call(c, "http_prepare_client", {
                        "action": "permissions", "permissions": requested}))
                    self.assertTrue(prepared["local_receipt_required"])
                    await refused(c, "http_execute_client", {"plan_id": prepared["plan_id"]})
                    await approve(c, prepared["plan_id"])
                    result = await execute_client(c, prepared["plan_id"])
                    self.assertEqual(result["client"]["permissions"], requested)

                async def exercise():
                    nonlocal rotated
                    read = {name: "read" for name in SERVICES}
                    full = {name: "full" for name in SERVICES}
                    write = {"folder": "INBOX", "uidvalidity": 71, "uids": [1], "seen": True,
                             "operation_id": "f" * 32}
                    async with client(TOKENS["read"]) as actor, client(TOKENS["other"]) as other, client(TOKENS["admin"]) as admin:
                        self.assertNotIn("email_set_read_state", {t.name for t in await actor.list_tools()})
                        await refused(actor, "email_set_read_state", write)
                        await refused(actor, "http_prepare_client", {"action": "permissions", "permissions": full,
                                                                    "manage_clients": True})
                        await permissions(actor, full)
                        self.assertIn("email_set_read_state", {t.name for t in await actor.list_tools()})
                        result = value(await call(actor, "email_set_read_state", write))
                        self.assertEqual(value(await call(actor, "email_operation_status", {
                            "operation_id": result["operation_id"]}))["status"], "completed")
                        await refused(other, "email_set_read_state", dict(write, operation_id="b" * 32))
                        # An approved permission reduction changes discovery and
                        # calls on the existing connection without token rotation.
                        await permissions(actor, dict(full, notes="none"))
                        self.assertNotIn("notes_sources", {t.name for t in await actor.list_tools()})
                        await refused(actor, "notes_sources")
                        self.assertTrue(value(await call(other, "notes_sources")))
                        await permissions(actor, read)
                        self.assertNotIn("email_set_read_state", {t.name for t in await actor.list_tools()})
                        await refused(actor, "email_set_read_state", dict(write, operation_id="c" * 32))
                        # Review a rotation as the independent administrator.
                        actor_id = "a" * 32
                        rotation = value(await call(admin, "http_prepare_client", {"action": "rotate", "client_id": actor_id}))
                        await refused(actor, "http_execute_client", {"plan_id": rotation["plan_id"]})
                        await approve(admin, rotation["plan_id"])
                        state = await execute_client(admin, rotation["plan_id"])
                        self.assertEqual(state["delivery_status"], "window_closed")
                        self.assertFalse(state["connection_verified"])
                        rotated = delivery.read_text()
                        self.assertNotEqual(rotated, TOKENS["read"])
                        self.assertEqual(status(TOKENS["read"]), 401)
                        async with client(rotated) as replacement:
                            self.assertTrue(value(await call(replacement, "email_list_folders")))
                            revoke = value(await call(admin, "http_prepare_client", {"action": "revoke", "client_id": actor_id}))
                            await approve(admin, revoke["plan_id"])
                            self.assertEqual((await execute_client(admin, revoke["plan_id"]))["client"]["status"], "revoked")
                            self.assertEqual(status(rotated), 401)
                        self.assertTrue(value(await call(other, "email_list_folders")))

                asyncio.run(asyncio.wait_for(exercise(), timeout=40))
            finally:
                process.terminate()
                stdout, stderr = process.communicate(timeout=15)
                if process.returncode not in (0, -15):
                    print(stderr.decode(errors="replace"), file=sys.stderr)
            self.assertEqual(process.returncode, 0)
            report = json.loads(audit.read_text())
            self.assertEqual(report["blocked_events"], [])
            self.assertEqual(report["calls"]["mail"]["writes"], 1)
            for name in SERVICES[1:]:
                self.assertEqual(report["calls"][name]["writes"], 0)
            for token in [*TOKENS.values(), rotated]:
                if token:
                    self.assertNotIn(token.encode(), stdout + stderr)


if __name__ == "__main__":
    unittest.main()
