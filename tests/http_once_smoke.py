"""Actual loopback MCP exact-once writes by read-scoped clients, synthetic only.

Both approval policies are covered. Apple providers and the owner's local dialog
are test doubles; the real tools, middleware, plan store and executors are used.
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

from http_services_smoke import ROOT, TOKENS


class HttpOnceTests(unittest.TestCase):
    def test_exact_temporary_changes_under_both_approval_policies(self):
        for policy in ("local-receipt", "trusted-client"):
            with self.subTest(policy=policy):
                self.run_policy(policy)

    def run_policy(self, policy):
        with tempfile.TemporaryDirectory(prefix="icloud-http-once-") as directory:
            home = Path(directory)
            runtime = home / "runtime"
            audit = home / "audit.json"
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            env = {"HOME": directory, "PATH": os.defpath,
                   "PYTHONPATH": os.pathsep.join((str(ROOT / "src"), str(ROOT / "tests"))),
                   "ICLOUD_RUNTIME_ROOT": str(runtime), "BRIDGE_TEST_AUDIT": str(audit),
                   "FASTMCP_CHECK_FOR_UPDATES": "off", "ICLOUD_MAIL_ACCOUNT": "synthetic@example.invalid",
                   "ICLOUD_MAIL_ACCESS": "read-only", "ICLOUD_HTTP_TRANSPORT": "1",
                   "ICLOUD_APPROVAL_POLICY": policy,
                   "ICLOUD_NATIVE_CONFIG": json.dumps({name: {"access_mode": "read-only", "source_ids": ["selected"]}
                                                       for name in ("notes", "reminders")}),
                   **{"ICLOUD_ENABLE_" + name.upper(): "1" for name in
                      ("mail", "calendar", "contacts", "notes", "reminders")}}
            process = subprocess.Popen([sys.executable, str(ROOT / "tests/http_services_server.py"), str(port)],
                cwd=home, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
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
                            self.fail("Synthetic HTTP once server failed to start")
                        time.sleep(.05)
                    finally:
                        connection.close()

                def client(identity):
                    return Client(StreamableHttpTransport(f"http://127.0.0.1:{port}/mcp",
                        headers={"Authorization": "Bearer " + TOKENS[identity]}))

                def data(result):
                    self.assertFalse(result.is_error, result)
                    return result.structured_content or json.loads(result.content[0].text)

                async def call(c, tool, args):
                    return await c.call_tool_mcp(tool, args)

                async def denied(c, tool, args):
                    self.assertTrue((await call(c, tool, args)).is_error, tool)

                async def exercise():
                    async with client("read") as actor, client("other") as other, client("full") as full:
                        names = {t.name for t in await actor.list_tools()}
                        for name in ("calendar_prepare_once", "notes_prepare_once", "reminders_prepare_once",
                                     "dav_execute_approved", "notes_execute_approved", "reminders_execute_approved"):
                            self.assertIn(name, names)
                        native = {"changes": [{"operation": "update", "source_id": "selected", "id": "one",
                                                "fields": {"title": "Synthetic once"}}]}
                        calendar = {"calendar_url": "https://caldav.icloud.com/synthetic/",
                            "changes": [{"operation": "create", "fields": {"summary": "Synthetic once",
                                "start": "2026-11-15T10:00:00Z", "end": "2026-11-15T11:00:00Z"}}]}
                        await denied(actor, "calendar_prepare_changes", calendar)
                        for service in ("notes", "reminders"):
                            await denied(actor, service + "_prepare_changes", native)
                            await denied(actor, service + "_prepare_grant", {"scope": {}})
                            outside = {"changes": [dict(native["changes"][0], source_id="unselected")]}
                            await denied(actor, service + "_prepare_once", outside)
                        await denied(actor, "calendar_prepare_once", dict(calendar, calendar_url="https://caldav.icloud.com/other/"))
                        for service in ("calendar", "notes", "reminders"):
                            args = calendar if service == "calendar" else native
                            execute = "dav_execute_approved" if service == "calendar" else service + "_execute_approved"
                            prepared = data(await call(actor, service + "_prepare_once", args))
                            self.assertTrue(prepared["local_receipt_required"])
                            self.assertEqual(prepared["authorization_path"], "local-receipt")
                            self.assertEqual(prepared["review"]["http_once"], {"client_id": "a" * 32, "service": service})
                            plan_id = prepared["plan_id"]
                            target = {"plan_id": plan_id}
                            await denied(actor, execute, target)
                            for substitute in (other, full):
                                for tool in ("owner_request_review", "owner_review_status", execute):
                                    await denied(substitute, tool, target)
                            data(await call(actor, "owner_request_review", target))
                            for _ in range(100):
                                state = data(await call(actor, "owner_review_status", target))
                                if state["status"] == "approved":
                                    break
                                await asyncio.sleep(.01)
                            self.assertEqual(state["status"], "approved")
                            # Local synthetic tampering represents a corrupted
                            # stored proposal; neither changed fields nor removal
                            # of the HTTP binding may route back to trusted mode.
                            path = runtime / (plan_id + ".json")
                            original = path.read_bytes()
                            altered = json.loads(original)
                            altered["payload"]["changes"][0]["fields"]["summary" if service == "calendar" else "title"] = "Altered"
                            path.write_text(json.dumps(altered))
                            await denied(actor, execute, target)
                            self.assertFalse((runtime / (plan_id + ".started")).exists())
                            del altered["http_once"]
                            path.write_text(json.dumps(altered))
                            await denied(full, execute, target)
                            self.assertFalse((runtime / (plan_id + ".started")).exists())
                            path.write_bytes(original)
                            self.assertEqual(data(await call(actor, execute, target))["status"], "applied")
                            await denied(actor, execute, target)
                            if service != "calendar":
                                status = data(await call(actor, service + "_access_status", {}))
                                self.assertEqual(status["effective_access"], "read-only")
                        await denied(actor, "calendar_prepare_changes", calendar)
                        for service in ("notes", "reminders"):
                            await denied(actor, service + "_prepare_changes", native)

                asyncio.run(asyncio.wait_for(exercise(), timeout=30))
            finally:
                process.terminate()
                stdout, stderr = process.communicate(timeout=15)
                if process.returncode not in (0, -15):
                    print(stderr.decode(errors="replace"), file=sys.stderr)
            self.assertEqual(process.returncode, 0)
            report = json.loads(audit.read_text())
            self.assertEqual(report["blocked_events"], [])
            for service, calls in report["calls"].items():
                self.assertEqual(calls["writes"], 1 if service in {"calendar", "notes", "reminders"} else 0, service)
            for token in TOKENS.values():
                self.assertNotIn(token.encode(), stdout + stderr)


if __name__ == "__main__":
    unittest.main()
