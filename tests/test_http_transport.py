"""Offline HTTP credential and authentication checks."""
import asyncio
import hmac
import json
import os
from pathlib import Path
import tempfile
import unittest

from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.http_keys import HttpKeys
from icloud_for_agents.http_transport import BearerGate, validated_environment
from test_http_keys import MemoryKeychain

TOKEN = b"ika_" + b"a" * 32 + b"." + b"b" * 43


class HttpTransportUnitTests(unittest.TestCase):
    def test_http_launch_uses_validated_settings_not_environment_overrides(self):
        with tempfile.TemporaryDirectory(prefix="http-settings-") as directory:
            root = Path(directory)
            (root / "local").mkdir()
            settings = root / "local/settings.json"
            settings.write_text(json.dumps({"account": "synthetic@example.invalid",
                                           "apple_keychain_service": "synthetic",
                                           "access_mode": "read-only", "dav_reads": False}))
            settings.chmod(0o600)
            environment = validated_environment({"HOME": directory, "PATH": os.defpath,
                "ICLOUD_CONFIG_ROOT": directory, "ICLOUD_MAIL_ACCESS": "write-enabled",
                "ICLOUD_HTTP_TRANSPORT": "0", "ICLOUD_SECRET_FD": "9"})
            self.assertEqual(environment["ICLOUD_MAIL_ACCESS"], "read-only")
            self.assertNotIn("ICLOUD_SECRET_FD", environment)
            self.assertNotIn("ICLOUD_HTTP_TRANSPORT", environment)
            settings.chmod(0o644)
            with self.assertRaises(ValueError):
                validated_environment({"ICLOUD_CONFIG_ROOT": directory})

    def test_gate_checks_every_http_request_and_rejects_duplicate_credentials(self):
        class SyntheticKeys:
            def authenticate(self, candidate):
                return {"client_id": "synthetic"} if hmac.compare_digest(candidate, TOKEN) else None

        async def run(headers):
            events = []

            async def app(scope, receive, send):
                self.assertFalse(any(name.lower() == b"authorization" for name, value in scope["headers"]))
                await send({"type": "http.response.start", "status": 204, "headers": []})

            async def send(event):
                events.append(event)

            await BearerGate(app, SyntheticKeys())({"type": "http", "headers": headers}, None, send)
            self.assertIn((b"cache-control", b"no-store"), events[0]["headers"])
            return events[0]["status"]

        self.assertEqual(asyncio.run(run([])), 401)
        self.assertEqual(asyncio.run(run([(b"authorization", b"Bearer bad")])), 401)
        auth = (b"authorization", b"Bearer " + TOKEN)
        self.assertEqual(asyncio.run(run([auth, auth])), 401)
        self.assertEqual(asyncio.run(run([auth])), 204)

    def test_key_revocation_takes_effect_at_the_http_gate(self):
        async def status(gate, token):
            events = []

            async def app(scope, receive, send):
                await send({"type": "http.response.start", "status": 204, "headers": []})

            async def send(event):
                events.append(event)

            gate.app = app
            await gate({"type": "http", "headers": [(b"authorization", b"Bearer " + token)]}, None, send)
            return events[0]["status"]

        with tempfile.TemporaryDirectory(prefix="http-gate-keys-") as directory:
            keys = HttpKeys(MemoryKeychain(), PlanStore(Path(directory) / "runtime"))
            read_fd, write_fd = os.pipe()
            try:
                identity = keys.create("Synthetic client", write_fd, permissions={name: "read" for name in ("mail", "calendar", "contacts", "notes", "reminders")}, owner_approved=True)
                os.close(write_fd)
                write_fd = None
                token = os.read(read_fd, 512)
            finally:
                os.close(read_fd)
                if write_fd is not None:
                    os.close(write_fd)
            gate = BearerGate(None, keys)
            self.assertEqual(asyncio.run(status(gate, token)), 204)
            keys.revoke(identity["client_id"], owner_approved=True)
            self.assertEqual(asyncio.run(status(gate, token)), 401)


if __name__ == "__main__":
    unittest.main()
