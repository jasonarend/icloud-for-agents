"""Icon resources and standards metadata, without imaging dependencies or I/O to services."""

import base64
import hashlib
from importlib.resources import files
from pathlib import Path
import struct
import unittest
import zlib

from icloud_for_agents.branding import WEBSITE_URL, server_icons

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs/assets/branding"


def png_dimensions(image):
    assert image[:8] == b"\x89PNG\r\n\x1a\n"
    offset = 8
    chunks = []
    dimensions = None
    while offset < len(image):
        length = struct.unpack(">I", image[offset:offset + 4])[0]
        kind = image[offset + 4:offset + 8]
        payload = image[offset + 8:offset + 8 + length]
        checksum = struct.unpack(">I", image[offset + 8 + length:offset + 12 + length])[0]
        assert checksum == zlib.crc32(kind + payload)
        if kind == b"IHDR":
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", payload)
            assert depth == 8 and color == 6, "Expected RGBA PNG with transparency"
            assert compression == filtering == interlace == 0
            dimensions = (width, height)
        chunks.append(kind)
        offset += 12 + length
    assert offset == len(image) and chunks[0] == b"IHDR" and chunks[-1] == b"IEND"
    assert b"IDAT" in chunks
    return dimensions


def assert_server_branding(info):
    """Shared wire-level assertion for old and current SDK protocol smoke tests."""
    assert info["name"] == "iCloud for Agents"
    assert info["websiteUrl"] == WEBSITE_URL
    icon, = info["icons"]
    assert icon["mimeType"] == "image/png"
    assert icon["sizes"] == ["128x128"]
    assert icon.get("theme") is None
    prefix = "data:image/png;base64,"
    assert icon["src"].startswith(prefix)
    image = base64.b64decode(icon["src"][len(prefix):], validate=True)
    assert image == files("icloud_for_agents").joinpath("assets", "icon-128.png").read_bytes()
    assert png_dimensions(image) == (128, 128)
    assert len(icon["src"]) < 24_000, "Keep initialization metadata small"


class BrandingTests(unittest.TestCase):
    def test_mcp_metadata_uses_packaged_offline_png(self):
        assert_server_branding({"name": "iCloud for Agents", "websiteUrl": WEBSITE_URL,
                                "icons": [icon.model_dump(by_alias=True, exclude_none=True)
                                          for icon in server_icons()]})

    def test_fresh_descriptors_do_not_share_mutable_metadata(self):
        icons = server_icons()
        icons[0].sizes.append("1x1")
        self.assertEqual(server_icons()[0].sizes, ["128x128"])

    def test_approved_source_is_preserved(self):
        image = (ASSETS / "icon-source.png").read_bytes()
        self.assertEqual(hashlib.sha256(image).hexdigest(),
                         "3dd8a1090bfacd9b04e0c22353d18a4a8ac43822c1036f51c694915dd7369257")
        self.assertEqual(png_dimensions(image), (1254, 1254))

    def test_exported_dimensions_and_packaged_copy(self):
        for size in (16, 32, 48, 64, 128, 256, 512, 1024):
            with self.subTest(size=size):
                self.assertEqual(png_dimensions((ASSETS / f"icon-{size}.png").read_bytes()), (size, size))
        self.assertEqual((ASSETS / "icon-128.png").read_bytes(),
                         files("icloud_for_agents").joinpath("assets", "icon-128.png").read_bytes())

    def test_favicon_entries_match_png_exports(self):
        image = (ASSETS / "favicon.ico").read_bytes()
        self.assertEqual(struct.unpack("<HHH", image[:6]), (0, 1, 4))
        next_offset = 6 + 4 * 16
        for index, size in enumerate((16, 32, 48, 256)):
            width, height, colors, reserved, planes, depth, length, offset = struct.unpack(
                "<BBBBHHII", image[6 + index * 16:22 + index * 16])
            self.assertEqual((width or 256, height or 256), (size, size))
            self.assertEqual((colors, reserved, planes, depth), (0, 0, 1, 32))
            self.assertEqual(offset, next_offset)
            self.assertEqual(image[offset:offset + length], (ASSETS / f"icon-{size}.png").read_bytes())
            next_offset += length
        self.assertEqual(next_offset, len(image))
