"""Real MCP stdio discovery in an isolated HOME, guarded against network/secrets."""
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
(ROOT / "build/reports").mkdir(parents=True, exist_ok=True)


def run():
    with tempfile.TemporaryDirectory(prefix="icloud-bridge-offline-") as directory:
        home = Path(directory)
        audit_path = home / "audit.json"
        env = {"HOME": str(home), "PATH": os.defpath, "PYTHONPATH": str(ROOT / "src"),
               "PYTHONUNBUFFERED": "1", "BRIDGE_TEST_AUDIT": str(audit_path),
               "FASTMCP_SHOW_SERVER_BANNER": "false", "FASTMCP_CHECK_FOR_UPDATES": "off",
               "ICLOUD_MAIL_ACCESS": "read-only", "ICLOUD_ENABLE_DAV_READ": "1"}
        with (home / "stderr.log").open("wb") as stderr:
            process = subprocess.Popen([sys.executable, str(ROOT / "tests/guarded_stdio_server.py")],
                                       cwd=home, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=stderr)
            messages = []

            def send(value):
                process.stdin.write((json.dumps(value) + "\n").encode())
                process.stdin.flush()

            def receive(request_id):
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if select.select([process.stdout], [], [], 1)[0]:
                        line = process.stdout.readline()
                        if not line:
                            raise AssertionError("Server exited: " + (home / "stderr.log").read_text())
                        message = json.loads(line)
                        messages.append(message)
                        if message.get("id") == request_id:
                            return message
                    elif process.poll() is not None:
                        raise AssertionError("Server exited: " + (home / "stderr.log").read_text())
                raise AssertionError("MCP response timeout")

            try:
                send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "protocolVersion": "2025-11-25", "capabilities": {},
                    "clientInfo": {"name": "offline-safety-smoke", "version": "1"}}})
                initialized = receive(1)
                assert "result" in initialized, initialized
                send({"jsonrpc": "2.0", "method": "notifications/initialized"})
                send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
                listed = receive(2)
                tools = listed["result"]["tools"]
                expected = {"email_list_folders", "email_page", "email_inspect", "email_set_read_state",
                            "email_archive", "email_prepare_trash", "email_restore_from_trash",
                            "email_prepare_send", "email_execute_approved", "email_plan_status",
                            "calendar_list", "calendar_events", "contacts_addressbooks", "contacts_list", "contacts_get",
                            "calendar_prepare_changes", "contacts_prepare_changes", "dav_execute_approved"}
                assert {tool["name"] for tool in tools} == expected
                schemas = {tool["name"]: tool["inputSchema"] for tool in tools}
                assert schemas["email_execute_approved"]["required"] == ["plan_id"]
                assert "uidvalidity" in schemas["email_prepare_trash"]["required"]
                assert "uids" in schemas["email_prepare_trash"]["required"]
                assert "body" in schemas["email_prepare_send"]["required"]
                assert all("annotations" in tool for tool in tools)
                # Status validation does not need authentication or network access.
                send({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                    "name": "email_plan_status", "arguments": {"plan_id": "invalid"}}})
                refused = receive(3)
                assert refused["result"]["isError"] is True
                # Strict schema validation must reject bool-as-UID before function invocation.
                send({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
                    "name": "email_inspect", "arguments": {"folder": "INBOX", "uidvalidity": 71,
                                                             "message_uid": True}}})
                invalid = receive(4)
                assert invalid["result"]["isError"] is True
                send({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {
                    "name": "email_archive", "arguments": {"folder": "INBOX", "uidvalidity": 71, "uids": [1]}}})
                write_refused = receive(5)
                assert write_refused["result"]["isError"] is True
                assert "read-only" in json.dumps(write_refused)
                send({"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {
                    "name": "contacts_prepare_changes", "arguments": {"addressbook_url": "https://contacts.icloud.com/book/", "changes": [{"operation": "create", "fields": {"full_name": "Synthetic"}}]}}})
                dav_write_refused = receive(6)
                assert dav_write_refused["result"]["isError"] is True
                assert "read-only" in json.dumps(dav_write_refused)
                send({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {
                    "name": "calendar_list", "arguments": {}}})
                dav_read = receive(7)
                assert not dav_read["result"].get("isError", False)


            finally:
                process.stdin.close()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=5)
                process.stdout.close()
        audit = json.loads(audit_path.read_text())
        assert audit["blocked_events"] == [], audit
        assert process.returncode == 0, (home / "stderr.log").read_text()
        return {"status": "passed", "transport": "real stdio JSON-RPC", "tool_count": len(tools),
                "tools": tools, "protocol": initialized["result"]["protocolVersion"],
                "invalid_plan_refused": True, "boolean_uid_refused": True, "read_only_write_refused_before_credentials": True, "dav_write_refused": True, "dav_read_allowed_with_synthetic_fixture": True,
                "network_listener_subprocess_credential_attempts": audit["blocked_events"],
                "isolated_home": True, "server_exit_code": process.returncode}


if __name__ == "__main__":
    result = run()
    (ROOT / "build/reports/STDIO-DISCOVERY.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "tools"}, indent=2))
