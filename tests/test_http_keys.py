"""Synthetic HTTP key lifecycle; never touch the real macOS Keychain."""
import fcntl
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError
from icloud_for_agents.http_keys import HttpKeys, MacKeychain, handoff_to_pipe


FULL = {name: "full" for name in ("mail", "calendar", "contacts", "notes", "reminders")}


class MemoryKeychain:
    def __init__(self):
        self.items = {}

    def read(self, identity):
        return self.items.get(identity)

    def read_interactive(self, identity, *, owner_approved=False):
        if owner_approved is not True:
            raise SafetyError("Synthetic owner approval missing")
        return self.read(identity)

    def add(self, identity, value):
        if identity in self.items:
            raise SafetyError("Duplicate synthetic key")
        self.items[identity] = value

    def replace(self, identity, value):
        if identity not in self.items:
            raise SafetyError("Missing synthetic key")
        self.items[identity] = value

    def delete(self, identity):
        return self.items.pop(identity, None) is not None


class HttpKeyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="http-key-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.backend = MemoryKeychain()
        self.store = PlanStore(Path(self.temp.name) / "runtime")
        self.keys = HttpKeys(self.backend, self.store, clock=lambda: 100)

    def pipe_action(self, action):
        read_fd, write_fd = os.pipe()
        try:
            result = action(write_fd)
            os.close(write_fd)
            write_fd = None
            return result, os.read(read_fd, 512)
        finally:
            if write_fd is not None:
                os.close(write_fd)
            os.close(read_fd)

    def create(self, label="Synthetic client"):
        return self.pipe_action(lambda fd: self.keys.create(label, fd, permissions=FULL, owner_approved=True))

    def test_create_returns_only_identity_and_authenticates_its_own_key(self):
        identity, token = self.create()
        self.assertEqual(set(identity), {"client_id", "label", "created_at", "rotated_at", "permissions", "manage_clients"})
        self.assertEqual(self.keys.authenticate(token), identity)
        self.assertNotIn(token.decode(), json.dumps(identity))
        self.assertIsNone(self.keys.authenticate(b"not-a-key"))
        self.assertIsNone(self.keys.authenticate(token[:-1] + (b"z" if token[-1:] != b"z" else b"x")))
        for item in self.store.root.iterdir():
            self.assertNotIn(token, item.read_bytes())

    def test_individual_keys_are_independent_and_do_not_contain_permissions(self):
        first, first_token = self.create("Client A")
        second, second_token = self.create("Client B")
        self.assertNotEqual(first["client_id"], second["client_id"])
        self.assertNotEqual(first_token, second_token)
        self.keys.revoke(first["client_id"], owner_approved=True)
        self.assertIsNone(self.keys.authenticate(first_token))
        self.assertEqual(self.keys.authenticate(second_token), second)
        self.assertEqual(second["permissions"], FULL)
        self.assertFalse(second["manage_clients"])

    def test_scope_changes_require_owner_review_and_apply_without_rotating_token(self):
        identity, token = self.create()
        restricted = {name: "none" for name in FULL}
        restricted["mail"] = "read"
        with self.assertRaises(SafetyError):
            self.keys.update_permissions(identity["client_id"], restricted)
        changed = self.keys.update_permissions(identity["client_id"], restricted, owner_approved=True)
        self.assertEqual(changed["permissions"], restricted)
        self.assertEqual(self.keys.authenticate(token), changed)
        self.assertFalse(changed["manage_clients"])
        admin = self.keys.update_permissions(identity["client_id"], restricted, manage_clients=True, owner_approved=True)
        self.assertTrue(self.keys.authenticate(token)["manage_clients"])
        self.assertNotIn(token.decode(), json.dumps(admin))

    def test_create_requires_explicit_permissions_and_preassigned_id_never_replays(self):
        with self.assertRaises(SafetyError):
            self.pipe_action(lambda fd: self.keys.create("Synthetic", fd, owner_approved=True))
        identity, token = self.pipe_action(lambda fd: self.keys.create("Synthetic", fd, permissions=FULL,
                                            client_id="e" * 32, owner_approved=True))
        self.assertEqual(identity["client_id"], "e" * 32)
        with patch("icloud_for_agents.http_keys.secrets.token_urlsafe", side_effect=AssertionError("Must not replace existing key")):
            with self.assertRaises(SafetyError):
                self.pipe_action(lambda fd: self.keys.create("Synthetic", fd, permissions=FULL,
                                                   client_id="e" * 32, owner_approved=True))
        self.assertEqual(self.keys.authenticate(token), identity)

    def test_legacy_key_has_no_http_access_until_explicit_scope_review(self):
        identity, token = self.create()
        legacy = json.loads(self.backend.items[identity["client_id"]])
        legacy["version"] = 1
        legacy.pop("permissions")
        legacy.pop("manage_clients")
        self.backend.items[identity["client_id"]] = json.dumps(legacy).encode()
        self.assertIsNone(self.keys.authenticate(token))
        self.assertEqual(self.keys.status(identity["client_id"])["status"], "permission_review_required")
        reviewed = self.keys.update_permissions(identity["client_id"], FULL, owner_approved=True)
        self.assertEqual(self.keys.authenticate(token), reviewed)

    def test_stale_review_refuses_before_generation_handoff_or_interactive_prompt(self):
        identity, token = self.create()
        client = identity["client_id"]
        before = self.keys.status(client)["record_sha256"]
        self.keys.update_permissions(client, dict(FULL, mail="read"), owner_approved=True)
        actions = [
            lambda: self.keys.rotate(client, 4, owner_approved=True, expected_sha256=before),
            lambda: self.keys.handoff(client, 4, owner_approved=True, expected_sha256=before),
            lambda: self.keys.revoke(client, owner_approved=True, expected_sha256=before),
            lambda: self.keys.update_permissions(client, FULL, owner_approved=True, expected_sha256=before),
            lambda: self.keys.authorize_access(client, owner_approved=True, expected_sha256=before),
        ]
        with patch("icloud_for_agents.http_keys.secrets.token_urlsafe", side_effect=AssertionError("No generation")), \
                patch("icloud_for_agents.http_keys.handoff_to_pipe", side_effect=AssertionError("No handoff")), \
                patch.object(self.backend, "read_interactive", side_effect=AssertionError("No prompt")):
            for action in actions:
                with self.assertRaisesRegex(SafetyError, "changed after review"):
                    action()
        self.assertIsNotNone(self.keys.authenticate(token))
        self.keys.revoke(client, owner_approved=True)
        with self.assertRaisesRegex(SafetyError, "changed after review"):
            self.keys.revoke(client, owner_approved=True, expected_sha256=before)

    def test_key_cannot_cross_runtime_or_effective_permission_configuration(self):
        identity, token = self.create()
        different_runtime = HttpKeys(self.backend, PlanStore(Path(self.temp.name) / "other-runtime"))
        self.assertIsNone(different_runtime.authenticate(token))
        with self.assertRaises(SafetyError):
            different_runtime.revoke(identity["client_id"], owner_approved=True)
        alternate = "disabled" if os.environ.get("ICLOUD_MAIL_ACCESS") != "disabled" else "write-enabled"
        with patch.dict(os.environ, {"ICLOUD_MAIL_ACCESS": alternate}):
            different_settings = HttpKeys(self.backend, self.store)
        self.assertIsNone(different_settings.authenticate(token))
        self.assertEqual(self.keys.authenticate(token), identity)
        self.assertEqual(different_settings.revoke(identity["client_id"], owner_approved=True)["status"], "revoked")
        self.assertIsNone(self.keys.authenticate(token))

    def test_rotation_to_changed_settings_requires_explicit_approval(self):
        identity, old = self.create()
        alternate = "disabled" if os.environ.get("ICLOUD_MAIL_ACCESS") != "disabled" else "write-enabled"
        with patch.dict(os.environ, {"ICLOUD_MAIL_ACCESS": alternate}):
            changed = HttpKeys(self.backend, self.store)
        with self.assertRaises(SafetyError):
            self.pipe_action(lambda fd: changed.rotate(identity["client_id"], fd))
        with self.assertRaises(SafetyError):
            self.pipe_action(lambda fd: changed.handoff(identity["client_id"], fd, owner_approved=True))
        _, replacement = self.pipe_action(lambda fd: changed.rotate(identity["client_id"], fd, owner_approved=True))
        self.assertIsNone(self.keys.authenticate(old))
        self.assertIsNone(self.keys.authenticate(replacement))
        self.assertIsNotNone(changed.authenticate(replacement))

    def test_rotation_invalidates_old_bearer_on_next_authentication(self):
        identity, old = self.create()
        replacement, new = self.pipe_action(lambda fd: self.keys.rotate(identity["client_id"], fd, owner_approved=True))
        self.assertEqual(replacement["client_id"], identity["client_id"])
        self.assertNotEqual(old, new)
        self.assertIsNone(self.keys.authenticate(old))
        self.assertEqual(self.keys.authenticate(new), replacement)

    def test_revocation_is_immediate_for_existing_manager_and_idempotent(self):
        identity, token = self.create()
        other = HttpKeys(self.backend, self.store)
        self.assertIsNotNone(other.authenticate(token))
        self.assertEqual(self.keys.revoke(identity["client_id"], owner_approved=True)["status"], "revoked")
        self.assertIsNone(other.authenticate(token))
        self.assertEqual(self.keys.revoke(identity["client_id"], owner_approved=True)["status"], "already_absent")

    def test_owner_approval_precedes_generation_or_backend_mutation(self):
        with patch("icloud_for_agents.http_keys.secrets.token_urlsafe", side_effect=AssertionError("Must not generate")):
            for approval in (False, None, 1, "yes"):
                with self.subTest(approval=approval), self.assertRaises(SafetyError):
                    self.keys.create("Synthetic", 4, owner_approved=approval)
        identity, token = self.create()
        for action in (lambda: self.keys.rotate(identity["client_id"], 4),
                       lambda: self.keys.revoke(identity["client_id"]),
                       lambda: self.keys.handoff(identity["client_id"], 4)):
            with self.assertRaises(SafetyError):
                action()
        self.assertIsNotNone(self.keys.authenticate(token))

    def test_handoff_rejects_files_read_ends_terminals_and_full_pipes(self):
        identity, token = self.create()
        with tempfile.TemporaryFile() as regular, self.assertRaises(SafetyError):
            handoff_to_pipe(regular.fileno(), token)
        read_fd, write_fd = os.pipe()
        try:
            with self.assertRaises(SafetyError):
                handoff_to_pipe(read_fd, token)
            flags = fcntl.fcntl(write_fd, fcntl.F_GETFL)
            fcntl.fcntl(write_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            try:
                while True:
                    os.write(write_fd, b"x" * 4096)
            except BlockingIOError:
                pass
            with self.assertRaises(SafetyError):
                self.keys.rotate(identity["client_id"], write_fd, owner_approved=True)
            self.assertIsNotNone(self.keys.authenticate(token))
        finally:
            os.close(read_fd)
            os.close(write_fd)
        with self.assertRaises(SafetyError):
            handoff_to_pipe(1, token)

    def test_management_lock_refuses_competing_rotation_and_revocation(self):
        identity, token = self.create()
        other = HttpKeys(self.backend, self.store)
        with self.keys._locked():
            with self.assertRaisesRegex(SafetyError, "Another HTTP credential action"):
                other.revoke(identity["client_id"], owner_approved=True)
            with self.assertRaisesRegex(SafetyError, "Another HTTP credential action"):
                other.rotate(identity["client_id"], 4, owner_approved=True)
        self.assertIsNotNone(self.keys.authenticate(token))

    def test_existing_key_handoff_requires_explicit_approval_and_preserves_identity(self):
        identity, token = self.create()
        observed, transferred = self.pipe_action(lambda fd: self.keys.handoff(identity["client_id"], fd, owner_approved=True))
        self.assertEqual(observed, identity)
        self.assertEqual(transferred, token)

    def test_corrupt_keychain_and_lookup_failures_refuse_authentication(self):
        identity, token = self.create()
        self.backend.items[identity["client_id"]] = b"not-json"
        self.assertIsNone(self.keys.authenticate(token))
        with patch.object(self.backend, "read", side_effect=SafetyError("Synthetic denied access")):
            self.assertIsNone(self.keys.authenticate(token))

    def test_revoked_key_cannot_be_rotated_back_into_existence(self):
        identity, token = self.create()
        self.keys.revoke(identity["client_id"], owner_approved=True)
        with self.assertRaisesRegex(SafetyError, "no longer active"):
            self.keys.rotate(identity["client_id"], 4, owner_approved=True)
        self.assertIsNone(self.keys.authenticate(token))

    def test_native_authentication_disables_ui_and_restores_state_on_failure(self):
        calls = []

        def previous(pointer):
            pointer._obj.value = 1
            return 0

        native = MacKeychain.__new__(MacKeychain)
        native.security = SimpleNamespace(
            SecKeychainGetUserInteractionAllowed=previous,
            SecKeychainSetUserInteractionAllowed=lambda value: calls.append(value) or 0,
        )
        with patch.object(native, "_find", side_effect=SafetyError("Synthetic denied access")):
            with self.assertRaises(SafetyError):
                native.read("a" * 32)
        self.assertEqual(calls, [False, 1])

    def test_authorized_access_probe_returns_no_secret_and_checks_noninteractive_access(self):
        identity, token = self.create()
        with patch.object(self.backend, "read_interactive", wraps=self.backend.read_interactive) as interactive:
            result = self.keys.authorize_access(identity["client_id"], owner_approved=True)
        interactive.assert_called_once_with(identity["client_id"], owner_approved=True)
        self.assertEqual(result["status"], "keychain_access_verified")
        self.assertEqual(result["scope"], "current_process_identity")
        self.assertNotIn(token.decode(), json.dumps(result))

    def test_access_probe_requires_exact_approval_before_any_keychain_read(self):
        with patch.object(self.backend, "read_interactive", side_effect=AssertionError("Must not access Keychain")):
            for approval in (False, None, 1, "yes"):
                with self.subTest(approval=approval), self.assertRaises(SafetyError):
                    self.keys.authorize_access("a" * 32, owner_approved=approval)

    def test_access_probe_handles_denial_without_retrying_or_leaking_secret(self):
        identity, _ = self.create()
        with patch.object(self.backend, "read_interactive", side_effect=SafetyError("Synthetic owner cancellation")) as interactive:
            with self.assertRaisesRegex(SafetyError, "Synthetic owner cancellation"):
                self.keys.authorize_access(identity["client_id"], owner_approved=True)
        self.assertEqual(interactive.call_count, 1)

    def test_interactive_read_alone_does_not_claim_noninteractive_access(self):
        identity, _ = self.create()
        stored = self.backend.items[identity["client_id"]]
        with patch.object(self.backend, "read_interactive", return_value=stored), \
                patch.object(self.backend, "read", side_effect=SafetyError("Synthetic needs UI")):
            with self.assertRaisesRegex(SafetyError, "Noninteractive Keychain access remains unavailable"):
                self.keys.authorize_access(identity["client_id"], owner_approved=True)

    def test_native_interactive_access_restores_prior_disabled_state(self):
        calls = []
        native = MacKeychain.__new__(MacKeychain)
        native.security = SimpleNamespace(
            SecKeychainGetUserInteractionAllowed=lambda pointer: 0,
            SecKeychainSetUserInteractionAllowed=lambda value: calls.append(value) or 0,
        )
        with patch.object(native, "_find", return_value=(b"synthetic", None)):
            with self.assertRaises(SafetyError):
                native.read_interactive("a" * 32)
            self.assertEqual(calls, [])
            self.assertEqual(native.read_interactive("a" * 32, owner_approved=True), b"synthetic")
        self.assertEqual(calls, [True, 0])


if __name__ == "__main__":
    unittest.main()
