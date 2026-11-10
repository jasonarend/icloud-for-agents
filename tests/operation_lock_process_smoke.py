"""Shared-runtime admission probes; run separately from the no-process suite."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError, digest
from icloud_for_agents.operation_lock import invocation_lock, require_http_write_journal


ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = "synthetic@example.com"
CHILD = r'''
import json, os, sys
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.server import safe
from fastmcp.exceptions import ToolError
name, plan_id, wait = sys.argv[1:]
def action():
    store = PlanStore()
    if plan_id:
        store.consume(plan_id, 'synthetic@example.com')
    print('entered', flush=True)
    if wait == 'crash':
        os._exit(17)
    if wait == 'wait':
        sys.stdin.readline()
    if plan_id:
        store.finish(plan_id, {'status':'applied'})
    return {'status':'applied'}
action.__name__ = name
try:
    print(json.dumps(safe(action)()), flush=True)
except ToolError as exc:
    print('refused: ' + str(exc), flush=True)
    sys.exit(3)
'''

CLEANUP_CHILD = r'''
import json, os, sys
from icloud_for_agents.cleanup_access import CleanupAccess
identity, uid, wait = sys.argv[1], int(sys.argv[2]), sys.argv[3]
def action():
    print('entered', flush=True)
    if wait == 'crash':
        os._exit(17)
    if wait == 'wait':
        sys.stdin.readline()
    return {'status':'marked_read', 'uids':[uid]}
try:
    result = CleanupAccess(identity, 'synthetic@example.com').run(
        'mark_read', 'INBOX', 71, [uid], None,
        [{'uid':uid, 'flags':[]}], action)
    print(json.dumps(result), flush=True)
except Exception as exc:
    print('refused: ' + str(exc), flush=True)
    sys.exit(3)
'''


class OperationLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="icloud-lock-test-")
        self.addCleanup(self.temp.cleanup)
        self.store = PlanStore(Path(self.temp.name) / "runtime")
        self.env = dict(HOME=self.temp.name, PATH=os.defpath,
                        PYTHONPATH=str(ROOT / "src"),
                        ICLOUD_RUNTIME_ROOT=str(self.store.root),
                        ICLOUD_MAIL_ACCOUNT=ACCOUNT,
                        ICLOUD_MAIL_ACCESS="write-enabled",
                        ICLOUD_APPROVAL_POLICY="local-receipt",
                        ICLOUD_ENABLE_MAIL="1", ICLOUD_ENABLE_CALENDAR="1",
                        FASTMCP_CHECK_FOR_UPDATES="off")

    def plan(self):
        value = self.store.prepare("contact_change", {"account": ACCOUNT,
                    "changes": [{"operation": "create"}]})
        identity = value["plan_id"]
        proposal = self.store.proposal(identity)
        self.store.write_once(self.store.path(identity, ".approved"),
                              {"plan_id": identity, "sha256": digest(proposal)})
        return identity

    def child(self, name="email_execute_approved", plan="", wait="no", http=False, **extra):
        env = self.env | extra
        if http:
            env["ICLOUD_HTTP_TRANSPORT"] = "1"
        child = subprocess.Popen([sys.executable, "-c", CHILD, name, plan, wait],
                    cwd=self.temp.name, env=env, text=True, stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(self.stop, child)
        return child

    @staticmethod
    def stop(child):
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=15)

    def test_stdio_and_http_cannot_execute_distinct_receipted_plans_together(self):
        first, second = self.plan(), self.plan()
        stdio = self.child(plan=first, wait="wait")
        self.assertEqual(stdio.stdout.readline().strip(), "entered")
        http = self.child(plan=second, http=True)
        output, errors = http.communicate(timeout=20)
        self.assertEqual(http.returncode, 3, errors)
        self.assertIn("Another server process", output)
        self.assertNotIn("entered", output)
        self.assertFalse(self.store.path(second, ".started").exists())
        self.assertEqual(self.store.status(first)["status"], "started_or_uncertain")
        stdio.stdin.write("finish\n")
        stdio.stdin.flush()
        stdio.communicate(timeout=20)
        retry = self.child(plan=second, http=True)
        output, errors = retry.communicate(timeout=20)
        self.assertEqual(retry.returncode, 0, errors)
        self.assertEqual(self.store.status(second)["status"], "applied")

    def test_status_remains_available_during_another_process_write(self):
        writer = self.child(wait="wait")
        self.assertEqual(writer.stdout.readline().strip(), "entered")
        reader = self.child(name="email_plan_status", http=True)
        output, errors = reader.communicate(timeout=20)
        self.assertEqual(reader.returncode, 0, errors)
        self.assertIn("entered", output)

    def test_process_death_releases_lock_but_does_not_allow_plan_replay(self):
        identity = self.plan()
        writer = self.child(plan=identity, wait="crash")
        output, errors = writer.communicate(timeout=20)
        self.assertEqual(writer.returncode, 17, errors)
        self.assertTrue(self.store.path(identity, ".started").exists())
        self.assertFalse(self.store.path(identity, ".result").exists())
        replay = self.child(plan=identity, http=True)
        output, errors = replay.communicate(timeout=20)
        self.assertEqual(replay.returncode, 3, errors)
        self.assertIn("never replay", output)
        with invocation_lock("email_execute_approved", self.store):
            pass

    def test_trusted_followup_after_crash_remains_blocked(self):
        first, second = self.plan(), self.plan()
        writer = self.child(plan=first, wait="crash")
        writer.communicate(timeout=20)
        followup = self.child(plan=second, http=True,
                              ICLOUD_APPROVAL_POLICY="trusted-client")
        output, errors = followup.communicate(timeout=20)
        self.assertEqual(followup.returncode, 3, errors)
        self.assertIn("unfinished or uncertain", output)
        self.assertFalse(self.store.path(second, ".started").exists())

    def test_invalid_lock_permissions_and_symlink_refuse(self):
        self.store.ready()
        path = self.store.root / "mcp-operation.lock"
        path.write_text("")
        path.chmod(0o644)
        with self.assertRaises(SafetyError):
            with invocation_lock("email_archive", self.store):
                self.fail("Invalid lock entered")
        path.unlink()
        target = Path(self.temp.name) / "target"
        target.write_text("")
        path.symlink_to(target)
        with self.assertRaises(OSError):
            with invocation_lock("email_archive", self.store):
                self.fail("Symlink lock entered")

    def test_http_direct_unjournaled_writes_refuse_before_callback(self):
        for name in ("email_set_read_state", "email_archive", "email_restore_from_trash"):
            with self.subTest(name=name):
                child = self.child(name=name, http=True)
                output, errors = child.communicate(timeout=20)
                self.assertEqual(child.returncode, 3, errors)
                self.assertNotIn("entered", output)
                self.assertIn("durable interrupted-write journal", output)

    def test_stdio_direct_calls_keep_existing_transport_behavior(self):
        with patch.dict(os.environ, {}, clear=True):
            for name in ("email_set_read_state", "email_archive", "email_restore_from_trash"):
                require_http_write_journal(name)

    def test_http_cleanup_paths_can_reach_existing_scope_enforcement(self):
        with patch.dict(os.environ, {"ICLOUD_HTTP_TRANSPORT":"1",
                                     "ICLOUD_CLEANUP_GRANT_ID":"a" * 32}, clear=True):
            require_http_write_journal("email_set_read_state")
            require_http_write_journal("email_archive")
            with self.assertRaises(SafetyError):
                require_http_write_journal("email_restore_from_trash")

    def cleanup_scope(self):
        identity = "a" * 32
        authority = PlanStore(self.store.root / "cleanup-authority")
        authority.write_once(authority.path(identity, ".json"), {
            "grant_id":identity, "account":ACCOUNT, "operations":["mark_read"],
            "source_folders":["INBOX"], "destinations":{}, "max_batch":1,
            "max_24h":1, "issued":1, "expires":None,
            "rules_reference":"Synthetic cross-process quota check"})
        return identity

    def cleanup_child(self, identity, uid, wait="no"):
        child = subprocess.Popen([sys.executable, "-c", CLEANUP_CHILD,
                                  identity, str(uid), wait],
                    cwd=self.temp.name, env=self.env, text=True, stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(self.stop, child)
        return child

    def test_cleanup_processes_share_execution_and_rolling_quota(self):
        identity = self.cleanup_scope()
        first = self.cleanup_child(identity, 1, "wait")
        self.assertEqual(first.stdout.readline().strip(), "entered")
        competing = self.cleanup_child(identity, 2)
        output, errors = competing.communicate(timeout=20)
        self.assertEqual(competing.returncode, 3, errors)
        self.assertNotIn("entered", output)
        first.stdin.write("finish\n")
        first.stdin.flush()
        first.communicate(timeout=20)
        quota = self.cleanup_child(identity, 2)
        output, errors = quota.communicate(timeout=20)
        self.assertEqual(quota.returncode, 3, errors)
        self.assertIn("rolling24h", output)
        self.assertNotIn("entered", output)

    def test_cleanup_crash_blocks_a_fresh_process_without_replay(self):
        identity = self.cleanup_scope()
        first = self.cleanup_child(identity, 1, "crash")
        first.communicate(timeout=20)
        self.assertEqual(first.returncode, 17)
        followup = self.cleanup_child(identity, 2)
        output, errors = followup.communicate(timeout=20)
        self.assertEqual(followup.returncode, 3, errors)
        self.assertIn("Uncertain standing cleanup", output)
        self.assertNotIn("entered", output)


if __name__ == "__main__":
    unittest.main()
