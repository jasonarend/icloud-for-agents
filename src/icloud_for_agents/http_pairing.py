"""Locally reviewed HTTP client credentials and human-only key delivery."""

import os
import fcntl
import re
import stat
import threading
import uuid

from .approvals import PlanStore
from .core import SafetyError, canonical, digest
from .http_keys import HttpKeys, validate_permissions
from .http_secret_dialog import show_client_key


CLIENT_ID = re.compile(r"[0-9a-f]{32}\Z")
ACTIONS = frozenset({"create", "rotate", "revoke", "show", "permissions", "authorize"})


def _atomic_record(store, path, value):
    """Publish a complete private status record without exposing partial JSON."""
    store.ready()
    encoded = canonical(value).encode("utf-8")
    if len(encoded) > 250000:
        raise SafetyError("Client status exceeds its safe size limit")
    temporary = store.path(uuid.uuid4().hex, ".http-tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)
    finally:
        temporary.unlink(missing_ok=True)
    directory = os.open(store.root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


class HttpPairing:
    """Prepare and execute exact local-owner-reviewed client changes.

    The owner sees the credential in a separate native window. Tool results,
    durable plans and status records never contain the credential itself.
    """

    def __init__(self, account, store=None, keys=None, presenter=None):
        if not isinstance(account, str) or not account:
            raise SafetyError("A configured account is required for client review")
        self.account = account
        self.store = store if store is not None else PlanStore()
        self.keys = keys if keys is not None else HttpKeys(store=self.store)
        self.presenter = presenter if presenter is not None else show_client_key
        self.lock = threading.Lock()
        self.active = set()

    def _record(self, client_id):
        with self.keys._locked():
            record = self.keys._read(client_id)
            if record is None:
                raise SafetyError("HTTP client key is unavailable; inspect it before preparing a change")
            return record

    def _credential_observation(self, client_id, *, uncertain=False):
        try:
            observation = self.keys.status(client_id)
            observation.pop("record_sha256", None)
            if uncertain and observation.get("status") == "inactive":
                observation["status"] = "not_observed_present"
            return observation
        except Exception:
            return {"client_id": client_id, "status": "unavailable_or_unknown"}

    def prepare(self, action, *, label=None, client_id=None, permissions=None, manage_clients=False,
                self_request=False):
        if action not in ACTIONS or type(manage_clients) is not bool:
            raise SafetyError("Choose a supported client action and explicit management setting")
        if action == "create":
            if client_id is not None or not isinstance(label, str) or not 1 <= len(label) <= 80 or any(ord(c) < 32 or ord(c) == 127 for c in label):
                raise SafetyError("Supply a short client name; its identifier is assigned locally")
            permissions = validate_permissions(permissions)
            client_id = uuid.uuid4().hex
            before = None
        else:
            if not isinstance(client_id, str) or not CLIENT_ID.fullmatch(client_id) or label is not None:
                raise SafetyError("Choose the exact existing client identifier")
            try:
                record = self._record(client_id)
            except SafetyError:
                if action != "authorize":
                    raise
                record = None
            label = record["label"] if record is not None else "Client " + client_id
            before = digest(record) if record is not None else None
            if action == "permissions":
                permissions = validate_permissions(permissions)
            else:
                if permissions is not None or manage_clients:
                    raise SafetyError("This action does not change client permissions")
                identity = self.keys._identity(record) if record is not None else {
                    "permissions": {name: "none" for name in ("mail", "calendar", "contacts", "notes", "reminders")},
                    "manage_clients": False}
                permissions = identity["permissions"]
                manage_clients = identity["manage_clients"]
        warnings = {
            "create": "Create a separate persistent client key. Its service permissions and client-management access are listed above; existing server limits and approvals still apply.",
            "rotate": "Replace this client's key. Its previous credential stops working for new requests.",
            "revoke": "Revoke this client's key. Already authorized work cannot be recalled.",
            "show": "Show the existing key locally for transfer into this client's secure credential field.",
            "permissions": "Change this client's server-enforced permissions for subsequent requests. Already authorized work cannot be recalled.",
            "authorize": "Allow a local Keychain access check for this exact client ID. The owner handles any macOS prompt; no key or client permissions are changed. If details are unavailable, the ID is the only verified target.",
        }
        payload = {"account": self.account, "action": action, "client_id": client_id,
                   "label": label, "permissions": permissions, "manage_clients": manage_clients,
                   "instance": self.keys.instance, "configuration": self.keys.configuration,
                   "before_sha256": before, "warning": warnings[action]}
        if self_request:
            from .http_authorization import self_client_id
            from .http_proposal_quota import prepare_self_permission
            if self_client_id() != client_id:
                raise SafetyError("Permission request belongs to another client")
            return prepare_self_permission(self.store, client_id, payload)
        return self.store.prepare("http_client", payload)

    def _proposal(self, plan_id):
        plan = self.store.proposal(plan_id)
        payload = plan.get("payload", {})
        if (plan.get("kind") != "http_client" or payload.get("account") != self.account
                or payload.get("instance") != self.keys.instance
                or payload.get("configuration") != self.keys.configuration
                or payload.get("action") not in ACTIONS
                or not CLIENT_ID.fullmatch(payload.get("client_id", ""))):
            raise SafetyError("Client proposal belongs to another account or installation configuration")
        if payload["action"] != "create" and not (payload["action"] == "authorize" and payload.get("before_sha256") is None):
            if digest(self._record(payload["client_id"])) != payload.get("before_sha256"):
                raise SafetyError("Client credential changed since review; prepare a new proposal")
        return plan

    def request(self, plan_id):
        """Consume an exact receipt and perform the approved action asynchronously."""
        with self.lock:
            plan = self._proposal(plan_id)
            if not self.store.path(plan_id, ".approved").exists():
                raise SafetyError("The owner must approve this exact client action in the local review window")
            self.store.consume(plan_id, self.account)
            try:
                if plan["payload"]["action"] == "create":
                    _atomic_record(self.store, self.store.path(plan_id, ".http-client-index"),
                                   {"plan_sha256": digest(plan), "client_id": plan["payload"]["client_id"]})
                self.active.add(plan_id)
                thread = threading.Thread(target=self._perform, args=(plan,), daemon=True,
                                          name="icloud-client-review")
                thread.start()
            except Exception:
                self.active.discard(plan_id)
                _atomic_record(self.store, self.store.path(plan_id, ".result"),
                               {"status": "failed_or_uncertain", "do_not_retry": True})
                raise SafetyError("Client action could not start; inspect its status before another action") from None
        return {"plan_id": plan_id, "client_id": plan["payload"]["client_id"], "status": "pending"}

    def _perform(self, plan):
        plan_id, payload = plan["plan_id"], plan["payload"]
        action, client_id = payload["action"], payload["client_id"]
        token = None
        try:
            if action in {"create", "rotate", "show"}:
                reader, writer = os.pipe()
                try:
                    if action == "create":
                        result = self.keys.create(payload["label"], writer, permissions=payload["permissions"],
                                                  manage_clients=payload["manage_clients"], client_id=client_id,
                                                  owner_approved=True)
                    elif action == "rotate":
                        result = self.keys.rotate(client_id, writer, expected_sha256=payload["before_sha256"], owner_approved=True)
                    else:
                        result = self.keys.handoff(client_id, writer, expected_sha256=payload["before_sha256"], owner_approved=True)
                    os.close(writer)
                    writer = None
                    token = os.read(reader, 512)
                finally:
                    os.close(reader)
                    if writer is not None:
                        os.close(writer)
            elif action == "revoke":
                result = self.keys.revoke(client_id, expected_sha256=payload["before_sha256"], owner_approved=True)
            elif action == "permissions":
                result = self.keys.update_permissions(client_id, payload["permissions"],
                                                      manage_clients=payload["manage_clients"],
                                                      expected_sha256=payload["before_sha256"], owner_approved=True)
            else:
                result = self.keys.authorize_access(client_id, expected_sha256=payload["before_sha256"], owner_approved=True)
            _atomic_record(self.store, self.store.path(plan_id, ".result"),
                           {"status": "completed", "client_id": client_id, "action": action,
                            "client": result, "connection_verified": False})
            if token is not None:
                try:
                    self.presenter(payload["label"], token)
                    outcome = "window_closed"
                except Exception:
                    outcome = "window_unavailable_or_interrupted"
                _atomic_record(self.store, self.store.path(plan_id, ".http-delivery"),
                               {"plan_sha256": digest(plan), "status": outcome})
        except Exception:
            try:
                if not self.store.path(plan_id, ".result").exists():
                    _atomic_record(self.store, self.store.path(plan_id, ".result"),
                                   {"status": "failed_or_uncertain", "client_id": client_id,
                                    "action": action, "do_not_retry": True})
            except Exception:
                pass
        finally:
            token = None
            with self.lock:
                self.active.discard(plan_id)

    def status(self, plan_id):
        plan = self.store.read(self.store.path(plan_id, ".json"))
        payload = plan.get("payload", {})
        if (plan.get("kind") != "http_client" or payload.get("account") != self.account
                or payload.get("instance") != self.keys.instance):
            raise SafetyError("Client proposal belongs to another account or installation")
        self.store.ready()
        descriptor = os.open(self.store.root / "approval-policy.lock",
                             os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                raise SafetyError("Invalid client approval status lock")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"plan_id": plan_id, "status": "pending"}
            status = self.store.status(plan_id)
        finally:
            os.close(descriptor)
        with self.lock:
            working = plan_id in self.active
        if payload.get("action") in {"create", "rotate", "show"} and not working:
            status["credential_observation"] = self._credential_observation(
                payload["client_id"], uncertain=status.get("status") != "completed")
        if self.store.path(plan_id, ".result").exists() and payload.get("action") in {"create", "rotate", "show"}:
            delivery = self.store.path(plan_id, ".http-delivery")
            if delivery.exists():
                evidence = self.store.read(delivery)
                if evidence.get("plan_sha256") != digest(plan) or evidence.get("status") not in {"window_closed", "window_unavailable_or_interrupted"}:
                    raise SafetyError("Invalid client key delivery evidence")
                status["delivery_status"] = evidence["status"]
            else:
                status["delivery_status"] = "pending_or_interrupted"
        if working:
            status["working_locally"] = True
            if status.get("status") == "started_or_uncertain":
                status["status"] = "pending"
        return status

    def clients(self, limit=20, after=None):
        """List bounded client identities created through this reviewed workflow."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise SafetyError("Choose a client page size from 1 to 100")
        if after is not None and (not isinstance(after, str) or not CLIENT_ID.fullmatch(after)):
            raise SafetyError("Invalid client page cursor")
        self.store.ready()
        ids = sorted(path.stem for path in self.store.root.glob("*.http-client-index") if CLIENT_ID.fullmatch(path.stem))
        if len(ids) > 10000:
            raise SafetyError("Client review inventory exceeds its safe bound")
        remaining = [identity for identity in ids if after is None or identity > after]
        page = remaining[:100]
        clients = []
        last = None
        with self.lock:
            active = set(self.active)
        for plan_id in page:
            last = plan_id
            plan = self.store.read(self.store.path(plan_id, ".json"))
            if not isinstance(plan, dict):
                raise SafetyError("Invalid client review inventory")
            payload = plan.get("payload", {})
            if not isinstance(payload, dict):
                raise SafetyError("Invalid client review inventory")
            if (plan.get("kind") != "http_client" or payload.get("action") != "create"
                    or payload.get("account") != self.account or payload.get("instance") != self.keys.instance
                    or not self.store.path(plan_id, ".started").exists()):
                continue
            index = self.store.read(self.store.path(plan_id, ".http-client-index"))
            started = self.store.read(self.store.path(plan_id, ".started"))
            receipt = self.store.read(self.store.path(plan_id, ".approved"))
            if (index != {"plan_sha256": digest(plan), "client_id": payload.get("client_id")}
                    or started.get("sha256") != digest(plan) or started.get("authorization") != "local-receipt"
                    or receipt.get("plan_id") != plan_id or receipt.get("sha256") != digest(plan)):
                raise SafetyError("Client inventory does not match its local owner review")
            client_id = payload.get("client_id")
            if active:
                observed = {"client_id": client_id, "status": "not_checked_while_action_pending"}
            elif not self.store.path(plan_id, ".result").exists():
                observed = {"client_id": client_id, "status": "pending_or_interrupted"}
            else:
                result = self.store.read(self.store.path(plan_id, ".result"))
                observed = self._credential_observation(client_id, uncertain=result.get("status") != "completed")
            clients.append({"client_id": client_id, "label": payload.get("label"),
                            "creation_plan_id": plan_id, "credential": observed})
            if len(clients) == limit:
                break
        more = bool(last is not None and any(identity > last for identity in remaining))
        return {"clients": clients, "next_cursor": last if more else None, "done": not more}
