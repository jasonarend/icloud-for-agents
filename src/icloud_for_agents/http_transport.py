"""Opt-in authenticated loopback MCP transport.

Client credentials are stored in separate Keychain items. Neither credentials
nor client requests belong in application logs.
"""

import argparse
import asyncio
import os
from pathlib import Path
import re


PROXY_HOST_PATTERN = re.compile(r"(?=.{1,253}\Z)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}\Z")


def validated_environment(source):
    """Rebuild service settings from the owned installation, not caller overrides."""
    from .local_config import read_settings, child_environment
    root = source.get("ICLOUD_CONFIG_ROOT", "")
    if not root or not Path(root).is_absolute():
        raise ValueError("HTTP requires an absolute installation root")
    root = Path(root).resolve(strict=True)
    environment = child_environment(read_settings(root), source)
    environment["ICLOUD_CONFIG_ROOT"] = str(root)
    return environment


class BearerGate:
    """Authenticate every HTTP request, including session reconnects and DELETE."""

    def __init__(self, app, keys):
        self.app = app
        self._keys = keys

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        values = [value for name, value in scope.get("headers", []) if name.lower() == b"authorization"]
        identity = None
        if len(values) == 1:
            scheme, separator, candidate = values[0].partition(b" ")
            if separator == b" " and scheme.lower() == b"bearer":
                try:
                    identity = await asyncio.to_thread(self._keys.authenticate, candidate)
                except Exception:
                    # Never expose Keychain or credential-bearing errors to clients or logs.
                    identity = None
        if identity is None:
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"www-authenticate", b"Bearer"), (b"cache-control", b"no-store")]})
            await send({"type": "http.response.body", "body": b"Unauthorized"})
            return
        await self.app(scope, receive, send)


def create_app(server, keys, proxy_host=None):
    """Create a protected Streamable HTTP endpoint with Host/Origin checks."""
    if proxy_host is not None and not PROXY_HOST_PATTERN.fullmatch(proxy_host):
        raise ValueError("Proxy host must be an exact DNS hostname")
    app = server.http_app(path="/mcp", transport="streamable-http", json_response=True,
                          stateless_http=True, host_origin_protection=True,
                          allowed_hosts=[proxy_host] if proxy_host else None)
    return BearerGate(app, keys)


def main():
    parser = argparse.ArgumentParser(description="Run an opt-in loopback MCP HTTP server")
    parser.add_argument("--port", type=int, default=8765, help="Loopback listening port (default: 8765)")
    parser.add_argument("--proxy-host", help="Exact DNS hostname used by an authorized private HTTPS proxy")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if args.proxy_host is not None and not PROXY_HOST_PATTERN.fullmatch(args.proxy_host):
        parser.error("proxy host must be an exact DNS hostname")
    try:
        environment = validated_environment(os.environ)
    except Exception:
        parser.exit(1, "HTTP requires a valid, private installation configuration\n")
    os.environ.clear()
    os.environ.update(environment)
    from .http_keys import HttpKeys
    try:
        keys = HttpKeys()
    except Exception:
        parser.exit(1, "HTTP Keychain support is unavailable\n")
    # Set before importing the server so transport-specific write restrictions
    # are enforced independently of client-supplied arguments or headers.
    os.environ["ICLOUD_HTTP_TRANSPORT"] = "1"
    from .server import mcp
    import uvicorn
    uvicorn.run(create_app(mcp, keys, args.proxy_host), host="127.0.0.1", port=args.port,
                access_log=False, proxy_headers=False, server_header=False, log_level="warning")


if __name__ == "__main__":
    main()
