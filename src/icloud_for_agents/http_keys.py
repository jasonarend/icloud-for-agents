"""Per-client bearer credentials stored only in the local macOS Keychain.

Lifecycle calls are local setup APIs, not MCP tools. Their approval flag records
the caller's explicit owner authorization; it cannot prove a person consented.
Bearer identity does not establish different tool permissions for each client.
"""

from contextlib import contextmanager
import ctypes
import fcntl
from functools import wraps
import hmac
import json
import os
import platform
import re
import secrets
import stat
import threading
import time
import uuid

from .approvals import PlanStore
from .core import SafetyError, digest
from .http_authorization import validate_permissions, SERVICES


SERVICE = b"icloud-for-agents-http-client"
CLIENT_ID = re.compile(r"[0-9a-f]{32}\Z")
BEARER = re.compile(rb"ika_([0-9a-f]{32})\.([A-Za-z0-9_-]{43})\Z")
_KEYCHAIN_PROCESS_LOCK = threading.RLock()


def _keychain_serialized(function):
    @wraps(function)
    def invoke(*args, **kwargs):
        with _KEYCHAIN_PROCESS_LOCK:
            return function(*args, **kwargs)
    return invoke


def _client_id(value):
    if not isinstance(value, str) or not CLIENT_ID.fullmatch(value):
        raise SafetyError("Invalid HTTP client identifier")
    return value


def _label(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 80
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise SafetyError("Supply a short nonsecret HTTP client name")
    return value


def _approved(value):
    if value is not True:
        raise SafetyError("Explicit owner approval is required for this HTTP credential action")


def handoff_to_pipe(descriptor, token):
    """Write one bearer to a trusted write-only pipe, never a file or terminal.

The local caller must establish the pipe reader's identity and protect its end.
The credential is deliberately not returned, printed or placed in environment.
"""
    if not isinstance(token, bytes) or not BEARER.fullmatch(token):
        raise SafetyError("Invalid HTTP credential")
    if type(descriptor) is not int or descriptor < 3:
        raise SafetyError("Credential handoff requires a separate write-only pipe")
    try:
        info = os.fstat(descriptor)
        flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        if (not stat.S_ISFIFO(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 0  # macOS anonymous pipes have no filesystem link.
                or flags & os.O_ACCMODE != os.O_WRONLY):
            raise SafetyError("Credential handoff requires an owned write-only pipe")
        # One sub-PIPE_BUF nonblocking write is atomic. A full or disconnected
        # pipe fails before activating a candidate credential.
        fcntl.fcntl(descriptor, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        try:
            if os.write(descriptor, token) != len(token):
                raise SafetyError("Credential handoff was incomplete")
        finally:
            fcntl.fcntl(descriptor, fcntl.F_SETFL, flags)
    except OSError:
        raise SafetyError("Credential handoff failed; no replacement key was activated") from None


class _KeychainAttribute(ctypes.Structure):
    _fields_ = [("tag", ctypes.c_uint32), ("length", ctypes.c_uint32), ("data", ctypes.c_void_p)]


class _KeychainAttributes(ctypes.Structure):
    _fields_ = [("count", ctypes.c_uint32), ("attributes", ctypes.POINTER(_KeychainAttribute))]


class MacKeychain:
    """Bounded generic-password operations through Apple's Security framework."""

    def __init__(self, *, keychain=None):
        if platform.system() != "Darwin":
            raise SafetyError("HTTP credential storage requires macOS Keychain")
        self.security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        self.core = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        # Explicit references support disposable isolated native verification.
        # Ownership and lifetime of a supplied reference belong to the caller.
        self._keychain = keychain
        pointer = ctypes.c_void_p
        uint = ctypes.c_uint32
        self.security.SecKeychainFindGenericPassword.argtypes = [pointer, uint, pointer, uint, pointer,
                                                                ctypes.POINTER(uint), ctypes.POINTER(pointer), ctypes.POINTER(pointer)]
        self.security.SecKeychainFindGenericPassword.restype = ctypes.c_int32
        self.security.SecKeychainAddGenericPassword.argtypes = [pointer, uint, pointer, uint, pointer,
                                                               uint, pointer, ctypes.POINTER(pointer)]
        self.security.SecKeychainAddGenericPassword.restype = ctypes.c_int32
        self.security.SecKeychainItemCreateFromContent.argtypes = [uint, ctypes.POINTER(_KeychainAttributes),
                                                                 uint, pointer, pointer, pointer,
                                                                 ctypes.POINTER(pointer)]
        self.security.SecKeychainItemCreateFromContent.restype = ctypes.c_int32
        self.security.SecKeychainItemModifyAttributesAndData.argtypes = [pointer, pointer, uint, pointer]
        self.security.SecKeychainItemModifyAttributesAndData.restype = ctypes.c_int32
        self.security.SecKeychainItemDelete.argtypes = [pointer]
        self.security.SecKeychainItemDelete.restype = ctypes.c_int32
        self.security.SecKeychainItemFreeContent.argtypes = [pointer, pointer]
        self.security.SecKeychainItemFreeContent.restype = ctypes.c_int32
        self.security.SecKeychainGetUserInteractionAllowed.argtypes = [ctypes.POINTER(ctypes.c_ubyte)]
        self.security.SecKeychainGetUserInteractionAllowed.restype = ctypes.c_int32
        self.security.SecKeychainSetUserInteractionAllowed.argtypes = [ctypes.c_ubyte]
        self.security.SecKeychainSetUserInteractionAllowed.restype = ctypes.c_int32
        self.core.CFRelease.argtypes = [pointer]
        self.core.CFRelease.restype = None

    def _find(self, client_id):
        account = _client_id(client_id).encode("ascii")
        length, data, item = ctypes.c_uint32(), ctypes.c_void_p(), ctypes.c_void_p()
        status = self.security.SecKeychainFindGenericPassword(getattr(self, "_keychain", None), len(SERVICE), SERVICE,
                  len(account), account, ctypes.byref(length), ctypes.byref(data), ctypes.byref(item))
        if status == -25300:  # errSecItemNotFound
            return None, None
        if status:
            raise SafetyError("HTTP Keychain item is unavailable; handle any macOS access prompt locally")
        try:
            if length.value > 4096:
                raise SafetyError("Invalid HTTP Keychain item")
            value = ctypes.string_at(data, length.value)
        except BaseException:
            if item:
                self.core.CFRelease(item)
            raise
        finally:
            self.security.SecKeychainItemFreeContent(None, data)
        return value, item

    @_keychain_serialized
    def read(self, client_id):
        # Authentication must not let an HTTP request summon a Keychain prompt.
        # This flag is process-local; serialize all our native Keychain calls
        # while temporarily disabling interaction and restore its prior state.
        return self._read_with_interaction(client_id, False)

    @_keychain_serialized
    def read_interactive(self, client_id, *, owner_approved=False):
        """Allow the owner to handle Apple's prompt during explicit local setup."""
        _approved(owner_approved)
        return self._read_with_interaction(client_id, True)

    def _read_with_interaction(self, client_id, allow_ui):
        previous = ctypes.c_ubyte()
        if (self.security.SecKeychainGetUserInteractionAllowed(ctypes.byref(previous))
                or self.security.SecKeychainSetUserInteractionAllowed(allow_ui)):
            raise SafetyError("HTTP Keychain authentication is unavailable")
        try:
            value, item = self._find(client_id)
            if item:
                self.core.CFRelease(item)
            return value
        finally:
            if self.security.SecKeychainSetUserInteractionAllowed(previous.value):
                raise SafetyError("HTTP Keychain interaction state could not be restored")

    @_keychain_serialized
    def add(self, client_id, value):
        account = _client_id(client_id).encode("ascii")
        if not isinstance(value, bytes) or len(value) > 4096:
            raise SafetyError("Invalid HTTP Keychain payload")
        if self._keychain is None:
            status = self.security.SecKeychainAddGenericPassword(None, len(SERVICE), SERVICE,
                        len(account), account, len(value), value, None)
        else:
            # Unlike AddGenericPassword, this API does not fall back to the
            # default Keychain when an explicitly supplied keychain fails.
            service_buffer, account_buffer = ctypes.create_string_buffer(SERVICE), ctypes.create_string_buffer(account)
            attributes = (_KeychainAttribute * 2)(
                _KeychainAttribute(0x73766365, len(SERVICE), ctypes.cast(service_buffer, ctypes.c_void_p)),
                _KeychainAttribute(0x61636374, len(account), ctypes.cast(account_buffer, ctypes.c_void_p)),
            )
            attribute_list = _KeychainAttributes(2, attributes)
            item = ctypes.c_void_p()
            status = self.security.SecKeychainItemCreateFromContent(
                0x67656E70, ctypes.byref(attribute_list), len(value), value,
                self._keychain, None, ctypes.byref(item))
            if item:
                self.core.CFRelease(item)
        if status:
            raise SafetyError("HTTP Keychain creation failed; no key activation was confirmed")

    @_keychain_serialized
    def replace(self, client_id, value):
        if not isinstance(value, bytes) or len(value) > 4096:
            raise SafetyError("Invalid HTTP Keychain payload")
        _, item = self._find(client_id)
        if not item:
            raise SafetyError("HTTP client key is no longer active")
        try:
            status = self.security.SecKeychainItemModifyAttributesAndData(item, None, len(value), value)
            if status:
                raise SafetyError("HTTP Keychain rotation failed; verify the active credential before retrying")
        finally:
            self.core.CFRelease(item)

    @_keychain_serialized
    def delete(self, client_id):
        _, item = self._find(client_id)
        if not item:
            return False
        try:
            if self.security.SecKeychainItemDelete(item):
                raise SafetyError("HTTP Keychain revocation failed; access may remain active")
            return True
        finally:
            self.core.CFRelease(item)


class HttpKeys:
    def __init__(self, backend=None, store=None, clock=time.time):
        self.backend = backend if backend is not None else MacKeychain()
        self.store = store if store is not None else PlanStore()
        self.clock = clock
        # Separate stable ownership from the effective access configuration. This
        # permits revocation after settings change without accepting an old key.
        settings = {key: value for key, value in os.environ.items()
                    if key.startswith("ICLOUD_") and not key.startswith("ICLOUD_HTTP_")
                    and key != "ICLOUD_SECRET_FD"}
        self.instance = digest({"runtime": str(self.store.root.resolve()),
                                "installation": os.environ.get("ICLOUD_CONFIG_ROOT", "")})
        self.configuration = digest(settings)

    @contextmanager
    def _locked(self):
        self.store.ready()
        descriptor = os.open(self.store.root / "http-key-management.lock",
                             os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                raise SafetyError("Invalid HTTP key management lock")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SafetyError("Another HTTP credential action is in progress") from None
            yield
        finally:
            os.close(descriptor)

    def _read(self, client_id, *, allow_ui=False):
        _client_id(client_id)
        value = (self.backend.read_interactive(client_id, owner_approved=True)
                 if allow_ui else self.backend.read(client_id))
        if value is None:
            return None
        try:
            record = json.loads(value)
            base = {"version", "instance", "configuration", "client_id", "label", "bearer", "created_at", "rotated_at"}
            expected = base if record.get("version") == 1 else base | {"permissions", "manage_clients"}
            if (set(record) != expected
                    or type(record["version"]) is not int or record["version"] not in {1, 2}
                    or record["instance"] != self.instance or record["client_id"] != client_id
                    or not isinstance(record["configuration"], str) or not re.fullmatch(r"[0-9a-f]{64}", record["configuration"])
                    or not BEARER.fullmatch(record["bearer"].encode("ascii"))
                    or BEARER.fullmatch(record["bearer"].encode("ascii"))[1].decode() != client_id
                    or type(record["created_at"]) not in (int, float)
                    or type(record["rotated_at"]) not in (int, float)):
                raise ValueError
            _label(record["label"])
            if record["version"] == 2:
                validate_permissions(record["permissions"])
                if type(record["manage_clients"]) is not bool:
                    raise ValueError
            return record
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
            raise SafetyError("Invalid HTTP Keychain record") from None

    @staticmethod
    def _identity(record):
        identity = {key: record[key] for key in ("client_id", "label", "created_at", "rotated_at")}
        identity["permissions"] = (validate_permissions(record["permissions"]) if record["version"] == 2
                                   else {name: "none" for name in SERVICES})
        identity["manage_clients"] = record.get("manage_clients", False)
        return identity

    @staticmethod
    def _expected(record, expected_sha256):
        if expected_sha256 is None:
            return
        if (not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
                or record is None or not hmac.compare_digest(digest(record), expected_sha256)):
            raise SafetyError("HTTP client credential or permissions changed after review; prepare a fresh proposal")

    def create(self, label, handoff_fd, *, permissions=None, manage_clients=False,
               client_id=None, owner_approved=False):
        _approved(owner_approved)
        _label(label)
        permissions = validate_permissions(permissions)
        if type(manage_clients) is not bool:
            raise SafetyError("Client management permission must be explicitly boolean")
        if client_id is not None:
            _client_id(client_id)
        with self._locked():
            client_id = client_id or uuid.uuid4().hex
            if self.backend.read(client_id) is not None:
                raise SafetyError("HTTP client identifier already exists; inspect it without creating another key")
            bearer = ("ika_" + client_id + "." + secrets.token_urlsafe(32)).encode("ascii")
            now = self.clock()
            record = {"version": 2, "instance": self.instance, "configuration": self.configuration,
                      "client_id": client_id, "label": label, "bearer": bearer.decode(),
                      "permissions": permissions, "manage_clients": manage_clients,
                      "created_at": now, "rotated_at": now}
            handoff_to_pipe(handoff_fd, bearer)
            self.backend.add(client_id, json.dumps(record, separators=(",", ":")).encode())
            return self._identity(record)

    def rotate(self, client_id, handoff_fd, *, owner_approved=False, expected_sha256=None):
        _approved(owner_approved)
        with self._locked():
            record = self._read(client_id)
            self._expected(record, expected_sha256)
            if record is None:
                raise SafetyError("HTTP client key is no longer active")
            if record["version"] != 2:
                raise SafetyError("Review explicit HTTP client permissions before rotating this legacy key")
            bearer = ("ika_" + client_id + "." + secrets.token_urlsafe(32)).encode("ascii")
            replacement = dict(record, bearer=bearer.decode(), rotated_at=self.clock(),
                               configuration=self.configuration)
            handoff_to_pipe(handoff_fd, bearer)
            self.backend.replace(client_id, json.dumps(replacement, separators=(",", ":")).encode())
            return self._identity(replacement)

    def revoke(self, client_id, *, owner_approved=False, expected_sha256=None):
        _approved(owner_approved)
        with self._locked():
            record = self._read(_client_id(client_id))
            self._expected(record, expected_sha256)
            if record is None:
                return {"client_id": client_id, "status": "already_absent"}
            removed = self.backend.delete(_client_id(client_id))
            return {"client_id": client_id, "status": "revoked" if removed else "already_absent"}

    def status(self, client_id):
        """Inspect nonsecret local identity without presenting a credential."""
        record = self._read(client_id)
        if record is None:
            return {"client_id": client_id, "status": "inactive"}
        return dict(self._identity(record), status="active" if record["version"] == 2 else "permission_review_required",
                    configuration_matches=record["configuration"] == self.configuration, record_sha256=digest(record))

    def handoff(self, client_id, descriptor, *, owner_approved=False, expected_sha256=None):
        _approved(owner_approved)
        with self._locked():
            record = self._read(client_id)
            self._expected(record, expected_sha256)
            if record is None:
                raise SafetyError("HTTP client key is no longer active")
            if record["version"] != 2:
                raise SafetyError("Review explicit HTTP client permissions before handing off this legacy key")
            if record["configuration"] != self.configuration:
                raise SafetyError("HTTP client key belongs to different service settings; approve a replacement")
            handoff_to_pipe(descriptor, record["bearer"].encode("ascii"))
            return self._identity(record)

    def update_permissions(self, client_id, permissions, *, manage_clients=False, owner_approved=False, expected_sha256=None):
        """Apply an explicitly reviewed policy without replacing the credential."""
        _approved(owner_approved)
        permissions = validate_permissions(permissions)
        if type(manage_clients) is not bool:
            raise SafetyError("Client management permission must be explicitly boolean")
        with self._locked():
            record = self._read(client_id)
            self._expected(record, expected_sha256)
            if record is None:
                raise SafetyError("HTTP client key is no longer active")
            replacement = dict(record, version=2, permissions=permissions,
                               manage_clients=manage_clients, configuration=self.configuration)
            self.backend.replace(client_id, json.dumps(replacement, separators=(",", ":")).encode())
            return self._identity(replacement)

    def authorize_access(self, client_id, *, owner_approved=False, expected_sha256=None):
        """Handle a local Keychain prompt, then verify a noninteractive read.

        Run under the same Python executable/code identity as the intended HTTP
        server. This verifies the current process identity only: it cannot prove
        that macOS has granted a different host or future build permanent access.
        The owner chooses any OS prompt; this API does not operate that prompt.
        """
        _approved(owner_approved)
        with self._locked():
            if expected_sha256 is not None:
                self._expected(self._read(client_id), expected_sha256)
            record = self._read(client_id, allow_ui=True)
            self._expected(record, expected_sha256)
            if record is None:
                raise SafetyError("HTTP client key is no longer active")
            try:
                observed = self._read(client_id)
            except SafetyError:
                raise SafetyError("Noninteractive Keychain access remains unavailable for this process identity") from None
            if (observed is None or not hmac.compare_digest(record["bearer"], observed["bearer"])):
                raise SafetyError("HTTP credential changed during the Keychain access check")
            return dict(self._identity(observed), status="keychain_access_verified", scope="current_process_identity")

    def authenticate(self, bearer):
        """Read Keychain afresh, so rotation/revocation affects the next request.

        Already authorized in-flight operations are not recalled by revocation.
        Call from a worker thread rather than an async HTTP event loop.
        """
        match = BEARER.fullmatch(bearer) if isinstance(bearer, bytes) else None
        if match is None:
            return None
        try:
            record = self._read(match[1].decode("ascii"))
            if (record is not None and record["version"] == 2 and record["configuration"] == self.configuration
                    and hmac.compare_digest(record["bearer"].encode("ascii"), bearer)):
                return self._identity(record)
        except SafetyError:
            pass
        return None
