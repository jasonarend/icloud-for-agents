"""Explicit human-approved calendar/contact batches; never implicit scheduling mail."""
import base64
from datetime import datetime, timezone
import hashlib
import http.client
import re
import ssl
from urllib.parse import urlsplit
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .version import __version__
from .core import SafetyError
from .dav import DAVReader, apple_url


class DAVWriteRefused(SafetyError):
    def __init__(self, status):
        self.http_status = int(status)
        super().__init__("Conditional DAV request refused; no automatic retry")



def unfold(value):
    if len(value.encode()) > 262144 or "\x00" in value:
        raise SafetyError("DAV resource exceeds bounded review size")
    return re.sub(r"\r?\n[ \t]", "", value).replace("\r\n", "\n").splitlines()


def prop(line):
    # vCard groups (item1.EMAIL) qualify a property; they do not change its name.
    return line.split(":", 1)[0].split(";", 1)[0].rsplit(".", 1)[-1].upper()


def escaped(value):
    if not isinstance(value, str) or len(value) > 10000 or "\x00" in value or "\r" in value:
        raise SafetyError("Invalid bounded DAV field text")
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")


def replace_properties(lines, replacements, end):
    begin = "BEGIN:" + end.split(":", 1)[1]
    result, active, depth = [], False, 0
    for line in lines:
        if line == begin:
            active, depth = True, 1
            result.append(line)
            continue
        if active and line == end and depth == 1:
            result.extend(item for values in replacements.values() for item in values)
            result.append(line)
            active = False
            continue
        if active and depth == 1 and prop(line) in replacements:
            continue
        result.append(line)
        if active and line.startswith("BEGIN:"):
            depth += 1
        elif active and line.startswith("END:"):
            depth -= 1
    folded = []
    for line in result:
        current = ""
        for character in line:
            if len((current + character).encode()) > 75:
                folded.append(current)
                current = " "
            current += character
        folded.append(current)
    return "\r\n".join(folded) + "\r\n"


def personal_event(raw):
    lines = unfold(raw)
    if lines.count("BEGIN:VEVENT") != 1 or lines.count("END:VEVENT") != 1 or "BEGIN:VCALENDAR" not in lines:
        raise SafetyError("Only one ordinary event resource per change is supported")
    if any(prop(line) in {"ATTENDEE", "ORGANIZER", "METHOD"} for line in lines):
        raise SafetyError("Attendee/organizer events require a separate invitation/cancellation workflow; automatic scheduling is disabled")
    if any(prop(line) in {"RRULE", "RDATE", "EXDATE", "RECURRENCE-ID"} for line in lines):
        raise SafetyError("Recurring-event changes need separate series/occurrence review; not supported by this workflow")
    return lines


def event_content(fields, before=None):
    if not isinstance(fields, dict) or not fields or set(fields) - {"summary", "start", "end", "description", "location", "timezone", "url", "free", "private"}:
        raise SafetyError("Use explicit summary/start/end/description/location/timezone/url/free/private fields")
    if before is None and not {"summary", "start", "end"} <= set(fields):
        raise SafetyError("New events need summary, start, and end")
    if ("start" in fields) != ("end" in fields):
        raise SafetyError("Supply start and end together")
    if "timezone" in fields and "start" not in fields:
        raise SafetyError("Named timezone changes require explicit start and end")
    replacements = {"DTSTAMP": ["DTSTAMP:" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")]}
    if "start" in fields:
        # RFC 5545 permits DTEND or DURATION, never both on the same VEVENT.
        # replace_properties removes this only at event depth, preserving alarms.
        replacements["DURATION"] = []
        try:
            values = [datetime.fromisoformat(fields[k].replace("Z", "+00:00")) for k in ("start", "end")]
            if any(v.tzinfo is None for v in values) or not 0 < (values[1]-values[0]).total_seconds() <= 366*86400:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise SafetyError("Event dates need explicit timezones and a positive duration <=366 days") from None
        if "timezone" in fields:
            try:
                zone_name = fields["timezone"]
                if not isinstance(zone_name, str) or len(zone_name)>100:
                    raise ValueError()
                zone = ZoneInfo(zone_name)
                if any(value.utcoffset() != value.astimezone(zone).utcoffset() for value in values):
                    raise ValueError()
            except (ZoneInfoNotFoundError, ValueError, TypeError):
                raise SafetyError("Use an IANA timezone matching both explicit date offsets") from None
            # UTC timestamps avoid floating-time/DST ambiguity; the validated
            # authoring zone is retained as explicit metadata for read-back.
            replacements["X-ICLOUD-MCP-TIMEZONE"] = ["X-ICLOUD-MCP-TIMEZONE:" + escaped(zone_name)]
        for name, value in zip(("DTSTART", "DTEND"), values):
            replacements[name] = [name + ":" + value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")]
    for field, name in (("summary", "SUMMARY"), ("description", "DESCRIPTION"), ("location", "LOCATION")):
        if field in fields:
            replacements[name] = [name + ":" + escaped(fields[field])]
    if "url" in fields:
        value = fields["url"]
        if not isinstance(value,str) or not 1 <= len(value) <= 2048 or any(ord(c)<33 or ord(c)>126 for c in value):
            raise SafetyError("Use a bounded escaped HTTPS event URL")
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None,443):
            raise SafetyError("Use an HTTPS event URL without embedded credentials")
        replacements["URL"] = ["URL:" + value]
    for field, prop_name, true_value, false_value in (("free","TRANSP","TRANSPARENT","OPAQUE"),("private","CLASS","PRIVATE","PUBLIC")):
        if field in fields:
            if type(fields[field]) is not bool:
                raise SafetyError("free/private event fields must be boolean")
            replacements[prop_name] = [prop_name + ":" + (true_value if fields[field] else false_value)]
    if before is None:
        lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//iCloud for Agents//Independent Project//EN",
                 "BEGIN:VEVENT", "UID:"+uuid.uuid4().hex, "END:VEVENT", "END:VCALENDAR"]
    else:
        lines = personal_event(before)
    return replace_properties(lines, replacements, "END:VEVENT")


def contact_content(fields, before=None):
    if not isinstance(fields, dict) or not fields or set(fields)-{"full_name", "emails", "phones", "organization", "note"}:
        raise SafetyError("Use explicit full_name/emails/phones/organization/note contact fields")
    if before is None and not fields.get("full_name"):
        raise SafetyError("A saved contact needs full_name")
    replacements = {}
    for field, name in (("full_name", "FN"), ("organization", "ORG"), ("note", "NOTE")):
        if field in fields:
            replacements[name] = [name + ":" + escaped(fields[field])]
    for field, name in (("emails", "EMAIL"), ("phones", "TEL")):
        if field in fields:
            values = fields[field]
            if not isinstance(values, list) or len(values) > 20:
                raise SafetyError("At most 20 explicit contact addresses/phones")
            for value in values:
                if not isinstance(value, str) or len(value)>254 or any(c in value for c in "\r\n\x00"):
                    raise SafetyError("Invalid contact address/phone")
                if field == "emails" and not re.fullmatch(r"[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+", value):
                    raise SafetyError("Contact emails must be bare explicit addresses")
            replacements[name] = [name + ":" + escaped(value) for value in values]
    if before is None:
        lines = ["BEGIN:VCARD", "VERSION:3.0", "UID:"+uuid.uuid4().hex, "N:;;;;", "END:VCARD"]
    else:
        lines = unfold(before)
        if lines.count("BEGIN:VCARD") != 1 or lines.count("END:VCARD") != 1:
            raise SafetyError("Only one vCard resource per contact change")
    return replace_properties(lines, replacements, "END:VCARD")


class DAVChanges:
    def __init__(self, reader, store, writer=None):
        self.reader, self.store = reader, store
        self.writer = writer or self._write

    def resource(self, kind, url):
        apple_url(url, kind)
        status, headers, raw = self.reader.transport("GET", url, "", 0)
        if status != 200 or len(raw) > 262144:
            raise SafetyError("Existing DAV resource read failed or is too large; no write attempted")
        etag = headers.get("etag", "")
        if not re.fullmatch(r'"[^"\r\n]{1,200}"', etag):
            raise SafetyError("A strong current ETag is required for safe conditional DAV changes")
        return {"etag": etag, "sha256": hashlib.sha256(raw).hexdigest(), "content": raw.decode("utf-8")}

    def prepare(self, kind, collection, changes):
        if kind not in {"cal", "card"} or not isinstance(changes, list) or not 1 <= len(changes) <= 20:
            raise SafetyError("Prepare one explicit batch of 1..20 calendar/contact changes")
        collection = self.reader.known_collection(kind, collection)
        prepared, identities = [], set()
        for change in changes:
            if not isinstance(change, dict) or set(change)-{"operation", "url", "fields", "irreversible_confirmed"}:
                raise SafetyError("Invalid DAV change fields")
            operation = change.get("operation")
            if operation not in {"create", "update", "delete"}:
                raise SafetyError("DAV operation must be create, update, or delete")
            if operation == "delete" and change.get("irreversible_confirmed") is not True:
                raise SafetyError("DAV deletion has no protocol Trash; explicitly acknowledge irreversible deletion before human review")
            if operation == "create" and change.get("url") is not None:
                raise SafetyError("Create resource identity is assigned by the reviewed proposal")
            url = (collection.rstrip('/')+'/'+uuid.uuid4().hex+('.ics' if kind=='cal' else '.vcf')) if operation=='create' else change.get("url", "")
            apple_url(url, kind)
            if not url.startswith(collection.rstrip('/') + '/') or any(segment in {'.','..'} for segment in urlsplit(url).path.split('/')) or re.search(r'%2e|%2f|%5c', urlsplit(url).path, re.I):
                raise SafetyError("Resource must remain inside the exact discovered collection")
            if url in identities:
                raise SafetyError("Duplicate resource in one batch")
            identities.add(url)
            before = None if operation=='create' else self.resource(kind, url)
            if before and kind=='cal':
                personal_event(before['content'])
            content = None
            if operation != 'delete':
                builder = event_content if kind=='cal' else contact_content
                content = builder(change.get('fields'), before['content'] if before else None)
            prepared.append({"operation": operation, "url": url, "fields": change.get('fields'),
                             "before": before, "content": content,
                             "deletion": "irreversible DAV DELETE; private pre-change snapshot retained" if operation=='delete' else None})
        payload={"account":self.reader.account,"kind":kind,"collection":collection,"changes":prepared,
                 "external_notifications":"none; attendee/organizer events and scheduling methods refused",
                 "recovery":"No protocol Trash. Snapshots remain in private local proposal files; provider recovery/import is separate and not guaranteed."}
        return self.store.prepare('calendar_change' if kind=='cal' else 'contact_change', payload)

    def _write(self, kind, item):
        url = apple_url(item['url'], kind)
        parsed = urlsplit(url)
        headers = {"Authorization": "Basic "+base64.b64encode((self.reader.account+':'+self.reader._password).encode()).decode(),
                   "Content-Type": ('text/calendar' if kind=='cal' else 'text/vcard')+'; charset=utf-8',
                   "User-Agent":"iCloud-MCP-Bridge/" + __version__}
        if item['operation']=='create':headers['If-None-Match']='*'
        else:headers['If-Match']=item['before']['etag']
        # RFC6638 section8.1 defines this header for attendee DELETE,
        # not ordinary personal-event PUT. Attendee resources remain refused.
        if kind=='cal' and item['operation']=='delete':headers['Schedule-Reply']='F'
        connection=http.client.HTTPSConnection(parsed.hostname,timeout=30,context=ssl.create_default_context())
        try:
            connection.request('DELETE' if item['operation']=='delete' else 'PUT',parsed.path,
                               body=(item['content'] or '').encode(),headers=headers)
            response=connection.getresponse()
            response.read(4096)
            if response.status not in {200,201,204}:
                raise DAVWriteRefused(response.status)
            return {"url":url,"operation":item['operation'],"status":"applied"}
        finally:connection.close()

    def execute(self, plan_id):
        plan=self.store.consume(plan_id,self.reader.account)
        completed=[]
        phase="collection_preflight"
        try:
            if plan['kind'] not in {'calendar_change','contact_change'}:
                raise SafetyError("Not a calendar/contact proposal")
            payload=plan['payload'];kind=payload['kind']
            self.reader.known_collection(kind,payload['collection'])
            # Verify all original identities before the first mutation; conditional
            # requests still protect the smaller race after this preflight.
            phase="identity_preflight"
            for item in payload['changes']:
                if item['before'] and self.resource(kind,item['url']) != item['before']:
                    raise SafetyError("DAV resource changed since human review; batch stopped")
            if plan.get("reconciliation_of"):
                phase="reconciled_absence_preflight"
                if kind != 'cal' or len(payload['changes']) != 1 or payload['changes'][0]['operation'] != 'create':
                    raise SafetyError("Invalid reconciled request")
                status, _, _ = self.reader.transport('GET', payload['changes'][0]['url'], '', 0)
                if status != 404:
                    raise SafetyError("Reconciled resource not definitely absent; no retry")
            phase="conditional_write"
            for item in payload['changes']:
                completed.append(self.writer(kind,item))
            result={"status":"applied","completed":completed,"do_not_retry":True}
        except Exception as exc:
            diagnostic={"phase":phase,"category":"http_refused" if isinstance(exc,DAVWriteRefused) else "safety_precondition" if isinstance(exc,SafetyError) else "transport_or_internal_error"}
            if isinstance(exc,DAVWriteRefused):diagnostic["http_status"]=exc.http_status
            result={"status":"failed_or_uncertain","completed":completed,"do_not_retry":True,"diagnostic":diagnostic,
                    "message":"Inspect exact resources before a new proposal; no automatic replay/rollback"}
        return self.store.finish(plan_id,result)


def register_change_tools(mcp, safe, service, *, calendar=True, contacts=True):
    write={"readOnlyHint":False,"destructiveHint":True,"idempotentHint":False,"openWorldHint":True}
    def selected(active):
        return mcp.tool(annotations=write) if active else lambda fn: fn
    def editor():
        owner=service()
        return DAVChanges(DAVReader(owner.account,owner._password),owner.store)

    @selected(calendar)
    @safe
    def calendar_prepare_changes(calendar_url: str, changes: list[dict]) -> dict:
        """Prepare <=20 exact create/update/delete personal-event changes for one human batch approval; no invitations."""
        return editor().prepare('cal',calendar_url,changes)

    @selected(contacts)
    @safe
    def contacts_prepare_changes(addressbook_url: str, changes: list[dict]) -> dict:
        """Prepare <=20 exact contact create/update/delete changes for one human batch approval; writes nothing yet."""
        return editor().prepare('card',addressbook_url,changes)

    @selected(calendar or contacts)
    @safe
    def dav_execute_approved(plan_id: str) -> dict:
        """Execute one exact Calendar/Contacts plan under the configured approval policy; refuse changed/replayed targets."""
        return editor().execute(plan_id)
