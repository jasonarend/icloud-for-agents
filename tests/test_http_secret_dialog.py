"""Credential display boundaries; native visibility is checked on macOS."""
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from icloud_for_agents.core import SafetyError
from icloud_for_agents.http_secret_dialog import show_client_key


TOKEN = b"ika_" + b"a" * 32 + b"." + b"b" * 43


class SecretDialogTests(unittest.TestCase):
    def test_valid_key_is_delivered_only_through_stdin(self):
        with patch("icloud_for_agents.http_secret_dialog.sys.platform", "darwin"), \
             patch("icloud_for_agents.http_secret_dialog.subprocess.run",
                   return_value=SimpleNamespace(returncode=0, stdout="CLOSED\n")) as run:
            self.assertEqual(show_client_key("Synthetic client", TOKEN), "window_closed")
        arguments, options = run.call_args.args[0], run.call_args.kwargs
        self.assertNotIn(TOKEN.decode(), str(arguments))
        self.assertNotIn(TOKEN.decode(), str(options["env"]))
        self.assertIn(TOKEN.decode(), options["input"])
        self.assertTrue(options["capture_output"])
        self.assertTrue(options["close_fds"])

    def test_native_failure_never_exposes_captured_key_or_error(self):
        failures = [SimpleNamespace(returncode=1, stdout=TOKEN.decode(), stderr=TOKEN.decode()),
                    SimpleNamespace(returncode=0, stdout=TOKEN.decode(), stderr=""),
                    subprocess.TimeoutExpired("osascript", 600, output=TOKEN)]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), \
                 patch("icloud_for_agents.http_secret_dialog.sys.platform", "darwin"), \
                 patch("icloud_for_agents.http_secret_dialog.subprocess.run") as run:
                if isinstance(failure, Exception):
                    run.side_effect = failure
                else:
                    run.return_value = failure
                with self.assertRaises(SafetyError) as caught:
                    show_client_key("Synthetic client", TOKEN)
                self.assertNotIn(TOKEN.decode(), str(caught.exception))
                self.assertIsNone(caught.exception.__cause__)

    def test_invalid_requests_and_non_macos_never_launch_a_display(self):
        with patch("icloud_for_agents.http_secret_dialog.subprocess.run") as run:
            for label, token in [("bad\nname", TOKEN), ("", TOKEN), ("a" * 81, TOKEN),
                                 ("Synthetic", b"invalid"), ("Synthetic", TOKEN.decode())]:
                with self.subTest(label=label), self.assertRaises(SafetyError):
                    show_client_key(label, token)
            with patch("icloud_for_agents.http_secret_dialog.sys.platform", "linux"), self.assertRaises(SafetyError):
                show_client_key("Synthetic", TOKEN)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
