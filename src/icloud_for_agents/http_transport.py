"""Opt-in authenticated loopback MCP transport.

Client credentials are stored in separate Keychain items. Neither credentials
nor client requests belong in application logs.
"""

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import re
import threading
from .http_authorization import PRINCIPAL, ClientPermissions
from .http_keys import BEARER


PROXY_HOST_PATTERN = re.compile(r"(?=.{1,253}\Z)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}\Z")
AUTHENTICATION_CONCURRENCY = 4
AUTHENTICATION_TIMEOUT = 5.0
HTTP_CONCURRENCY = 64


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
        self._auth_slots = threading.BoundedSemaphore(AUTHENTICATION_CONCURRENCY)
        # Keychain can serialize or stall. Keep its bounded work off the shared
        # executor, which other application operations may also need.
        self._auth_executor = ThreadPoolExecutor(max_workers=AUTHENTICATION_CONCURRENCY,
                                                 thread_name_prefix="icloud-http-auth")

    async def _refuse(self, send, status):
        headers = [(b"cache-control", b"no-store")]
        headers.append((b"www-authenticate", b"Bearer") if status == 401 else (b"retry-after", b"1"))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": b"Unauthorized" if status == 401
                    else b"Authentication temporarily unavailable"})

    def _authenticate(self, candidate):
        try:
            return self._keys.authenticate(candidate)
        except Exception:
            # Handle native failures in the worker: a late exception must not
            # be logged by an abandoned or shielded asyncio future.
            return None

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            try:
                await self.app(scope, receive, send)
            finally:
                self._auth_executor.shutdown(wait=False, cancel_futures=True)
            return
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        values = [value for name, value in scope.get("headers", []) if name.lower() == b"authorization"]
        identity = None
        if len(values) == 1:
            scheme, separator, candidate = values[0].partition(b" ")
            if separator == b" " and scheme.lower() == b"bearer" and BEARER.fullmatch(candidate):
                if not self._auth_slots.acquire(blocking=False):
                    await self._refuse(send, 503)
                    return
                try:
                    worker = self._auth_executor.submit(self._authenticate, candidate)
                except Exception:
                    self._auth_slots.release()
                    await self._refuse(send, 503)
                    return
                # A canceled or timed-out HTTP request cannot stop a native
                # Keychain call. Release its slot only when that work finishes.
                worker.add_done_callback(lambda completed: self._auth_slots.release())
                authentication = asyncio.wrap_future(worker)
                try:
                    identity = await asyncio.wait_for(asyncio.shield(authentication),
                                                      timeout=AUTHENTICATION_TIMEOUT)
                except TimeoutError:
                    await self._refuse(send, 503)
                    return
                except Exception:
                    # Never expose Keychain or credential-bearing errors to clients or logs.
                    identity = None
        if identity is None:
            await self._refuse(send, 401)
            return
        token = PRINCIPAL.set(identity)
        try:
            downstream = dict(scope, headers=[(name, value) for name, value in scope.get("headers", [])
                                               if name.lower() != b"authorization"])

            async def private_send(message):
                if message["type"] == "http.response.start":
                    headers = [(name, value) for name, value in message.get("headers", [])
                               if name.lower() != b"cache-control"]
                    headers.append((b"cache-control", b"no-store"))
                    message = dict(message, headers=headers)
                await send(message)

            await self.app(downstream, receive, private_send)
        finally:
            PRINCIPAL.reset(token)


def create_app(server, keys, proxy_host=None):
    """Create a protected Streamable HTTP endpoint with Host/Origin checks."""
    if proxy_host is not None and not PROXY_HOST_PATTERN.fullmatch(proxy_host):
        raise ValueError("Proxy host must be an exact DNS hostname")
    if not getattr(server, "_icloud_client_permissions", False):
        server.add_middleware(ClientPermissions())
        server._icloud_client_permissions = True
    app = server.http_app(path="/mcp", transport="streamable-http", json_response=True,
                          stateless_http=True, host_origin_protection=True,
                          allowed_hosts=[proxy_host] if proxy_host else None,
                          allowed_origins=[f"https://{proxy_host}"] if proxy_host else None)
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
                access_log=False, proxy_headers=False, server_header=False, log_level="warning",
                limit_concurrency=HTTP_CONCURRENCY)


if __name__ == "__main__":
    main()
