"""Exercise current MCP negotiation and tool schemas without real account access."""

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile

from mcp import Client, StdioServerParameters
from test_branding import assert_server_branding
from test_metadata import assert_server_metadata


ROOT = Path(__file__).resolve().parents[1]


async def run():
    with tempfile.TemporaryDirectory(prefix="icloud-modern-mcp-") as directory:
        home = Path(directory)
        audit = home / "audit.json"
        env = {
            "HOME": str(home),
            "PATH": os.defpath,
            "PYTHONPATH": str(ROOT / "src"),
            "BRIDGE_TEST_AUDIT": str(audit),
            "FASTMCP_SHOW_SERVER_BANNER": "false",
            "FASTMCP_CHECK_FOR_UPDATES": "off",
            "ICLOUD_MAIL_ACCESS": "read-only",
            "ICLOUD_ENABLE_DAV_READ": "1",
            "ICLOUD_ENABLE_NOTES": "1",
            "ICLOUD_ENABLE_REMINDERS": "1",
        }
        params = StdioServerParameters(
            command=sys.executable,
            args=[str(ROOT / "tests/guarded_stdio_server.py")],
            env=env,
            cwd=home,
        )
        async with Client(params, mode="auto", read_timeout_seconds=20) as client:
            assert client.session.protocol_version == "2026-07-28"
            assert_server_branding(client.server_info.model_dump(by_alias=True, exclude_none=True))
            assert_server_metadata(client.server_info.model_dump(by_alias=True, exclude_none=True))
            result = await client.list_tools()
            tools = {tool.name: tool for tool in result.tools}
            assert {"email_inspect", "calendar_list", "contacts_get", "notes_query", "reminders_query"} <= tools.keys()
            assert all(tool.input_schema.get("type") == "object" and tool.annotations for tool in tools.values())
            for name in ("email_inspect", "calendar_list", "contacts_get", "notes_query", "reminders_query"):
                assert tools[name].annotations.read_only_hint is True
            for name in ("email_archive", "notes_execute_approved", "reminders_execute_approved", "reminders_reconcile_lifecycle"):
                assert tools[name].annotations.read_only_hint is False
            assert tools["reminders_reconcile_lifecycle"].annotations.idempotent_hint is False
            reminder_changes = tools["reminders_prepare_changes"].description
            assert "move_reminder" in reminder_changes and "include_descendants" in reminder_changes
            assert "moves are paused" not in reminder_changes
            refused = await client.call_tool("email_plan_status", {"plan_id": "invalid"})
            assert refused.is_error is True
            print(f"Current MCP negotiation, {len(tools)} tool schemas and error handling passed")
        assert json.loads(audit.read_text())["blocked_events"] == []


if __name__ == "__main__":
    asyncio.run(run())
