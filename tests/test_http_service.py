"""HTTP service ownership, review and lifecycle without launchd or credentials."""
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from icloud_for_agents import http_service as service
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.http_keys import HttpKeys


class FakeLaunchctl:
    def __init__(self, profile):
        self.profile = profile
        self.calls = []
        self.loaded = False
        self.running = False
        self.identity_valid = True
        self.domain_available = True
        self.delay = 0
        self.bootout_code = 0
        self.kickstart_code = 0
        self.activation_runs = True

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[1] == "print":
            if len(argv[-1].split("/")) == 2:
                return SimpleNamespace(returncode=0 if self.domain_available else 113, stdout=b"")
            if self.delay:
                self.delay -= 1
                if not self.delay:
                    self.loaded = False
            if not self.loaded:
                return SimpleNamespace(returncode=113, stdout=b"")
            args = service.definition(self.profile)["ProgramArguments"]
            output = "program = " + (args[0] if self.identity_valid else "/unrelated/program")
            output += "\narguments = {\n" + "\n".join(args) + "\n}\n"
            if self.running:
                output += "pid = 123\n"
            return SimpleNamespace(returncode=0, stdout=output.encode())
        if argv[1] == "bootstrap":
            self.loaded = True
            self.running = self.profile["start_at_login"]
        elif argv[1] == "kickstart":
            self.running = self.activation_runs and self.kickstart_code == 0
            return SimpleNamespace(returncode=self.kickstart_code, stdout=b"")
        elif argv[1] == "bootout":
            if not self.delay:
                self.loaded = False
        return SimpleNamespace(returncode=self.bootout_code if argv[1] == "bootout" else 0, stdout=b"")


def fixture(directory):
    root = Path(directory) / "project"
    home = Path(directory) / "home"
    home.mkdir(mode=0o700)
    (root / "local").mkdir(parents=True, mode=0o700)
    (root / ".venv/bin").mkdir(parents=True)
    (root / ".venv/bin/python").symlink_to(Path(sys.executable).resolve())
    (root / "scripts").mkdir()
    for name in ("manage-http.py", "run-http.py"):
        (root / "scripts" / name).write_text("# Synthetic script, never executed\n")
    settings = root / "local/settings.json"
    settings.write_text(json.dumps({"account": "synthetic@example.invalid", "apple_keychain_service": "synthetic",
                                    "access_mode": "read-only", "dav_reads": False}))
    settings.chmod(0o600)
    return root.resolve(), home.resolve()


class HttpServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="http-service-")
        self.addCleanup(self.temp.cleanup)
        self.root, self.home = fixture(self.temp.name)

    def configured(self, **options):
        preview = service.prepare(self.root, home=self.home, **options)
        service.configure(self.root, preview["review_sha256"], home=self.home, **options)
        return preview["profile"]

    def installed(self, **options):
        profile = self.configured(**options)
        service.install(self.root, self.home)
        return profile, FakeLaunchctl(profile)

    def test_preview_is_nonmutating_and_fingerprint_matches_client_keys(self):
        with patch("subprocess.run", side_effect=AssertionError("No process")), \
             patch("icloud_for_agents.http_keys.MacKeychain", side_effect=AssertionError("No credentials")):
            preview = service.prepare(self.root, home=self.home, proxy_host="mcp.example.com")
        self.assertFalse((self.root / "local/http-service.json").exists())
        self.assertFalse((self.root / "local/http-service.lock").exists())
        with patch.dict(os.environ, service._environment(self.root, self.home), clear=True):
            keys = HttpKeys(backend=object(), store=PlanStore(preview["profile"]["runtime"]))
        self.assertEqual(keys.configuration, preview["profile"]["configuration"])
        self.assertEqual(keys.instance, preview["profile"]["instance"])
        self.assertFalse(preview["remote_proxy_configured"])

    def test_exact_review_and_existing_profile_are_required(self):
        preview = service.prepare(self.root, home=self.home)
        with self.assertRaises(service.HttpServiceError):
            service.configure(self.root, preview["review_sha256"], home=self.home, port=8766)
        self.assertFalse((self.root / "local/http-service.json").exists())
        service.configure(self.root, preview["review_sha256"], home=self.home)
        with self.assertRaises(FileExistsError):
            service.configure(self.root, preview["review_sha256"], home=self.home)
        with patch("subprocess.run", side_effect=AssertionError("No startup")):
            installed = service.install(self.root, self.home)
        self.assertFalse(installed["started"])
        target = self.home / "Library/LaunchAgents" / (service.label(self.root) + ".plist")
        definition = plistlib.loads(target.read_bytes())
        self.assertNotEqual(definition["Label"], "local.icloud-for-agents.tunnel")
        self.assertFalse(definition["RunAtLoad"])
        with self.assertRaises(FileExistsError):
            service.install(self.root, self.home)

    def test_start_stop_and_removal_only_target_reviewed_job(self):
        profile, runner = self.installed(proxy_host="mcp.example.com")
        with patch.object(service, "_port_free") as port:
            result = service.start(self.root, self.home, runner)
        self.assertTrue(result["started"])
        port.assert_called_once_with(8765)
        self.assertFalse(result["connection_verified"])
        self.assertTrue(service.status(self.root, self.home, runner)["running"])
        self.assertFalse(service.start(self.root, self.home, runner)["started"])
        with self.assertRaises(service.HttpServiceError):
            service.remove(self.root, self.home, runner)
        self.assertTrue(service.stop(self.root, self.home, runner)["stopped"])
        result = service.remove(self.root, self.home, runner)
        self.assertTrue(result["runtime_records_and_credentials_retained"])
        self.assertTrue((self.root / "local/settings.json").exists())
        for call in runner.calls:
            self.assertEqual(call[0], "/bin/launchctl")
            self.assertNotIn("local.icloud-for-agents.tunnel", str(call))

    def test_stop_rejects_loaded_job_with_different_program(self):
        _, runner = self.installed()
        runner.loaded = runner.running = True
        runner.identity_valid = False
        with self.assertRaises(service.HttpServiceError):
            service.stop(self.root, self.home, runner)
        self.assertFalse(any(call[1] == "bootout" for call in runner.calls))

    def test_stop_verifies_delayed_completion_and_retains_profile(self):
        _, runner = self.installed()
        runner.loaded = runner.running = True
        runner.delay = 3
        runner.bootout_code = 1
        result = service.stop(self.root, self.home, runner, sleeper=lambda _: None)
        self.assertTrue(result["stopped"])
        self.assertTrue(result["stop_request_warning"])
        self.assertEqual(sum(call[1] == "bootout" for call in runner.calls), 1)
        self.assertTrue((self.root / "local/http-service.json").exists())

    def test_domain_failure_never_claims_stopped_or_starts(self):
        _, runner = self.installed()
        runner.domain_available = False
        self.assertEqual(service.status(self.root, self.home, runner)["status"], "unavailable")
        for action in (service.start, service.stop):
            with self.assertRaises(service.HttpServiceError):
                action(self.root, self.home, runner)
        self.assertFalse(any(call[1] in {"bootstrap", "bootout"} for call in runner.calls))

    def test_settings_drift_prevents_start_but_not_stop(self):
        _, runner = self.installed()
        settings = self.root / "local/settings.json"
        data = json.loads(settings.read_text())
        data["access_mode"] = "write-enabled"
        settings.write_text(json.dumps(data))
        with self.assertRaises(service.HttpServiceError):
            service.start(self.root, self.home, runner)
        self.assertFalse(runner.calls)
        runner.loaded = runner.running = True
        self.assertTrue(service.stop(self.root, self.home, runner)["stopped"])

    def test_port_conflict_never_bootstraps_or_stops_another_process(self):
        _, runner = self.installed()
        with patch.object(service, "_port_free", side_effect=service.HttpServiceError("Port occupied")), \
             self.assertRaises(service.HttpServiceError):
            service.start(self.root, self.home, runner)
        self.assertFalse(any(call[1] in {"bootstrap", "bootout"} for call in runner.calls))

    def test_launching_uses_exact_profile_and_sanitized_environment(self):
        profile = self.configured(proxy_host="mcp.example.com")
        with patch.dict(os.environ, {"TUNNEL_TOKEN": "synthetic-sensitive", "ICLOUD_MAIL_ACCESS": "write-enabled"}), \
             patch("os.execve") as execute:
            service.serve(self.root, self.home)
        binary, argv, environment = execute.call_args.args
        self.assertEqual(binary, str(self.root / ".venv/bin/python"))
        self.assertEqual(argv[-2:], ["--proxy-host", "mcp.example.com"])
        self.assertNotIn("TUNNEL_TOKEN", environment)
        self.assertEqual(environment["ICLOUD_MAIL_ACCESS"], "read-only")
        self.assertEqual(service._configuration(environment), profile["configuration"])

    def test_symlink_files_and_parent_directories_refuse(self):
        self.configured()
        profile = self.root / "local/http-service.json"
        saved = profile.read_bytes()
        profile.unlink()
        target = self.root / "local/other.json"
        target.write_bytes(saved)
        target.chmod(0o600)
        profile.symlink_to(target)
        with self.assertRaises(OSError):
            service.install(self.root, self.home)
        profile.unlink()
        profile.write_bytes(saved)
        profile.chmod(0o600)
        (self.home / "Library").symlink_to(self.root / "local")
        with self.assertRaises(service.HttpServiceError):
            service.install(self.root, self.home)

    def test_registered_but_not_running_is_not_reported_started(self):
        _, runner = self.installed()
        runner.loaded = True
        result = service.status(self.root, self.home, runner)
        self.assertFalse(result["running"])
        self.assertFalse(result["connection_verified"])

    def test_start_recovers_matching_stopped_job_without_replacing_it(self):
        _, runner = self.installed(start_at_login=True)
        runner.loaded = True
        with patch.object(service, "_port_free") as port:
            result = service.start(self.root, self.home, runner)
        self.assertTrue(result["started"])
        self.assertFalse(result["connection_verified"])
        port.assert_called_once_with(8765)
        actions = [call for call in runner.calls if call[1] != "print"]
        self.assertEqual(actions, [["/bin/launchctl", "kickstart", f"gui/{os.getuid()}/{service.label(self.root)}"]])

    def test_start_leaves_already_running_job_alone(self):
        _, runner = self.installed()
        runner.loaded = runner.running = True
        with patch.object(service, "_port_free", side_effect=AssertionError("No preflight needed")):
            self.assertFalse(service.start(self.root, self.home, runner)["started"])
        self.assertTrue(all(call[1] == "print" for call in runner.calls))

    def test_stopped_job_recovery_refuses_wrong_identity_or_occupied_port(self):
        _, runner = self.installed()
        runner.loaded = True
        runner.identity_valid = False
        with patch.object(service, "_port_free", side_effect=AssertionError("No preflight")), \
             self.assertRaises(service.HttpServiceError):
            service.start(self.root, self.home, runner)
        runner.identity_valid = True
        with patch.object(service, "_port_free", side_effect=service.HttpServiceError("Port occupied")), \
             self.assertRaises(service.HttpServiceError):
            service.start(self.root, self.home, runner)
        self.assertTrue(all(call[1] == "print" for call in runner.calls))

    def test_recovery_rechecks_loaded_identity_after_port_preflight(self):
        _, runner = self.installed()
        runner.loaded = True
        def changed_identity(_port):
            runner.identity_valid = False
        with patch.object(service, "_port_free", side_effect=changed_identity), \
             self.assertRaises(service.HttpServiceError):
            service.start(self.root, self.home, runner)
        self.assertTrue(all(call[1] == "print" for call in runner.calls))

    def test_recovery_failure_is_not_replayed_or_reported_successful(self):
        _, runner = self.installed()
        runner.loaded = True
        runner.kickstart_code = 1
        with patch.object(service, "_port_free"), self.assertRaises(service.HttpServiceError):
            service.start(self.root, self.home, runner)
        self.assertEqual(sum(call[1] == "kickstart" for call in runner.calls), 1)
        self.assertTrue((self.root / "local/http-service.json").exists())

    def test_recovery_allows_macos_launch_throttle(self):
        _, runner = self.installed()
        runner.loaded = True

        def throttled(command, **options):
            if command[1] == "kickstart" and options["timeout"] <= 10:
                raise subprocess.TimeoutExpired(command, options["timeout"])
            return runner(command, **options)

        with patch.object(service, "_port_free"):
            result = service.start(self.root, self.home, throttled)
        self.assertTrue(result["started"])
        self.assertFalse(result["connection_verified"])
        self.assertEqual(sum(call[1] == "kickstart" for call in runner.calls), 1)

    def test_recovery_timeout_is_bounded_and_never_replayed(self):
        _, runner = self.installed()
        runner.loaded = True
        activations = []

        def unavailable(command, **options):
            if command[1] == "kickstart":
                activations.append((command, options["timeout"]))
                raise subprocess.TimeoutExpired(command, options["timeout"])
            return runner(command, **options)

        with patch.object(service, "_port_free"), self.assertRaises(subprocess.TimeoutExpired):
            service.start(self.root, self.home, unavailable)
        self.assertEqual(len(activations), 1)
        self.assertGreater(activations[0][1], 10)
        self.assertLessEqual(activations[0][1], 20)
        self.assertTrue((self.root / "local/http-service.json").exists())

    def test_recovery_requires_running_readback(self):
        _, runner = self.installed()
        runner.loaded = True
        runner.activation_runs = False
        now = [0.0]
        def advance(seconds):
            now[0] += seconds
        with patch.object(service, "_port_free"):
            result = service.start(self.root, self.home, runner, clock=lambda: now[0], sleeper=advance)
        self.assertFalse(result["started"])
        self.assertEqual(result["status"], "loaded")
        self.assertEqual(sum(call[1] == "kickstart" for call in runner.calls), 1)


if __name__ == "__main__":
    unittest.main()
