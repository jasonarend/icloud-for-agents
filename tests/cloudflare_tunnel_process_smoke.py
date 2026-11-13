"""Exercise the tunnel launch in an isolated process with invented credentials."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="cloudflare-launch-") as directory:
        root = Path(directory)
        (root / "local").mkdir(mode=0o700)
        settings = root / "local/settings.json"
        settings.write_text(json.dumps({"account": "synthetic@example.invalid", "apple_keychain_service": "synthetic",
                                        "access_mode": "read-only", "dav_reads": False}))
        settings.chmod(0o600)
        shared = root / "shared"
        shared.mkdir(mode=0o777)
        shared.chmod(0o777)
        binary = shared / "cloudflared"
        binary.write_text(f"#!{sys.executable}\n" + r'''
import base64, json, os, sys
assert sys.argv[1:] == ["tunnel", "--config", os.devnull, "--no-autoupdate", "run"]
assert json.loads(base64.b64decode(os.environ["TUNNEL_TOKEN"]))["t"] == "12345678-1234-4234-8234-123456789abc"
assert "CONTROL_PLANE_API_KEY" not in os.environ
print(os.environ["TUNNEL_TOKEN"])
print(os.environ["TUNNEL_TOKEN"], file=sys.stderr)
''')
        binary.chmod(0o700)
        checksum = hashlib.sha256(binary.read_bytes()).hexdigest()
        code = r'''
import base64, json, sys
from pathlib import Path
from icloud_for_agents.cloudflare_tunnel import start
from icloud_for_agents import secure_executable
identity = "12345678-1234-4234-8234-123456789abc"
token = base64.b64encode(json.dumps({"a": "a" * 32, "t": identity,
    "s": base64.b64encode(b"x" * 32).decode()}).encode()).decode()
cache = Path(sys.argv[1]) / "cache"
cache.mkdir(mode=0o700)
secure_executable._cache_directory = lambda: cache
def key_loader(**kwargs):
    # Simulate pathname replacement while the owner handles Keychain.
    Path(sys.argv[2]).write_text("#!/bin/sh\nexit 42\n")
    return token
start(sys.argv[1], sys.argv[2], identity, sys.argv[3], key_loader=key_loader)
'''
        result = subprocess.run([sys.executable, "-c", code, str(root), str(binary), checksum], cwd=directory,
                                env={"HOME": directory, "PATH": os.defpath, "CONTROL_PLANE_API_KEY": "not-for-child",
                                     "PYTHONPATH": str(ROOT / "src")}, capture_output=True, timeout=30)
        if result.returncode or result.stdout or result.stderr:
            raise RuntimeError("Synthetic Cloudflare launch failed or exposed child output")
        print("Synthetic Cloudflare launch used reviewed bytes after source replacement; output redaction passed")


if __name__ == "__main__":
    main()
