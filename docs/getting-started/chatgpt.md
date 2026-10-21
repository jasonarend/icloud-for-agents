# Connect privately to ChatGPT

ChatGPT does not automatically inherit a local server installed on your Mac. This project uses an owner-controlled Secure MCP Tunnel to its stdio server; it does not expose a public HTTP endpoint.

1. Confirm your account/workspace provides **Add custom MCP server** and the **Tunnel** option. Availability and UI vary. Use your own Platform organization and your own tunnel; never reuse another person's tunnel or keys.
2. Follow [OpenAI's Secure MCP Tunnel documentation](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels). Install the official client from a verified official source. Do not infer pricing or billing consent from this guide; inspect current terms before accepting any charge.
3. Use only the runtime permissions needed for the tunnel. Use Tunnels Read and Use where supported, with unrelated permissions disabled. Enter the runtime key personally into a **separate** Keychain item: service `icloud-for-agents-tunnel-runtime`, account your exact tunnel ID. Never paste the key into chat or a tracked file.
4. Configure your own validated local tunnel profile and nonsecret settings, as described in [background setup](background-service.md). Run `scripts/start-private-tunnel.py --check` through the project interpreter first. Starting with `--start-with-keychain` retrieves the existing item; it does not create it.
5. In ChatGPT's custom MCP UI select your private tunnel. Enter a descriptive name and description. Use the authentication options actually supported by that tunnel flow; do not invent a URL or claim standalone OAuth support. Review the actions and final connection approval personally.
6. Verify actual read-only calls, not only a local tools list. With all three services selected, the base schema has 18 tools; optional policy management adds five Mail policy tools. Tool registration and live access switches are distinct.

Use the client's supported **Refresh tools** control after a schema change. Depending on the account surface this may appear under app management. Do not delete/recreate a working connection merely to refresh it. Verify actual calls after refresh; discovery and existing conversation catalogs can differ.

The Python package version and a custom app wrapper version are independent. Follow the client's supported release workflow rather than assuming source installation updates listing metadata.

[OpenAI custom MCP guidance](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt) is the source for current account-facing behavior. This private connection is not a public plugin listing; see [distribution](../contributing/releasing.md).
