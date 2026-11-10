"""Synthetic MCP servers with one shared runtime, without provider access."""
import hmac
import os
from pathlib import Path
import sys
import time

mode = sys.argv[1]
if mode == "http":
    os.environ["ICLOUD_HTTP_TRANSPORT"] = "1"

from icloud_for_agents.server import mcp, safe  # noqa: E402

root = Path(os.environ["SYNTHETIC_EXCHANGE"])
TOKEN = b"ika_" + b"a" * 32 + b"." + b"b" * 43


@mcp.tool()
@safe
def synthetic_pause() -> dict:
    (root / "entered").write_text("entered")
    until = time.monotonic() + 20
    while not (root / "release").exists():
        if time.monotonic() > until:
            raise RuntimeError("Synthetic test timed out")
        time.sleep(0.02)
    return {"status": "finished"}


@mcp.tool()
@safe
def synthetic_change() -> dict:
    (root / "changed").write_text("changed")
    return {"status": "finished"}


if mode == "stdio":
    mcp.run(transport="stdio", show_banner=False)
elif mode == "http":
    import uvicorn
    from icloud_for_agents.http_transport import create_app

    class SyntheticKeys:
        def authenticate(self, candidate):
            return {"client_id": "synthetic"} if hmac.compare_digest(candidate, TOKEN) else None

    uvicorn.run(create_app(mcp, SyntheticKeys()), host="127.0.0.1", port=int(sys.argv[2]),
                access_log=False, proxy_headers=False, server_header=False, log_level="warning")
else:
    raise ValueError("Invalid synthetic transport")
