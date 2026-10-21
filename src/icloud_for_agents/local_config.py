"""Owner-controlled nonsecret settings; never load a password or API key here."""
import json
import os
import re
import stat
from pathlib import Path


def read_settings(root):
    path = Path(root) / "local/settings.json"
    if path.is_symlink():
        raise ValueError("Settings must be a private owned regular file")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("Settings must be a private owned regular file")
    data = json.loads(path.read_text())
    required = {"account", "apple_keychain_service", "access_mode", "dav_reads"}
    optional = {"folder_overrides", "cleanup_grant_id", "approval_policy", "tunnel_id", "runtime_keychain_service", "services", "policy_management_enabled", "runtime_directory"}
    if not isinstance(data, dict) or not required <= set(data) or set(data) - required - optional:
        raise ValueError("Unexpected settings fields")
    tunnel_fields = {"tunnel_id", "runtime_keychain_service"} & set(data)
    if tunnel_fields and tunnel_fields != {"tunnel_id", "runtime_keychain_service"}:
        raise ValueError("Tunnel settings must be supplied together")
    for key in ("account", "apple_keychain_service") + (("runtime_keychain_service",) if tunnel_fields else ()):
        if not isinstance(data[key], str) or not 1 <= len(data[key]) <= 254 or any(ord(c) < 33 for c in data[key]):
            raise ValueError("Invalid nonsecret settings")
    if tunnel_fields and (not isinstance(data["tunnel_id"], str) or not re.fullmatch(r"tunnel_[a-zA-Z0-9]+", data["tunnel_id"])):
        raise ValueError("Invalid tunnel identifier")
    if data["access_mode"] not in {"disabled", "read-only", "write-enabled"} or type(data["dav_reads"]) is not bool:
        raise ValueError("Invalid access settings")
    overrides = data.get("folder_overrides", {})
    if not isinstance(overrides, dict) or set(overrides) - {"Archive", "Trash", "Sent"}:
        raise ValueError("Invalid folder override roles")
    for value in overrides.values():
        if not isinstance(value, str) or not 1 <= len(value) <= 1000 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("Invalid exact folder name")
    if data.get("approval_policy", "local-receipt") not in {"local-receipt", "trusted-client"}:
        raise ValueError("Invalid approval policy")
    grant = data.get("cleanup_grant_id")
    if grant is not None and (not isinstance(grant,str) or not re.fullmatch(r"[0-9a-f]{32}",grant)):
        raise ValueError("Invalid owner-installed cleanup grant")
    if type(data.get("policy_management_enabled", False)) is not bool:
        raise ValueError("Managed policy opt-in must be boolean")
    runtime = data.get("runtime_directory")
    if runtime is not None and (not isinstance(runtime,str) or not Path(runtime).is_absolute() or any(ord(c)<32 for c in runtime)):
        raise ValueError("Runtime directory must be an explicit absolute private path")
    from .services import configured
    configured(data)
    return data


def child_environment(settings, source):
    result = {k: source[k] for k in ("HOME", "PATH", "TMPDIR", "LANG") if k in source}
    result.update(ICLOUD_MAIL_ACCOUNT=settings["account"],
                  ICLOUD_KEYCHAIN_SERVICE=settings["apple_keychain_service"],
                  ICLOUD_MAIL_ACCESS=settings["access_mode"],
                  ICLOUD_ENABLE_DAV_READ="1" if settings["dav_reads"] else "0")
    from .services import configured
    for name, active in configured(settings).items():
        result["ICLOUD_ENABLE_" + name.upper()] = "1" if active else "0"
    if settings.get("runtime_directory"):
        result["ICLOUD_RUNTIME_ROOT"] = settings["runtime_directory"]
    result["ICLOUD_ENABLE_POLICY_MANAGEMENT"] = "1" if settings.get("policy_management_enabled", False) else "0"
    result["ICLOUD_APPROVAL_POLICY"] = settings.get("approval_policy", "local-receipt")
    for role, name in settings.get("folder_overrides", {}).items():
        result["ICLOUD_" + role.upper() + "_FOLDER"] = name
    if settings.get("cleanup_grant_id"):
        result["ICLOUD_CLEANUP_GRANT_ID"] = settings["cleanup_grant_id"]
    return result
