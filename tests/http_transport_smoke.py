"""Synthetic loopback transport tests; no iCloud credentials or account data."""
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

from icloud_for_agents.version import __version__


ROOT = Path(__file__).resolve().parents[1]
TOKEN = b"ika_" + b"a" * 32 + b"." + b"b" * 43
SECOND_TOKEN = b"ika_" + b"c" * 32 + b"." + b"d" * 43


class HttpTransportTests(unittest.TestCase):
    def test_actual_http_mcp_authentication_sessions_and_reconnect(self):
        with tempfile.TemporaryDirectory(prefix="icloud-http-synthetic-") as home:
            local = Path(home) / "local"
            local.mkdir()
            settings = local / "settings.json"
            settings.write_text(json.dumps({"account": "synthetic@example.invalid",
                                            "apple_keychain_service": "synthetic",
                                            "access_mode": "read-only", "dav_reads": False}))
            settings.chmod(0o600)
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            env = {
                "HOME": home, "PATH": os.defpath, "PYTHONPATH": str(ROOT / "src"),
                "ICLOUD_CONFIG_ROOT": home, "ICLOUD_MAIL_ACCESS": "write-enabled",
                "ICLOUD_ENABLE_MAIL": "1", "ICLOUD_ENABLE_CALENDAR": "0",
                "ICLOUD_ENABLE_CONTACTS": "0", "ICLOUD_ENABLE_NOTES": "0",
                "ICLOUD_ENABLE_REMINDERS": "0", "FASTMCP_CHECK_FOR_UPDATES": "off",
            }
            process = subprocess.Popen(
                [sys.executable, str(ROOT / "tests/http_server_fixture.py"), str(port)],
                cwd=home, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )

            def request(method="GET", headers=(), path="/mcp", host=None):
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
                try:
                    connection.putrequest(method, path, skip_host=True)
                    connection.putheader("Host", host or "127.0.0.1:" + str(port))
                    for name, value in headers:
                        connection.putheader(name, value)
                    connection.endheaders()
                    response = connection.getresponse()
                    result = response.status, response.read()
                    return result
                finally:
                    connection.close()

            try:
                deadline = time.monotonic() + 15
                while True:
                    try:
                        status, _ = request()
                        break
                    except (ConnectionRefusedError, ConnectionResetError):
                        if process.poll() is not None or time.monotonic() > deadline:
                            self.fail("Synthetic HTTP server failed to start")
                        time.sleep(0.05)
                self.assertEqual(status, 401)
                self.assertEqual(request(headers=[("Authorization", "Bearer wrong")])[0], 401)
                self.assertEqual(request(headers=[("Authorization", "Bearer " + TOKEN.decode()),
                                                  ("Authorization", "Bearer " + TOKEN.decode())])[0], 401)
                self.assertEqual(request(headers=[("Authorization", "Bearer " + TOKEN.decode()),
                                                  ("Origin", "https://attacker.example")])[0], 403)
                self.assertEqual(request(headers=[("Authorization", "Bearer " + TOKEN.decode())],
                                         host="attacker.example")[0], 421)
                self.assertNotEqual(request(headers=[("Authorization", "Bearer " + TOKEN.decode())],
                                            host="synthetic.example")[0], 421)
                self.assertEqual(request(method="DELETE")[0], 401)
                self.assertEqual(request(method="POST", path="/other")[0], 401)

                async def one_client(token=TOKEN):
                    transport = StreamableHttpTransport(
                        f"http://127.0.0.1:{port}/mcp", headers={"Authorization": "Bearer " + token.decode()}
                    )
                    async with Client(transport) as client:
                        result = await client.list_tools()
                        names = {tool.name for tool in result}
                        self.assertIn("email_list_folders", names)
                        self.assertNotIn("calendar_list", names)
                        self.assertNotIn("notes_query", names)
                        self.assertNotIn("reminders_query", names)
                        self.assertEqual(client.server_info.version, __version__)
                        refused = await client.call_tool_mcp("email_archive", {
                            "folder": "synthetic", "uidvalidity": 1, "uids": [1]
                        })
                        self.assertTrue(refused.is_error)

                async def clients():
                    await asyncio.gather(one_client(), one_client(SECOND_TOKEN), one_client())
                    await one_client()  # Fresh session after the prior sessions close.

                asyncio.run(clients())
            finally:
                process.terminate()
                stdout, stderr = process.communicate(timeout=15)
            self.assertNotIn(TOKEN, stdout + stderr)
            self.assertNotIn(SECOND_TOKEN, stdout + stderr)
            self.assertNotIn(b"Authorization", stdout + stderr)


if __name__ == "__main__":
    unittest.main()
