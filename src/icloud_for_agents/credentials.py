"""Runtime-only secret resolution. No .env, HTTP passwords, or credential logging."""

import json
import os
import subprocess


def credentials() -> tuple[str, str]:
    """Explicit user handoff by inherited FD, or pre-existing macOS Keychain item.

    Never provision/write a credential. Invocation is deferred until a mail tool is called.
    ICLOUD_SECRET_FD is intended to refer to an anonymous pipe supplied by a trusted
    local launcher. This is a launcher contract: the reader validates descriptor
    number and bounded JSON payload, not descriptor type or anonymous-pipe provenance.
    It does not use fstat to prove that the descriptor is a pipe.
    Its JSON payload is {"email": ..., "app_password": ...}; consumed once by the server.
    """
    fd = os.environ.get("ICLOUD_SECRET_FD")
    if fd is not None:
        number = int(fd)
        if number < 3:
            raise ValueError("Secret handoff requires a separate inherited descriptor")
        with os.fdopen(number, "rb", closefd=True) as stream:
            raw = stream.read(8193)
        os.environ.pop("ICLOUD_SECRET_FD", None)
        if len(raw) > 8192:
            raise ValueError("Secret handoff exceeded size limit")
        data = json.loads(raw)
        account, password = data["email"], data["app_password"]
    else:
        account = os.environ.get("ICLOUD_MAIL_ACCOUNT", "")
        if not account:
            raise ValueError("A local account and user-managed secret handoff are required")
        # The user creates this item through Keychain Access after separate approval.
        # No password in argv, environment, stdout logs, or configuration.
        result = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s",
             os.environ.get("ICLOUD_KEYCHAIN_SERVICE", "icloud-for-agents"), "-a", account, "-w"],
            capture_output=True, check=False, timeout=120,
        )
        if result.returncode:
            raise ValueError("Keychain item unavailable; user must authorize access locally")
        password = result.stdout.decode("utf-8").rstrip("\r\n")
    if not isinstance(account, str) or not isinstance(password, str) or not account or not password:
        raise ValueError("Invalid local credential handoff")
    if any(c in account + password for c in "\r\n\x00"):
        raise ValueError("Invalid credential characters")
    return account, password
