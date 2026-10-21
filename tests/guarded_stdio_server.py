"""Credential-free discovery target; blocks network, child processes and secret lookup.

Only used by the offline test harness. Production entrypoint remains unchanged.
"""
import atexit
import json
import os
from pathlib import Path
import sys

events = []


def audit(event, args):
    if event in {"socket.connect", "socket.connect_ex", "socket.bind", "socket.getaddrinfo", "subprocess.Popen"}:
        events.append({"blocked": event})
        raise RuntimeError("Offline test forbids network, listeners and subprocesses")


sys.addaudithook(audit)


def report():
    Path(os.environ["BRIDGE_TEST_AUDIT"]).write_text(json.dumps({"blocked_events": events}))


atexit.register(report)

from icloud_for_agents import credentials


def no_credentials():
    events.append({"blocked": "credential_lookup"})
    raise AssertionError("Discovery must never request a credential")


credentials.credentials = no_credentials
from icloud_for_agents import server
from types import SimpleNamespace
from icloud_for_agents.dav import DAVReader
server._service = SimpleNamespace(account="synthetic@example.com", _password="fake-only")
server._dav_owner = server._service
DAVReader.collections = lambda self, kind: [{"name": "Synthetic", "url": "https://caldav.icloud.com/synthetic/"}]
from icloud_for_agents.server import main

main()
