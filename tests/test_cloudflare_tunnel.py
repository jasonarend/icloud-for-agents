"""Cloudflare launcher boundaries, using synthetic executable and credentials."""
import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from icloud_for_agents import cloudflare_tunnel as tunnel
from icloud_for_agents.tunnel_runtime import RuntimeSetupError

ID = "12345678-1234-4234-8234-123456789abc"
TOKEN = base64.b64encode(json.dumps({"a": "a" * 32, "t": ID,
                                    "s": base64.b64encode(b"x" * 32).decode()}).encode()).decode()


class CloudflareTunnelTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "local").mkdir(mode=0o700)
        settings = self.root / "local/settings.json"
        settings.write_text(json.dumps({"account": "synthetic@example.invalid", "apple_keychain_service": "synthetic",
                                        "access_mode": "read-only", "dav_reads": False}))
        settings.chmod(0o600)
        self.binary = self.root / "cloudflared"
        self.binary.write_bytes(b"synthetic executable")
        self.binary.chmod(0o700)
        self.checksum = hashlib.sha256(self.binary.read_bytes()).hexdigest()

    def test_preflight_never_reads_a_secret_or_starts_a_process(self):
        with patch.object(tunnel, "keychain_runtime_key") as loader, patch.object(tunnel.os, "execve") as launch:
            result = tunnel.preflight(self.root, self.binary, ID, self.checksum)
        self.assertEqual(result["tunnel_id"], ID)
        loader.assert_not_called()
        launch.assert_not_called()

    def test_launch_is_keychain_scoped_and_never_puts_key_in_arguments(self):
        loader = Mock(return_value=TOKEN)
        launch = Mock()
        tunnel.start(self.root, self.binary, ID, self.checksum, key_loader=loader, exec_runner=launch)
        loader.assert_called_once_with(service=tunnel.KEYCHAIN_SERVICE, account=ID)
        binary, args, env = launch.call_args.args
        self.assertEqual(binary, str(self.binary))
        self.assertEqual(args, [str(self.binary), "tunnel", "--config", os.devnull, "--no-autoupdate", "run"])
        self.assertNotIn(TOKEN, args)
        self.assertEqual(env["TUNNEL_TOKEN"], TOKEN)
        self.assertNotIn("CONTROL_PLANE_API_KEY", env)

    def test_changed_binary_and_bad_identity_refuse_before_keychain(self):
        loader = Mock(return_value=TOKEN)
        for binary, identity, checksum in [(self.binary, "not-an-id", self.checksum),
                                           (self.binary, ID, "0" * 64),
                                           (Path("relative"), ID, self.checksum)]:
            with self.subTest(binary=binary, identity=identity), self.assertRaises(RuntimeSetupError):
                tunnel.start(self.root, binary, identity, checksum, key_loader=loader, exec_runner=Mock())
        loader.assert_not_called()

    def test_rechecks_binary_after_owner_keychain_prompt(self):
        def changed(**kwargs):
            self.binary.write_bytes(b"changed after review")
            return TOKEN
        launch = Mock()
        with self.assertRaises(RuntimeSetupError):
            tunnel.start(self.root, self.binary, ID, self.checksum, key_loader=changed, exec_runner=launch)
        launch.assert_not_called()

    def test_refuses_symlink_and_unprivate_runtime(self):
        alias = self.root / "alias"
        alias.symlink_to(self.binary)
        with self.assertRaises(RuntimeSetupError):
            tunnel.preflight(self.root, alias, ID, self.checksum)
        (self.root / "local/cloudflare").mkdir(mode=0o755)
        with self.assertRaises(RuntimeSetupError):
            tunnel.start(self.root, self.binary, ID, self.checksum,
                         key_loader=Mock(return_value="synthetic"), exec_runner=Mock())

    def test_refuses_token_for_another_tunnel_or_custom_edge(self):
        def encoded(fields):
            return base64.b64encode(json.dumps(fields).encode()).decode()
        secret = base64.b64encode(b"x" * 32).decode()
        for token in ("not-a-token", encoded({"a": "a" * 32, "s": secret, "t": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}),
                      encoded({"a": "a" * 32, "s": secret, "t": ID, "e": "https://other.example"})):
            with self.subTest(case=token[:4]), self.assertRaises(RuntimeSetupError):
                tunnel.start(self.root, self.binary, ID, self.checksum,
                             key_loader=Mock(return_value=token), exec_runner=Mock())

    def test_lock_refuses_a_second_launcher_before_keychain(self):
        held = tunnel._lock(self.root, ID)
        self.addCleanup(os.close, held)
        loader = Mock(return_value="synthetic")
        with self.assertRaises(RuntimeSetupError):
            tunnel.start(self.root, self.binary, ID, self.checksum, key_loader=loader, exec_runner=Mock())
        loader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
