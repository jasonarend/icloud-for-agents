# Connect an HTTP-compatible assistant

iCloud for Agents supports local MCP clients and, optionally, clients that connect using Streamable HTTP. This HTTP connection works with compatible custom-built agents and hosted assistants, including Meta Muse. ChatGPT's existing private OpenAI tunnel is a separate connection.

The HTTP server runs on your Mac and listens only there. A client on another device or a hosted service also needs an HTTPS route it can reach. You and your assistant choose that route; enabling the local HTTP server alone does not publish it.

## Set up a connection

Ask an assistant with access to your Mac to help you:

1. Choose the client and which Apple services it may access. Each client can have no access, read access or full supported access to each service. Permission to manage other clients is a separate choice. The installation's service settings and approval rules still apply.
2. Choose how the client reaches your Mac. A private connection such as Tailscale Serve works only for clients that can reach your tailnet. A public HTTPS connection, such as a separately configured Cloudflare Tunnel, is reachable from the internet and protected by the client key. Your assistant should explain the provider, account or domain requirements, any costs and the information the client may receive.
3. Review the client access request in the local iCloud for Agents window. When the key window appears, copy the key directly into the client's secure credential field. Keep Apple passwords, client keys and tunnel tokens out of chat and ordinary configuration files.
4. Have the assistant start the approved connection and check that the client can authenticate and discover its tools. You can then choose a small read to verify the selected services if you want the client to receive that information.

For specific client instructions, see the [client guide](clients.md). Your assistant can follow the [technical HTTP setup guide](../reference/http-setup.md) for client-key management, startup and route configuration. No Terminal commands are required from you.

## Managing access

Ask your assistant to review, change, rotate or revoke a client's key. Each action needs your local approval. Revoking a key prevents new requests using it; it does not erase data already sent to the client or stop work already in progress. Clients with access to the same service share that service's configured account and selected sources; individual keys do not restrict specific Mail folders.

The Mac and the chosen connection must be running when a remote client needs access. The local HTTP server can optionally start at login. The Cloudflare launcher currently runs in the foreground and needs to be started again after it stops. Cloudflare and Tailscale are optional; use the route that your client can actually reach. A named Cloudflare tunnel requires a Cloudflare account and domain, and its full setup with this project has not yet been verified.

An AI client and its provider can receive the content returned by the tools you allow. A connection provider can process traffic passing through its service. Review the scope and providers before connecting.
