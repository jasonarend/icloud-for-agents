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


async def run(mode, client_deletion=False, graphical=False):
    with tempfile.TemporaryDirectory(prefix="calendar-once-stdio-") as directory:
        home = Path(directory)
        env = {"HOME": str(home), "PATH": os.defpath, "PYTHONPATH": str(ROOT / "src"),
               "BRIDGE_TEST_AUDIT": str(home / "audit.json"), "ICLOUD_MAIL_ACCOUNT": "synthetic@example.com",
               "ICLOUD_ENABLE_MAIL": "0", "ICLOUD_ENABLE_CALENDAR": "1", "ICLOUD_ENABLE_CONTACTS": "0",
               "ICLOUD_MAIL_ACCESS": "read-only", "ICLOUD_APPROVAL_POLICY": mode,
               "FASTMCP_SHOW_SERVER_BANNER": "false", "FASTMCP_CHECK_FOR_UPDATES": "off"}
        if graphical:
            env['BRIDGE_FAKE_LOCAL_REVIEW'] = '1'
        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "tests/calendar_once_stdio_server.py")], env=env, cwd=home)
        async with Client(params, mode="auto", read_timeout_seconds=20) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert tools["calendar_prepare_once"].annotations.read_only_hint is False
            async def approve(plan_id, irreversible=False):
                if graphical:
                    requested = content(await client.call_tool('owner_request_review', {'plan_id': plan_id}))
                    assert requested['status'] in {'pending', 'approved'}
                    for _ in range(200):
                        observed = content(await client.call_tool('owner_review_status', {'plan_id': plan_id}))
                        if observed['status'] != 'pending':
                            break
                        await asyncio.sleep(0.01)
                    assert observed['status'] == 'approved', observed
                    return
                # Exercise the real approval interface with a separately simulated owner TTY.
                phrase = ("IRREVERSIBLE DELETE " if irreversible else "") + "APPROVE CALENDAR " + plan_id
                code = '''from io import StringIO
from unittest.mock import patch
from icloud_for_agents.approvals import PlanStore, approve_interactively
class OwnerTTY(StringIO):
 def readline(self): return %r + "\\n"
 def __exit__(self,*args): pass
with patch("builtins.open",return_value=OwnerTTY()): approve_interactively(PlanStore(),%r)
''' % (phrase, plan_id)
                reviewed = subprocess.run([sys.executable, "-c", code], cwd=home, env=env, capture_output=True, timeout=20)
                assert reviewed.returncode == 0, reviewed.stderr.decode()
            created_urls = []
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
                    await approve(plan_id)
                result = content(await client.call_tool("dav_execute_approved", {"plan_id": plan_id}))
                assert result["status"] == "applied"
                assert result["completed"][0]["verification"] == "reviewed_recurring_event_readback"
                created_urls.append(item["url"])
                assert (await client.call_tool("dav_execute_approved", {"plan_id": plan_id})).is_error
            if mode == "local-receipt":
                forbidden = {"calendar_url": COLLECTION, "deletion_authorization": "trusted-client",
                             "changes": [{"operation": "delete", "url": created_urls[0],
                                          "recurrence_scope": "series", "irreversible_confirmed": True}]}
                assert (await client.call_tool("calendar_prepare_once", forbidden)).is_error
            occurrence_urls = []
            for number in range(2):
                change = {"operation": "create", "fields": {
                    "summary": "Synthetic occurrence series " + str(number),
                    "start": "2026-11-09T16:00:00Z", "end": "2026-11-09T17:00:00Z", "timezone": "UTC",
                    "recurrence": {"frequency": "weekly", "count": 3}}}
                proposal = content(await client.call_tool("calendar_prepare_once", {"calendar_url": COLLECTION, "changes": [change]}))
                occurrence_urls.append(proposal["review"]["payload"]["changes"][0]["url"])
                if mode == "local-receipt":
                    await approve(proposal["plan_id"])
                assert content(await client.call_tool("dav_execute_approved", {"plan_id": proposal["plan_id"]}))["status"] == "applied"
            for operation, resource in zip(("update", "delete_occurrence"), occurrence_urls):
                change = {"operation": operation, "url": resource, "recurrence_scope": "occurrence",
                          "occurrence_start": "2026-11-16T16:00:00Z"}
                if operation == "update":
                    change["fields"] = {"summary": "Updated synthetic occurrence"}
                else:
                    change["irreversible_confirmed"] = True
                args = {"calendar_url": COLLECTION, "changes": [change]}
                if operation == "delete_occurrence" and client_deletion:
                    args["deletion_authorization"] = "trusted-client"
                proposal = content(await client.call_tool("calendar_prepare_once", args))
                item = proposal["review"]["payload"]["changes"][0]
                assert item["occurrence_proof"]["recurrence_id"] == "20261116T160000Z"
                assert item["occurrence_proof"]["recurrence_completeness_verified"] is False
                needs_receipt = mode == "local-receipt" or (operation == "delete_occurrence" and not client_deletion)
                if operation == "delete_occurrence":
                    assert proposal["local_receipt_required"] == needs_receipt
                if needs_receipt:
                    assert (await client.call_tool("dav_execute_approved", {"plan_id": proposal["plan_id"]})).is_error
                    await approve(proposal["plan_id"], irreversible=operation == "delete_occurrence")
                result = content(await client.call_tool("dav_execute_approved", {"plan_id": proposal["plan_id"]}))
                assert result["status"] == "applied" and result["completed"][0]["verification"] == "reviewed_occurrence_change_readback"
                assert (await client.call_tool("dav_execute_approved", {"plan_id": proposal["plan_id"]})).is_error
            schedules = [
                {"start": "2026-11-09T16:00:00Z", "end": "2026-11-09T17:00:00Z", "timezone": "UTC"},
                {"start": "2026-10-26T09:00:00-05:00", "end": "2026-10-26T10:00:00-05:00", "timezone": "America/Chicago"},
            ]
            for schedule in schedules:
                schedule["recurrence"] = {"frequency": "weekly", "count": 3}
                change = {"operation": "update", "url": created_urls[1], "recurrence_scope": "series", "fields": schedule}
                proposal = content(await client.call_tool("calendar_prepare_once", {"calendar_url": COLLECTION, "changes": [change]}))
                item = proposal["review"]["payload"]["changes"][0]
                assert any("past occurrences" in effect for effect in item["recurrence_effects"])
                assert item["content"].count("BEGIN:VTIMEZONE") == (schedule["timezone"] != "UTC")
                if mode == "local-receipt":
                    await approve(proposal["plan_id"])
                result = content(await client.call_tool("dav_execute_approved", {"plan_id": proposal["plan_id"]}))
                assert result["status"] == "applied" and result["completed"][0]["verification"] == "reviewed_recurring_event_readback"
            update = {"operation": "update", "url": created_urls[0], "recurrence_scope": "series",
                      "fields": {"summary": "Updated synthetic series"}}
            proposal = content(await client.call_tool("calendar_prepare_once", {"calendar_url": COLLECTION, "changes": [update]}))
            item = proposal["review"]["payload"]["changes"][0]
            assert "SUMMARY:Updated synthetic series" in item["content"] and "RRULE:" in item["content"]
            if mode == "local-receipt":
                await approve(proposal["plan_id"])
            result = content(await client.call_tool("dav_execute_approved", {"plan_id": proposal["plan_id"]}))
            assert result["status"] == "applied" and result["completed"][0]["verification"] == "reviewed_recurring_event_readback"

            deletion = {"operation": "delete", "url": created_urls[0], "recurrence_scope": "series", "irreversible_confirmed": True}
            args = {"calendar_url": COLLECTION, "changes": [deletion]}
            if client_deletion:
                args["deletion_authorization"] = "trusted-client"
            proposal = content(await client.call_tool("calendar_prepare_once", args))
            assert proposal["local_receipt_required"] is not client_deletion
            if not client_deletion:
                assert (await client.call_tool("dav_execute_approved", {"plan_id": proposal["plan_id"]})).is_error
                await approve(proposal["plan_id"], irreversible=True)
            result = content(await client.call_tool("dav_execute_approved", {"plan_id": proposal["plan_id"]}))
            assert result["status"] == "applied" and result["completed"][0]["verification"] == "series_resource_absent"
        audit = json.loads((home / "audit.json").read_text())
        assert audit["blocked_events"] == []
        # Each named-zone proposal reads the provider definition once at review
        # and once immediately before dispatch; each create gets exact readback.
        assert audit["requests"] == {"timezone_reads": 4, "event_reads": 26, "occurrence_reads": 4, "writes": 10}, audit
        print(mode + (" explicit client deletion" if client_deletion else "") + (" graphical owner review" if graphical else "") + ": series and occurrence changes verified with scoped approval; replay refused")


async def main():
    await run("trusted-client")
    await run("local-receipt")
    await run("trusted-client", client_deletion=True)
    await run("local-receipt", graphical=True)


if __name__ == "__main__":
    asyncio.run(main())
