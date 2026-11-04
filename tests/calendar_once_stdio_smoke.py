"""Exercise exact one-time recurring Calendar approval over real MCP stdio."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from mcp import Client, StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]
COLLECTION = "https://caldav.icloud.com/synthetic/"


def content(result):
    assert not result.is_error, result
    return result.structured_content or json.loads(result.content[0].text)


async def run(mode):
    with tempfile.TemporaryDirectory(prefix="calendar-once-stdio-") as directory:
        home = Path(directory)
        env = {"HOME": str(home), "PATH": os.defpath, "PYTHONPATH": str(ROOT / "src"),
               "BRIDGE_TEST_AUDIT": str(home / "audit.json"), "ICLOUD_MAIL_ACCOUNT": "synthetic@example.com",
               "ICLOUD_ENABLE_MAIL": "0", "ICLOUD_ENABLE_CALENDAR": "1", "ICLOUD_ENABLE_CONTACTS": "0",
               "ICLOUD_MAIL_ACCESS": "read-only", "ICLOUD_APPROVAL_POLICY": mode,
               "FASTMCP_SHOW_SERVER_BANNER": "false", "FASTMCP_CHECK_FOR_UPDATES": "off"}
        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "tests/calendar_once_stdio_server.py")], env=env, cwd=home)
        async with Client(params, mode="auto", read_timeout_seconds=20) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert tools["calendar_prepare_once"].annotations.read_only_hint is False
            fields = [
                {"summary": "Synthetic UTC recurrence", "start": "2026-11-09T16:00:00Z",
                 "end": "2026-11-09T17:00:00Z", "timezone": "UTC"},
                # Three weekly occurrences span Chicago's autumn clock change.
                {"summary": "Synthetic local recurrence", "start": "2026-10-26T09:00:00-05:00",
                 "end": "2026-10-26T10:00:00-05:00", "timezone": "America/Chicago"},
            ]
            for values in fields:
                change = {"operation": "create", "fields": dict(values, recurrence={"frequency": "weekly", "count": 3})}
                args = {"calendar_url": COLLECTION, "changes": [change]}
                assert (await client.call_tool("calendar_prepare_changes", args)).is_error
                proposal = content(await client.call_tool("calendar_prepare_once", args))
                assert proposal["review"]["payload"]["temporary_elevation"] == "calendar-exact-batch-once"
                item = proposal["review"]["payload"]["changes"][0]
                if values["timezone"] == "America/Chicago":
                    binding = item["timezone_binding"]
                    assert binding["name"] == "America/Chicago"
                    assert len(binding["sha256"]) == 64
                    assert binding["coverage"]["indefinite_future_verified"] is False
                    assert "BEGIN:VTIMEZONE\r\n" in item["content"]
                    assert "DTSTART;TZID=America/Chicago:20261026T090000\r\n" in item["content"]
                    assert "DTEND;TZID=America/Chicago:20261026T100000\r\n" in item["content"]
                plan_id = proposal["plan_id"]
                if mode == "local-receipt":
                    assert (await client.call_tool("dav_execute_approved", {"plan_id": plan_id})).is_error
                    # Exercise the real approval interface with a separately simulated owner TTY.
                    code = '''from io import StringIO
from unittest.mock import patch
from icloud_for_agents.approvals import PlanStore, approve_interactively
class OwnerTTY(StringIO):
 def readline(self): return "APPROVE CALENDAR " + %r + "\\n"
 def __exit__(self,*args): pass
with patch("builtins.open",return_value=OwnerTTY()): approve_interactively(PlanStore(),%r)
''' % (plan_id, plan_id)
                    reviewed = subprocess.run([sys.executable, "-c", code], cwd=home, env=env, capture_output=True, timeout=20)
                    assert reviewed.returncode == 0, reviewed.stderr.decode()
                result = content(await client.call_tool("dav_execute_approved", {"plan_id": plan_id}))
                assert result["status"] == "applied"
                assert result["completed"][0]["verification"] == "reviewed_recurring_event_readback"
                assert (await client.call_tool("dav_execute_approved", {"plan_id": plan_id})).is_error
        audit = json.loads((home / "audit.json").read_text())
        assert audit["blocked_events"] == []
        # Each named-zone proposal reads the provider definition once at review
        # and once immediately before dispatch; each create gets exact readback.
        assert audit["requests"] == {"timezone_reads": 2, "event_reads": 2, "writes": 2}, audit
        print(mode + ": UTC and named-zone recurring batches verified; replay refused")


async def main():
    await run("trusted-client")
    await run("local-receipt")


if __name__ == "__main__":
    asyncio.run(main())
