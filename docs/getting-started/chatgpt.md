# Connect privately to ChatGPT

A local assistant can prepare this connection and guide you through the account, credential and approval steps. The commands and configuration examples below are for the assistant or for someone who prefers manual setup.

ChatGPT does not automatically inherit a local server installed on your Mac. This project uses an owner-controlled Secure MCP Tunnel to its stdio server; it does not expose a public HTTP endpoint.

1. Confirm your account/workspace provides **Add custom MCP server** and the **Tunnel** option. Availability and UI vary. Use your own Platform organization and your own tunnel; never reuse another person's tunnel or keys.
2. Follow [OpenAI's Secure MCP Tunnel documentation](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) and the executable installation steps below. Do not infer pricing or billing consent from this guide; inspect current terms before accepting any charge.
3. Use only the runtime permissions needed for the tunnel. Use Tunnels Read and Use where supported, with unrelated permissions disabled. Enter the runtime key personally into a **separate** Keychain item: service `icloud-for-agents-tunnel-runtime`, account your exact tunnel ID. Never paste the key into chat or a tracked file.
4. Configure your own validated local tunnel profile and nonsecret settings, as described in [background setup](background-service.md). Run `scripts/start-private-tunnel.py --check` through the project interpreter first. Starting with `--start-with-keychain` retrieves the existing item; it does not create it.
5. In ChatGPT's custom MCP UI select your private tunnel. Enter a descriptive name and description. Use the authentication options actually supported by that tunnel flow; do not invent a URL or claim standalone OAuth support. Review the actions and final connection approval personally.
6. Verify actual read-only calls, not only a local tools list. The available tools depend on the selected services and optional features. Notes and Reminders use the same private connection when enabled. Tool registration and live access switches are distinct.

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

The OpenAI connection also requires a protected installation: other macOS users must not be able to replace its settings, tunnel profile, Python interpreter or server scripts through a writable containing directory or ACL. Safe interpreter symlinks are supported; their destination and containing directories are checked too. A private installation under your home directory is the usual choice. Shared drives and some shared Python installations may need migration. Preflight identifies the unsafe path before requesting a Keychain credential; it does not change that path's permissions.

If your existing connection needs migration, ask your setup assistant to follow [upgrade and relocation](maintenance.md). The assistant should stop the affected tunnel, prepare the same release and locked dependencies in a protected location, preserve your settings, service permissions, Keychain references and runtime records, and create a profile using the new absolute paths. It must use a protected Python distribution if the old interpreter has writable ancestors. After your review, it updates only the intended login-service registration and verifies the connection read-only before retiring the old installation. You do not need to run Terminal commands or change shared-drive permissions yourself.

Your setup assistant verifies the downloaded executable against the publisher's release information, explains the selected version and checksum, and records your approval. It can supply `--tunnel-client-path` and `--tunnel-client-sha256` when creating the nonsecret settings with `scripts/configure-local.py --account YOUR_ICLOUD_LOGIN --tunnel-id YOUR_TUNNEL_ID`. These options save the selection in the owner-only `local/settings.json`:

```json
{
  "tunnel_client_path": "/absolute/path/to/icloud-for-agents/tools/tunnel-client",
  "tunnel_client_sha256": "<publisher-verified lowercase SHA-256 of the executable>"
}
```

For an existing installation, the assistant can use `scripts/configure-local.py --set-tunnel-executable --tunnel-client-path PATH --tunnel-client-sha256 SHA256` after the same publisher verification and owner review. This preserves your other settings, access permissions and tunnel profile. You do not need to run Terminal commands yourself. A missing or mismatched checksum stops startup before Keychain access; the assistant must verify the selected release rather than silently trust the file currently at an old path.

The launcher validates the executable's ownership, permissions and recorded checksum, then copies those exact bytes and the validated nonsecret profile into an owner-private cache before retrieving a key. It runs those copies so replacing the original paths during a Keychain prompt cannot redirect the credential. The staged profile removes the tunnel credential through macOS's system launcher before starting the MCP Python interpreter, including its startup imports. The cache under `/private/tmp/icloud-for-agents-executables-UID` has mode 0700 and contains reviewed executable bytes plus nonsecret profiles; it contains no credentials. An inherited environment variable or PATH entry cannot select the executable. Configurations without an explicit path still select `tools/tunnel-client-v0.0.16/tunnel-client` and require its reviewed checksum; that legacy directory name is not a version-detection mechanism.

Run `.venv/bin/python scripts/start-private-tunnel.py --check` before explicit startup. If a newer release changes the supported command/profile format, stop and update/review the adapter rather than renaming the binary to imply compatibility. Keep a known-working installation while testing any upgrade.
