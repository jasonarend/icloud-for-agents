"""Portable MCP branding, available offline from installed package resources."""

import base64
from functools import lru_cache
from importlib.resources import files

from mcp.types import Icon

# Retain this import for callers of the original branding module.
from .metadata import WEBSITE_URL


@lru_cache(maxsize=1)
def _icon_uri() -> str:
    image = files("icloud_for_agents").joinpath("assets", "icon-128.png").read_bytes()
    return "data:image/png;base64," + base64.b64encode(image).decode("ascii")


def server_icons() -> list[Icon]:
    """One small, theme-neutral PNG, without a network or local-file dependency.

    PNG and data URIs are part of the MCP icon contract (2025-11-25+). Clients
    without icon support still use the server's existing name and version.
    Higher-resolution images for directory uploads live in docs/assets/branding.
    """
    return [Icon(src=_icon_uri(), mimeType="image/png", sizes=["128x128"])]
