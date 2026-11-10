"""Synthetic client-pairing server: no real GUI, credentials or network."""
import atexit
import json
import os
from pathlib import Path
import sys

blocked, shown = [], []


def audit(event, args):
    if event in {"socket.connect", "socket.connect_ex", "socket.bind", "socket.getaddrinfo", "subprocess.Popen"}:
        blocked.append(event)
        raise RuntimeError("Synthetic pairing forbids network and subprocesses")


sys.addaudithook(audit)
atexit.register(lambda: Path(os.environ["BRIDGE_TEST_AUDIT"]).write_text(
    json.dumps({"blocked_events": blocked, "shown_count": len(shown)})))

from icloud_for_agents import credentials, server
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.http_keys import HttpKeys
from icloud_for_agents.http_pairing import HttpPairing
from icloud_for_agents.owner_review import OwnerReview
from test_http_keys import MemoryKeychain


def no_credentials():
    blocked.append("credential_lookup")
    raise AssertionError("Synthetic pairing must not resolve Apple credentials")


credentials.credentials = no_credentials
server.credentials = no_credentials
store = PlanStore()
keys = HttpKeys(MemoryKeychain(), store)


def show(label, token):
    assert label == "Synthetic client" and keys.authenticate(token) is not None
    shown.append(True)  # Never record or print the synthetic key.


server._http_pairing = HttpPairing(os.environ["ICLOUD_MAIL_ACCOUNT"], store, keys, presenter=show)
server._owner_reviews = OwnerReview(store, dialog=lambda review, heading: "approved")
server.main()
