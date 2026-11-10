"""Private execution-lock validation across supported transports."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError
from icloud_for_agents.operation_lock import invocation_lock


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

    def test_operation_status_and_observation_remain_available_without_outer_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PlanStore(Path(directory) / "absent")
            for name in ('email_operation_status', 'email_operations', 'email_observe_operation'):
                with invocation_lock(name, store):
                    pass
            self.assertFalse(store.root.exists())


if __name__ == "__main__":
    unittest.main()
