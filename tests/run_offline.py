"""Run synthetic suites with an audit hook that refuses network/listeners/children."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
(ROOT / "build/reports").mkdir(parents=True, exist_ok=True)
blocked = []


def audit(event, args):
    if event in {"socket.connect", "socket.connect_ex", "socket.bind", "socket.getaddrinfo", "subprocess.Popen"}:
        blocked.append(event)
        raise RuntimeError("Synthetic suite forbids real networking, listeners and child processes")


sys.addaudithook(audit)
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

with tempfile.TemporaryDirectory(prefix="bridge-synthetic-home-") as directory:
    os.environ.clear()
    os.environ.update(HOME=directory, PATH=os.defpath)
    stream = io.StringIO()
    started = time.monotonic()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
        unittest.defaultTestLoader.discover(str(ROOT / "tests")))
    report = {"tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
              "passed": result.wasSuccessful() and not blocked, "seconds": round(time.monotonic()-started, 3),
              "blocked_network_listener_subprocess_attempts": blocked,
              "isolated_home_and_environment": True, "real_credentials_used": False,
              "fixtures": "in-memory IMAP, mocked SMTP/TTY, synthetic inherited pipe"}
    (ROOT / "build/reports/UNIT-TEST-RESULTS.json").write_text(json.dumps(report, indent=2)+"\n")
    (ROOT / "build/reports/UNIT-TEST-OUTPUT.txt").write_text(stream.getvalue())
    print(json.dumps(report, indent=2))
    sys.exit(0 if report["passed"] else 1)
