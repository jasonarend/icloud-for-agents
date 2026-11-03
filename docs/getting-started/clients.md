# Connect your AI client

The server runs on your Mac and retrieves credentials from macOS Keychain. A local client launches it over stdio; ChatGPT connects through your private Secure MCP Tunnel. The model name does not determine the connection: Claude in a local app and Claude in a browser have different capabilities.

Tested and supported clients are ChatGPT through the private tunnel, Claude Desktop on macOS, and Claude Code through local stdio MCP. OpenClaw has the narrower verified read-only collection path described below. The tested platform is macOS arm64 with Python 3.14. Other client routes follow official client documentation and remain unverified with this project unless stated otherwise. Verify the installed client version and current documentation before changing configuration.

## Prepare the local server

Complete [installation and credentials](installation.md), select the owner's approved services, and keep initial access read-only. From the project root, obtain the actual launch values:

```sh
.venv/bin/python scripts/configure-local.py --show-stdio-config
```

Use its absolute `command` and `args` values. The examples below use `/ABSOLUTE/PATH/icloud-for-agents` as a placeholder; replace it with the existing checkout path. The Python executable must belong to that checkout's installed virtual environment. No password or API key belongs in these entries.

Merge one named server into the existing configuration, preserving other servers and settings. Inspect only relevant nonsecret configuration, make a recoverable backup before editing, and use the client's own configuration editor where available. A GUI application's working directory and PATH can differ from Terminal, so use absolute paths.

## ChatGPT

Follow the full [ChatGPT private connection guide](chatgpt.md): check Tunnel availability in the owner's account/workspace, install the official tunnel client, configure the owner's tunnel and separate runtime credential, run preflight, start the connection, and let the human approve it in ChatGPT. Verify read-only calls from ChatGPT itself.

A local stdio entry alone does not connect ChatGPT. If the account lacks the required Tunnel option, report that blocker and offer a compatible local client. This repository supplies no public HTTP endpoint.

## Claude Desktop on macOS

Open Settings → Developer → Edit Config. In `~/Library/Application Support/Claude/claude_desktop_config.json`, merge:

```json
{
  "mcpServers": {
    "icloud-for-agents": {
      "command": "/ABSOLUTE/PATH/icloud-for-agents/.venv/bin/python",
      "args": ["/ABSOLUTE/PATH/icloud-for-agents/scripts/run-stdio.py"]
    }
  }
}
```

Fully quit and reopen Claude Desktop, inspect the server's status/tools, then perform the verification below. This is a local server configuration; a browser-based Claude connector asking for a URL is a different route.

Source: [official local-server tutorial](https://modelcontextprotocol.io/docs/develop/connect-local-servers).

## Claude Code

For a personal registration, run after reviewing the installed CLI's help:

```sh
claude mcp add --transport stdio --scope user icloud-for-agents -- /ABSOLUTE/PATH/icloud-for-agents/.venv/bin/python /ABSOLUTE/PATH/icloud-for-agents/scripts/run-stdio.py
```

Choose a narrower project/local scope if that matches the owner's request; personal account configuration should not be committed to a shared repository. Inspect `claude mcp list`, restart an existing session if needed, and use `/mcp` to inspect the connection before a read-only call. Claude Code's editor integration must use the configuration scope that its actual session loads.

Source: [Claude Code MCP guide](https://code.claude.com/docs/en/mcp).

## Codex CLI, desktop app and IDE extension

Use the local Codex MCP configuration loaded by the intended surface. For the CLI:

```sh
codex mcp add icloud-for-agents -- /ABSOLUTE/PATH/icloud-for-agents/.venv/bin/python /ABSOLUTE/PATH/icloud-for-agents/scripts/run-stdio.py
```

Alternatively, merge this into the appropriate Codex `config.toml`:

```toml
[mcp_servers.icloud-for-agents]
command = "/ABSOLUTE/PATH/icloud-for-agents/.venv/bin/python"
args = ["/ABSOLUTE/PATH/icloud-for-agents/scripts/run-stdio.py"]
```

Inspect `codex mcp list` and the target surface's MCP settings; restart or refresh as supported. Organization policies can restrict servers. A cloud task has a different execution environment and cannot use the Mac's paths or Keychain merely because local Codex has this entry.

Source: [OpenAI Codex MCP documentation](https://developers.openai.com/codex/mcp).

## Hermes: current verification blocker

Hermes v0.22.5+1741 is not yet verified for live iCloud reads in this test environment. Its standalone MCP probe connected, but the conversation gateway failed before Python initialization with `EPERM` opening the checkout virtual environment on an external volume. A successful probe does not certify conversation access.

Check the actual gateway owner and macOS permission attribution, not only the Desktop app's Files & Folders toggle. Desktop and CLI share a gateway that can outlive the Desktop window. In the installed version, `hermes_cli/gateway_launchd_runtime.py` intentionally launches an unmanaged macOS gateway through a one-off launchd/osascript job for Local Network privacy behavior. A normal Desktop relaunch therefore need not change its responsible process. In this test, TCC attributed the denied removable-volume access to `com.apple.osascript`; Hermes.app's existing Removable Volumes grant was on, and a separately approved official gateway stop/restart recreated the same launch context and failure. This is an unresolved client/runtime compatibility issue on this machine, not a demonstrated requirement for every installation.

Do not repeat permission resets or claim that restarting the gateway fixes this failure. Do not grant Full Disk Access, broaden a scripting host's permissions, move the server to another path or change its launcher to evade a denial. Obtain a supported Hermes/macOS remedy for the identified responsible process, then verify actual read tool results. Stopping a shared Hermes gateway can interrupt other sessions and ends detached terminal shells; preserve work and obtain approval for that interruption first.

Upstream tracks this attribution problem in [issue #144255](https://github.com/NousResearch/hermes-agent/issues/144255). As checked on 2026-10-27, [PR #139529](https://github.com/NousResearch/hermes-agent/pull/139529) proposes a dedicated gateway app identity and [PR #130745](https://github.com/NousResearch/hermes-agent/pull/130745) proposes a direct-launch identity switch; both remain open and the installed version has neither switch. They are review candidates, not verified installation steps. The issue reporter's broad osascript Full Disk Access workaround is not part of this project's setup: it affects unrelated scripts and needs separate informed approval. Prefer a released vendor fix that gives the gateway a specific, grantable identity, then repeat actual conversation tests.

Source: [Hermes Desktop lifecycle and macOS permissions](https://hermes-agent.nousresearch.com/docs/user-guide/desktop).

## OpenClaw: verified read-only collection access

Verified on macOS arm64 with iCloud for Agents 1.1.0, OpenClaw 2026.10.7 (`157d8c2`), and the OpenAI Codex runtime using `openai/gpt-6.1-terra`. Actual harness calls passed for Mail folder listing twice with identical results, Calendar collection listing, and Contacts address-book listing. Eight read tool schemas were exposed. A later Gateway-backed CLI turn in the same isolated session passed after app/Gateway restart: one email header record read twice with matching UIDVALIDITY, UID and read/star flags; one calendar queried for a one-hour window returning zero events; and one contact metadata reference. These tests used explicit owner approval for sending those bounded private records to OpenClaw and its cloud-hosted Terra model. Mail bodies, full contact cards, nonempty event results, writes, Notes, Reminders, other runtimes and long-term stability were not tested; this is scoped read-only verification, not full read/write certification. Desktop remained on an updater warning despite official update status reporting success, so this does not certify the Desktop UI as ready.

Use OpenClaw's official MCP registry (`openclaw mcp add` or Settings → MCP). Back up its existing personal configuration and merge only the new `mcp.servers` entry. For an independently restricted client when the checkout's shared settings may allow writes, the tested entry uses the installed **console executable directly**, with explicit environment settings:

```json
{
  "mcp": {
    "servers": {
      "icloud-for-agents-readonly": {
        "command": "/ABSOLUTE/PATH/icloud-for-agents/.venv/bin/icloud-for-agents",
        "args": [],
        "env": {
          "ICLOUD_MAIL_ACCOUNT": "OWNER_ACCOUNT_FROM_LOCAL_SETTINGS",
          "ICLOUD_KEYCHAIN_SERVICE": "EXISTING_KEYCHAIN_SERVICE_FROM_LOCAL_SETTINGS",
          "ICLOUD_MAIL_ACCESS": "read-only",
          "ICLOUD_ENABLE_MAIL": "1",
          "ICLOUD_ENABLE_CALENDAR": "1",
          "ICLOUD_ENABLE_CONTACTS": "1",
          "ICLOUD_ENABLE_DAV_READ": "1",
          "ICLOUD_ENABLE_NOTES": "0",
          "ICLOUD_ENABLE_REMINDERS": "0",
          "ICLOUD_ENABLE_POLICY_MANAGEMENT": "0",
          "ICLOUD_APPROVAL_POLICY": "local-receipt",
          "ICLOUD_NATIVE_CONFIG": "{}",
          "ICLOUD_RUNTIME_ROOT": "/ABSOLUTE/PRIVATE/PATH/openclaw-icloud-readonly-runtime",
          "FASTMCP_CHECK_FOR_UPDATES": "off"
        },
        "toolFilter": {
          "include": [
            "email_list_folders", "email_page", "email_inspect",
            "calendar_list", "calendar_events",
            "contacts_addressbooks", "contacts_list", "contacts_get"
          ]
        }
      }
    }
  }
}
```

Replace account and Keychain service placeholders with the existing nonsecret references in `local/settings.json`; do not insert the app-specific password. Choose an absolute private runtime directory for this client. The direct entry deliberately does not load `local/settings.json`. Do not replace its command with `scripts/run-stdio.py` while relying on these environment restrictions: that launcher constructs its environment from shared settings and can overwrite them. Do not change shared service selections to restrict a single client.

Keep normal approval checks enabled; do not set `codex.defaultToolsApprovalMode` to `approve` as a setup shortcut. The include filter restricts this server's projected tools, while the server's `read-only` mode independently blocks mutations. It does not restrict other connectors already installed in the harness. Inspect the actual tool namespace and use only this server during verification.

Run `openclaw mcp doctor icloud-for-agents-readonly --probe --json` to check transport setup, then verify from a fresh or idle isolated conversation. With supported Gateway hot reload, the next turn discovers a changed entry; a shared Gateway restart was not needed in the tested setup. Select and verify the desired model inside OpenClaw. Ask it to call `email_list_folders` twice, `calendar_list`, and `contacts_addressbooks`, reporting only counts and repeat consistency. Verify actual tool results, rather than a model's connection claim. The tested Codex runtime exposed native tools with the prefix `mcp__icloud_for_agents_readonly__`; its generic compiled-tool count did not include that native MCP namespace. The official `openclaw agent --session-key ... --model openai/gpt-6.1-terra --message-file ... --json` route completed the same isolated UI session; omit `--deliver` for local testing. Its terminal receipt identified the effective model and successful server-qualified tool names, and `openclaw sessions export-trajectory` supplied call/result evidence.

Mail pagination can return zero records with `done=false` because it scans bounded numeric UID windows; that does not establish an empty mailbox. Follow the returned checkpoint for a complete scan, or use a narrowly approved window based on the returned UIDVALIDITY/high watermark. Keep `page_size=1` for a one-header test, repeat identical arguments for comparison, and do not fetch bodies or mutate flags. An empty calendar window verifies the request path but does not validate event-content parsing.

A successful probe alone does not prove account access or conversation compatibility. If tools are missing, inspect server enablement and include/exclude filters, the chosen runtime, and tool policy: OpenClaw documents that the `minimal` profile and `tools.deny: ["bundle-mcp"]` can hide MCP tools. Do not broaden unrelated policy to make a test pass. For Python `EPERM` opening a virtual environment on an external volume, inspect the responsible app's normal macOS Files & Folders permissions and logs; do not assume an enabled toggle proves the running process has access. A separate Hermes Desktop test encountered this machine-specific denial, and its repair remains unverified; Full Disk Access is not a demonstrated general installation requirement.

Sources: [OpenClaw MCP setup](https://docs.openclaw.ai/tools/mcp), [saved-server registry and runtime behavior](https://docs.openclaw.ai/cli/mcp/registry), [agent CLI](https://docs.openclaw.ai/cli/agent), [session evidence commands](https://docs.openclaw.ai/cli/sessions), [Hermes macOS permission troubleshooting](https://hermes-agent.nousresearch.com/docs/user-guide/desktop#macos-permissions-and-local-rebuilds-tcc).

## Cursor

Merge the Claude Desktop JSON shape above into personal `~/.cursor/mcp.json`, or project `.cursor/mcp.json` if explicitly desired. Open Cursor's MCP settings, enable the server, and refresh/restart as required. Use a local agent session on the Mac and verify a tool call. Cloud agents require a separate connection design.

Source: [Cursor MCP configuration](https://cursor.com/docs/context/mcp).

## VS Code with GitHub Copilot

Use **MCP: Add Server** and choose a local stdio command, or **MCP: Open User Configuration** for personal scope. Workspace configuration uses `.vscode/mcp.json` with a different top-level key:

```json
{
  "servers": {
    "icloud-for-agents": {
      "type": "stdio",
      "command": "/ABSOLUTE/PATH/icloud-for-agents/.venv/bin/python",
      "args": ["/ABSOLUTE/PATH/icloud-for-agents/scripts/run-stdio.py"]
    }
  }
}
```

Start the server through VS Code's MCP controls, let the human review trust prompts, and select its tools in an agent-capable chat. In SSH, container or other remote windows, confirm the server runs on the Mac; remote paths and Keychain access are not interchangeable with local ones.

Sources: [add servers](https://code.visualstudio.com/docs/agent-customization/mcp-servers), [configuration reference](https://code.visualstudio.com/docs/agents/reference/mcp-configuration).

## Gemini CLI

Merge the `mcpServers` JSON entry above into the applicable Gemini CLI `settings.json` (personal configuration is normally `~/.gemini/settings.json`). Keep tool confirmations enabled; do not set `trust: true` as a setup shortcut. Restart the session, inspect `/mcp`, and verify a read-only call. Use the local CLI, rather than assuming the Gemini web app reads its configuration.

Source: [Gemini CLI MCP guide](https://geminicli.com/docs/tools/mcp-server/).

## Cline

Open Cline's MCP Servers panel → Configure → Configure MCP Servers. Merge the `mcpServers` JSON entry above into the file the installed extension opens. For the standalone CLI, inspect its configured settings location rather than assuming the extension's file is shared. Keep automatic tool approval disabled during setup. Restart the server in Cline and verify from the intended chat.

Source: [Cline MCP configuration](https://docs.cline.bot/mcp/mcp-overview).

## Windsurf / Cascade

Open the installed application's MCP configuration editor and add the `mcpServers` JSON entry above. Existing Windsurf installations commonly use `~/.codeium/windsurf/mcp_config.json`; confirm the actual path in the installed application, since product versions and configuration surfaces change. Refresh the server, select its tools, and verify a local read-only call. Respect managed allowlists.

Source: [Cascade MCP configuration](https://docs.windsurf.com/windsurf/cascade/mcp).

## Continue

Add a local MCP entry to the active Continue YAML configuration, preserving its existing top-level metadata and other settings:

```yaml
mcpServers:
  - name: icloud-for-agents
    command: /ABSOLUTE/PATH/icloud-for-agents/.venv/bin/python
    args:
      - /ABSOLUTE/PATH/icloud-for-agents/scripts/run-stdio.py
```

Use the configuration selected by the installed extension or CLI. Reload it, choose an agent/tool-capable model and mode, and verify that the server tools are available and callable.

Sources: [Continue MCP guide](https://docs.continue.dev/customize/mcp-tools), [YAML reference](https://docs.continue.dev/reference).

## Grok coding CLI

The local Grok coding CLI documents stdio MCP support. On the Mac containing the installation, register the generated launch command:

```sh
grok mcp add icloud-for-agents -- /ABSOLUTE/PATH/icloud-for-agents/.venv/bin/python /ABSOLUTE/PATH/icloud-for-agents/scripts/run-stdio.py
```

Inspect `grok mcp list` and `grok mcp doctor icloud-for-agents`, then verify a read-only call in the actual session. This is a documented transport match, not a tested project integration.

Source: [Grok coding CLI MCP servers](https://docs.x.ai/build/features/mcp-servers).

## Grok Bot and Grok web

Grok Bot supports custom MCP servers, including commands that run on the computer used by its conversation. Confirm that execution location: a command running on the Bot's cloud computer cannot access this Mac's installation or Keychain. Grok web's custom connectors require a publicly reachable MCP server URL. Neither route has been validated with this repository.

The repository currently provides stdio and a ChatGPT-specific private tunnel, rather than a general authenticated HTTP MCP endpoint. Connecting these Grok surfaces back to the Mac needs additional implementation and testing. Contributors are welcome to propose a secure connection that preserves local credential storage and the existing approval model.

Sources: [Grok Bot execution and custom servers](https://docs.x.ai/grok-bot/team-bots), [Grok custom connectors](https://docs.x.ai/grok/connectors).

## Meta Muse Code

Muse Code documents local stdio MCP support. Merge the following into its active settings file, preserving existing settings and using the generated absolute launch paths:

```json
{
  "mcp_servers": {
    "icloud-for-agents": {
      "transport": "stdio",
      "command": "/ABSOLUTE/PATH/icloud-for-agents/.venv/bin/python",
      "args": ["/ABSOLUTE/PATH/icloud-for-agents/scripts/run-stdio.py"]
    }
  }
}
```

Run Muse Code locally on the Mac, reload its configuration as required, inspect `/mcp`, and verify a read-only call. This is a documented transport match, not a tested project integration.

Source: [Muse Code MCP configuration](https://dev.meta.ai/docs/muse-code/extending).

## Meta Muse consumer app

Treat the consumer app separately from Muse Code. We have not verified an official local stdio configuration route or a working connection to this repository. Its connector features alone do not establish compatibility with the Mac-hosted server. Contributors can help identify an officially documented connection method and test it; until then, compatibility remains unconfirmed.

Source: [Meta Muse product information](https://ai.meta.com/muse/).

## Other MCP clients and agent SDKs

A compatible client must launch a local stdio subprocess, exchange MCP messages over its stdin/stdout, and support tool discovery and invocation. Use the generated `command` and `args` with its documented stdio transport. SDK applications must retain the subprocess for the session and close it cleanly. A model provider alone is not an MCP client.

HTTP/SSE-only clients, browser-only clients and hosted agents cannot directly launch this Mac's server. The documented ChatGPT tunnel is specific to that connection; it is not a generic URL for other clients. Additional adapters or hosted deployment require separate implementation and review.

## Verify every client connection

First run the offline and stdio smoke checks in [agent setup](agent-setup.md). Then verify through the actual target client, with the owner's approved service subset:

1. Confirm the server connects and exposes the expected tools for the selected services.
2. For Mail, list exact folders, request a bounded header page, and inspect one message without changing its read state.
3. For Calendar, list calendars and read a small explicit date range. For Contacts, list address books, request a small contact page and retrieve one contact.
4. Report the client/version, execution location, selected services, access mode, successful live calls and unresolved steps. Tool discovery alone is not a successful account connection.

Let the human handle Keychain and client trust prompts. If startup fails, inspect the client's server error output without exposing credentials or private message content. Check absolute paths, the installed environment, existing settings, service selection and Keychain identity before changing configuration. Keep writes and recurring cleanup for separately approved tasks.

## Help expand compatibility

Client tests, corrected setup instructions and implementations for missing connection routes are welcome. See [contributing and testing](../contributing/development.md#client-and-platform-compatibility-contributions) for what to include in an issue or pull request. Report transport compatibility, successful discovery and live service verification separately so others know exactly what has been demonstrated.
