"""Bound unattended self-service permission proposals without deleting audit evidence."""

import fcntl
import math
import os
import re
import stat
import uuid
from contextlib import contextmanager

from .core import SafetyError


SLOTS = 8
_CLIENT = re.compile(r"[0-9a-f]{32}\Z")


@contextmanager
def approval_lock(store):
    """Coordinate quota cleanup with owner receipts and execution admission."""
    store.ready()
    fd = os.open(store.root / "approval-policy.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise SafetyError("Invalid owner-review admission lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SafetyError("Owner-review admission is busy; try again shortly") from None
        yield
    finally:
        os.close(fd)


def prepare_self_permission(store, client_id, payload):
    """Reserve bounded durable slots for one non-admin HTTP client's requests."""
    if (not isinstance(client_id, str) or not _CLIENT.fullmatch(client_id)
            or payload.get("action") != "permissions" or payload.get("client_id") != client_id
            or payload.get("manage_clients") is not False):
        raise SafetyError("Invalid self-service permission request")
    with approval_lock(store):
        free = None
        now = store.clock()
        for slot in range(SLOTS):
            marker = store.root / f".http-self-{client_id}-{slot}"
            if not marker.exists():
                if free is None:
                    free = marker
                continue
            record = store.read(marker)
            if not isinstance(record, dict):
                raise SafetyError("Invalid self-service proposal reservation")
            plan_id, expiry = record.get("plan_id"), record.get("expires")
            if (set(record) != {"client_id", "plan_id", "expires"} or record["client_id"] != client_id
                    or not isinstance(plan_id, str) or not _CLIENT.fullmatch(plan_id)
                    or type(expiry) not in (int, float) or not math.isfinite(expiry)):
                raise SafetyError("Invalid self-service proposal reservation")
            if now <= expiry:
                continue
            proposal = store.path(plan_id, ".json")
            related = [path for path in store.root.glob(plan_id + ".*") if path != proposal]
            if proposal.exists() and not related:
                plan = store.read(proposal)
                if (plan.get("kind") != "http_client" or plan.get("plan_id") != plan_id
                        or plan.get("payload", {}).get("client_id") != client_id
                        or plan.get("payload", {}).get("action") != "permissions"
                        or type(plan.get("expires")) not in (int, float)
                        or not math.isfinite(plan["expires"]) or now <= plan["expires"]):
                    raise SafetyError("Self-service proposal cannot be safely reclaimed")
                proposal.unlink()
            # If approval, execution, or any other record exists, preserve it and
            # its proposal. Only the reservation expires; it is not audit data.
            marker.unlink()
            directory = os.open(store.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            if free is None:
                free = marker
        if free is None:
            raise SafetyError("Too many pending permission requests for this client; wait for one to expire")
        plan_id = uuid.uuid4().hex
        # Reserve before writing the plan so a crash cannot publish an
        # uncounted proposal. The grace covers the separate plan clock read.
        store.write_once(free, {"client_id": client_id, "plan_id": plan_id, "expires": now + 1860})
        return store.prepare("http_client", payload, plan_id=plan_id)
