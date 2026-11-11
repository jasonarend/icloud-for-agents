"""Exercise isolated real launchd HTTP-service control with a harmless dummy.

No HTTP listener, Keychain access, Apple account or production job is used.
The temporary project gives the product's label function a unique job identity.
"""
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

from icloud_for_agents import http_service as service


def run():
    domain = f"gui/{os.getuid()}"

    def launchctl(*arguments):
        return subprocess.run(["/bin/launchctl", *arguments], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=10, check=False)

    available = launchctl("print", domain)
    if available.returncode in {113, 125}:
        return {"status": "skipped", "reason": "gui_launchd_domain_unavailable",
                "launchctl_returncode": available.returncode, "production_service_touched": False}
    if available.returncode:
        raise AssertionError("GUI launchd domain could not be inspected")

    cycles = 0
    recoveries = 0
    for start_at_login in (False, True):
        with tempfile.TemporaryDirectory(prefix="icloud-http-launchd-") as directory:
            root = Path(directory).resolve() / "project"
            home = Path(directory).resolve() / "home"
            ready = root / "ready"
            home.mkdir(mode=0o700)
            (root / "local").mkdir(parents=True, mode=0o700)
            (root / ".venv/bin").mkdir(parents=True)
            # Exercise launchd with an owned launcher, independently of the
            # shared CI interpreter's ownership or group-write permissions.
            python = root / ".venv/bin/python"
            python.write_text("#!/bin/sh\nexec " + shlex.quote(str(Path(sys.executable).resolve())) + " \"$@\"\n")
            python.chmod(0o700)
            (root / "scripts").mkdir()
            (root / "scripts/run-http.py").write_text("# Synthetic placeholder; never executed\n")
            (root / "scripts/manage-http.py").write_text(
                "import os, signal, sys, time\n"
                "from pathlib import Path\n"
                "assert sys.argv[1:] == ['--serve']\n"
                f"assert os.environ['HOME'] == {str(home)!r}\n"
                "def stop(*args):\n"
                "    time.sleep(0.3)\n"
                "    raise SystemExit(0)\n"
                "signal.signal(signal.SIGTERM, stop)\n"
                f"Path({str(ready)!r}).write_text(str(os.getpid()))\n"
                "while True: time.sleep(1)\n")
            for name in ("manage-http.py", "run-http.py"):
                (root / "scripts" / name).chmod(0o600)
            settings = root / "local/settings.json"
            settings.write_text(json.dumps({"account": "synthetic@example.invalid",
                                            "apple_keychain_service": "synthetic",
                                            "access_mode": "read-only", "dav_reads": False}))
            settings.chmod(0o600)
            # Bind only to choose an available port; no socket ever listens.
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            target = f"{domain}/{service.label(root)}"
            plist = home / "Library/LaunchAgents" / (service.label(root) + ".plist")

            def wait_for(predicate, description):
                deadline = time.monotonic() + 7
                while time.monotonic() < deadline:
                    if predicate():
                        return
                    time.sleep(0.05)
                raise AssertionError(description)

            def assert_absent():
                if launchctl("print", target).returncode != 113 or launchctl("print", domain).returncode:
                    raise AssertionError("Synthetic HTTP service absence could not be verified")

            try:
                with patch("icloud_for_agents.http_keys.MacKeychain",
                           side_effect=AssertionError("No Keychain use in lifecycle test")):
                    preview = service.prepare(root, home=home, port=port, start_at_login=start_at_login)
                    assert not (root / "local/http-service.json").exists()
                    service.configure(root, preview["review_sha256"], home=home,
                                      port=port, start_at_login=start_at_login)
                    assert service.install(root, home)["started"] is False
                    assert service.status(root, home)["status"] == "not_loaded"
                    assert service.start(root, home)["started"] is True
                    wait_for(ready.exists, "Synthetic HTTP service did not execute its reviewed arguments")
                    assert service.status(root, home)["running"] is True
                    assert service.start(root, home)["reason"] == "already_loaded"
                    assert service.stop(root, home)["stopped"] is True
                    assert_absent()
                    assert plist.exists()
                    ready.unlink()
                    assert service.start(root, home)["started"] is True
                    wait_for(ready.exists, "Synthetic HTTP service did not restart")
                    # A normal termination leaves the job registered. Exercise
                    # the product's recovery path for that stopped registration.
                    assert launchctl("kill", "SIGTERM", target).returncode == 0
                    wait_for(lambda: service.status(root, home).get("running") is False,
                             "Synthetic HTTP service did not become stopped")
                    ready.unlink()
                    assert service.start(root, home)["started"] is True
                    wait_for(ready.exists, "Synthetic HTTP service did not recover")
                    recoveries += 1
                    assert service.stop(root, home)["stopped"] is True
                    assert_absent()
                    assert service.remove(root, home)["removed"] is True
                    assert not plist.exists() and not (root / "local/http-service.json").exists()
                    assert settings.exists()
                    cycles += 1
            finally:
                # Cleanup is restricted to the random temporary project's job.
                if launchctl("print", target).returncode == 0:
                    launchctl("kill", "SIGKILL", target)
                    launchctl("bootout", target)
                    wait_for(lambda: launchctl("print", target).returncode == 113,
                             "Synthetic HTTP service cleanup was not confirmed")
                assert_absent()
    return {"status": "passed", "cycles": cycles, "stopped_job_recoveries": recoveries,
            "real_launchd_used": True, "production_service_touched": False,
            "credentials_used": False, "network_listener_used": False}


if __name__ == "__main__":
    print(json.dumps(run()))
