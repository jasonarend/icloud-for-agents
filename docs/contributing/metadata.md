# Server and package metadata

This guide explains where project identity and version information come from, and what to update when changing them. Client setup is covered in the [client guide](../getting-started/clients.md).

## Sources of metadata

| Metadata | Source | Used by |
| --- | --- | --- |
| Server name, description and public URLs | `src/icloud_for_agents/metadata.py` | MCP initialization and metadata tests |
| Runtime version | `src/icloud_for_agents/version.py` | MCP `serverInfo.version` |
| Package name, version, description and project URLs | `pyproject.toml` | Installed Python distribution metadata |
| Runtime icon | `src/icloud_for_agents/branding.py` and `assets/icon-128.png` | MCP `serverInfo.icons` |
| Client listing name, version and artwork | The client's own connection or listing settings | That client's interface |

Keep the package version, runtime version and README version consistent. Update the changelog when preparing a release, following [the release workflow](releasing.md).

The MCP protocol revision is independent of the application version. A negotiated date such as `2026-07-28` identifies the protocol, while `serverInfo.version` identifies this project's release. A client's separately stored plugin version can differ from both.

## Implementation details

`server.py` constructs FastMCP with the runtime version, website URL and packaged icon. The current FastMCP constructor does not accept title and description, so the server assigns those public SDK attributes after construction. When updating FastMCP, check its API and retain protocol tests for the emitted metadata. This adapter affects presentation metadata; service selection and authorization are handled separately.

Use package resources for runtime assets. The installed package must work outside the checkout without reading `pyproject.toml` or fetching an image from the network. See [branding](branding.md) for asset sizes and formats.

## Validate a metadata change

Run the offline suite and both stdio checks from [development](development.md). They compare package/runtime identity and inspect actual initialization responses, including supported protocol revisions. Build a wheel and run:

```sh
.venv/bin/python scripts/check-branding-package.py dist/icloud_for_agents-VERSION.whl
```

Use the actual wheel filename. This checks the packaged metadata module, icon, package summary, version and project URLs against the source tree.

Installing an updated package does not restart an existing server or refresh a client's cached tool descriptions. Restart the affected connection when safe and use the client's refresh controls. For ChatGPT, follow [OpenAI's metadata refresh guidance](https://developers.openai.com/plugins/deploy/connect-chatgpt#refresh-metadata). Separately entered listing text, version or artwork may require its own update in that client's management interface.

## Directory publication

Directory manifests and plugin listings are separate from MCP initialization metadata. The repository does not include an MCP Registry `server.json` or a ChatGPT plugin package. Those files are not required to use the documented direct connections.

A contribution adding a directory listing should use its current schema, a verified namespace and a real installable package or reachable service. Include validation and publication instructions, and avoid embedding a user's private endpoint. [Branding](branding.md) describes the available images. Directory publication requires the maintainer's approval.

References: [MCP implementation metadata](https://modelcontextprotocol.io/specification/2026-07-28/basic), [MCP Registry schema](https://github.com/modelcontextprotocol/registry/blob/main/docs/reference/server-json/draft/server.schema.json), [Python project URLs](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/#urls).
