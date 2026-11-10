"""Serialize upgraded MCP processes sharing one private runtime directory.

This outer execution lock does not replace per-plan, native or cleanup journals.
It prevents overlap, but cannot establish the outcome of an interrupted write.
Processes must use the same runtime and load this code to participate.
"""

from contextlib import contextmanager
import fcntl
import os
import stat

from .approvals import PlanStore
from .core import SafetyError


# Status and read calls remain available while another process is changing data.
# Proposals, policy changes and writes acquire the common outer lock before any
# domain-specific admission locks, and hold it through result persistence.
READ_CALLS = frozenset({
    "email_list_folders", "email_page", "email_inspect", "email_plan_status",
    "email_cleanup_grant_status", "calendar_list", "calendar_events",
    "contacts_addressbooks", "contacts_list", "contacts_get", "policy_status",
    "owner_request_review", "owner_review_status",
})


@contextmanager
def invocation_lock(name, store=None):
    if name in READ_CALLS:
        yield
        return
    store = store if store is not None else PlanStore()
    store.ready()
    descriptor = os.open(store.root / "mcp-operation.lock",
                         os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise SafetyError("Invalid shared MCP operation lock")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SafetyError("Another server process is executing an operation; inspect its status before trying again") from None
        yield
    finally:
        os.close(descriptor)


def require_http_write_journal(name):
    """Limit HTTP to Mail writes with an existing durable uncertainty journal.

    The HTTP launcher owns this process marker; MCP arguments cannot select it.
    Configured standing cleanup validates its scope and journal in MailService.
    Ordinary direct Mail writes keep their existing local stdio behavior.
    """
    if os.environ.get("ICLOUD_HTTP_TRANSPORT") != "1":
        return
    if (name == "email_restore_from_trash"
            or name in {"email_set_read_state", "email_archive"}
            and not os.environ.get("ICLOUD_CLEANUP_GRANT_ID")):
        raise SafetyError("This direct Mail change is unavailable over HTTP because it lacks a durable interrupted-write journal; use the local stdio connection or an approved standing cleanup scope where supported")
