"""Private execution-lock validation and HTTP direct-Mail restrictions."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError
from icloud_for_agents.operation_lock import invocation_lock, require_http_write_journal


class OperationLockUnitTests(unittest.TestCase):
    def test_read_status_does_not_create_a_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PlanStore(Path(directory) / "absent")
            with invocation_lock("email_plan_status", store):
                pass
            self.assertFalse(store.root.exists())

    def test_invalid_lock_permissions_and_symlink_refuse(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PlanStore(Path(directory) / "runtime")
            store.ready()
            path = store.root / "mcp-operation.lock"
            path.write_text("")
            path.chmod(0o644)
            with self.assertRaises(SafetyError):
                with invocation_lock("email_archive", store):
                    self.fail("Invalid lock entered")
            path.unlink()
            target = Path(directory) / "target"
            target.write_text("")
            path.symlink_to(target)
            with self.assertRaises(OSError):
                with invocation_lock("email_archive", store):
                    self.fail("Symlink lock entered")

    def test_http_direct_changes_require_existing_durable_path(self):
        with patch.dict(os.environ, {"ICLOUD_HTTP_TRANSPORT": "1"}, clear=True):
            for name in ("email_set_read_state", "email_archive", "email_restore_from_trash"):
                with self.subTest(name=name), self.assertRaises(SafetyError):
                    require_http_write_journal(name)
            require_http_write_journal("email_execute_approved")
        with patch.dict(os.environ, {"ICLOUD_HTTP_TRANSPORT":"1",
                                     "ICLOUD_CLEANUP_GRANT_ID":"a" * 32}, clear=True):
            require_http_write_journal("email_set_read_state")
            require_http_write_journal("email_archive")
            with self.assertRaises(SafetyError):
                require_http_write_journal("email_restore_from_trash")

    def test_stdio_keeps_existing_direct_mail_behavior(self):
        with patch.dict(os.environ, {}, clear=True):
            for name in ("email_set_read_state", "email_archive", "email_restore_from_trash"):
                require_http_write_journal(name)


if __name__ == "__main__":
    unittest.main()
