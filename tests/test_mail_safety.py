"""Offline synthetic tests. Written for post-approval execution; never uses real mail."""

import ast
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from icloud_for_agents import core
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.service import MailService


class FakeIMAP:
    use_uid = True

    def __init__(self, uids=(1, 2, 3), capabilities=("MOVE", "UIDPLUS")):
        self.boxes = {"INBOX": {n: self.row(n) for n in uids}, "Retained": {}, "Bin": {}, "Sent": {}}
        self.validity = {"INBOX": 71, "Retained": 72, "Bin": 73, "Sent": 74}
        self.next_uid = max(uids, default=0) + 1
        self.capabilities = set(capabilities)
        self.calls = []
        self.selected = None
        self.readonly = True

    @staticmethod
    def row(n):
        return {b"FLAGS": (b"\\Flagged", b"custom"), b"RFC822.SIZE": 100,
                b"BODY[HEADER.FIELDS (FROM TO CC SUBJECT DATE MESSAGE-ID)]<0>":
                    f"From: sender@example.com\r\nSubject: Synthetic {n}\r\nMessage-ID: <{n}@test>\r\n\r\n".encode()}

    def list_folders(self):
        return [((), b"/", "INBOX"), ((b"\\Archive",), b"/", "Retained"),
                ((b"\\Trash",), b"/", "Bin"), ((b"\\Sent",), b"/", "Sent")]

    def select_folder(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        self.selected, self.readonly = folder, readonly
        return {b"UIDVALIDITY": self.validity[folder], b"UIDNEXT": self.next_uid}

    def search(self, criteria, charset=None):
        self.calls.append(("search", criteria, charset))
        low, high = map(int, criteria[1].split(":"))
        return [n for n in self.boxes[self.selected] if low <= n <= high]

    def fetch(self, uids, fields):
        self.calls.append(("fetch", list(uids), fields))
        return {n: copy.deepcopy(self.boxes[self.selected][n]) for n in uids if n in self.boxes[self.selected]}

    def has_capability(self, capability):
        return capability in self.capabilities

    def get_flags(self, uids):
        return {n: self.boxes[self.selected][n][b"FLAGS"] for n in uids}

    def add_flags(self, uids, values, silent=False):
        assert not self.readonly
        self.calls.append(("add_flags", uids, values))
        for n in uids:
            fs = set(self.boxes[self.selected][n][b"FLAGS"])
            fs.update(v.encode() for v in values)
            self.boxes[self.selected][n][b"FLAGS"] = tuple(sorted(fs))

    def remove_flags(self, uids, values, silent=False):
        assert not self.readonly
        for n in uids:
            self.boxes[self.selected][n][b"FLAGS"] = tuple(f for f in self.boxes[self.selected][n][b"FLAGS"]
                                                        if f not in [v.encode() for v in values])

    def copy(self, uids, destination):
        assert not self.readonly
        self.calls.append(("copy", uids, destination))
        for n in uids:
            self.boxes[destination][n + 10000] = copy.deepcopy(self.boxes[self.selected][n])
        return f"[COPYUID {self.validity[destination]} {','.join(map(str, uids))} " \
               f"{','.join(str(n + 10000) for n in uids)}] copied".encode()

    def move(self, uids, destination):
        self.calls.append(("move", uids, destination))
        response = self.copy(uids, destination)
        for n in uids:
            del self.boxes[self.selected][n]
        return response

    def delete_messages(self, uids, silent=False):
        self.calls.append(("delete", uids))
        self.add_flags(uids, ["\\Deleted"], silent)

    def uid_expunge(self, uids):
        assert not self.readonly
        self.calls.append(("uid_expunge", uids))
        for n in uids:
            if b"\\Deleted" in self.boxes[self.selected][n][b"FLAGS"]:
                del self.boxes[self.selected][n]

    def expunge(self, *args):
        raise AssertionError("Broad EXPUNGE must never occur")

    def close_folder(self):
        raise AssertionError("CLOSE must never occur")

    def logout(self):
        pass

    def shutdown(self):
        pass


class PaginationTests(unittest.TestCase):
    def test_backlog_reaches_oldest_and_newest_without_repetition(self):
        client = FakeIMAP(range(1, 2506))
        cursor, seen, pages = None, [], 0
        while True:
            result = core.page(client, "account", "INBOX", cursor, page_size=100)
            seen += [m["uid"] for m in result["messages"]]
            pages += 1
            self.assertLess(pages, 50)
            if result["done"]:
                break
            cursor = result["next_cursor"]
        self.assertEqual(seen, list(range(1, 2506)))
        for call in client.calls:
            if call[0] == "fetch":
                self.assertLessEqual(len(call[1]), 100)
                self.assertTrue(any("BODY.PEEK" in field for field in call[2]))
            if call[0] == "select":
                self.assertTrue(call[2])

    def test_sparse_empty_page_still_advances(self):
        c = FakeIMAP((5001,))
        first = core.page(c, "a", "INBOX")
        self.assertEqual(first["messages"], [])
        self.assertFalse(first["done"])
        second = core.page(c, "a", "INBOX", first["next_cursor"])
        third = core.page(c, "a", "INBOX", second["next_cursor"])
        self.assertEqual(third["messages"][0]["uid"], 5001)
        self.assertTrue(third["done"])

    def test_new_arrivals_do_not_move_snapshot_watermark(self):
        c = FakeIMAP((1, 2, 3))
        first = core.page(c, "a", "INBOX", page_size=1)
        c.boxes["INBOX"][4] = c.row(4)
        c.next_uid = 5
        second = core.page(c, "a", "INBOX", first["next_cursor"])
        self.assertEqual([m["uid"] for m in second["messages"]], [2, 3])

    def test_uidvalidity_change_invalidates_checkpoint(self):
        c = FakeIMAP()
        first = core.page(c, "a", "INBOX", page_size=1)
        c.validity["INBOX"] = 88
        with self.assertRaises(core.SafetyError):
            core.page(c, "a", "INBOX", first["next_cursor"])

    def test_changed_filter_or_account_refused(self):
        c = FakeIMAP()
        first = core.page(c, "a", "INBOX", page_size=1)
        for kwargs in ({"subject": "changed"}, {"account": "b"}):
            args = {"account": "a", "folder": "INBOX", "cursor": first["next_cursor"], **kwargs}
            with self.assertRaises(core.SafetyError):
                core.page(c, **args)

    def test_incremental_requires_matching_validity(self):
        c = FakeIMAP()
        with self.assertRaises(core.SafetyError):
            core.page(c, "a", "INBOX", after_uid=2)
        with self.assertRaises(core.SafetyError):
            core.page(c, "a", "INBOX", after_uid=2, expected_uidvalidity=72)
        result = core.page(c, "a", "INBOX", after_uid=2, expected_uidvalidity=71)
        self.assertEqual([m["uid"] for m in result["messages"]], [3])

    def test_read_body_is_peek_and_readonly(self):
        c = FakeIMAP()
        c.boxes["INBOX"][1] = {b"FLAGS": (b"\\Flagged",), b"RFC822.SIZE": 500000,
                                 b"BODY[]<0>": b"Content-Type: text/plain\r\n\r\nhello"}
        result = core.inspect(c, "INBOX", 71, 1)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["flags"], ["\\Flagged"])
        self.assertIn("BODY.PEEK[]<0.262144>", c.calls[-1][2])
        self.assertTrue(c.readonly)


class MutationTests(unittest.TestCase):
    def test_exact_roles_do_not_guess_english_names(self):
        c = FakeIMAP()
        self.assertEqual(core.role_folder(c, "Trash", {}), "Bin")
        with self.assertRaises(core.SafetyError):
            core.exact_folder(c, "bin")
        with self.assertRaises(core.SafetyError):
            core.role_folder(c, "Archive", {"Archive": "Bin"})

    def test_missing_or_ambiguous_role_fails_closed(self):
        c = FakeIMAP()
        c.list_folders = lambda: [((), b"/", "Trash")]
        with self.assertRaises(core.SafetyError):
            core.role_folder(c, "Trash", {})

    def test_seen_only_preserves_star_and_custom_flags(self):
        c = FakeIMAP()
        core.set_seen(c, "INBOX", 71, [1], True)
        self.assertEqual(set(c.boxes["INBOX"][1][b"FLAGS"]), {b"\\Flagged", b"custom", b"\\Seen"})
        core.set_seen(c, "INBOX", 71, [1], False)
        self.assertEqual(set(c.boxes["INBOX"][1][b"FLAGS"]), {b"\\Flagged", b"custom"})

    def test_uid_move_preserves_retained_flags(self):
        c = FakeIMAP()
        items = core.snapshot(c, "INBOX", 71, [1, 2])
        result = core.safe_move(c, "INBOX", 71, [1, 2], "Retained", items)
        self.assertEqual(result["uid_mapping"], {"1": 10001, "2": 10002})
        self.assertEqual(c.boxes["Retained"][10001][b"FLAGS"], (b"\\Flagged", b"custom"))
        self.assertIn(3, c.boxes["INBOX"])

    def test_uidplus_fallback_never_expunges_unrelated_deleted_mail(self):
        c = FakeIMAP(capabilities=("UIDPLUS",))
        c.boxes["INBOX"][3][b"FLAGS"] = (b"\\Deleted",)
        items = core.snapshot(c, "INBOX", 71, [1, 2])
        result = core.safe_move(c, "INBOX", 71, [1, 2], "Bin", items)
        self.assertEqual(result["method"], "UIDPLUS")
        self.assertIn(3, c.boxes["INBOX"])
        self.assertIn(("uid_expunge", [1, 2]), c.calls)

    def test_missing_uidplus_refuses_before_copy_or_move(self):
        c = FakeIMAP(capabilities=("MOVE",))
        items = core.snapshot(c, "INBOX", 71, [1])
        with self.assertRaises(core.SafetyError):
            core.safe_move(c, "INBOX", 71, [1], "Bin", items)
        self.assertFalse(any(call[0] in {"move", "copy", "delete"} for call in c.calls))

    def test_missing_copyuid_never_deletes_original(self):
        c = FakeIMAP(capabilities=("UIDPLUS",))
        c.copy = lambda *_: b"OK copied without mapping"
        items = core.snapshot(c, "INBOX", 71, [1])
        with self.assertRaises(core.SafetyError):
            core.safe_move(c, "INBOX", 71, [1], "Bin", items)
        self.assertIn(1, c.boxes["INBOX"])
        self.assertFalse(any(call[0] == "delete" for call in c.calls))

    def test_changed_flags_invalidates_review(self):
        c = FakeIMAP()
        items = core.snapshot(c, "INBOX", 71, [1])
        c.boxes["INBOX"][1][b"FLAGS"] = (b"\\Seen",)
        with self.assertRaises(core.SafetyError):
            core.safe_move(c, "INBOX", 71, [1], "Bin", items)

    def test_trash_to_trash_never_purges(self):
        c = FakeIMAP()
        with self.assertRaises(core.SafetyError):
            core.safe_move(c, "Bin", 73, [1], "Bin", [])

    def test_invalid_uid_batches(self):
        for values in ([], [0], [-1], [True], [1, 1], list(range(1, 102))):
            with self.assertRaises(core.SafetyError):
                core.batch(values)


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = 1000
        self.store = PlanStore(Path(self.temp.name) / "plans", clock=lambda: self.now)

    def approve_fixture(self, proposal):
        # Test fixture, not MCP API. Real operator path requires /dev/tty review.
        plan_id = proposal["plan_id"]
        self.store.write_once(self.store.path(plan_id, ".approved"),
                              {"plan_id": plan_id, "sha256": proposal["sha256"]})

    def test_approval_required(self):
        result = self.store.prepare("trash", {"account": "a", "uids": [1, 2]})
        with self.assertRaises(FileNotFoundError):
            self.store.consume(result["plan_id"], "a")

    def test_batch_receipt_consumed_once(self):
        result = self.store.prepare("trash", {"account": "a", "uids": [1, 2]})
        self.approve_fixture(result)
        self.store.consume(result["plan_id"], "a")
        with self.assertRaises(core.SafetyError):
            self.store.consume(result["plan_id"], "a")

    def test_content_changes_invalidate_send_approval(self):
        result = self.store.prepare("send", {"account": "a", "body": "original"})
        self.approve_fixture(result)
        path = self.store.path(result["plan_id"], ".json")
        plan = json.loads(path.read_text()); plan["payload"]["body"] = "changed"
        path.write_text(core.canonical(plan))
        with self.assertRaises(core.SafetyError):
            self.store.consume(result["plan_id"], "a")

    def test_expiry_and_account_binding(self):
        result = self.store.prepare("send", {"account": "a"})
        self.approve_fixture(result)
        with self.assertRaises(core.SafetyError):
            self.store.consume(result["plan_id"], "b")
        self.now += 1801
        with self.assertRaises(core.SafetyError):
            self.store.consume(result["plan_id"], "a")

    def test_crash_leaves_uncertain_status_and_blocks_replay(self):
        result = self.store.prepare("send", {"account": "a"})
        self.approve_fixture(result)
        self.store.consume(result["plan_id"], "a")
        self.assertEqual(self.store.status(result["plan_id"])["status"], "started_or_uncertain")

    def test_path_traversal_refused(self):
        with self.assertRaises(core.SafetyError):
            self.store.status("../../elsewhere")

    def test_trash_service_batch_and_recovery(self):
        c = FakeIMAP()
        service = MailService("a", "synthetic-only", store=self.store, connector=lambda: c)
        result = service.prepare_trash("INBOX", 71, [1, 2], "specific synthetic junk batch")
        self.assertEqual(set(c.boxes["INBOX"]), {1, 2, 3})
        self.approve_fixture(result)
        moved = service.execute(result["plan_id"])
        self.assertEqual(moved["status"], "moved")
        restored = service.restore(73, [10001, 10002], "INBOX")
        self.assertEqual(restored["status"], "moved")
        self.assertEqual(set(c.boxes["Bin"]), set())

    def test_send_does_not_connect_smtp_without_approval(self):
        c = FakeIMAP()
        service = MailService("me@example.com", "synthetic-only", store=self.store, connector=lambda: c)
        proposal = service.prepare_send(["you@example.com"], "review", "exact body")
        with patch("icloud_for_agents.service.smtplib.SMTP") as smtp:
            with self.assertRaises(FileNotFoundError):
                service.execute(proposal["plan_id"])
            smtp.assert_not_called()

    def test_partial_smtp_acceptance_and_sent_append_failure_never_replays(self):
        c = FakeIMAP()
        service = MailService("me@example.com", "synthetic-only", store=self.store, connector=lambda: c)
        proposal = service.prepare_send(["you@example.com"], "review", "exact body")
        self.approve_fixture(proposal)
        with patch("icloud_for_agents.service.smtplib.SMTP") as smtp:
            smtp.return_value.__enter__.return_value.send_message.return_value = {}
            result = service.execute(proposal["plan_id"])
            self.assertEqual(result["status"], "sent")
            self.assertEqual(result["sent_copy"], "unconfirmed")
            with self.assertRaises(core.SafetyError):
                service.execute(proposal["plan_id"])
            self.assertEqual(smtp.call_count, 1)


class SurfaceTests(unittest.TestCase):
    def test_no_calendar_contacts_purge_or_general_move_tools(self):
        source = (Path(__file__).parents[1] / "src/icloud_for_agents/server.py").read_text()
        tree = ast.parse(source)
        tools = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                 and any(isinstance(d, ast.Call) and ((isinstance(d.func, ast.Attribute) and d.func.attr == "tool") or (isinstance(d.func, ast.Name) and d.func.id == "mail_tool"))
                         for d in node.decorator_list)]
        self.assertEqual(len(tools), 10)
        self.assertTrue(all(name.startswith("email_") for name in tools))
        self.assertFalse(any(any(word in name for word in ("purge", "empty", "permanent", "calendar", "contact"))
                             for name in tools))

    def test_recipient_and_header_injection_rejected(self):
        for to, subject in ((["you@example.com\r\nBcc: other@example.com"], "ok"),
                            (["you@example.com"], "bad\nheader")):
            with self.assertRaises(core.SafetyError):
                core.compose("me@example.com", to, subject, "body")


if __name__ == "__main__":
    unittest.main()
