"""Installed-library contracts and additional synthetic mutation/approval tests."""
import base64
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from imapclient import IMAPClient

from icloud_for_agents import core
from icloud_for_agents.approvals import PlanStore, approve_interactively
from icloud_for_agents.credentials import credentials
from icloud_for_agents.service import MailService
from test_mail_safety import FakeIMAP


class WireContracts(unittest.TestCase):
    def client(self):
        client = IMAPClient.__new__(IMAPClient)  # Bypass socket-creating constructor entirely.
        client.use_uid = True
        client.folder_encode = True
        client._starttls_done = False
        client._cached_capabilities = (b"UIDPLUS", b"MOVE")
        client._imap = Mock()
        client._imap.uid.return_value = ("OK", [b"[COPYUID 73 1:2 10001:10002] copied"])
        return client

    def test_real_imapclient_copy_uses_uid_and_exact_folder(self):
        client = self.client()
        response = client.copy([1, 2], "Bin")
        self.assertEqual(core.copy_mapping(response, [1, 2]), (73, {1: 10001, 2: 10002}))
        client._imap.uid.assert_called_once_with("copy", b"1,2", b'"Bin"')

    def test_real_imapclient_move_and_uid_expunge_use_uid_commands(self):
        client = self.client()
        response = client.move([1, 2], "Bin")
        self.assertEqual(core.copy_mapping(response, [1, 2])[0], 73)
        client._imap.uid.assert_called_with("move", b"1,2", b'"Bin"')
        client.uid_expunge([1, 2])
        client._imap.uid.assert_called_with("EXPUNGE", b"1,2")

    def test_untagged_only_copyuid_move_is_uncertain_not_retried(self):
        # IMAPClient 3.0.1 returns tagged data only. Deliberately fail closed when
        # the server emits COPYUID solely as untagged OK; never add blind fallback.
        client = self.client()
        client._imap.uid.return_value = ("OK", [b"MOVE completed"])
        client._imap.untagged_responses = {"COPYUID": [b"73 1:2 10001:10002"]}
        response = client.move([1, 2], "Bin")
        with self.assertRaises(core.SafetyError):
            core.copy_mapping(response, [1, 2])
        self.assertEqual(client._imap.uid.call_count, 1)

    def test_copyuid_invalid_or_oversized_mapping_refused(self):
        for value in (b"[COPYUID 73 1:1000000 2:1000001]", b"[COPYUID 73 2 4]",
                      b"[COPYUID 73 1:2 7,7]", b"[COPYUID 0 1 2]"):
            with self.assertRaises(core.SafetyError):
                core.copy_mapping(value, [1, 2])


class AdditionalSafetyTests(unittest.TestCase):
    def test_move_and_new_arrivals_between_pages_do_not_skip_retained_backlog(self):
        c = FakeIMAP(range(1, 151))
        first = core.page(c, "a", "INBOX", page_size=50)
        seen = [m["uid"] for m in first["messages"]]
        items = core.snapshot(c, "INBOX", 71, list(range(1, 51)))
        core.safe_move(c, "INBOX", 71, list(range(1, 51)), "Retained", items)
        del c.boxes["INBOX"][75]  # Concurrent other-client removal.
        c.boxes["INBOX"][151] = c.row(151)
        c.next_uid = 152
        result = core.page(c, "a", "INBOX", first["next_cursor"], page_size=100)
        seen += [m["uid"] for m in result["messages"]]
        self.assertEqual(seen, [n for n in range(1, 151) if n != 75])
        self.assertTrue(result["done"])
        self.assertEqual(result["snapshot_high_uid"], 150)
        incremental = core.page(c, "a", "INBOX", after_uid=150, expected_uidvalidity=71)
        self.assertEqual([m["uid"] for m in incremental["messages"]], [151])

    def test_peek_and_archive_retain_unread_and_starred_state(self):
        c = FakeIMAP((1, 2))
        c.boxes["INBOX"][2][b"FLAGS"] += (b"\\Seen",)
        core.page(c, "a", "INBOX")
        before = {n: set(row[b"FLAGS"]) for n, row in c.boxes["INBOX"].items()}
        items = core.snapshot(c, "INBOX", 71, [1, 2])
        core.safe_move(c, "INBOX", 71, [1, 2], "Retained", items)
        for n, expected in before.items():
            self.assertEqual(set(c.boxes["Retained"][n + 10000][b"FLAGS"]), expected)

    def test_bad_copied_flags_preserve_originals(self):
        c = FakeIMAP(capabilities=("UIDPLUS",))
        original_copy = c.copy
        def corrupt(uids, destination):
            response = original_copy(uids, destination)
            c.boxes[destination][10001][b"FLAGS"] = (b"\\Seen",)
            return response
        c.copy = corrupt
        items = core.snapshot(c, "INBOX", 71, [1])
        with self.assertRaises(core.SafetyError):
            core.safe_move(c, "INBOX", 71, [1], "Bin", items)
        self.assertIn(1, c.boxes["INBOX"])
        self.assertNotIn(b"\\Deleted", c.boxes["INBOX"][1][b"FLAGS"])

    def test_disappeared_fetch_row_is_explicit_and_cursor_advances(self):
        c = FakeIMAP()
        original_fetch = c.fetch
        def vanish(uids, fields):
            result = original_fetch(uids, fields)
            result.pop(2, None)
            return result
        c.fetch = vanish
        result = core.page(c, "a", "INBOX")
        self.assertEqual(result["messages"][1]["status"], "vanished")
        self.assertTrue(result["done"])

    def test_tampered_checkpoint_out_of_range_refused(self):
        c = FakeIMAP()
        first = core.page(c, "a", "INBOX", page_size=1)
        state = json.loads(base64.urlsafe_b64decode(first["next_cursor"]))
        state["next_uid"] = -1
        malformed = base64.urlsafe_b64encode(json.dumps(state).encode()).decode()
        with self.assertRaises(core.SafetyError):
            core.page(c, "a", "INBOX", malformed)

    def test_trash_in_trash_refused_without_any_mutation(self):
        c = FakeIMAP()
        with tempfile.TemporaryDirectory() as directory:
            service = MailService("a", "synthetic", connector=lambda: c, store=PlanStore(Path(directory)/"p"))
            with self.assertRaises(core.SafetyError):
                service.prepare_trash("Bin", 73, [1], "cannot purge")
        self.assertFalse(any(call[0] in {"move", "copy", "delete", "uid_expunge"} for call in c.calls))


class HumanGateAndCredentialTests(unittest.TestCase):
    def test_wrong_human_confirmation_creates_no_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PlanStore(Path(directory)/"p")
            proposal = store.prepare("trash", {"account": "synthetic", "uids": [1, 2]})
            tty = Mock()
            tty.__enter__ = Mock(return_value=tty)
            tty.__exit__ = Mock(return_value=False)
            tty.readline.return_value = "yes\n"
            with patch("builtins.open", return_value=tty):
                with self.assertRaises(core.SafetyError):
                    approve_interactively(store, proposal["plan_id"])
            self.assertFalse(store.path(proposal["plan_id"], ".approved").exists())

    def test_exact_human_batch_confirmation_not_per_email(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PlanStore(Path(directory)/"p")
            proposal = store.prepare("trash", {"account": "synthetic", "uids": [1, 2, 3]})
            tty = Mock()
            tty.__enter__ = Mock(return_value=tty)
            tty.__exit__ = Mock(return_value=False)
            tty.readline.return_value = "TRASH " + proposal["plan_id"] + "\n"
            with patch("builtins.open", return_value=tty):
                approve_interactively(store, proposal["plan_id"])
            approved = store.consume(proposal["plan_id"], "synthetic")
            self.assertEqual(approved["payload"]["uids"], [1, 2, 3])
            self.assertEqual(tty.readline.call_count, 1)

    def test_synthetic_fd_handoff_does_not_invoke_keychain(self):
        reader, writer = os.pipe()
        os.write(writer, json.dumps({"email": "synthetic@example.com", "app_password": "fake-only"}).encode())
        os.close(writer)
        with patch.dict(os.environ, {"ICLOUD_SECRET_FD": str(reader)}, clear=True):
            with patch("icloud_for_agents.credentials.subprocess.run") as run:
                self.assertEqual(credentials(), ("synthetic@example.com", "fake-only"))
                run.assert_not_called()
                self.assertNotIn("ICLOUD_SECRET_FD", os.environ)

    def test_no_configuration_never_queries_keychain(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("icloud_for_agents.credentials.subprocess.run") as run:
                with self.assertRaises(ValueError):
                    credentials()
                run.assert_not_called()

    def test_unapproved_send_recipient_change_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PlanStore(Path(directory)/"p")
            proposal = store.prepare("send", {"account": "a", "to": ["first@example.com"], "body": "fixed"})
            store.write_once(store.path(proposal["plan_id"], ".approved"),
                             {"plan_id": proposal["plan_id"], "sha256": proposal["sha256"]})
            plan = store.read(store.path(proposal["plan_id"], ".json"))
            plan["payload"]["to"] = ["changed@example.com"]
            store.path(proposal["plan_id"], ".json").write_text(core.canonical(plan))
            with self.assertRaises(core.SafetyError):
                store.consume(proposal["plan_id"], "a")

class ExplicitArchiveRoleTests(unittest.TestCase):
    def test_no_special_use_requires_exact_discovered_override(self):
        c=Mock()
        c.list_folders.return_value=[((),b'/', 'INBOX'),((),b'/', 'Retained Mail'),((b'\\Trash',),b'/','Bin')]
        with self.assertRaises(core.SafetyError):core.role_folder(c,'Archive',{})
        self.assertEqual(core.role_folder(c,'Archive',{'Archive':'Retained Mail'}),'Retained Mail')
        with self.assertRaises(core.SafetyError):core.role_folder(c,'Archive',{'Archive':'Archive'})
        with self.assertRaises(core.SafetyError):core.role_folder(c,'Archive',{'Archive':'Bin'})
