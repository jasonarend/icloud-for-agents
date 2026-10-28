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


def run(native=False):
    with tempfile.TemporaryDirectory(prefix="icloud-bridge-offline-") as directory:
        home = Path(directory)
        audit_path = home / "audit.json"
        env = {"HOME": str(home), "PATH": os.defpath, "PYTHONPATH": str(ROOT / "src"),
               "PYTHONUNBUFFERED": "1", "BRIDGE_TEST_AUDIT": str(audit_path),
               "FASTMCP_SHOW_SERVER_BANNER": "false", "FASTMCP_CHECK_FOR_UPDATES": "off",
               "ICLOUD_MAIL_ACCESS": "read-only", "ICLOUD_ENABLE_DAV_READ": "1"}
        if native:
            env.update(ICLOUD_ENABLE_NOTES="1", ICLOUD_ENABLE_REMINDERS="1")
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
                if native:
                    expected |= {s+"_"+op for s in ("notes","reminders") for op in ("prepare_grant","prepare_grant_replacement","activate_grant","finalize_grant","grant_status","revoke_grant","prepare_with_grant")}
                    expected |= {"notes_folder_role", "notes_prepare_folder_rename_correction", "notes_reconcile_folder_rename_correction", "notes_prepare_move_recovery", "notes_accept_move_recovery", "notes_finalize_move_recovery", "notes_abandon_move_recovery", "notes_finalize_stopped_plan", "reminders_finalize_stopped_plan", "notes_cancel_plan", "reminders_cancel_plan", "notes_prepare_once", "reminders_prepare_once", "notes_finalize_creation_recovery", "notes_prepare_creation_recovery", "notes_execute_creation_recovery", "notes_prepare_os_access", "notes_request_os_access", "reminders_prepare_os_access", "reminders_request_os_access", "notes_prepare_changes", "notes_execute_approved", "notes_plan_status", "notes_reconcile_created", "notes_reconcile_known_changes",
                                 "reminders_prepare_changes", "reminders_execute_approved", "reminders_plan_status", "reminders_reconcile_created", "reminders_reconcile_known_changes",
                                 "reminders_prepare_update_correction", "reminders_reconcile_correction",
                                 "notes_prepare_edit_recovery", "notes_accept_edit_recovery", "notes_finalize_edit_recovery", "notes_abandon_edit_recovery", "notes_inspect_uncertain_edit", "notes_model_folder_role", "notes_locate", "notes_search", "notes_attachments", "notes_status", "notes_sources", "notes_folders", "notes_default_folder", "notes_query", "notes_get", "notes_read_text",
                                 "notes_access_status", "reminders_access_status", "reminders_daily", "reminders_status", "reminders_sources", "reminders_lists", "reminders_default_list", "reminders_list_sections", "reminders_list_features", "reminders_query", "reminders_get", "reminders_inspect_hierarchy", "reminders_recovery_lookup", "reminders_recovery_tree", "reminders_recovery_list", "reminders_inspect_recovery_hierarchy"}
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
                if native:
                    for request_id, name, arguments in [(8, "notes_query", {"folder_id":"synthetic"}),
                                                        (9, "reminders_get", {"id":"synthetic"}),
                                                        (10, "notes_query", {"folder_id":"synthetic", "limit":True}),
                                                        (11,"notes_prepare_changes",{"changes":[{"operation":"create_note"}]}),
                                                        (12,"reminders_prepare_changes",{"changes":[{"operation":"create_reminder"}]})]:
                        send({"jsonrpc":"2.0", "id":request_id, "method":"tools/call",
                              "params":{"name":name,"arguments":arguments}})
                        response = receive(request_id)
                        assert response["result"].get("isError") is True



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
