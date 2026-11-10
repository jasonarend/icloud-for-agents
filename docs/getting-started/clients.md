# Connect your AI client

The server runs on your Mac and retrieves credentials from macOS Keychain. A local client launches it over stdio; ChatGPT connects through your private Secure MCP Tunnel. The model name does not determine the connection: Claude in a local app and Claude in a browser have different capabilities.

ChatGPT connects through a private tunnel; Claude Desktop, Claude Code and compatible local clients connect over stdio. Unsloth Desktop, Hermes Desktop and OpenClaw have also been verified for the read-only operations described in their sections. The tested platform is macOS arm64 with Python 3.14. Other client routes remain unverified with this project unless stated otherwise. Check the installed client's documentation when its interface differs from these instructions.

## Prepare the local server

Complete [installation and credentials](installation.md), select the services you want, and keep initial access read-only. Your assistant can obtain the actual launch values from the project root; you do not need to run this command yourself:

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

## Unsloth Desktop

Connect iCloud for Agents in Unsloth's interface:

1. Complete the local server setup above, with only your chosen services enabled and access set to read-only for the first test. If an assistant is helping, it can obtain the launch values and handle local setup commands for you.
2. In a chat, open **Tools and attachments → More → MCP** if the **MCP** control is hidden. Then open **MCP → Manage MCP servers → Add server**. Keep any existing servers.
3. Set **Display name** to `iCloud for Agents`. In **URL or executable**, enter the absolute Python path from the existing installation, such as `/ABSOLUTE/PATH/icloud-for-agents/.venv/bin/python`. Add one **Arguments** row containing `/ABSOLUTE/PATH/icloud-for-agents/scripts/run-stdio.py`. Use the actual checkout path in both fields.
4. Choose **Test connection**, then **Add server** after it connects. The number of tools depends on the services you selected.
5. Keep the server enabled in the manager and enable MCP use in the chat. The server's row in the MCP menu also toggles its availability, so leave it enabled for the test. Choose a model that supports tool calling and keep Unsloth's normal tool approvals enabled.
6. Ask Unsloth to read something from a selected service, such as “Show my calendars.” Check the actual tool result. Follow the [verification guidance below](#verify-every-client-connection) for the services you chose.

The standard launcher reads your service selections and access modes from `local/settings.json` and retrieves Apple credentials from Keychain. Leave credentials out of Unsloth's argument and environment fields. Environment overrides beside the standard launcher do not restrict the shared settings it loads.

Choose an on-device model to process tool results locally. A connected cloud model may send those results to its provider. Temporary chat avoids saving the conversation in Unsloth's normal history; it does not replace a review of the client's logging and storage settings. Notes and Reminders still need their separate [native-service setup](native-services.md), approved sources and macOS access.

Unsloth's **Settings → API → Agent access (MCP)** exposes Unsloth itself to other agents. Adding iCloud for Agents uses the outgoing server manager described above. A normal local Desktop installation supports stdio; a remote Unsloth instance cannot use the Mac's paths or Keychain automatically. If local servers are unavailable, check whether Unsloth's Remote Access is active and whether tools are enabled; follow Unsloth's own guidance before changing either setting.

For Notes and Reminders, follow the macOS permission prompts and select the accounts you want to make available. Reminders access in macOS is broader than the server's selected-source and read-only controls. If access is refused, check the helper's permission and service status as described in [native-service setup](native-services.md).

Read-only collection access has been verified for all five services in Unsloth. Writes and the first-use permission prompt on another Mac have not been verified in this client.

Sources: [Unsloth Desktop](https://unsloth.ai/docs/desktop), [Unsloth tool permissions and security](https://unsloth.ai/blog/security).

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

## Hermes Desktop

In Hermes Desktop, open **Capabilities → Connectors → Add your own**. Name the connector and enter the absolute **Command to launch** and **Arguments** from [Prepare the local server](#prepare-the-local-server); save it without removing existing connectors. Enable the connector and the read tools needed for the selected services, then ask Hermes to make the bounded checks in [Verify every client connection](#verify-every-client-connection). Keep Apple credentials in Keychain, not connector fields. A cloud model receives any tool results it processes; choose a model and service scope appropriate for the information you intend to share.

Read-only collection access has been verified for Mail, Calendar and Contacts in Hermes Desktop. Notes, Reminders and writes have not yet been verified in this client.

If startup fails, check the actual gateway's error and macOS permission attribution; Desktop and CLI can share a gateway that outlives the Desktop window. A standalone probe alone does not prove that a conversation can use the server. Do not reset privacy permissions, grant a scripting host broad access, or change paths to evade a denial. Stopping a shared gateway can interrupt other sessions, so coordinate with its owner. Upstream tracks the attribution issue in [issue #144255](https://github.com/NousResearch/hermes-agent/issues/144255).

Source: [Hermes Desktop lifecycle and macOS permissions](https://hermes-agent.nousresearch.com/docs/user-guide/desktop).

## OpenClaw

OpenClaw connects to iCloud for Agents through a local MCP server. Read-only access to selected Mail, Calendar and Contacts operations has been verified; Notes, Reminders and writes have not yet been verified in this client.

Use OpenClaw's official MCP registry (Settings → MCP, or `openclaw mcp add` when your assistant handles setup). Preserve its existing configuration and add the new server. To restrict this client independently when the shared settings may allow writes, use the installed **console executable directly**, with explicit environment settings:

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

Check the connector with OpenClaw's MCP controls, then ask it to read from a selected service in a conversation. Check the tool result, not just whether the server connects. Select a tool-capable model and remember that cloud-hosted models receive the information returned by tools.

Mail pagination can return zero records with `done=false` because it scans bounded numeric UID windows; that does not establish an empty mailbox. Follow the returned checkpoint to continue the scan.

If tools are missing, inspect server enablement and include/exclude filters, the chosen runtime, and tool policy: OpenClaw documents that the `minimal` profile and `tools.deny: ["bundle-mcp"]` can hide MCP tools. For an `EPERM` error opening a virtual environment on an external volume, inspect the responsible app's macOS Files & Folders permissions and logs.

Sources: [OpenClaw MCP setup](https://docs.openclaw.ai/tools/mcp), [saved-server registry and runtime behavior](https://docs.openclaw.ai/cli/mcp/registry).

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

### Grok Bot

Grok Bot supports custom MCP connectors configured with either a **URL** or a **Command** and **Arguments**. The command-based option is a route to try with iCloud for Agents' existing stdio launcher. End-to-end use has not been tested because the maintainer did not have a Grok subscription. Whether it can launch this server on the local Mac and complete service calls still needs verification.

Confirm where the MCP command executes before adding the connector. Grok Bot uses a cloud computer by default and also documents approved command execution on your own computer. That alone does not establish where a custom MCP connector's command runs. A cloud process cannot access this Mac's installation, Keychain or native Apple apps.

If the connector can run its command on the Mac hosting iCloud for Agents:

1. Complete [installation and credentials](installation.md) and the [local server preparation above](#prepare-the-local-server). Keep initial access read-only.
2. Choose the custom connector's **Command** option. Use the absolute `command` and `args` returned by `scripts/configure-local.py --show-stdio-config`: the installed Python executable and `scripts/run-stdio.py` argument. Leave passwords and API keys out of the connector fields.
3. Confirm tool discovery, then make an actual read-only call for each selected service using [the verification steps below](#verify-every-client-connection). Report discovery separately from successful service access; test writes only with explicit authorization.

If your connector runs only in the cloud or asks for a URL, a secure connection to the Mac must be verified separately. The [optional authenticated HTTP transport](http.md) listens on loopback and does not by itself make the server reachable from the cloud. The OpenAI private tunnel is specific to ChatGPT. Do not copy Apple credentials into cloud connector fields to work around the execution location.

Trying Grok Bot? [Open a GitHub issue](https://github.com/jasonarend/icloud-for-agents/issues) if you run into a problem, or share a working setup so the guide can improve. Include the Grok client/version, where the MCP command ran, your macOS and project versions, and sanitized setup steps or errors. Keep credentials, account details and private content out of reports. See the [compatibility contribution guide](../contributing/development.md#client-and-platform-compatibility-contributions) for details.

### Grok web

Grok web's custom connectors require a publicly reachable MCP server URL. The local command configuration above does not provide that URL, and the ChatGPT tunnel cannot be assumed to work with Grok web. This route has not been validated with the project; contributors are welcome to propose a secure connection that preserves local credential storage and the existing approval model.

Sources: [Grok Bot computer, apps and custom connectors](https://docs.x.ai/grok-bot/computer-and-apps), [Grok web custom connectors](https://docs.x.ai/grok/connectors).

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

Treat the consumer app separately from Muse Code. Muse's **Custom Connector** flow uses an HTTPS endpoint and can use a bearer key entered by the owner in Muse's secure credential form. It does not use the local stdio setup described for Muse Code.

A connection to iCloud for Agents needs the [HTTP transport](http.md) and a secure HTTPS route that Muse can actually reach. The server listens on your Mac's loopback address, so enabling HTTP alone does not make it reachable from Muse. A private route from Muse to the Mac has not yet been established for this project. Do not expose your Apple data through a public tunnel as a setup shortcut.

Muse has completed MCP discovery and read operations for all five services against an isolated synthetic server through a temporary HTTPS tunnel. This verifies that connection flow with invented data; reads from a real Apple account and a private remote route remain unverified. Muse's built-in Apple connectors are separate integrations. Keep keys out of chat and ordinary configuration files, and verify each selected service with a bounded read when a suitable connection is available.

Sources: [Muse connector help, including Custom Connectors](https://www.meta.com/help/superintelligence/1687253048996149/), [Muse Connector Platform guidelines](https://platform.muse.ai/docs), [Meta Muse product information](https://ai.meta.com/muse/).

## Other MCP clients and agent SDKs

A local stdio client must launch a subprocess, exchange MCP messages over its stdin/stdout, and support tool discovery and invocation. Use the generated `command` and `args` with its documented stdio transport. SDK applications must retain the subprocess for the session and close it cleanly. Compatible Streamable HTTP clients may instead use the [optional authenticated HTTP connection](http.md). A model provider alone is not an MCP client.

HTTP/SSE-only clients, browser-only clients and hosted agents cannot directly launch this Mac's stdio server. The HTTP endpoint remains on loopback unless a separately configured private connection is used. Legacy SSE-only clients are not supported by the Streamable HTTP endpoint. The documented ChatGPT tunnel is specific to that connection; it is not a generic URL for other clients.

## Verify every client connection

First run the offline and stdio smoke checks in [agent setup](agent-setup.md). Then verify through the actual target client, with the owner's approved service subset:

1. Confirm the server connects and exposes the expected tools for the selected services.
2. For Mail, list exact folders, request a bounded header page, and inspect one message without changing its read state.
3. For Calendar, list calendars and read a small explicit date range. For Contacts, list address books, request a small contact page and retrieve one contact.
4. For Notes and Reminders, follow [native-service verification](native-services.md), retaining the approved sources and read-only modes. Start with bounded folder/list discovery before reading a selected item.
5. Report the client/version, execution location, selected services, access mode, successful live calls and unresolved steps. Tool discovery alone is not a successful account connection.

Let the human handle Keychain and client trust prompts. If startup fails, inspect the client's server error output without exposing credentials or private message content. Check absolute paths, the installed environment, existing settings, service selection and Keychain identity before changing configuration. Keep writes and recurring cleanup for separately approved tasks.

## Help expand compatibility

Client tests, corrected setup instructions and implementations for missing connection routes are welcome. See [contributing and testing](../contributing/development.md#client-and-platform-compatibility-contributions) for what to include in an issue or pull request. Report transport compatibility, successful discovery and live service verification separately so others know exactly what has been demonstrated.
