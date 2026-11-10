# Icons and branding

The project includes a transparent cloud icon for MCP metadata and client or directory artwork. Source and exported images are in [`docs/assets/branding/`](../assets/branding/). Keep the original `icon-source.png` when regenerating exports.

## Runtime icon

`src/icloud_for_agents/branding.py` loads the packaged 128 × 128 PNG through Python package resources and advertises it as a `data:image/png;base64,...` URI in `serverInfo.icons`. The descriptor uses `mimeType: "image/png"` and `sizes: ["128x128"]`. There is no theme restriction. Larger images are kept out of initialization responses.

The icon works without a public image host or access to the checkout. Clients choose whether and how to render server icons; some display their own connection artwork instead. The server name and version provide the text fallback. See [metadata](metadata.md) for the package, protocol and client-listing layers.

## Available exports

| File | Intended use |
| --- | --- |
| `icon-512.png` | General client or directory upload |
| `icon-1024.png` | Larger listing artwork |
| `icon-128.png`, `icon-256.png` | Compact client artwork |
| `icon-16.png`, `icon-32.png`, `icon-48.png`, `icon-64.png` | Small interface images |
| `favicon.ico` | Website favicon with 16, 32, 48 and 256 px entries |

Use the receiving client's supported upload format. A favicon is not an MCP icon. For directory submissions, follow the current directory schema: the official MCP Registry uses an HTTPS icon URL rather than the runtime data URI. Host the chosen export at a stable public URL, verify that it returns the image without authentication, and prefer a versioned or commit-pinned URL. Publishing the repository's image does not update external client listings automatically.

## Regenerate and check assets

On macOS, run:

```sh
.venv/bin/python scripts/build-branding.py
```

The script uses the built-in `sips` tool to preserve the square composition and transparency, copies the 128 px export into the runtime package, and builds the ICO with Python's standard library. It adds no runtime dependency.

Run the offline and stdio checks in [development](development.md). They verify initialization metadata, decoded image bytes and dimensions, including compatibility with older protocol revisions. Inspect the rendered images when changing the artwork. Before distribution, use `scripts/check-sdist.py` for the source archive and `scripts/check-branding-package.py` for the wheel; the source archive includes listing exports and the wheel includes the runtime PNG.

References: [MCP icons specification](https://modelcontextprotocol.io/specification/2026-07-28/basic#icons), [MCP Registry schema](https://github.com/modelcontextprotocol/registry/blob/main/docs/reference/server-json/draft/server.schema.json).
