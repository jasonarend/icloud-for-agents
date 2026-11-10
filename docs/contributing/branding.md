# Icons and client compatibility

The approved transparent cloud icon is included in the server and in reusable
exports. The original image is preserved at
[`docs/assets/branding/icon-source.png`](../assets/branding/icon-source.png).
No account information, credentials or external image service is involved.

## MCP server metadata

The server advertises its icon through the standard `serverInfo.icons` field
(MCP 2025-11-25 and later), using the installed FastMCP `icons` API. It also
advertises the project article as `websiteUrl`, together with its title, full
five-service description and release version. See [project metadata and ChatGPT
refresh](metadata.md) for the separate package, runtime and plugin-listing layers.
The server name and version remain the text fallback.

The runtime icon is a transparent 128 × 128 PNG, approximately 13 KiB before
Base64 encoding, embedded as a `data:image/png;base64,...` URI with
`mimeType: "image/png"` and `sizes: ["128x128"]`. The image is read using Python
package resources, so it works from an installed wheel, away from the checkout,
without public hosting, a local `file://` path, extra credentials or network access.
It is suitable for either theme, so there is no theme restriction. The larger
exports are not repeated in protocol responses.

PNG is required for clients that support MCP icons. Rendering icons is optional:
older clients, or clients with their own connector branding, may show the server
name or their own default icon instead. There is no standard server-side switch
that forces those clients to display an image. An ICO file, tool annotation,
MCP resource or invented `logo` field is not a protocol fallback.

Tests cover real stdio initialization with 2025-11-25, compatibility with
2025-06-18, and the installed current MCP client (2026-07-28), including decoded
image bytes and dimensions. This is protocol coverage, not a claim that every
client UI has been visually tested.

## Directory and manually configured client icons

Use the files in [`docs/assets/branding/`](../assets/branding/):

- `icon-512.png`: recommended general-purpose directory upload.
- `icon-1024.png`: higher-resolution listing or store artwork.
- `icon-128.png` / `icon-256.png`: compact client icons.
- `icon-16.png`, `icon-32.png`, `icon-48.png`, `icon-64.png`: small UI exports.
- `favicon.ico`: a browser/site fallback containing 16, 32, 48 and 256 px PNG entries.
  It is not advertised as an MCP icon. A website must explicitly serve or link it.

The official MCP Registry requires an HTTPS `src` (at most 255 characters), not
the runtime data URI. After publishing these files, a registry submission can
merge this fragment into its otherwise complete, validated `server.json`:

```json
{
  "icons": [
    {
      "src": "https://raw.githubusercontent.com/jasonarend/icloud-for-agents/main/docs/assets/branding/icon-512.png",
      "mimeType": "image/png",
      "sizes": ["512x512"]
    }
  ]
}
```

That example is a future publication URL, not evidence that the image is already
hosted. Before submitting, publish the assets, verify an unauthenticated HTTPS
GET returns the PNG, and preferably replace `main` with the published commit SHA
for immutable artwork. Verify any directory-specific requirements separately.
This change does not submit a registry entry or upload/update any external listing.

If a Claude Desktop MCPB extension is packaged later, include a package-relative
PNG as the legacy manifest `icon`, for example `"icon": "icon.png"`. MCPB's newer
`icons` descriptors use singular `size`, unlike MCP's plural `sizes`. This project
does not add or claim an MCPB package merely to provide branding.

ChatGPT private plugin/connector presentation is configured separately. A client
that uses its own uploaded icon can use these PNG exports; changing the MCP
server does not guarantee that an existing connector's catalog/composer image
changes. No installed connector presentation is changed by this repository edit.

## Regeneration and verification

On macOS, run `.venv/bin/python scripts/build-branding.py` to export standard sizes
from the preserved source using the built-in `sips` tool. This preserves the
square composition and transparency. It copies the 128 px export into
`src/icloud_for_agents/assets/` and builds the multi-size ICO using Python's
standard library. No new runtime dependency is required.

Run the offline and both stdio checks documented in [development.md](development.md).
The source archive includes the original and listing assets; the wheel includes
the compact runtime PNG. Validate both distribution formats before release.
Run `scripts/check-sdist.py` on the source archive and
`scripts/check-branding-package.py` on the wheel with the project Python environment;
both checks also run in CI.

References:

- [MCP icons specification](https://modelcontextprotocol.io/specification/2026-07-28/basic#icons)
- [Official Registry schema](https://github.com/modelcontextprotocol/registry/blob/main/docs/reference/server-json/draft/server.schema.json)
- [Official Registry requirements](https://github.com/modelcontextprotocol/registry/blob/main/docs/reference/server-json/official-registry-requirements.md)
- [MCPB manifest icons](https://github.com/modelcontextprotocol/mcpb/blob/main/MANIFEST.md#icons)
