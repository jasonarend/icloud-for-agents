# Troubleshooting


Current durable setup and Keychain item names: [Background startup](../getting-started/background-service.md). A key tracking ID is not a usable secret; save a replacement one-time value in its approved Keychain item before closing the key-creation screen. Default launcher invocation only checks configuration.

Start by distinguishing installation, tunnel transport, plugin installation/tool discovery, Apple authentication, and the actual mailbox action. Success at one layer does not prove the next. Preserve the exact error and a redacted description of what was attempted.

| Symptom | Check and next safe action |
| --- | --- |
| Tunnel is absent in ChatGPT | Confirm the selected Platform organization, ChatGPT workspace association, and Tunnels Read/Use permission. Workspace custom-MCP access is separate. Reuse the correct tunnel; do not create duplicates or broaden access automatically. |
| Tunnel exists but discovery/calls fail | Confirm the foreground client is still running and the Mac is awake/online. Use the installed client's supported health/readiness/doctor checks with secrets redacted. Then verify the exact local stdio command/interpreter. |
| A localhost URL does not connect | This bridge has no HTTP MCP endpoint. Use the private Tunnel stdio route or a supported local stdio client. Do not add an unauthenticated HTTP adapter as a workaround. |
| Plugin installed but unavailable in the conversation | Check the correct account/workspace, installation state, and `@` selection. Refresh tools in app details after an authorized server change. Another client's MCP registration is not ChatGPT installation. |
| Tools appear but writes fail | Expected if the owner-selected runtime is read-only. Discovery lists schemas, not enabled permissions. Do not remove read-only mode unless the owner approves that scope change. |
| Calendar/Contacts are missing | Check whether the actual running child has ICLOUD_ENABLE_DAV_READ=1 and whether its owner-selected access permits reads. Compare live discovery before changing a working runtime. Refresh the client catalog through supported controls; local discovery alone does not prove that conversation sees all 18 tools. |
| Mail tools report disabled access | Inspect the approved nonsecret launch setting. `disabled` intentionally refuses mail-service access. Do not silently enable it. |
| Keychain item unavailable or authentication fails | Have the owner verify the exact login/service match, app-specific password, and native Keychain access prompt privately. Do not print the password, dump Keychain, or collect it through chat. |
| Archive/Trash/Sent cannot resolve | List exact server folders. Have the owner choose an existing exact role override only when needed. Do not guess localized names, create folders, or accept conflicting special-use roles. |
| UIDVALIDITY/checkpoint mismatch | Stop reusing the cursor/UIDs. Rediscover that folder and start a new bounded scan, retaining the earlier review record. |
| Empty page still has a cursor | Continue with that cursor until `done=true`. It may represent an empty numeric UID window, not a completed mailbox scan. |
| Body appears incomplete or blank | Check `body_format`, `truncated`, `mime_defects_detected` and `html_only_or_no_plaintext`. HTML-only mail now has bounded static text extraction; no browser/resources or attachments are opened. Image-only or complex styling may still prevent readable extraction. An HTML fallback keeps html_only_or_no_plaintext=true even when text is present. Do not treat unreadable mail as disposable. |
| Runtime directory/receipt permissions fail | The owner should inspect only the exact relevant path and ownership. Do not recursively loosen permissions or rewrite approval files to bypass checks. |
| Human approval is missing/expired/changed | Prepare a new exact proposal and obtain fresh human review when appropriate. Never synthesize a receipt or approve on the user's behalf. |

**Interrupted send or move**

Do not retry the original mutation. For send/Trash plans, inspect `email_plan_status` for the exact plan ID. `started_or_uncertain`, `failed_or_uncertain`, missing COPYUID, or an interrupted network response can mean the server acted even though the client did not receive proof. A missing result file is not proof nothing happened.

For moves, reconcile source and destination using exact folders, UIDVALIDITY, current identities, and returned mappings where available. A missing mapping after COPY can leave a duplicate; after MOVE it can mean the original already moved. For sends, inspect Sent and the recorded result; a failed Sent append can follow accepted delivery. Avoid an automatic replacement send. An expired plan may refuse status before any recorded start; preserve the record and let the owner inspect the exact local situation rather than deleting it to retry.

**Sending is not enabled just because approval exists**

New configurations default to read-only; an existing launcher uses its owner-selected mode. Local receipts cannot override that guard. Changing to a write-enabled configuration is separate work, not a troubleshooting shortcut or an implied repair.

**Cost, account access, and platform differences**

Do not infer that tunnel use is free from statements about another OpenAI API's per-tool fees. Check the intended account's actual terms and notices before any paid commitment. Do not buy credits, change a plan, or grant broader roles merely because a connection error appears.

The pinned dependency lock and recorded tests target macOS arm64/CPython 3.14. For a different interpreter/platform, report the compatibility gap and review a new environment rather than dropping version/hash checks. Browser UI labels and access can change; follow the current [official custom MCP](https://developers.openai.com/api/docs/guides/custom-mcp-server) and [tunnel guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) if the screens differ.

**Safe support report**

Include the bridge/client versions, OS/interpreter, whether the runtime is read-only, the affected tool name, sanitized error, and whether a mutation might already have started. Keep keys, passwords, proposal contents, private mail, and full process environments out of the report. Use an established maintainer reporting channel if available; never send secret evidence.
