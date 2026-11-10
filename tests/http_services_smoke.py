"""Real loopback MCP coverage of all five services with synthetic providers.

Run directly with the project Python. No Apple services, real credentials, OS
prompts, tunnels or external network are used. Native adapters and the owner's
dialog are test doubles; this does not establish native/macOS compatibility.
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
READS = {"mail": "email_list_folders", "calendar": "calendar_list",
         "contacts": "contacts_addressbooks", "notes": "notes_sources",
         "reminders": "reminders_sources"}
TOKENS = {name: "ika_" + char * 32 + "." + char * 43
          for name, char in (("read", "a"), ("full", "b"), ("mail", "c"))}


class HttpServicesTests(unittest.TestCase):
    def test_all_services_scope_approval_and_replay_over_loopback(self):
        with tempfile.TemporaryDirectory(prefix="icloud-http-services-") as directory:
            home = Path(directory)
            audit = home / "audit.json"
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            env = {"HOME": directory, "PATH": os.defpath,
                   "PYTHONPATH": os.pathsep.join((str(ROOT / "src"), str(ROOT / "tests"))),
                   "ICLOUD_RUNTIME_ROOT": str(home / "runtime"),
                   "BRIDGE_TEST_AUDIT": str(audit), "FASTMCP_CHECK_FOR_UPDATES": "off",
                   "ICLOUD_MAIL_ACCOUNT": "synthetic@example.invalid",
                   "ICLOUD_MAIL_ACCESS": "write-enabled", "ICLOUD_HTTP_TRANSPORT": "1",
                   "ICLOUD_APPROVAL_POLICY": "local-receipt",
                   "ICLOUD_NATIVE_CONFIG": json.dumps({name: {"access_mode": "read-only",
                        "source_ids": ["selected"]} for name in ("notes", "reminders")}),
                   **{"ICLOUD_ENABLE_" + name.upper(): "1" for name in READS}}
            process = subprocess.Popen([sys.executable, str(ROOT / "tests/http_services_server.py"), str(port)],
                                       cwd=home, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout = stderr = b""
            try:
                deadline = time.monotonic() + 20
                while True:
                    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                    try:
                        connection.request("GET", "/mcp")
                        self.assertEqual(connection.getresponse().status, 401)
                        break
                    except OSError:
                        if process.poll() is not None or time.monotonic() >= deadline:
                            self.fail("Synthetic HTTP service failed to start")
                        time.sleep(0.05)
                    finally:
                        connection.close()

                def client(identity):
                    return Client(StreamableHttpTransport(f"http://127.0.0.1:{port}/mcp",
                        headers={"Authorization": "Bearer " + TOKENS[identity]}))

                def data(result):
                    self.assertFalse(result.is_error, result)
                    value = result.structured_content
                    if value is None:
                        value = json.loads(result.content[0].text)
                    return value

                async def call(c, name, arguments=None):
                    return await c.call_tool_mcp(name, arguments or {})

                async def denied(c, name, arguments):
                    self.assertTrue((await call(c, name, arguments)).is_error, name)

                async def review(c, plan):
                    plan_id = plan["plan_id"]
                    data(await call(c, "owner_request_review", {"plan_id": plan_id}))
                    for _ in range(100):
                        status = data(await call(c, "owner_review_status", {"plan_id": plan_id}))
                        if status["status"] == "approved":
                            return
                        await asyncio.sleep(0.02)
                    self.fail("Synthetic owner review did not complete: " + repr(status))

                native_change = {"changes": [{"operation": "update", "source_id": "selected",
                    "id": "one", "fields": {"title": "Synthetic after"}}]}
                write_args = {
                    "email_set_read_state": {"folder": "INBOX", "uidvalidity": 71, "uids": [1], "seen": True,
                                             "operation_id": "e" * 32},
                    "calendar_prepare_changes": {"calendar_url": "https://caldav.icloud.com/synthetic/",
                        "changes": [{"operation": "create", "fields": {"summary": "Synthetic meeting",
                            "start": "2026-11-15T10:00:00Z", "end": "2026-11-15T11:00:00Z"}}]},
                    "contacts_prepare_changes": {"addressbook_url": "https://contacts.icloud.com/synthetic/",
                        "changes": [{"operation": "create", "fields": {"full_name": "Synthetic person"}}]},
                    "notes_prepare_once": native_change, "reminders_prepare_once": native_change}

                async def exercise():
                    async with client("read") as reader, client("full") as writer, client("mail") as mail:
                        names = {tool.name for tool in await reader.list_tools()}
                        for name in READS.values():
                            self.assertIn(name, names)
                        for name in write_args:
                            self.assertNotIn(name, names)
                        # Concurrent requests retain independent principals: the
                        # writer's presence must not elevate the read-only key.
                        concurrent = await asyncio.gather(
                            *(call(reader, name) for name in READS.values()),
                            call(reader, "calendar_prepare_changes", write_args["calendar_prepare_changes"]),
                            writer.list_tools(), mail.list_tools())
                        for result in concurrent[:len(READS)]:
                            self.assertTrue(data(result))
                        self.assertTrue(concurrent[len(READS)].is_error)
                        self.assertIn("calendar_prepare_changes", {t.name for t in concurrent[-2]})
                        self.assertNotIn("calendar_list", {t.name for t in concurrent[-1]})
                        for name in READS.values():
                            result = data(await call(reader, name))
                            self.assertTrue(result)
                        for name, args in write_args.items():
                            await denied(reader, name, args)
                        mail_names = {tool.name for tool in await mail.list_tools()}
                        self.assertIn("email_list_folders", mail_names)
                        for service, name in READS.items():
                            if service != "mail":
                                self.assertNotIn(name, mail_names)
                                await denied(mail, name, {})
                        await denied(writer, "http_clients", {})
                        result = data(await call(writer, "email_set_read_state", write_args["email_set_read_state"]))
                        operation_id = result["operation_id"]
                        self.assertEqual(data(await call(writer, "email_operation_status",
                            {"operation_id": operation_id}))["status"], "completed")
                        # An identical retry returns its journal rather than writing again.
                        data(await call(writer, "email_set_read_state", write_args["email_set_read_state"]))
                        trash_args = {"folder": "INBOX", "uidvalidity": 71, "uids": [2],
                                      "reason": "Synthetic recoverable-deletion test"}
                        await denied(reader, "email_prepare_trash", trash_args)
                        trash = data(await call(writer, "email_prepare_trash", trash_args))
                        exact_trash = {"plan_id": trash["plan_id"]}
                        await denied(writer, "email_execute_approved", exact_trash)
                        await denied(reader, "owner_request_review", exact_trash)
                        await review(writer, trash)
                        await denied(reader, "email_execute_approved", exact_trash)
                        self.assertEqual(data(await call(writer, "email_execute_approved", exact_trash))["status"], "moved")
                        await denied(writer, "email_execute_approved", exact_trash)
                        for prepare in list(write_args)[1:]:
                            plan = data(await call(writer, prepare, write_args[prepare]))
                            plan_id = plan["plan_id"]
                            execute = (prepare.split("_")[0] + "_execute_approved"
                                       if prepare.startswith(("notes_", "reminders_")) else "dav_execute_approved")
                            args = {"plan_id": plan_id}
                            await denied(writer, execute, args)  # Receipt is mandatory.
                            await denied(reader, "owner_request_review", args)
                            await denied(mail, "owner_request_review", args)
                            await denied(mail, "email_plan_status", args)  # Generic status cannot leak other apps.
                            await review(writer, plan)
                            # A receipt does not override this key's ACL or consume the plan on refusal.
                            await denied(reader, execute, args)
                            await denied(mail, execute, args)
                            self.assertEqual(data(await call(writer, execute, args))["status"], "applied")
                            await denied(writer, execute, args)

                asyncio.run(asyncio.wait_for(exercise(), timeout=30))
            finally:
                process.terminate()
                stdout, stderr = process.communicate(timeout=15)
                if process.returncode not in (0, -15):
                    print(stderr.decode(errors="replace"), file=sys.stderr)
            self.assertEqual(process.returncode, 0)
            report = json.loads(audit.read_text())
            self.assertEqual(report["blocked_events"], [])
            for service in READS:
                self.assertGreater(report["calls"][service]["reads"], 0)
                self.assertEqual(report["calls"][service]["writes"], 2 if service == "mail" else 1, service)
                if service != "mail":
                    # Every unauthorized read above must stop before its provider.
                    self.assertEqual(report["calls"][service]["reads"], 2, service)
            for token in TOKENS.values():
                self.assertNotIn(token.encode(), stdout + stderr)


if __name__ == "__main__":
    unittest.main()
