"""Reviewed, optional per-user HTTP service; credentials stay in Keychain.

Profiles, login registration and process startup are separate owner actions.
Only launchd's exact reviewed job is stopped; no stored PID is ever signalled.
"""
import argparse
import fcntl
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import socket
import stat
import subprocess
import time

from .core import digest
from .http_transport import PROXY_HOST_PATTERN
from .local_config import child_environment, read_settings

TIMEOUT = 5
PROFILE = "http-service.json"


class HttpServiceError(Exception):
    """Fixed nonsecret operator errors only."""


def _private_read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise HttpServiceError("Service files must be owned private regular files")
        value = stream.read(65537)
    if len(value) > 65536:
        raise HttpServiceError("Service file is oversized")
    return value


def _directory(path):
    if path.is_symlink():
        raise HttpServiceError("Service directory must not be a symlink")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise HttpServiceError("Service directory must be owner-controlled")


def _write_new(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def _serialized(function):
    @wraps(function)
    def wrapped(root, *args, **kwargs):
        root = Path(root).resolve()
        _directory(root / "local")
        fd = os.open(root / "local/http-service.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                raise HttpServiceError("Invalid HTTP service management lock")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise HttpServiceError("Another HTTP service action is in progress") from None
            return function(root, *args, **kwargs)
        finally:
            os.close(fd)
    return wrapped


def label(root):
    identity = hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:16]
    return "local.icloud-for-agents.http." + identity


def _paths(root, home):
    root = Path(root).resolve()
    home = Path.home() if home is None else Path(home).resolve()
    if (root / "local").is_symlink():
        raise HttpServiceError("Local configuration directory must not be a symlink")
    return root, home, root / "local" / PROFILE, home / "Library/LaunchAgents" / (label(root) + ".plist")


def _environment(root, home):
    root, home = Path(root).resolve(), Path(home).resolve()
    settings = read_settings(root)
    environment = child_environment(settings, {"HOME": str(home), "PATH": os.defpath})
    environment["ICLOUD_CONFIG_ROOT"] = str(root)
    return environment


def _configuration(environment):
    settings = {key: value for key, value in environment.items()
                if key.startswith("ICLOUD_") and not key.startswith("ICLOUD_HTTP_")
                and key != "ICLOUD_SECRET_FD"}
    return digest(settings)


def _executables(root):
    python = root / ".venv/bin/python"
    for path in (python.resolve(strict=True), root / "scripts/manage-http.py", root / "scripts/run-http.py"):
        if path.is_symlink():
            raise HttpServiceError("HTTP service script must not be a symlink")
        info = path.stat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid not in {0, os.getuid()}
                or info.st_mode & 0o022):
            raise HttpServiceError("HTTP service executable is not owner-controlled")
    if not os.access(python, os.X_OK):
        raise HttpServiceError("HTTP Python executable is unavailable")


def prepare(root, *, port=8765, proxy_host=None, start_at_login=False, home=None):
    """Preview a complete nonsecret profile without writing or launching anything."""
    root, home, _, _ = _paths(root, home)
    if (type(port) is not int or not 1 <= port <= 65535 or type(start_at_login) is not bool
            or (proxy_host is not None and (not isinstance(proxy_host, str)
                or not PROXY_HOST_PATTERN.fullmatch(proxy_host)))):
        raise HttpServiceError("Choose an exact port, optional DNS proxy hostname and login preference")
    _executables(root)
    environment = _environment(root, home)
    runtime = environment.get("ICLOUD_RUNTIME_ROOT", str(home / "Library/Application Support/icloud-for-agents/runtime"))
    profile = {"version": 1, "root": str(root), "home": str(home), "port": port,
               "proxy_host": proxy_host, "start_at_login": start_at_login,
               "configuration": _configuration(environment), "runtime": runtime,
               "instance": digest({"runtime": str(Path(runtime).resolve()), "installation": str(root)})}
    return {"profile": profile, "review_sha256": digest(profile), "credentials_read": False,
            "network_exposure": "loopback_only", "remote_proxy_configured": False}


@_serialized
def configure(root, review_sha256, **options):
    """Save only the exact preview after owner authorization; never replace a profile."""
    preview = prepare(root, **options)
    if preview["review_sha256"] != review_sha256:
        raise HttpServiceError("HTTP profile changed since review; prepare it again")
    root, _, profile_path, _ = _paths(root, options.get("home"))
    _directory(root / "local")
    _write_new(profile_path, (json.dumps(preview["profile"], sort_keys=True) + "\n").encode())
    return {"configured": True, "started": False, "login_service_installed": False}


def _profile(root, home=None, *, current=True):
    root, home, path, _ = _paths(root, home)
    profile = json.loads(_private_read(path))
    expected = {"version", "root", "home", "port", "proxy_host", "start_at_login", "configuration", "runtime", "instance"}
    if (not isinstance(profile, dict) or set(profile) != expected or type(profile.get("version")) is not int or profile["version"] != 1
            or profile.get("root") != str(root) or profile.get("home") != str(home)
            or type(profile.get("port")) is not int or not 1 <= profile["port"] <= 65535
            or type(profile.get("start_at_login")) is not bool
            or (profile.get("proxy_host") is not None and (not isinstance(profile["proxy_host"], str)
                or not PROXY_HOST_PATTERN.fullmatch(profile["proxy_host"])))
            or not isinstance(profile.get("runtime"), str) or not Path(profile["runtime"]).is_absolute()
            or any(ord(c) < 32 or ord(c) == 127 for c in profile["runtime"])
            or any(not isinstance(profile.get(name), str) or not re.fullmatch(r"[0-9a-f]{64}", profile[name])
                   for name in ("configuration", "instance"))):
        raise HttpServiceError("HTTP service profile is invalid")
    if current:
        reviewed = prepare(root, port=profile["port"], proxy_host=profile["proxy_host"],
                           start_at_login=profile["start_at_login"], home=home)["profile"]
        if profile != reviewed:
            raise HttpServiceError("HTTP installation settings changed; review a replacement profile before starting")
    return profile


def definition(profile):
    root = Path(profile["root"])
    return {"Label": label(root), "ProgramArguments": [str(root / ".venv/bin/python"),
            str(root / "scripts/manage-http.py"), "--serve"], "WorkingDirectory": str(root),
            "EnvironmentVariables": {"HOME": profile["home"], "PATH": os.defpath},
            "RunAtLoad": profile["start_at_login"], "KeepAlive": False,
            "LimitLoadToSessionType": "Aqua", "ProcessType": "Background", "Umask": 63,
            "StandardOutPath": "/dev/null", "StandardErrorPath": "/dev/null"}


@_serialized
def install(root, home=None):
    """Register the reviewed login definition on disk; do not bootstrap/start it."""
    profile = _profile(root, home)
    _, _, _, path = _paths(root, home)
    _directory(path.parent.parent)
    _directory(path.parent)
    _write_new(path, plistlib.dumps(definition(profile)))
    return {"installed": True, "started": False, "start_at_login": profile["start_at_login"]}


def _reviewed(root, home=None):
    profile = _profile(root, home, current=False)
    _, _, _, path = _paths(root, home)
    if plistlib.loads(_private_read(path)) != definition(profile):
        raise HttpServiceError("Installed HTTP service differs from its reviewed definition")
    return profile, path


def status(root, home=None, runner=subprocess.run, *, timeout=TIMEOUT):
    profile, _ = _reviewed(root, home)
    try:
        result = runner(["/bin/launchctl", "print", f"gui/{os.getuid()}/{label(root)}"],
                        capture_output=True, check=False, timeout=max(0.001, timeout / 2))
        if result.returncode == 0:
            output = result.stdout.decode("utf-8", "replace") if isinstance(result.stdout, bytes) else result.stdout
            expected = definition(profile)["ProgramArguments"]
            arguments = re.search(r"(?m)^\s*arguments = \{\s*\n(.*?)^\s*\}", output, re.S)
            program = re.search(r"(?m)^\s*program = (.+)$", output)
            if (not arguments or not program or program.group(1).strip() not in {expected[0], str(Path(expected[0]).resolve())}
                    or [line.strip() for line in arguments.group(1).splitlines() if line.strip()] != expected):
                return {"status": "unavailable", "reason": "loaded_job_identity_mismatch", "connection_verified": False}
            pid = re.search(r"(?m)^\s*pid = ([1-9][0-9]*)\s*$", output)
            return {"status": "loaded", "running": pid is not None, "connection_verified": False}
        if result.returncode == 113:
            domain = runner(["/bin/launchctl", "print", f"gui/{os.getuid()}"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=max(0.001, timeout / 2))
            if domain.returncode == 0:
                return {"status": "not_loaded", "connection_verified": False}
    except (OSError, subprocess.SubprocessError):
        pass
    return {"status": "unavailable", "connection_verified": False}


def _port_free(port):
    try:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    except OSError:
        raise HttpServiceError("HTTP port is occupied; inspect the existing process without stopping it") from None


@_serialized
def start(root, home=None, runner=subprocess.run, *, clock=time.monotonic, sleeper=time.sleep):
    profile, path = _reviewed(root, home)
    _profile(root, home)  # settings/fingerprint revalidation before external action
    before = status(root, home, runner)
    if before["status"] == "loaded" and before.get("running") is True:
        return {"started": False, "reason": "already_loaded", "connection_verified": False}
    if before["status"] not in {"loaded", "not_loaded"}:
        raise HttpServiceError("HTTP service status is unavailable; no startup attempted")
    _port_free(profile["port"])
    # The service may have changed while checking its port. Revalidate the exact
    # reviewed definition and loaded identity before requesting any activation.
    _reviewed(root, home)
    _profile(root, home)
    current = status(root, home, runner)
    if current["status"] == "loaded" and current.get("running") is True:
        return {"started": False, "reason": "already_loaded", "connection_verified": False}
    if current["status"] != before["status"]:
        raise HttpServiceError("HTTP service status changed; no startup attempted")
    recovering = current["status"] == "loaded"
    if not recovering:
        result = runner(["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)],
                        capture_output=True, check=False, timeout=TIMEOUT)
        if result.returncode != 0:
            raise HttpServiceError("HTTP service startup was not confirmed; inspect status before another action")
    if recovering or not profile["start_at_login"]:
        result = runner(["/bin/launchctl", "kickstart", f"gui/{os.getuid()}/{label(root)}"],
                        capture_output=True, check=False, timeout=TIMEOUT)
        if result.returncode != 0:
            raise HttpServiceError("HTTP service activation was not confirmed; inspect status before another action")
    deadline = clock() + 5
    after = {"status": "unavailable"}
    while clock() < deadline:
        after = status(root, home, runner, timeout=min(TIMEOUT, deadline - clock()))
        if after["status"] != "loaded" or after.get("running"):
            break
        sleeper(min(0.1, max(0, deadline - clock())))
    return {"started": after["status"] == "loaded" and after.get("running") is True, "status": after["status"],
            "connection_verified": False, "next_step": "Verify authenticated MCP discovery through the client"}


@_serialized
def stop(root, home=None, runner=subprocess.run, *, clock=time.monotonic, sleeper=time.sleep):
    _reviewed(root, home)
    before = status(root, home, runner)
    if before["status"] == "not_loaded":
        return {"stopped": True, "profile_and_login_registration_retained": True, "stop_request_warning": False}
    if before["status"] != "loaded":
        raise HttpServiceError("HTTP job identity is unavailable; no stop requested")
    try:
        result = runner(["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{label(root)}"],
                        capture_output=True, check=False, timeout=TIMEOUT)
        warning = result.returncode not in {0, 113}
    except (OSError, subprocess.SubprocessError):
        warning = True
    deadline = clock() + 10
    while clock() < deadline:
        observed = status(root, home, runner, timeout=min(TIMEOUT, deadline - clock()))
        if observed["status"] == "not_loaded":
            _reviewed(root, home)
            return {"stopped": True, "profile_and_login_registration_retained": True,
                    "stop_request_warning": warning}
        sleeper(min(0.1, max(0, deadline - clock())))
    raise HttpServiceError("HTTP service stop remains unconfirmed; configuration retained and no retry performed")


@_serialized
def remove(root, home=None, runner=subprocess.run):
    """Remove this stopped opt-in definition/profile; retain all runtime and keys."""
    _profile(root, home, current=False)
    _, _, profile_path, service_path = _paths(root, home)
    if service_path.exists() or service_path.is_symlink():
        if status(root, home, runner)["status"] != "not_loaded":
            raise HttpServiceError("Stop and verify the reviewed HTTP service before removing its configuration")
        _reviewed(root, home)
        service_path.unlink()
    _private_read(profile_path)
    profile_path.unlink()
    return {"removed": True, "runtime_records_and_credentials_retained": True}


def serve(root, home=None):
    profile = _profile(root, home)
    environment = _environment(Path(profile["root"]), Path(profile["home"]))
    argv = [str(Path(profile["root"]) / ".venv/bin/python"), "-m", "icloud_for_agents.http_transport", "--port", str(profile["port"])]
    if profile["proxy_host"]:
        argv.extend(["--proxy-host", profile["proxy_host"]])
    os.execve(argv[0], argv, environment)


def main(root=None):
    parser = argparse.ArgumentParser(description="Manage the optional reviewed local HTTP service")
    actions = parser.add_mutually_exclusive_group(required=True)
    for name in ("prepare", "configure", "install", "start", "status", "stop", "remove", "serve"):
        actions.add_argument("--" + name, action="store_true")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--proxy-host")
    parser.add_argument("--start-at-login", action="store_true")
    parser.add_argument("--review-sha256")
    args = parser.parse_args()
    if not (args.prepare or args.configure) and (args.port != 8765 or args.proxy_host or args.start_at_login):
        parser.error("Connection options belong to the reviewed prepare/configure actions")
    if args.review_sha256 and not args.configure:
        parser.error("The review digest belongs to configure")
    root = Path(__file__).resolve().parents[2] if root is None else root
    try:
        options = dict(port=args.port, proxy_host=args.proxy_host, start_at_login=args.start_at_login)
        if args.prepare:
            result = prepare(root, **options)
        elif args.configure:
            result = configure(root, args.review_sha256, **options)
        else:
            result = {"install": install, "start": start, "status": status, "stop": stop, "remove": remove, "serve": serve}[
                next(name for name in ("install", "start", "status", "stop", "remove", "serve") if getattr(args, name))](root)
        print(json.dumps(result))
    except Exception:
        parser.exit(1, "HTTP service action refused or unconfirmed; inspect the reviewed profile and status. No automatic retry.\n")
