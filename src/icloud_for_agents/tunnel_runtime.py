"""Restart-safe tunnel launcher. Never provisions, saves, prints, or caches a key."""
import argparse
import fcntl
import os
from pathlib import Path
import stat
import re
import shlex
import subprocess

import yaml
from .local_config import read_settings
from .secure_executable import ExecutableSafetyError, executable_digest, stage_executable, stage_profile, trusted_path

KEYCHAIN_SERVICE = "icloud-for-agents-tunnel-runtime"


class RuntimeSetupError(Exception):
    """Fixed nonsecret operator errors only."""


def project_root():
    return Path(__file__).resolve().parents[2]


def launch_plan(root):
    try:
        root = trusted_path(Path(root), directory=True)
    except (OSError, ValueError) as exc:
        raise RuntimeSetupError("OpenAI tunnel installation is not protected from other users. "
                                + str(exc) + "; ask your setup assistant to migrate to a private installation; no key was requested") from None
    profile = root / "local/tunnel/icloud-for-agents.yaml"
    settings = read_settings(root)
    # Owner-selected executable, never an inherited PATH/environment override.
    # Preserve the original installation location for existing configurations.
    binary = Path(settings.get("tunnel_client_path", root / "tools/tunnel-client-v0.0.16/tunnel-client"))
    if not settings.get("tunnel_id") or not settings.get("runtime_keychain_service"):
        raise RuntimeSetupError("This is a direct stdio configuration; an OpenAI tunnel is optional and is not configured")
    expected = settings.get("tunnel_client_sha256")
    if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        raise RuntimeSetupError("Have your setup assistant verify the installed tunnel client and save its reviewed tunnel_client_sha256; no key was requested")
    command = shlex.join([str(root / ".venv/bin/python"), str(root / "scripts/run-stdio.py")])
    try:
        trusted_path(root / "local/settings.json")
        trusted_path(profile)
        trusted_path(root / ".venv/bin/python", executable=True)
        trusted_path(root / "scripts/run-stdio.py")
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
        checksum = executable_digest(binary)
        if checksum != expected:
            raise ValueError()
        if not (root / ".venv/bin/python").exists() or not (root / "scripts/run-stdio.py").is_file():
            raise ValueError()
    except ExecutableSafetyError as exc:
        raise RuntimeSetupError("OpenAI tunnel paths need a protected installation. " + str(exc)
                                + "; ask your setup assistant to migrate the installation or Python runtime; no key was requested") from None
    except Exception:
        raise RuntimeSetupError("Nonsecret preflight failed: verify the exact private tunnel profile and installed paths; no key was requested.") from None
    return {"root": root, "binary": binary, "sha256": checksum, "profile": profile, "profile_data": data, "settings": settings,
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


def mcp_child_command(root):
    """Remove the tunnel credential before Python startup or package imports."""
    return shlex.join(["/usr/bin/env", "-u", "CONTROL_PLANE_API_KEY",
                       str(root / ".venv/bin/python"), str(root / "scripts/run-stdio.py")])


def start(root=None, key_loader=keychain_runtime_key, exec_runner=os.execve):
    os.umask(0o077)
    plan = launch_plan(project_root() if root is None else root)
    fd = runtime_lock(plan["root"])
    try:
        try:
            trusted_path(Path("/usr/bin/env"), executable=True)
            binary = stage_executable(plan["binary"], plan["sha256"])
            # Keep existing owner profiles compatible, but give the tunnel's
            # MCP subprocess a system-owned credential-stripping launcher.
            profile_data = dict(plan["profile_data"], mcp={"commands": [
                {"channel": "main", "command": mcp_child_command(plan["root"])}]})
            profile = stage_profile(yaml.safe_dump(profile_data))
        except (OSError, ValueError):
            raise RuntimeSetupError("The reviewed tunnel launch files could not be staged privately; no key was requested") from None
        key = (key_loader(service=plan["settings"]["runtime_keychain_service"], account=plan["settings"]["tunnel_id"])
               if key_loader is keychain_runtime_key else key_loader())
        environment = runtime_environment(key)
        print("Starting the exact private tunnel from user-managed Keychain; key is not printed or saved. Runtime lifetime is controlled by the foreground owner or login service.")
        exec_runner(str(binary), [str(binary), "run", "--config", str(profile)], environment)
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
