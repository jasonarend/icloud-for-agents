"""Isolated HTTP fixture: production tools, authorization and approval engines.

Only provider boundaries and the human review dialog are synthetic. Never use
this fixture with a real configuration or expose it beyond loopback.
"""
import atexit
from contextlib import contextmanager
import hmac
import json
import os
from pathlib import Path
import signal
import sys
from types import SimpleNamespace

PORT = int(sys.argv[1])
events = []
calls = {name: {"reads": 0, "writes": 0} for name in
         ("mail", "calendar", "contacts", "notes", "reminders")}


def audit(event, args):
    forbidden = event in {"socket.connect", "socket.connect_ex", "socket.getaddrinfo", "subprocess.Popen"}
    if event == "socket.bind":
        forbidden = args[1] != ("127.0.0.1", PORT)
    if forbidden:
        events.append(event)
        raise AssertionError("Synthetic server forbids outgoing network and subprocesses")


sys.addaudithook(audit)
atexit.register(lambda: Path(os.environ["BRIDGE_TEST_AUDIT"]).write_text(
    json.dumps({"blocked_events": events, "calls": calls})))

from icloud_for_agents import credentials


def no_credentials():
    events.append("credential_lookup")
    raise AssertionError("Synthetic server must not retrieve credentials")


credentials.credentials = no_credentials
from icloud_for_agents import server, native_tools, native_read
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.dav import DAVReader
from icloud_for_agents.dav_changes import DAVChanges
from icloud_for_agents.http_transport import create_app
from icloud_for_agents.owner_review import OwnerReview
from icloud_for_agents.service import MailService
from test_mail_operations import RecoveryIMAP
from test_native_once import Adapter

store = PlanStore()
imap = RecoveryIMAP()
original_select = imap.select_folder
original_add = imap.add_flags
original_move = imap.move


def select(*args, **kwargs):
    calls["mail"]["reads"] += 1
    return original_select(*args, **kwargs)


def add(*args, **kwargs):
    calls["mail"]["writes"] += 1
    return original_add(*args, **kwargs)


def move(*args, **kwargs):
    calls["mail"]["writes"] += 1
    return original_move(*args, **kwargs)


imap.select_folder = select
imap.add_flags = add
imap.move = move
server._service = MailService(os.environ["ICLOUD_MAIL_ACCOUNT"], "synthetic-not-a-credential",
                              store=store, connector=lambda: imap)
server._dav_owner = SimpleNamespace(account=os.environ["ICLOUD_MAIL_ACCOUNT"],
                                    _password="synthetic-not-a-credential", store=store)
# This is an explicitly synthetic person, not a real owner-approval assertion.
server._owner_reviews = OwnerReview(store, dialog=lambda review, heading: "approved")
collections = {"cal": "https://caldav.icloud.com/synthetic/",
               "card": "https://contacts.icloud.com/synthetic/"}
objects = {}


def discover(self, kind):
    calls["calendar" if kind == "cal" else "contacts"]["reads"] += 1
    return [{"url": collections[kind], "name": "Synthetic " + kind}]


def known(self, kind, url):
    if url != collections[kind]:
        raise AssertionError("Only synthetic collections are allowed")
    return url


def transport(self, method, url, body, depth):
    if method != "GET" or not any(url.startswith(c) for c in collections.values()):
        raise AssertionError("Unexpected synthetic DAV read")
    value = objects.get(url)
    return (200, {"etag": value["etag"]}, value["content"].encode()) if value else (404, {}, b"")


def write(self, kind, item):
    service = "calendar" if kind == "cal" else "contacts"
    if item["operation"] != "create" or item["url"] in objects:
        raise AssertionError("Expected a new synthetic DAV object")
    calls[service]["writes"] += 1
    objects[item["url"]] = {"etag": '"synthetic"', "content": item["content"]}
    return {"url": item["url"], "operation": "create", "status": "applied"}


DAVReader.collections = discover
DAVReader.known_collection = known
DAVReader._https = transport
DAVChanges._write = write


def read(self, operation, **kwargs):
    if operation != "sources":
        raise AssertionError("Unexpected synthetic native read")
    calls[self.name]["reads"] += 1
    return {"items": [{"id": "selected", "title": "Synthetic " + self.name}], "done": True}


native_read.NativeReader.read = read


class CountedAdapter(Adapter):
    def apply(self, item):
        result = super().apply(item)
        calls[self.name]["writes"] += 1
        return result


adapters = {name: CountedAdapter(name) for name in ("notes", "reminders")}
native_tools.NotesCreateAdapter = lambda: adapters["notes"]
native_tools.RemindersCreateAdapter = lambda: adapters["reminders"]

SERVICES = tuple(calls)
TOKENS = {name: ("ika_" + char * 32 + "." + char * 43).encode()
          for name, char in (("read", "a"), ("full", "b"), ("mail", "c"), ("other", "d"))}


class SyntheticKeys:
    def authenticate(self, token):
        for identity, expected in TOKENS.items():
            if hmac.compare_digest(token, expected):
                return {"client_id": expected[4:36].decode(), "manage_clients": False,
                        "permissions": {name: ("full" if identity == "full" else
                            "read" if identity in {"read", "other"} or name == "mail" else "none") for name in SERVICES}}
        return None


import uvicorn


class SyntheticServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self):
        # Uvicorn normally re-raises SIGTERM, bypassing Python atexit. Preserve
        # graceful shutdown so the isolated provider audit is always collected.
        previous = signal.signal(signal.SIGTERM, self.handle_exit)
        try:
            yield
        finally:
            signal.signal(signal.SIGTERM, previous)


SyntheticServer(uvicorn.Config(create_app(server.mcp, SyntheticKeys()), host="127.0.0.1", port=PORT,
                              access_log=False, log_level="warning")).run()
