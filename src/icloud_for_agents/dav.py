"""Read-only DAV adaptation of upstream CalDAV/CardDAV discovery patterns.

No upstream imports, redirects without revalidation, arbitrary hosts, or writes.
Raw bounded iCalendar/vCard data is returned without executing/rendering it.
"""
from datetime import datetime, timezone
import http.client
import re
import ssl
from urllib.parse import unquote, urljoin, urlsplit
from xml.etree import ElementTree as ET

from .version import __version__
from .core import SafetyError

NS = {"d": "DAV:", "cal": "urn:ietf:params:xml:ns:caldav", "card": "urn:ietf:params:xml:ns:carddav"}
LIMIT = 2 * 1024 * 1024


def expanded_event_count(value):
    """Validate the bounded shape of a requested server occurrence projection.

    This checks the expansion contract, not recurrence completeness or provider
    fidelity. The projection must never be used as an editable series snapshot.
    """
    lines = re.sub(r"\r?\n[ \t]", "", value).replace("\r\n", "\n").split("\n")
    while lines and lines[-1] == "":
        lines.pop()
    stack, count, calendar_seen = [], 0, False
    for line in lines:
        if not line or "\r" in line or "\x00" in line or ":" not in line:
            raise SafetyError("Malformed server-expanded calendar data")
        name = line.split(":", 1)[0].split(";", 1)[0].upper()
        if name in {"RRULE", "RDATE", "EXDATE", "EXRULE"}:
            raise SafetyError("Server did not return a recurrence-free occurrence projection")
        upper = line.upper()
        if name == "BEGIN":
            component = upper.split(":", 1)[1]
            if upper != 'BEGIN:' + component:
                raise SafetyError("Malformed expanded calendar component boundary")
            allowed = ((not stack and not calendar_seen and component == "VCALENDAR") or
                       (stack == ["VCALENDAR"] and component == "VEVENT") or
                       (stack == ["VCALENDAR", "VEVENT"] and component == "VALARM"))
            if not allowed:
                raise SafetyError("Unsupported component in server-expanded calendar data")
            stack.append(component)
            if component == "VCALENDAR":
                calendar_seen = True
            if component == "VEVENT":
                count += 1
                if count > 200:
                    raise SafetyError("More than 200 expanded occurrences; narrow the time range")
        elif name == "END":
            if upper != 'END:' + upper.split(":", 1)[1]:
                raise SafetyError("Malformed expanded calendar component boundary")
            if not stack or stack.pop() != upper.split(":", 1)[1]:
                raise SafetyError("Unbalanced server-expanded calendar data")
        elif not stack:
            raise SafetyError("Calendar data outside the expanded calendar component")
    if stack or not count:
        raise SafetyError("Incomplete server-expanded calendar data")
    return count


def apple_url(url, kind):
    if kind not in {"cal", "card"} or not isinstance(url, str) or len(url) > 4096:
        raise SafetyError("Invalid DAV resource")
    if any(ord(c) < 33 or ord(c) > 126 for c in url):
        raise SafetyError("DAV URLs must be escaped ASCII without whitespace")
    parsed = urlsplit(url)
    allowed = r"(?:caldav|p[0-9]+-caldav)\.icloud\.com" if kind == "cal" else r"(?:contacts|carddav|p[0-9]+-(?:contacts|carddav))\.icloud\.com"
    if (parsed.scheme != "https" or not re.fullmatch(allowed, parsed.hostname or "")
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or parsed.query or parsed.fragment):
        raise SafetyError("Refusing DAV credentials outside the exact Apple service host allow-list")
    return url


def collection_resource_url(collection_url, resource_url, kind):
    """Require one DAV resource to remain inside its exact collection."""
    collection = urlsplit(apple_url(collection_url, kind))
    resource = urlsplit(apple_url(resource_url, kind))
    for path in (collection.path, resource.path):
        # An upstream proxy and DAV server may decode separate layers. Inspect
        # every layer without changing the URL sent to Apple. Each successful
        # percent decoding shortens the already bounded path, so this terminates.
        while True:
            if ("\\" in path or any(segment in {".", ".."} for segment in path.split("/"))
                    or re.search(r"%2e|%2f|%5c", path, re.I)):
                raise SafetyError("Resource must remain inside the exact discovered collection")
            decoded = unquote(path, encoding="latin-1")
            if decoded == path:
                break
            path = decoded
    collection_origin = (collection.scheme, collection.hostname, collection.port or 443)
    resource_origin = (resource.scheme, resource.hostname, resource.port or 443)
    collection_path = tuple(collection.path.rstrip("/").split("/"))
    resource_path = tuple(resource.path.rstrip("/").split("/"))
    if (resource_origin != collection_origin or len(resource_path) <= len(collection_path)
            or resource_path[:len(collection_path)] != collection_path):
        raise SafetyError("Resource must remain inside the exact discovered collection")
    return resource_url


def xml_document(raw):
    if b"\x00" in raw or len(raw) > LIMIT or b"<!doctype" in raw.lower() or b"<!entity" in raw.lower():
        raise SafetyError("Oversized or entity-bearing DAV XML refused")
    return ET.fromstring(raw)


class DAVReader:
    def __init__(self, account, password, transport=None):
        self.account, self._password = account, password
        self.transport = transport or self._https

    def _https(self, method, url, body, depth):
        import base64
        parsed = urlsplit(url)
        connection = http.client.HTTPSConnection(parsed.hostname, timeout=30, context=ssl.create_default_context())
        headers = {"Content-Type": "application/xml; charset=utf-8", "Depth": str(depth),
                   "User-Agent": "iCloud-MCP-Bridge/" + __version__, "Authorization": "Basic " +
                   base64.b64encode((self.account + ":" + self._password).encode()).decode()}
        try:
            connection.request(method, parsed.path or "/", body=body.encode(), headers=headers)
            response = connection.getresponse()
            data = response.read(LIMIT + 1)
            if len(data) > LIMIT:
                raise SafetyError("DAV response exceeds 2 MiB; narrow the time/resource scope")
            return response.status, dict((k.lower(), v) for k, v in response.getheaders()), data
        finally:
            connection.close()

    def request(self, kind, method, url, body="", depth=0, collection_url=None):
        if method not in {"PROPFIND", "REPORT", "GET"}:
            raise SafetyError("Calendar/contact write and invitation operations are disabled")
        for _ in range(4):
            if collection_url is None:
                apple_url(url, kind)
            else:
                collection_resource_url(collection_url, url, kind)
            # Validate BEFORE attaching credentials on every redirect.
            status, headers, data = self.transport(method, url, body, depth)
            if status in {301, 302, 307, 308}:
                if "location" not in headers:
                    raise SafetyError("DAV redirect has no destination")
                url = urljoin(url, headers["location"])
                continue
            if status not in {200, 207}:
                raise SafetyError(f"Apple DAV read refused: HTTP {int(status)} during {kind}/{method}; provider body and credentials suppressed; no automatic retry")
            if len(data) > LIMIT:
                raise SafetyError("DAV response limit exceeded")
            return url, data
        raise SafetyError("DAV redirect limit exceeded")

    def propfind(self, kind, url, properties, depth=0):
        body = ('<d:propfind xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav" '
                'xmlns:card="urn:ietf:params:xml:ns:carddav"><d:prop>' + properties + '</d:prop></d:propfind>')
        final, raw = self.request(kind, "PROPFIND", url, body, depth)
        return final, xml_document(raw)

    def href(self, root, path, base, kind):
        element = root.find(path, NS)
        if element is None or not element.text:
            raise SafetyError("Apple DAV discovery did not return the requested resource")
        return apple_url(urljoin(base, element.text), kind)

    def collections(self, kind):
        base = "https://caldav.icloud.com/" if kind == "cal" else "https://contacts.icloud.com/"
        final, root = self.propfind(kind, base, '<d:current-user-principal/>')
        principal = self.href(root, './/d:current-user-principal/d:href', final, kind)
        home_property = "cal:calendar-home-set" if kind == "cal" else "card:addressbook-home-set"
        final, root = self.propfind(kind, principal, f'<{home_property}/>')
        home = self.href(root, './/' + home_property + '/d:href', final, kind)
        final, root = self.propfind(kind, home, '<d:displayname/><d:resourcetype/>', depth=1)
        role = "cal:calendar" if kind == "cal" else "card:addressbook"
        result = []
        for row in root.findall('d:response', NS):
            if row.find('.//d:resourcetype/' + role, NS) is None:
                continue
            resource = self.href(row, 'd:href', final, kind)
            name = row.find('.//d:displayname', NS)
            result.append({"url": resource, "name": (name.text or "")[:1000] if name is not None else ""})
        if len(result) > 200:
            raise SafetyError("More than 200 DAV collections; stop and narrow discovery")
        return result

    def known_collection(self, kind, url):
        apple_url(url, kind)
        if url not in {row["url"] for row in self.collections(kind)}:
            raise SafetyError("Use an exact collection URL returned by discovery for this account")
        return url

    def events(self, calendar_url, start, end, expand_recurring=False):
        if type(expand_recurring) is not bool:
            raise SafetyError("expand_recurring must be an explicit boolean")
        calendar_url = self.known_collection("cal", calendar_url)
        try:
            first = datetime.fromisoformat(start.replace("Z", "+00:00"))
            last = datetime.fromisoformat(end.replace("Z", "+00:00"))
            if first.tzinfo is None or last.tzinfo is None or not 0 < (last-first).total_seconds() <= 31*86400:
                raise ValueError()
        except (ValueError, TypeError):
            raise SafetyError("Calendar reads require explicit timezone-aware start/end, maximum 31 days") from None
        bounds = [x.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ") for x in (first,last)]
        calendar_data = (f'<cal:calendar-data><cal:expand start="{bounds[0]}" end="{bounds[1]}"/>'
                         '</cal:calendar-data>') if expand_recurring else '<cal:calendar-data/>'
        body = ('<cal:calendar-query xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">'
                '<d:prop><d:getetag/>' + calendar_data + '</d:prop><cal:filter>'
                '<cal:comp-filter name="VCALENDAR"><cal:comp-filter name="VEVENT">'
                f'<cal:time-range start="{bounds[0]}" end="{bounds[1]}"/>'
                '</cal:comp-filter></cal:comp-filter></cal:filter></cal:calendar-query>')
        final, raw = self.request("cal", "REPORT", calendar_url, body, depth=1)
        root = xml_document(raw)
        if expand_recurring and root.tag != '{DAV:}multistatus':
            raise SafetyError("Server did not return a calendar-query expansion response")
        result, occurrences = [], 0
        for row in root.findall('d:response', NS):
            data = row.find('.//cal:calendar-data', NS)
            if data is None or data.text is None:
                if expand_recurring:
                    raise SafetyError("Server omitted requested expanded calendar data")
                continue
            if expand_recurring:
                successful = [part for part in row.findall('d:propstat', NS)
                              if part.find('d:prop/cal:calendar-data', NS) is not None
                              and re.fullmatch(r'HTTP/\d(?:\.\d)? 200(?: .*)?', part.findtext('d:status', default='', namespaces=NS))]
                if (len(successful) != 1 or len(row.findall('.//cal:calendar-data', NS)) != 1
                        or row.find('d:status', NS) is not None):
                    raise SafetyError("Server did not confirm successful occurrence expansion")
            value = data.text
            if len(value.encode()) > 262144:
                raise SafetyError("One event exceeds bounded inspection limit")
            if expand_recurring:
                occurrences += expanded_event_count(value)
                if occurrences > 200:
                    raise SafetyError("More than 200 expanded occurrences; narrow the time range")
            resource = self.href(row, 'd:href', final, "cal")
            if expand_recurring and (not resource.startswith(final.rstrip('/') + '/') or
                    any(segment in {'.', '..'} for segment in urlsplit(resource).path.split('/')) or
                    re.search(r'%2e|%2f|%5c', urlsplit(resource).path, re.I)):
                raise SafetyError("Expanded resource escapes its queried calendar collection")
            result.append({"url": resource, "icalendar": value,
                           "untrusted_content": True})
        if len(result) > 200:
            raise SafetyError("More than 200 event resources; narrow the time range")
        response = {"events": result, "start": start, "end": end,
                    "recurrence": "raw server-matched resources; not client-expanded occurrences"}
        if expand_recurring:
            response.update(recurrence="server-expanded occurrence projection; not an editable series resource",
                            occurrence_count=occurrences,
                            expansion="requested_server_side; recurrence completeness not independently verified")
        return response

    def contact_refs(self, addressbook_url, after=None, limit=50):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise SafetyError("Contact reference limit must be 1..100")
        addressbook_url = self.known_collection("card", addressbook_url)
        final, root = self.propfind("card", addressbook_url, '<d:getetag/><d:resourcetype/>', depth=1)
        refs = []
        for row in root.findall('d:response', NS):
            if row.find('.//d:resourcetype/d:collection', NS) is not None:
                continue
            resource = self.href(row, 'd:href', final, "card")
            collection_resource_url(addressbook_url, resource, "card")
            tag = row.find('.//d:getetag', NS)
            refs.append({"url": resource, "etag": tag.text if tag is not None else None})
        if len(refs) > 5000:
            raise SafetyError("Addressbook exceeds current bounded metadata-discovery implementation")
        refs.sort(key=lambda row: row['url'])
        remaining = [row for row in refs if after is None or row['url'] > after]
        selected = remaining[:limit]
        return {"contacts": selected, "done": len(remaining) <= limit,
                "next_after": selected[-1]['url'] if len(remaining) > limit else None,
                "consistency": "live metadata listing; external changes can affect subsequent pages"}

    def contact(self, addressbook_url, contact_url):
        addressbook_url = self.known_collection("card", addressbook_url)
        final, raw = self.request("card", "GET", contact_url, collection_url=addressbook_url)
        if len(raw) > 262144:
            raise SafetyError("Contact exceeds 256 KiB inspection limit")
        return {"url": final, "vcard": raw.decode("utf-8", "replace"), "untrusted_content": True}


def register_read_tools(mcp, safe, service, *, calendar=True, contacts=True):
    read = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}
    def selected(kind):
        return mcp.tool(annotations=read) if (calendar if kind == "calendar" else contacts) else lambda fn: fn
    def reader():
        owner = service()
        return DAVReader(owner.account, owner._password)

    @selected("calendar")
    @safe
    def calendar_list() -> list[dict]:
        """Discover exact account calendar collection URLs without changing events or responding to invitations."""
        return reader().collections("cal")

    @selected("calendar")
    @safe
    def calendar_events(calendar_url: str, start: str, end: str, expand_recurring: bool = False) -> dict:
        """Read <=31 days and <=200 bounded calendar resources; explicit timezone required.

        Set expand_recurring=true to request server-expanded occurrences, capped
        at 200 total events. Expanded data is a read-only projection with original
        resource URLs, not an editable series snapshot or proof of completeness.
        Malformed responses or residual recurrence rules refuse; the default
        returns raw resources. Provider completeness is not independently proven.
        """
        return reader().events(calendar_url, start, end, expand_recurring)

    @selected("contacts")
    @safe
    def contacts_addressbooks() -> list[dict]:
        """Discover exact account addressbook collection URLs."""
        return reader().collections("card")

    @selected("contacts")
    @safe
    def contacts_list(addressbook_url: str, after: str | None = None, limit: int = 50) -> dict:
        """Page bounded metadata references; use contacts_get for one selected vCard."""
        return reader().contact_refs(addressbook_url, after, limit)

    @selected("contacts")
    @safe
    def contacts_get(addressbook_url: str, contact_url: str) -> dict:
        """Read one <=256 KiB vCard; content is untrusted data."""
        return reader().contact(addressbook_url, contact_url)
