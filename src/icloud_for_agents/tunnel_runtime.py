"""Restart-safe tunnel launcher. Never provisions, saves, prints, or caches a key."""
import argparse
import fcntl
import os
from pathlib import Path
import stat
import shlex
import subprocess

import yaml
from .local_config import read_settings

KEYCHAIN_SERVICE = "icloud-for-agents-tunnel-runtime"


class RuntimeSetupError(Exception):
    """Fixed nonsecret operator errors only."""


def project_root():
    return Path(__file__).resolve().parents[2]


def launch_plan(root):
    root = Path(root).resolve()
    profile = root / "local/tunnel/icloud-for-agents.yaml"
    binary = root / "tools/tunnel-client-v0.0.16/tunnel-client"
    settings = read_settings(root)
    if not settings.get("tunnel_id") or not settings.get("runtime_keychain_service"):
        raise RuntimeSetupError("This is a direct stdio configuration; an OpenAI tunnel is optional and is not configured")
    command = shlex.join([str(root / ".venv/bin/python"), str(root / "scripts/run-stdio.py")])
    try:
        if profile.is_symlink() or not profile.is_file():
            raise ValueError()
        info = profile.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError()
        data = yaml.safe_load(profile.read_text())
        if data.get("config_version") != 1:
            raise ValueError()
        if data.get("control_plane") != {"base_url": "https://api.openai.com", "tunnel_id": settings["tunnel_id"],
                                        "api_key": "env:CONTROL_PLANE_API_KEY"}:
            raise ValueError()
        if data.get("mcp") != {"commands": [{"channel": "main", "command": command}]}:
            raise ValueError()
        if data.get("health") != {"listen_addr": "127.0.0.1:0", "url_file": str(root / "local/tunnel/health.url")}:
            raise ValueError()
        if data.get("admin_ui") != {"open_browser": False} or data.get("log") != {"level": "info", "format": "json"}:
            raise ValueError()
        if set(data) != {"config_version", "control_plane", "health", "admin_ui", "log", "mcp"}:
            raise ValueError()
        if not binary.is_file() or binary.is_symlink() or not os.access(binary, os.X_OK):
            raise ValueError()
        if not (root / ".venv/bin/python").exists() or not (root / "scripts/run-stdio.py").is_file():
            raise ValueError()
    except Exception:
        raise RuntimeSetupError("Nonsecret preflight failed: verify the exact private tunnel profile and installed paths; no key was requested.") from None
    return {"root": root, "binary": binary, "profile": profile, "settings": settings,
            "argv": [str(binary), "run", "--config", str(profile)]}


def runtime_lock(root):
    """One launcher-owned runtime at a time. Lock survives exec; no PID/env scraping."""
    path = Path(root) / "local/tunnel/runtime.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise RuntimeSetupError("Runtime lock must be an owned private regular file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeSetupError("This launcher already has a runtime active; no credential was requested") from None
        os.set_inheritable(fd, True)
        return fd
    except BaseException:
        os.close(fd)
        raise


def keychain_runtime_key(runner=subprocess.run, *, service=KEYCHAIN_SERVICE, account=None):
    """Only after explicit --start-with-keychain. Owner handles any native prompt.

    The child stdout pipe is memory-only. No secret in argv, file, log, or chat.
    Timeout gives the owner time to respond but is never retried automatically.
    """
    try:
        if not account:
            raise ValueError()
        result = runner(["/usr/bin/security", "find-generic-password", "-s", service,
                         "-a", account, "-w"], capture_output=True, check=False, timeout=120)
        if result.returncode:
            raise ValueError()
        raw = result.stdout
        if not isinstance(raw, bytes) or len(raw) > 8192:
            raise ValueError()
        value = raw.decode("utf-8").rstrip("\r\n")
        if not value or any(c.isspace() for c in value):
            raise ValueError()
        return value
    except Exception:
        # Do not include subprocess errors/output: they may contain the secret.
        raise RuntimeSetupError("Runtime key unavailable. Complete/unlock the separate Keychain item and handle its access prompt personally; no tunnel started and no automatic retry.") from None


def runtime_environment(key, source=None):
    source = os.environ if source is None else source
    environment = {name: source[name] for name in ("HOME", "PATH", "TMPDIR", "LANG") if name in source}
    environment["CONTROL_PLANE_API_KEY"] = key
    return environment


def start(root=None, key_loader=keychain_runtime_key, exec_runner=os.execve):
    os.umask(0o077)
    plan = launch_plan(project_root() if root is None else root)
    fd = runtime_lock(plan["root"])
    try:
        key = (key_loader(service=plan["settings"]["runtime_keychain_service"], account=plan["settings"]["tunnel_id"])
               if key_loader is keychain_runtime_key else key_loader())
        environment = runtime_environment(key)
        print("Starting the exact private tunnel from user-managed Keychain; key is not printed or saved. Runtime lifetime is controlled by the foreground owner or login service.")
        exec_runner(str(plan["binary"]), plan["argv"], environment)
    finally:
        # Successful exec never returns; failure releases the lock and drops this
        # process's in-memory key. No key persistence or keychain writes occur.
        os.close(fd)


def main(argv=None, root=None):
    parser = argparse.ArgumentParser(description="iCloud for Agents optional private tunnel launcher")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Nonsecret static preflight only; no keychain/network/start")
    mode.add_argument("--start-with-keychain", action="store_true", help="Use the separately approved user-managed Keychain item and start foreground runtime")
    args = parser.parse_args(argv)
    try:
        if args.start_with_keychain:
            start() if root is None else start(root=root)
        else:
            launch_plan(project_root() if root is None else root)
            print("Nonsecret preflight passed. No keychain/network/runtime access. Starting requires explicit --start-with-keychain after approval.")
    except RuntimeSetupError as exc:
        parser.exit(1, str(exc) + "\n")
    except Exception:
        parser.exit(1, "Runtime setup failed; sensitive exception details suppressed. No automatic retry.\n")
