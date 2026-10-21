"""Create nonsecret per-user stdio settings, with an optional OpenAI tunnel."""
import argparse
import json
import os
from pathlib import Path
import shlex
import yaml
from icloud_for_agents.local_config import read_settings


def stdio_configuration(root):
    """Generic command/args shape; client-specific registration is the agent's job."""
    root = Path(root).resolve()
    read_settings(root)
    return {"command": str(root / ".venv/bin/python"),
            "args": [str(root / "scripts/run-stdio.py")]}


def configure(root, account, tunnel_id=None, write_enabled=False, dav_reads=False, services=None):
    root = Path(root).resolve()
    local = root / "local"
    settings = local / "settings.json"
    profile = local / "tunnel/icloud-for-agents.yaml"
    if settings.exists() or profile.exists() or settings.is_symlink() or profile.is_symlink():
        raise ValueError("Existing private configuration is preserved; inspect it rather than overwriting")
    local.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = {"account": account, "apple_keychain_service": "icloud-for-agents",
            "access_mode": "write-enabled" if write_enabled else "read-only", "dav_reads": dav_reads}
    if services is not None:
        data["services"] = services
    if tunnel_id is not None:
        data.update(tunnel_id=tunnel_id, runtime_keychain_service="icloud-for-agents-tunnel-runtime")
    with settings.open("x") as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(data, stream, indent=2)
    try:
        read_settings(root)
        if tunnel_id is not None:
            folder = profile.parent
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            command = shlex.join([str(root / ".venv/bin/python"), str(root / "scripts/run-stdio.py")])
            content = {"config_version": 1,
                       "control_plane": {"base_url": "https://api.openai.com", "tunnel_id": tunnel_id, "api_key": "env:CONTROL_PLANE_API_KEY"},
                       "health": {"listen_addr": "127.0.0.1:0", "url_file": str(folder / "health.url")},
                       "admin_ui": {"open_browser": False}, "log": {"level": "info", "format": "json"},
                       "mcp": {"commands": [{"channel": "main", "command": command}]}}
            with profile.open("x") as stream:
                os.fchmod(stream.fileno(), 0o600)
                yaml.safe_dump(content, stream)
    except BaseException:
        settings.unlink()
        raise
    return stdio_configuration(root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", help="Exact nonsecret iCloud login; required for new setup")
    parser.add_argument("--tunnel-id", help="Optional existing owner-controlled OpenAI tunnel")
    parser.add_argument("--enable-writes", action="store_true", help="Only after explicit owner approval of ongoing writes")
    parser.add_argument("--enable-dav-reads", action="store_true", help="Only after owner approves calendar/contact reads")
    parser.add_argument("--services", help="Comma-separated mail,calendar,contacts; use none to disable all")
    parser.add_argument("--show-stdio-config", action="store_true", help="Show generic command/args for existing validated settings")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.show_stdio_config:
        if args.account or args.tunnel_id or args.enable_writes or args.enable_dav_reads or args.services is not None:
            parser.error("Showing existing configuration does not modify settings")
        config = stdio_configuration(root)
    else:
        if not args.account:
            parser.error("--account is required for new setup")
        selection = None
        if args.services is not None:
            if args.enable_dav_reads:
                parser.error("Use either explicit --services or the legacy DAV flag")
            names = [] if args.services == "none" else args.services.split(",")
            if len(set(names)) != len(names) or set(names) - {"mail", "calendar", "contacts"}:
                parser.error("Unknown or duplicate service selection")
            selection = {name: name in names for name in ("mail", "calendar", "contacts")}
        config = configure(root, args.account, args.tunnel_id, args.enable_writes, args.enable_dav_reads, selection)
    print(json.dumps({"stdio": config, "note": "Use your client's supported registration format; no credential is included."}, indent=2))
