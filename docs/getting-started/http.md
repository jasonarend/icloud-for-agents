# Optional HTTP connections

The optional Streamable HTTP transport lets a compatible MCP client connect to iCloud for Agents over HTTP. Stdio remains the default, and the ChatGPT private tunnel continues to use its existing connection. The HTTP server starts only when deliberately launched and listens on `127.0.0.1`, with an authenticated `/mcp` endpoint. It does not enable remote or public access on its own.

## Before connecting

Ask your assistant to review your existing installation and explain the client, the services and permissions that the HTTP instance will use. Approve each persistent client credential and any private remote connection separately. Keep your Apple credentials and HTTP client keys out of chat, URLs, logs and plaintext configuration. Your assistant should use a supported secure credential handoff; it must not claim a client is connected merely because it can discover the server.

HTTP clients connected to the same server share its configured services, access mode, approval policy and safeguards. A separate key identifies and revokes a client; it does **not** assign different tool permissions to that client. Existing local owner reviews and service-specific approvals still apply. Supported Mail read-state changes, archive and Trash restoration use the same durable operation and recovery checks over HTTP and stdio.

The processes serving the same account must use the same private runtime directory and load the updated code for the shared operation locks to coordinate. An older process that is already running does not acquire the new lock. Plan and approve a coordinated restart of all affected clients and services before using them together; do not assume updating the files changed an existing process. Separate runtime directories do not share locks, approval records or recovery state.

## Client keys

Each HTTP client needs its own randomly generated bearer key stored in macOS Keychain, separately from the Apple app-specific password and the ChatGPT tunnel credential. A local setup agent can create, rotate, revoke or hand off an individual key only after the owner's explicit approval for that action. It should use the `HttpKeys` local setup API and a trusted write-only pipe to an identified client or its secure credential manager. The key is not returned in tool results, printed, added to process arguments, stored in project settings or pasted into a conversation.

Keys are bound to the installation, its private runtime and effective service settings. A key cannot authenticate a differently configured instance. If those settings change, the assistant must arrange newly approved keys for the new configuration; it must not silently broaden existing client access. An old key can still be revoked from the same installation and runtime after a settings change. The setup agent must construct the key manager with the same validated settings environment as the server and retain each nonsecret client ID privately for later management. It can use `authorize_access` with separate owner approval to let the owner handle any Keychain prompt and verify noninteractive access for the server's Python identity. Changing the app or executable may require authorization again.

Key rotation replaces that client's key; revocation disables that client without changing other keys. The server checks Keychain on subsequent authenticated requests, so these actions do not require an HTTP server restart. An operation already authorized and in progress cannot be recalled. If Keychain is locked or access is unavailable, authentication fails until the owner resolves it. Secure delivery depends on the target client's actual credential interface; do not substitute a plaintext configuration file if the client lacks one.

During creation or rotation, the pipe delivers a candidate before Keychain activation completes. The setup agent must wait for the successful lifecycle result before using it. If activation fails or its outcome is uncertain, do not assume the candidate works or repeat the action without checking the Keychain state first.

## Start and verify

An assistant with access to the installation can launch `scripts/run-http.py` using the installation's Python environment and validated local settings. The default port is 8765; the port can be selected explicitly. It should first check for an existing server, avoid disrupting it, and use only the approved service configuration. The endpoint is stateless: each request authenticates separately, and reconnecting clients establish their own connection without reusing another client's session. Start with read-only service access and verify the actual client can authenticate, discover tools and complete bounded reads for the selected services. An HTTP listener or successful discovery alone does not establish access to Apple data.

## Private remote access

[Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve) can optionally provide an HTTPS connection from devices authorized on a tailnet to the loopback server. Tailscale is not a project dependency, and using it requires a separate review and approval of the tailnet, access rules, HTTPS configuration, identity, and exposure. Configure the HTTP server's exact `--proxy-host` to the approved Serve hostname, and continue to require the individual bearer key. Do not rely on forwarded identity headers as authorization. Access across a tailnet is not the same as a public endpoint; Tailscale Funnel and other public tunnels expose a different surface and require a separate security design and approval.

The consumer Meta Muse app is distinct from Muse Code. Meta documents custom connectors and a secure credential store, but those documents do not establish a verified Streamable HTTP connection or a private route from the consumer service to this Mac. Do not create a Muse credential, change its access or claim compatibility until its actual connection and secure credential handoff are confirmed. See [Meta Muse in the client guide](clients.md#meta-muse-consumer-app).
