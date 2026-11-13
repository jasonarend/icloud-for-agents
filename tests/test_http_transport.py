"""Offline HTTP credential and authentication checks."""
import asyncio
import hmac
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.http_keys import HttpKeys
from icloud_for_agents.http_transport import BearerGate, validated_environment
from test_http_keys import MemoryKeychain

TOKEN = b"ika_" + b"a" * 32 + b"." + b"b" * 43


class HttpTransportUnitTests(unittest.TestCase):
    async def gate_status(self, gate, candidate=TOKEN):
        events=[]
        async def send(event): events.append(event)
        await gate({"type":"http", "headers":[(b"authorization",b"Bearer "+candidate)]},None,send)
        self.assertIn((b"cache-control",b"no-store"),events[0]["headers"])
        if events[0]["status"]==503:
            self.assertIn((b"retry-after",b"1"),events[0]["headers"])
        return events[0]["status"]

    def test_malformed_bearers_never_schedule_keychain_work(self):
        async def run():
            keys=Mock()
            gate=BearerGate(None,keys)
            try:
                for candidate in (b"bad",TOKEN+b"extra",b"ika_"+b"a"*32+b"."+b"!"*43,b"x"*100000):
                    self.assertEqual(await self.gate_status(gate,candidate),401)
                keys.authenticate.assert_not_called()
            finally: gate._auth_executor.shutdown(wait=True)
        asyncio.run(run())

    def test_authentication_flood_is_bounded_even_after_request_cancellation(self):
        async def run():
            loop=asyncio.get_running_loop()
            started=asyncio.Event()
            release=threading.Event()
            calls=[]
            lock=threading.Lock()
            class Keys:
                def authenticate(self,candidate):
                    with lock:
                        calls.append(candidate)
                        if len(calls)==2: loop.call_soon_threadsafe(started.set)
                    if not release.wait(2): raise RuntimeError("Synthetic release timed out")
                    return {"client_id":"synthetic"}
            async def app(scope,receive,send):
                await send({"type":"http.response.start","status":204,"headers":[]})
            with patch('icloud_for_agents.http_transport.AUTHENTICATION_CONCURRENCY',2):
                gate=BearerGate(app,Keys())
            requests=[asyncio.create_task(self.gate_status(gate)) for _ in range(2)]
            try:
                await asyncio.wait_for(started.wait(),1)
                results=await asyncio.gather(*(self.gate_status(gate) for _ in range(20)))
                self.assertEqual(results,[503]*20)
                self.assertEqual(len(calls),2)
                # Native authentication does not consume the shared executor.
                self.assertEqual(await asyncio.wait_for(asyncio.to_thread(lambda:42),1),42)
                for task in requests: task.cancel()
                await asyncio.gather(*requests,return_exceptions=True)
                self.assertEqual(await self.gate_status(gate),503)
                self.assertEqual(len(calls),2)
                release.set()
                for _ in range(100):
                    if await self.gate_status(gate)==204: break
                    await asyncio.sleep(.001)
                else: self.fail("Authentication capacity was not restored")
            finally:
                release.set()
                for task in requests: task.cancel()
                await asyncio.gather(*requests,return_exceptions=True)
                gate._auth_executor.shutdown(wait=True)
        asyncio.run(run())

    def test_authentication_timeout_keeps_capacity_until_native_work_finishes(self):
        async def run():
            release=threading.Event()
            keys=Mock()
            keys.authenticate.side_effect=lambda candidate: release.wait(2) and {"client_id":"synthetic"}
            async def app(scope,receive,send):
                await send({"type":"http.response.start","status":204,"headers":[]})
            with patch('icloud_for_agents.http_transport.AUTHENTICATION_CONCURRENCY',1), \
                    patch('icloud_for_agents.http_transport.AUTHENTICATION_TIMEOUT',.01):
                gate=BearerGate(app,keys)
                try:
                    self.assertEqual(await self.gate_status(gate),503)
                    self.assertEqual(await self.gate_status(gate),503)
                    self.assertEqual(keys.authenticate.call_count,1)
                    release.set()
                    for _ in range(100):
                        if await self.gate_status(gate)==204: break
                        await asyncio.sleep(.001)
                    else: self.fail("Timed-out work leaked its authentication slot")
                finally:
                    release.set()
                    gate._auth_executor.shutdown(wait=True)
        asyncio.run(run())

    def test_native_failure_is_private_and_releases_authentication_capacity(self):
        async def run():
            async def app(scope,receive,send):
                await send({"type":"http.response.start","status":204,"headers":[]})
            keys=Mock()
            keys.authenticate.side_effect=[RuntimeError("private synthetic failure"),{"client_id":"synthetic"}]
            with patch('icloud_for_agents.http_transport.AUTHENTICATION_CONCURRENCY',1):
                gate=BearerGate(app,keys)
            try:
                self.assertEqual(await self.gate_status(gate),401)
                self.assertEqual(await self.gate_status(gate),204)
            finally: gate._auth_executor.shutdown(wait=True)
        asyncio.run(run())

    def test_late_native_failure_is_observed_after_response_timeout(self):
        async def run():
            release=threading.Event()
            errors=[]
            loop=asyncio.get_running_loop()
            previous=loop.get_exception_handler()
            loop.set_exception_handler(lambda loop,context: errors.append(context))
            class Keys:
                calls=0
                def authenticate(self,candidate):
                    self.calls+=1
                    if self.calls==1:
                        release.wait(2)
                        raise RuntimeError("private synthetic late failure")
                    return {"client_id":"synthetic"}
            async def app(scope,receive,send):
                await send({"type":"http.response.start","status":204,"headers":[]})
            with patch('icloud_for_agents.http_transport.AUTHENTICATION_CONCURRENCY',1), \
                    patch('icloud_for_agents.http_transport.AUTHENTICATION_TIMEOUT',.01):
                gate=BearerGate(app,Keys())
                try:
                    self.assertEqual(await self.gate_status(gate),503)
                    release.set()
                    for _ in range(100):
                        if await self.gate_status(gate)==204: break
                        await asyncio.sleep(.001)
                    else: self.fail("Late native failure leaked its authentication slot")
                    await asyncio.sleep(0)
                    self.assertEqual(errors,[])
                finally:
                    release.set()
                    gate._auth_executor.shutdown(wait=True)
                    loop.set_exception_handler(previous)
        asyncio.run(run())

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
