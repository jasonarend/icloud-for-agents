"""Exercise real concurrent stdio and HTTP MCP servers with synthetic tools."""
import asyncio
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

from fastmcp import Client
from fastmcp.client.transports import PythonStdioTransport, StreamableHttpTransport

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/dual_transport_fixture.py"
TOKEN = "ika_" + "a" * 32 + "." + "b" * 43


async def exercise(home, port, env):
    stdio = PythonStdioTransport(FIXTURE, args=["stdio"], env=env, cwd=str(home), keep_alive=False)
    http = StreamableHttpTransport(f"http://127.0.0.1:{port}/mcp",
                                   headers={"Authorization": "Bearer " + TOKEN})
    async with Client(stdio) as local, Client(http) as remote:
        pending = asyncio.create_task(local.call_tool_mcp("synthetic_pause", {}))
        deadline = time.monotonic() + 15
        while not (home / "entered").exists():
            if pending.done() or time.monotonic() > deadline:
                raise AssertionError("Synthetic stdio operation did not start")
            await asyncio.sleep(0.02)
        try:
            refusal = await remote.call_tool_mcp("synthetic_change", {})
            assert refusal.is_error
            assert "Another server process" in str(refusal.content)
            assert not (home / "changed").exists()
        finally:
            (home / "release").write_text("release")
        assert not (await pending).is_error
        assert not (await remote.call_tool_mcp("synthetic_change", {})).is_error
        assert (home / "changed").exists()


with tempfile.TemporaryDirectory(prefix="icloud-dual-mcp-") as directory:
    home = Path(directory)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = {"HOME": str(home), "PATH": os.defpath, "PYTHONPATH": str(ROOT / "src"),
           "SYNTHETIC_EXCHANGE": str(home), "ICLOUD_RUNTIME_ROOT": str(home / "runtime"),
           "ICLOUD_MAIL_ACCESS": "write-enabled", "ICLOUD_ENABLE_MAIL": "1",
           "ICLOUD_ENABLE_CALENDAR": "0", "ICLOUD_ENABLE_CONTACTS": "0",
           "ICLOUD_ENABLE_NOTES": "0", "ICLOUD_ENABLE_REMINDERS": "0",
           "FASTMCP_CHECK_FOR_UPDATES": "off"}
    process = subprocess.Popen([sys.executable, str(FIXTURE), "http", str(port)], cwd=home,
                               env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        until = time.monotonic() + 15
        while True:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                if process.poll() is not None or time.monotonic() > until:
                    raise AssertionError("Synthetic HTTP server did not start")
                time.sleep(0.05)
        asyncio.run(exercise(home, port, env))
    finally:
        process.terminate()
        stdout, stderr = process.communicate(timeout=15)
    assert TOKEN.encode() not in stdout + stderr
    print("Real stdio/HTTP process contention and retry passed with synthetic tools")
