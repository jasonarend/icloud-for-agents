"""Exercise owner-reviewed client setup through real synthetic MCP stdio."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile

from mcp import Client, StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]
PERMISSIONS = {"mail": "read", "calendar": "none", "contacts": "none", "notes": "none", "reminders": "none"}


def content(result):
    assert not result.is_error, result
    return result.structured_content or json.loads(result.content[0].text)


async def run(native_only=False, all_off=False):
    with tempfile.TemporaryDirectory(prefix="http-pair-stdio-") as directory:
        home = Path(directory)
        env = {"HOME": str(home), "PATH": os.defpath, "PYTHONPATH": str(ROOT / "src"),
               "BRIDGE_TEST_AUDIT": str(home / "audit.json"),
               "ICLOUD_MAIL_ACCOUNT": "synthetic@example.invalid", "ICLOUD_MAIL_ACCESS": "disabled" if native_only or all_off else "read-only",
               "ICLOUD_ENABLE_MAIL": "0" if native_only or all_off else "1", "ICLOUD_ENABLE_NOTES": "1" if native_only else "0",
               "ICLOUD_APPROVAL_POLICY": "trusted-client",
               "FASTMCP_CHECK_FOR_UPDATES": "off", "FASTMCP_SHOW_SERVER_BANNER": "false"}
        params = StdioServerParameters(command=sys.executable,
                 args=[str(ROOT / "tests/http_pairing_stdio_server.py")], env=env, cwd=home)
        async with Client(params, mode="auto", read_timeout_seconds=20) as client:
            permissions = (dict(PERMISSIONS, mail="none", notes="read") if native_only else
                           dict(PERMISSIONS, mail="none") if all_off else PERMISSIONS)
            prepared = content(await client.call_tool("http_prepare_client", {
                "action": "create", "label": "Synthetic client", "permissions": permissions}))
            plan_id = prepared["plan_id"]
            assert prepared["local_receipt_required"] is True
            assert (await client.call_tool("http_execute_client", {"plan_id": plan_id})).is_error
            content(await client.call_tool("owner_request_review", {"plan_id": plan_id}))
            for _ in range(300):
                state = content(await client.call_tool("owner_review_status", {"plan_id": plan_id}))
                if state["status"] != "pending":
                    break
                await asyncio.sleep(0.01)
            assert state["status"] == "approved"
            started = content(await client.call_tool("http_execute_client", {"plan_id": plan_id}))
            assert started["status"] == "pending"
            for _ in range(300):
                state = content(await client.call_tool("http_client_status", {"plan_id": plan_id}))
                if state.get("status") == "completed" and not state.get("working_locally"):
                    break
                await asyncio.sleep(0.01)
            assert state["status"] == "completed" and state["delivery_status"] == "window_closed"
            assert state["connection_verified"] is False
            assert "ika_" not in json.dumps([prepared, started, state])
            listing = content(await client.call_tool("http_clients", {}))
            assert any(item["client_id"] == started["client_id"] for item in listing["clients"])
            assert "ika_" not in json.dumps(listing)
            assert (await client.call_tool("http_execute_client", {"plan_id": plan_id})).is_error
            if all_off:
                revocation = content(await client.call_tool("http_prepare_client", {
                    "action": "revoke", "client_id": started["client_id"]}))
                revoke_id = revocation["plan_id"]
                content(await client.call_tool("owner_request_review", {"plan_id": revoke_id}))
                for _ in range(300):
                    decision = content(await client.call_tool("owner_review_status", {"plan_id": revoke_id}))
                    if decision["status"] != "pending":
                        break
                    await asyncio.sleep(0.01)
                assert decision["status"] == "approved"
                content(await client.call_tool("http_execute_client", {"plan_id": revoke_id}))
                for _ in range(300):
                    revoked = content(await client.call_tool("http_client_status", {"plan_id": revoke_id}))
                    if revoked.get("status") == "completed" and not revoked.get("working_locally"):
                        break
                    await asyncio.sleep(0.01)
                assert revoked["status"] == "completed" and revoked["client"]["status"] == "revoked"
        audit = json.loads((home / "audit.json").read_text())
        assert audit == {"blocked_events": [], "shown_count": 1}
    print("Synthetic stdio client setup, owner review and local-only key delivery passed")


if __name__ == "__main__":
    asyncio.run(run())
    asyncio.run(run(native_only=True))
    asyncio.run(run(all_off=True))
