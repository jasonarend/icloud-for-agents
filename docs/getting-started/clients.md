# Connect your AI client

The server runs on your Mac and retrieves credentials from macOS Keychain. A local client launches it over stdio; ChatGPT connects through your private Secure MCP Tunnel. The model name does not determine the connection: Claude in a local app and Claude in a browser have different capabilities.

Tested and supported clients are ChatGPT through the private tunnel, Claude Desktop on macOS, and Claude Code through local stdio MCP. The tested platform is macOS arm64 with Python 3.14. Other client routes follow official client documentation and remain unverified with this project unless stated otherwise. Verify the installed client version and current documentation before changing configuration.

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
