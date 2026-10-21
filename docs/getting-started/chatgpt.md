# Connect privately to ChatGPT

ChatGPT does not automatically inherit a local server installed on your Mac. This project uses an owner-controlled Secure MCP Tunnel to its stdio server; it does not expose a public HTTP endpoint.

1. Confirm your account/workspace provides **Add custom MCP server** and the **Tunnel** option. Availability and UI vary. Use your own Platform organization and your own tunnel; never reuse another person's tunnel or keys.
2. Follow [OpenAI's Secure MCP Tunnel documentation](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) and the executable installation steps below. Do not infer pricing or billing consent from this guide; inspect current terms before accepting any charge.
3. Use only the runtime permissions needed for the tunnel. Use Tunnels Read and Use where supported, with unrelated permissions disabled. Enter the runtime key personally into a **separate** Keychain item: service `icloud-for-agents-tunnel-runtime`, account your exact tunnel ID. Never paste the key into chat or a tracked file.
4. Configure your own validated local tunnel profile and nonsecret settings, as described in [background setup](background-service.md). Run `scripts/start-private-tunnel.py --check` through the project interpreter first. Starting with `--start-with-keychain` retrieves the existing item; it does not create it.
5. In ChatGPT's custom MCP UI select your private tunnel. Enter a descriptive name and description. Use the authentication options actually supported by that tunnel flow; do not invent a URL or claim standalone OAuth support. Review the actions and final connection approval personally.
6. Verify actual read-only calls, not only a local tools list. With all three services selected, the base schema has 18 tools; optional policy management adds five Mail policy tools. Tool registration and live access switches are distinct.

Use the client's supported **Refresh tools** control after a schema change. Depending on the account surface this may appear under app management. Do not delete/recreate a working connection merely to refresh it. Verify actual calls after refresh; discovery and existing conversation catalogs can differ.

The Python package version and a custom app wrapper version are independent. Follow the client's supported release workflow rather than assuming source installation updates listing metadata.

[OpenAI custom MCP guidance](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt) is the source for current account-facing behavior. This private connection is not a public plugin listing; see [distribution](../contributing/releasing.md).

## Install and select the tunnel executable

Download the macOS binary matching your architecture from the download link in your Platform tunnel settings or the official [openai/tunnel-client releases](https://github.com/openai/tunnel-client/releases/latest). Verify the publisher and any supplied checksum/signature before making it executable; retain the release version, source URL and SHA-256 digest for your installation record. Extract the actual executable rather than pointing the launcher at an archive or installer script. No download or automatic upgrade occurs in this repository's launcher.

The adapter was reviewed with tunnel-client **v0.0.16**, using a version-1 YAML profile and `run --config PROFILE`. A newer official release is not automatically a tested combination: inspect its help and confirm that command/profile format before supplying credentials. Run these nonsecret checks against the extracted binary:

```sh
/absolute/path/to/tunnel-client --version
/absolute/path/to/tunnel-client run --help
```

Keep the executable in an owner-controlled location, executable by your user and not writable by other users. For example, place it at `tools/tunnel-client` inside your checkout. That directory is ignored and must not be committed. A symlink executable is refused; select the actual file.

After creating your nonsecret settings with `scripts/configure-local.py --account YOUR_ICLOUD_LOGIN --tunnel-id YOUR_TUNNEL_ID`, merge this field into the existing owner-only `local/settings.json`, preserving all other settings:

```json
{
  "tunnel_client_path": "/absolute/path/to/icloud-for-agents/tools/tunnel-client"
}
```

This is a field to merge, not a replacement settings file. Use an absolute path. The launcher validates that the file is executable, owned by your user or root, and not group/world-writable before retrieving a key. An inherited environment variable or PATH entry cannot select it. Existing installations without this field continue to use `tools/tunnel-client-v0.0.16/tunnel-client` exactly; that legacy directory name is not a version-detection mechanism.

Run `.venv/bin/python scripts/start-private-tunnel.py --check` before explicit startup. If a newer release changes the supported command/profile format, stop and update/review the adapter rather than renaming the binary to imply compatibility. Keep a known-working installation while testing any upgrade.
