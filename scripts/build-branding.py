#!/usr/bin/env python3
"""Export approved branding at standard sizes using macOS's built-in sips.

This is a maintainer-only asset build, not an installation or runtime step.
It preserves the approved square composition and alpha; no generative edits.
"""

from pathlib import Path
import shutil
import struct
import subprocess


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs/assets/branding"
SIZES = (16, 32, 48, 64, 128, 256, 512, 1024)


def main():
    source = ASSETS / "icon-source.png"
    if not source.is_file():
        raise SystemExit("Missing approved icon source")
    if not Path("/usr/bin/sips").is_file():
        raise SystemExit("Asset regeneration requires macOS sips; committed assets work on all platforms")
    for size in SIZES:
        subprocess.run(["/usr/bin/sips", "--resampleHeightWidth", str(size), str(size),
                        "--setProperty", "format", "png", str(source),
                        "--out", str(ASSETS / f"icon-{size}.png")], check=True, capture_output=True)
    package_assets = ROOT / "src/icloud_for_agents/assets"
    package_assets.mkdir(exist_ok=True)
    shutil.copyfile(ASSETS / "icon-128.png", package_assets / "icon-128.png")

    # Standard ICO directory with PNG-compressed entries, supported by current
    # browsers and Windows Vista+. PNG is the MCP/directory format, not ICO.
    icon_sizes = (16, 32, 48, 256)
    images = [(ASSETS / f"icon-{size}.png").read_bytes() for size in icon_sizes]
    offset = 6 + 16 * len(images)
    entries = []
    for size, image in zip(icon_sizes, images):
        entries.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(image), offset))
        offset += len(image)
    (ASSETS / "favicon.ico").write_bytes(
        struct.pack("<HHH", 0, 1, len(images)) + b"".join(entries) + b"".join(images))
    print("Exported PNG sizes 16–1024, packaged MCP icon and multi-size favicon.ico")


if __name__ == "__main__":
    main()
