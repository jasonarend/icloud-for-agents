"""Optional owner-selected Cloudflare tunnel; no credentials in arguments or files."""

import argparse
import base64
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import uuid

from .local_config import read_settings
from .tunnel_runtime import RuntimeSetupError, keychain_runtime_key
from .secure_executable import executable_digest, stage_executable

KEYCHAIN_SERVICE = "icloud-for-agents-cloudflare-tunnel"


def _tunnel_id(value):
    try:
        parsed = str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise RuntimeSetupError("Use the exact Cloudflare tunnel ID") from None
    if value != parsed:
        raise RuntimeSetupError("Use the exact lowercase Cloudflare tunnel ID")
    return parsed


def _binary(path, expected):
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise RuntimeSetupError("Supply the verified lowercase SHA-256 of the Cloudflare executable")
    binary = Path(path)
    if not binary.is_absolute() or binary.is_symlink():
        raise RuntimeSetupError("Use the exact reviewed Cloudflare executable, not a symlink")
    try:
        checksum = executable_digest(binary)
        if checksum != expected:
            raise ValueError()
    except (OSError, ValueError):
        raise RuntimeSetupError("The reviewed Cloudflare executable is unavailable, unsafe or changed") from None
    return binary


def preflight(root, binary, tunnel_id, expected_sha256):
    root = Path(root).resolve(strict=True)
    read_settings(root)
    return {"root": root, "binary": _binary(binary, expected_sha256),
            "tunnel_id": _tunnel_id(tunnel_id), "expected_sha256": expected_sha256}


def _validate_token(value, tunnel_id):
    """Confirm the saved token names the reviewed tunnel and default Cloudflare edge."""
    try:
        def object_pairs(pairs):
            result = {}
            for key, item in pairs:
                if key in result:
                    raise ValueError()
                result[key] = item
            return result

        raw = base64.b64decode(value, validate=True)
        if len(raw) > 8192:
            raise ValueError()
        token = json.loads(raw, object_pairs_hook=object_pairs)
        if (not isinstance(token, dict) or set(token) - {"a", "s", "t", "e"}
                or not {"a", "s", "t"} <= set(token)
                or token["t"] != tunnel_id or token.get("e", "") != ""
                or not isinstance(token["a"], str) or not re.fullmatch(r"[0-9a-f]{32}", token["a"])
                or not isinstance(token["s"], str)
                or not base64.b64decode(token["s"], validate=True)):
            raise ValueError()
    except (ValueError, TypeError, UnicodeError):
        raise RuntimeSetupError("The saved Cloudflare token does not match the reviewed tunnel") from None


def _lock(root, tunnel_id):
    local = Path(root) / "local"
    if local.is_symlink() or not local.is_dir():
        raise RuntimeSetupError("The local configuration directory is unavailable")
    info = local.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise RuntimeSetupError("The local configuration directory must be owned and not writable by others")
    directory = local / "cloudflare"
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink() or not directory.is_dir():
        raise RuntimeSetupError("Invalid Cloudflare runtime directory")
    info = directory.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise RuntimeSetupError("Cloudflare runtime directory must be private")
    fd = os.open(directory / (tunnel_id + ".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise RuntimeSetupError("Invalid Cloudflare runtime lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeSetupError("This Cloudflare tunnel is already running") from None
        os.set_inheritable(fd, True)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _exec_silently(binary, argv, environment):
    """Do not forward third-party diagnostics that could contain credentials."""
    fd = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(fd, 1)
        os.dup2(fd, 2)
    finally:
        if fd > 2:
            os.close(fd)
    os.execve(binary, argv, environment)


def start(root, binary, tunnel_id, expected_sha256, *, key_loader=keychain_runtime_key, exec_runner=_exec_silently):
    plan = preflight(root, binary, tunnel_id, expected_sha256)
    lock = _lock(plan["root"], plan["tunnel_id"])
    try:
        try:
            staged = stage_executable(plan["binary"], plan["expected_sha256"])
        except (OSError, ValueError):
            raise RuntimeSetupError("The reviewed Cloudflare executable could not be staged privately; no key was requested") from None
        secret = key_loader(service=KEYCHAIN_SERVICE, account=plan["tunnel_id"])
        _validate_token(secret, plan["tunnel_id"])
        env = {k: os.environ[k] for k in ("HOME", "TMPDIR", "LANG") if k in os.environ}
        env.update(PATH="/usr/bin:/bin:/usr/sbin:/sbin", TUNNEL_TOKEN=secret)
        # cloudflared receives the credential only in its process environment.
        # Third-party stdout/stderr are discarded by the fixed launcher.
        # Override cloudflared's default config discovery so another tunnel's
        # settings cannot silently alter this reviewed connection.
        exec_runner(str(staged), [str(staged), "tunnel", "--config", os.devnull,
                                          "--no-autoupdate", "run"], env)
    finally:
        os.close(lock)


def main(argv=None, *, root=None):
    parser = argparse.ArgumentParser(description="Run an optional owner-configured Cloudflare tunnel")
    parser.add_argument("--binary", required=True, help="Absolute path to the verified cloudflared executable")
    parser.add_argument("--binary-sha256", required=True, help="Verified SHA-256 of that executable")
    parser.add_argument("--tunnel-id", required=True, help="Exact Cloudflare tunnel ID; not its token")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Check settings and executable without reading Keychain")
    mode.add_argument("--start-with-keychain", action="store_true", help="Start using the owner's separate Keychain token")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2] if root is None else root
    try:
        if args.start_with_keychain:
            start(root, args.binary, args.tunnel_id, args.binary_sha256)
        else:
            preflight(root, args.binary, args.tunnel_id, args.binary_sha256)
            print("Cloudflare preflight passed. No Keychain item was read and no tunnel was started.")
    except Exception:
        parser.exit(1, "Cloudflare setup could not be verified or started. Check the reviewed settings and Keychain access.\n")
