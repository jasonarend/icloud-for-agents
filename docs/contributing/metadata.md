# Project metadata and ChatGPT refresh

There are separate metadata layers. Updating one does not publish or refresh the
others.

## Repository, Python package and MCP server

`src/icloud_for_agents/metadata.py` holds the public server name, description and
URLs. The package metadata in `pyproject.toml` must match it. Tests check this
without accessing credentials or services.

- **Homepage / MCP `websiteUrl`:**
  <https://jasonarend.com/news/icloud-for-agents-apple-ai-assistants/>
- **Repository:** <https://github.com/jasonarend/icloud-for-agents>
- **Documentation:** <https://github.com/jasonarend/icloud-for-agents#readme>
- **Issues:** <https://github.com/jasonarend/icloud-for-agents/issues>
- **Changelog:** <https://github.com/jasonarend/icloud-for-agents/blob/main/CHANGELOG.md>
- **Description:** Connect AI assistants to iCloud Mail, Calendar and Contacts,
  plus Apple Notes and Reminders on macOS, with explicit access controls.

The MCP server advertises its name, title, description, release version, website
and [packaged icon](branding.md). The version comes from
`src/icloud_for_agents/version.py`, not a hardcoded `1.0` and not the MCP protocol
date. Keep the Python package, runtime and README versions in sync. Update the
dated changelog when preparing a release under [the release workflow](releasing.md).

The reviewed FastMCP 4.x constructor supports version, website and icons but does
not expose the SDK's title and description arguments. `server.py` sets those two
public SDK attributes immediately after construction. Real stdio tests cover
this adapter and the emitted metadata across supported protocol versions. This
adds presentation information only; it does not change enabled services,
authorization, tool permissions or approval requirements.

`[project.urls]` also puts the links in built Python distribution metadata. Merely
editing the checkout does not rebuild an installed wheel, restart an existing
process, publish a package, change GitHub's About settings or publish the website.
The homepage is the intended public destination; setting it is not a deployment
or availability check.

## Why ChatGPT can still show version 1.0 or an old description

A ChatGPT custom MCP connection has its own plugin/app listing. Its entered name
and description, plugin release version and uploaded image are separate from the
MCP server's `serverInfo` and Python package version. Do not change the server's
version to match a listing, or assume a `1.0` listing means the bridge is running
version 1.0. Check the actual MCP initialization metadata to diagnose the runtime.

For a custom MCP connection, OpenAI's documented metadata refresh is:

1. Install or deploy the intended server revision and restart the affected server
   or tunnel process when safe to interrupt its active sessions.
2. Open the existing connection in [ChatGPT Plugins](https://chatgpt.com/plugins).
3. Select **Refresh** and confirm the advertised metadata has changed.
4. Start a new conversation and test the affected tools.

Refreshing pulls tool descriptions and server instructions. It is not a promise
that separately stored listing text, plugin release version or artwork will be
replaced. If those remain old, update the existing plugin's presentation through
its available management controls. Published plugin information changes follow
the plugin's version/review/publication workflow. Keep the existing connection,
audience and access scope unless a separate change is intended. Recreating a
connection is not the default troubleshooting step.

This repository does not contain an installed ChatGPT plugin manifest or publish
one automatically. Updating the repository does not change the user's installed
plugin, restart a live tunnel or grant new access.

## MCP Registry and other directories

An official MCP Registry `server.json` is a separate publication manifest, not
an MCP initialization response and not a ChatGPT plugin manifest. It needs a
verified registry namespace, a release version and a real installable package or
remote endpoint, in addition to its description, repository and website fields.
Validate that full manifest against the Registry's current schema before
publishing. Do not invent a package release or expose a private tunnel endpoint
just to populate a listing.

This checkout does not add a speculative `server.json` or submit a listing.
[Branding documentation](branding.md) describes the HTTPS icon requirement and
available directory exports. Publish and verify those assets before referring
to them from an external listing. Standard MCP clients can use server metadata
without any registry submission.

## Checks

Run the [offline and both stdio suites](development.md) after editing metadata.
The offline tests compare package/runtime values; stdio checks assert the name,
title, current release version, description and homepage from actual MCP
initialization/discovery. After building a wheel, run
`scripts/check-branding-package.py` to verify the packaged metadata module, icon,
summary, version and project URLs. Neither test establishes how a particular
client renders its separately stored plugin listing.

References:

- [OpenAI: add a custom MCP server](https://developers.openai.com/api/docs/guides/custom-mcp-server#how-to-use)
- [OpenAI: refresh custom MCP metadata](https://developers.openai.com/plugins/deploy/connect-chatgpt#refresh-metadata)
- [MCP implementation metadata](https://modelcontextprotocol.io/specification/2026-07-28/basic)
- [MCP Registry schema](https://github.com/modelcontextprotocol/registry/blob/main/docs/reference/server-json/draft/server.schema.json)
- [Python project URLs](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/#urls)
