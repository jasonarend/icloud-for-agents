"""Isolated real HTTP server using synthetic per-client keys and no Apple data."""
import hmac
import sys

from icloud_for_agents import http_keys, http_transport

TOKEN = b"ika_" + b"a" * 32 + b"." + b"b" * 43
SECOND_TOKEN = b"ika_" + b"c" * 32 + b"." + b"d" * 43


class SyntheticKeys:
    def authenticate(self, token):
        if hmac.compare_digest(token, TOKEN):
            return {"client_id": "first", "permissions": {s: "full" for s in ("mail", "calendar", "contacts", "notes", "reminders")}, "manage_clients": False}
        if hmac.compare_digest(token, SECOND_TOKEN):
            return {"client_id": "second", "permissions": {s: "read" for s in ("mail", "calendar", "contacts", "notes", "reminders")}, "manage_clients": False}
        return None


http_keys.HttpKeys = SyntheticKeys
sys.argv = [sys.argv[0], "--port", sys.argv[1], "--proxy-host", "synthetic.example"]
http_transport.main()
