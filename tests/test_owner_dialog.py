"""The graphical review transport never launches a live GUI in these tests."""

import json
import os
import subprocess
import unittest
from unittest.mock import Mock, patch

from icloud_for_agents.core import SafetyError
from icloud_for_agents.owner_dialog import (
    MAX_REQUEST_BYTES, MAX_REVIEW_CHARACTERS, REVIEW_TIMEOUT_SECONDS,
    _REVIEW_SCRIPT, request_owner_decision,
)


class OwnerDialogTests(unittest.TestCase):
    def setUp(self):
        self.platform = patch("icloud_for_agents.owner_dialog.sys.platform", "darwin")
        self.platform.start()
        self.addCleanup(self.platform.stop)
        self.run = patch("icloud_for_agents.owner_dialog.subprocess.run")
        self.process = self.run.start()
        self.addCleanup(self.run.stop)
        self.process.return_value = Mock(returncode=0, stdout="APPROVED\n", stderr="")

    def test_fixed_program_and_private_stdin_with_sanitized_environment(self):
        private = 'Synthetic review with `shell` $(substitution) and "quotes"'
        with patch.dict(os.environ, {"ICLOUD_SECRET_FD": "9", "DYLD_INSERT_LIBRARIES": "/synthetic", "API_KEY": "fake"}):
            self.assertEqual(request_owner_decision(private, "Irreversible synthetic change"), "approved")
        args, kwargs = self.process.call_args
        self.assertEqual(args[0], ["/usr/bin/osascript", "-l", "JavaScript", "-e", _REVIEW_SCRIPT])
        self.assertNotIn(private, " ".join(args[0]))
        self.assertEqual(json.loads(kwargs["input"])["review"], private)
        self.assertEqual(kwargs["timeout"], REVIEW_TIMEOUT_SECONDS)
        self.assertTrue(kwargs["close_fds"])
        self.assertNotIn("shell", kwargs)
        for key in ("ICLOUD_SECRET_FD", "DYLD_INSERT_LIBRARIES", "API_KEY"):
            self.assertNotIn(key, kwargs["env"])

    def test_complete_text_escapes_controls_unicode_and_bidi_without_truncation(self):
        review = "first\nsecond\t\x00\x1b\r\u202e\u200b\U0001f4ddlast"
        request_owner_decision(review, "Review\n\u202e")
        payload = json.loads(self.process.call_args.kwargs["input"])
        self.assertEqual(payload["review"], "first\nsecond\\t\\u0000\\u001b\\r\\u202e\\u200b\\ud83d\\udcddlast")
        self.assertEqual(payload["heading"], "Review\\n\\u202e")
        self.assertLessEqual(len(self.process.call_args.kwargs["input"].encode()), MAX_REQUEST_BYTES)

    def test_cancel_returns_declined_only(self):
        for output in ("DECLINED", "DECLINED\n"):
            self.process.return_value.stdout = output
            self.assertEqual(request_owner_decision("Synthetic complete review", "Review"), "declined")

    def test_invalid_or_partial_responses_never_approve(self):
        for output in ("", "approved", "APPROVED\nextra", "APPROVED\r\n", "APPROVED ", "DECLINED\nAPPROVED", "{\"approved\":true}"):
            self.process.return_value.stdout = output
            with self.subTest(output=output), self.assertRaises(SafetyError):
                request_owner_decision("Synthetic", "Review")

    def test_process_failure_does_not_expose_provider_or_private_output(self):
        self.process.return_value = Mock(returncode=1, stdout="APPROVED\n", stderr="synthetic private content")
        with self.assertRaises(SafetyError) as caught:
            request_owner_decision("Synthetic", "Review")
        self.assertNotIn("synthetic private content", str(caught.exception))

    def test_timeout_and_launch_failure_fail_closed(self):
        for failure in (subprocess.TimeoutExpired("synthetic", 180), OSError("private launch details"),
                        subprocess.SubprocessError("private child details")):
            self.process.side_effect = failure
            with self.subTest(failure=type(failure).__name__), self.assertRaises(SafetyError) as caught:
                request_owner_decision("Synthetic", "Review")
            self.assertNotIn("private", str(caught.exception))

    def test_review_bounds_refuse_instead_of_truncating(self):
        for review, heading in (("", "Review"), (None, "Review"), ("text", ""), ("text", "x" * 161),
                                ("x" * (MAX_REVIEW_CHARACTERS + 1), "Review"),
                                ("\u202e" * 300000, "Review")):
            with self.subTest(length=len(review) if isinstance(review, str) else None), self.assertRaises(SafetyError):
                request_owner_decision(review, heading)
        self.process.assert_not_called()

    def test_unavailable_platform_never_spawns_an_alternative_or_terminal(self):
        with patch("icloud_for_agents.owner_dialog.sys.platform", "linux"), self.assertRaises(SafetyError):
            request_owner_decision("Synthetic", "Review")
        self.process.assert_not_called()

    def test_ui_is_read_only_and_cancel_is_the_default(self):
        self.assertIn("NSScrollView", _REVIEW_SCRIPT)
        self.assertIn("text.editable = false", _REVIEW_SCRIPT)
        self.assertIn("text.richText = false", _REVIEW_SCRIPT)
        self.assertLess(_REVIEW_SCRIPT.index("addButtonWithTitle($('Cancel'))"),
                        _REVIEW_SCRIPT.index("addButtonWithTitle($('Approve'))"))
        self.assertIn("approve.keyEquivalent = $('')", _REVIEW_SCRIPT)
        self.assertIn("alert.window.initialFirstResponder = cancel", _REVIEW_SCRIPT)


if __name__ == "__main__":
    unittest.main()
