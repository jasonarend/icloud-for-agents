"""Read-only identity evidence for a narrow UTC recurring-event workflow.

The provider attests recurrence membership. This module checks its exact resource,
version, and instance identity; it does not prove provider expansion completeness.
No expanded calendar document is suitable as a replacement series resource.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import re
from urllib.parse import urljoin, urlsplit
from xml.sax.saxutils import escape

from .calendar_recurrence import recurrence_rule, utc_datetime
from .core import SafetyError, digest
from .dav import NS, apple_url, xml_document


_LIMIT = 262144
_ETAG = re.compile(r'"[\x21\x23-\x7e]{1,200}"')
_COMMON = {"UID", "DTSTART", "DTEND", "DTSTAMP", "CREATED", "LAST-MODIFIED", "SEQUENCE",
           "SUMMARY", "DESCRIPTION", "LOCATION", "URL", "TRANSP", "CLASS", "X-ICLOUD-MCP-TIMEZONE"}


def _refuse():
    raise SafetyError("Occurrence identity proof is missing, changed, ambiguous, or unsupported")


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", value):
        _refuse()
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        _refuse()


def _parse(raw, *, master):
    if not isinstance(raw, str):
        _refuse()
    try:
        if len(raw.encode("utf-8")) > _LIMIT:
            _refuse()
    except UnicodeError:
        _refuse()
    if any((ord(c) < 32 and c not in "\r\n\t") or 127 <= ord(c) <= 159 for c in raw):
        _refuse()
    lines = re.sub(r"\r?\n[ \t]", "", raw).replace("\r\n", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if (not lines or len(lines) > 4096 or lines[0] != "BEGIN:VCALENDAR"
            or lines[-1] != "END:VCALENDAR"):
        _refuse()
    root, events, event = {}, [], None
    for line in lines[1:-1]:
        if not line or len(line.encode()) > 16384 or "\r" in line or "\t" in line or ":" not in line:
            _refuse()
        name, value = line.split(":", 1)
        if line == "BEGIN:VEVENT" and event is None:
            event = {}
        elif line == "END:VEVENT" and event is not None:
            events.append(event)
            event = None
            if len(events) > 200:
                _refuse()
        else:
            allowed = (_COMMON | ({"RRULE"} if master else {"RECURRENCE-ID"})) if event is not None else {"VERSION", "PRODID", "CALSCALE"}
            destination = event if event is not None else root
            if name not in allowed or name in destination:
                _refuse()
            destination[name] = value
    if event is not None or root.get("VERSION") != "2.0" or root.get("CALSCALE", "GREGORIAN") != "GREGORIAN":
        _refuse()
    if not events or (master and len(events) != 1):
        _refuse()
    for item in events:
        if not {"UID", "DTSTART", "DTEND"} <= set(item) or not 1 <= len(item["UID"]) <= 255:
            _refuse()
        start, end = _date(item["DTSTART"]), _date(item["DTEND"])
        if not timedelta(0) < end - start <= timedelta(days=366):
            _refuse()
        if item.get("X-ICLOUD-MCP-TIMEZONE", "UTC") not in {"UTC", "Etc/UTC"}:
            _refuse()
        if "RECURRENCE-ID" in item:
            _date(item["RECURRENCE-ID"])
    return events


def _master(raw):
    event = _parse(raw, master=True)[0]
    rule = event.get("RRULE", "")
    fields = {}
    for part in rule.split(";"):
        if part.count("=") != 1:
            _refuse()
        key, value = part.split("=")
        if key in fields or key not in {"FREQ", "INTERVAL", "COUNT", "UNTIL", "BYDAY"}:
            _refuse()
        fields[key] = value
    recurrence = {"frequency": fields.get("FREQ", "").lower()}
    for key, name in (("INTERVAL", "interval"), ("COUNT", "count")):
        if key in fields:
            if not re.fullmatch(r"[1-9][0-9]{0,3}", fields[key]):
                _refuse()
            recurrence[name] = int(fields[key])
    if "UNTIL" in fields:
        recurrence["until"] = _date(fields["UNTIL"]).isoformat()
    if "BYDAY" in fields:
        recurrence["weekdays"] = fields["BYDAY"].split(",")
    # Reuse the supported authoring grammar; accepting a projection never adds
    # permission to mutate an unfamiliar master recurrence representation.
    recurrence_rule({"timezone": "UTC", "start": _date(event["DTSTART"]).isoformat(),
                     "end": _date(event["DTEND"]).isoformat(), "recurrence": recurrence})
    return event


def _snapshot(snapshot):
    if (not isinstance(snapshot, dict) or set(snapshot) != {"etag", "sha256", "content"}
            or not isinstance(snapshot["etag"], str) or not _ETAG.fullmatch(snapshot["etag"])
            or not isinstance(snapshot["content"], str)):
        _refuse()
    try:
        raw = snapshot["content"].encode("utf-8")
    except UnicodeError:
        _refuse()
    if len(raw) > _LIMIT or hashlib.sha256(raw).hexdigest() != snapshot["sha256"]:
        _refuse()


def _text_property(propstat, name):
    matches = propstat.findall("d:prop/" + name, NS)
    if len(matches) != 1 or len(matches[0]) or matches[0].text is None:
        _refuse()
    return matches[0].text


def prove_occurrence(reader, collection, url, snapshot, occurrence_start):
    """Return repeatable JSON evidence for one original UTC occurrence start.

    ``snapshot`` is the full strong-ETag snapshot returned by DAVChanges.resource.
    Both reads use direct transport so redirects are refused. The exact resource
    is re-read after the projection, and callers must still condition any later
    write on the reviewed ETag. Membership is provider-attested, not independently
    expanded or a guarantee of complete recurrence results.
    """
    apple_url(collection, "cal")
    apple_url(url, "cal")
    if (not url.startswith(collection.rstrip("/") + "/")
            or any(segment in {".", ".."} for segment in urlsplit(url).path.split("/"))
            or re.search(r"%2e|%2f|%5c", urlsplit(url).path, re.I)):
        _refuse()
    if reader.known_collection("cal", collection) != collection:
        _refuse()
    _snapshot(snapshot)
    master = _master(snapshot["content"])
    target = utc_datetime(occurrence_start)
    initial = _date(master["DTSTART"])
    if target < initial:
        _refuse()
    try:
        bound = target + timedelta(seconds=1)
    except OverflowError:
        _refuse()
    first, last = [value.strftime("%Y%m%dT%H%M%SZ") for value in (target, bound)]
    body = ('<cal:calendar-multiget xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">'
            '<d:prop><d:getetag/><cal:calendar-data><cal:expand start="' + first + '" end="' + last +
            '"/></cal:calendar-data></d:prop><d:href>' + escape(urlsplit(url).path) + '</d:href></cal:calendar-multiget>')
    status, _, raw = reader.transport("REPORT", collection, body, 1)
    if status != 207:
        _refuse()
    try:
        root = xml_document(raw)
    except Exception:
        _refuse()
    if root.tag != "{DAV:}multistatus" or len(root) != 1 or root[0].tag != "{DAV:}response":
        _refuse()
    row = root[0]
    hrefs, parts = row.findall("d:href", NS), row.findall("d:propstat", NS)
    if (len(hrefs) != 1 or len(hrefs[0]) or not hrefs[0].text
            or urljoin(collection, hrefs[0].text) != url or len(parts) != 1 or len(row) != 2
            or len(parts[0].findall("d:prop", NS)) != 1 or len(parts[0].findall("d:status", NS)) != 1
            or len(parts[0]) != 2):
        _refuse()
    part = parts[0]
    status_element = part.find("d:status", NS)
    if (len(status_element) or not re.fullmatch(r"HTTP/\d(?:\.\d)? 200(?: .*)?", status_element.text or "")
            or len(part.find("d:prop", NS)) != 2):
        _refuse()
    etag = _text_property(part, "d:getetag")
    projection = _text_property(part, "cal:calendar-data")
    if etag != snapshot["etag"]:
        _refuse()
    instances = _parse(projection, master=False)
    duration = _date(master["DTEND"]) - initial
    identities, selected = set(), None
    for instance in instances:
        start = _date(instance["DTSTART"])
        original = _date(instance.get("RECURRENCE-ID", master["DTSTART"]))
        if (instance["UID"] != master["UID"] or start != original or start < initial
                or ("RECURRENCE-ID" not in instance and start != initial)
                or _date(instance["DTEND"]) - start != duration
                or not (start < bound and _date(instance["DTEND"]) > target)
                or original in identities):
            _refuse()
        identities.add(original)
        if start == target:
            selected = instance
    if selected is None:
        _refuse()
    # A projection may carry altered text. Require the unmodified simple-master
    # content as well as matching identity/timing before authorizing an override.
    ignored = {"RRULE", "RECURRENCE-ID", "DTSTART", "DTEND", "DTSTAMP", "CREATED", "LAST-MODIFIED", "SEQUENCE"}
    if {k: v for k, v in selected.items() if k not in ignored} != {k: v for k, v in master.items() if k not in ignored}:
        _refuse()
    status, headers, current = reader.transport("GET", url, "", 0)
    if (status != 200 or headers.get("etag") != snapshot["etag"] or not isinstance(current, bytes)
            or len(current) > _LIMIT or current != snapshot["content"].encode("utf-8")):
        _refuse()
    return {"collection": collection, "url": url, "etag": etag, "snapshot_sha256": snapshot["sha256"],
            "uid": master["UID"], "recurrence_id": first, "start": selected["DTSTART"], "end": selected["DTEND"],
            "window_start": first, "window_end": last, "selected_sha256": digest(selected),
            "projection_sha256": digest(instances), "projection_instances": len(instances),
            "membership": "provider_attested_original_utc_instance", "recurrence_completeness_verified": False}
