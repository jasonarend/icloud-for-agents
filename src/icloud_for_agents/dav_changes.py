"""Explicit human-approved calendar/contact batches; never implicit scheduling mail."""
import base64
from datetime import datetime, timezone
import hashlib
import http.client
import os
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


def event_content(fields, before=None, *, recurring_master=False, timezone_definition=None):
    recurrence = None
    local_recurrence = None
    if isinstance(fields, dict) and "recurrence" in fields:
        if before is not None and not recurring_master:
            raise SafetyError("Recurring event editing requires a separate explicit occurrence or series workflow")
        from .calendar_recurrence import recurrence_rule, zoned_datetime
        recurrence = recurrence_rule(fields)
        if fields["timezone"] not in {"UTC", "Etc/UTC"}:
            if timezone_definition is None:
                raise SafetyError("Named-zone recurrence requires the selected calendar's matching timezone definition")
            from .calendar_timezone import extract_timezone_definition, validate_timezone_coverage, validate_wall_duration
            component = extract_timezone_definition(timezone_definition, fields["timezone"])
            local_start = zoned_datetime(fields["start"], fields["timezone"])
            local_end = zoned_datetime(fields["end"], fields["timezone"])
            validate_timezone_coverage(timezone_definition, fields["timezone"], local_start)
            validate_wall_duration(timezone_definition, fields["timezone"], local_start, local_end)
            local_recurrence = (fields["timezone"], local_start, local_end, component)
        fields = {key: value for key, value in fields.items() if key != "recurrence"}
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
    elif recurring_master:
        from .calendar_recurrence import validate_series_deletion, verify_created
        validate_series_deletion(before)
        verify_created(before, before)
        lines = unfold(before)
        if lines.count("BEGIN:VEVENT") != 1 or lines.count("END:VEVENT") != 1:
            raise SafetyError("Series updates require canonical event boundaries")
        if recurrence is not None:
            from .calendar_recurrence import detach_timezone
            from .calendar_occurrence_changes import EVENT_PROPERTIES
            lines, old_component = detach_timezone(lines)
            if local_recurrence is not None and old_component:
                from .calendar_timezone import timezone_verification_key
                zone, local_start, local_end, component = local_recurrence
                old_ids = [line[5:] for line in old_component if line.startswith('TZID:')]
                if old_ids == [zone]:
                    def wrapped(parts):
                        return '\r\n'.join(['BEGIN:VCALENDAR', 'VERSION:2.0', *parts, 'END:VCALENDAR', ''])
                    if timezone_verification_key(wrapped(old_component), zone) == timezone_verification_key(wrapped(component), zone):
                        # Preserve valid provider history when the new calendar
                        # definition has the same modern transition rules.
                        local_recurrence = (zone, local_start, local_end, list(old_component))
            start, end = lines.index("BEGIN:VEVENT"), lines.index("END:VEVENT")
            event_properties, in_alarm = [], False
            for line in lines[start + 1:end]:
                if line.upper() == "BEGIN:VALARM":
                    in_alarm = True
                elif line.upper() == "END:VALARM":
                    in_alarm = False
                elif not in_alarm:
                    event_properties.append(line)
            if any(prop(line) not in EVENT_PROPERTIES for line in event_properties):
                raise SafetyError("Series rescheduling requires supported simple event properties")
    else:
        lines = personal_event(before)
    if recurrence is not None:
        replacements["RRULE"] = ["RRULE:" + recurrence]
    if local_recurrence is not None:
        zone, start, end, component = local_recurrence
        replacements["DTSTART"] = ["DTSTART;TZID=" + zone + ":" + start.strftime("%Y%m%dT%H%M%S")]
        replacements["DTEND"] = ["DTEND;TZID=" + zone + ":" + end.strftime("%Y%m%dT%H%M%S")]
        index = lines.index("BEGIN:VEVENT")
        lines[index:index] = component
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

    def prepare(self, kind, collection, changes, *, temporary=False, deletion_authorization='local-receipt'):
        if type(temporary) is not bool or (temporary and kind != 'cal'):
            raise SafetyError("Temporary approval is available only for an exact Calendar proposal")
        if kind not in {"cal", "card"} or not isinstance(changes, list) or not 1 <= len(changes) <= 20:
            raise SafetyError("Prepare one explicit batch of 1..20 calendar/contact changes")
        if not isinstance(deletion_authorization, str) or deletion_authorization not in {'local-receipt', 'trusted-client'}:
            raise SafetyError("Use the configured local receipt or explicit trusted-client deletion authorization")
        if deletion_authorization == 'trusted-client':
            if (not temporary or kind != 'cal' or len(changes) != 1 or not isinstance(changes[0], dict)
                    or changes[0].get('operation') not in {'delete', 'delete_occurrence'}
                    or changes[0].get('irreversible_confirmed') is not True
                    or os.environ.get('ICLOUD_APPROVAL_POLICY', 'local-receipt') != 'trusted-client'):
                raise SafetyError("Client-authorized deletion requires one exact Calendar deletion and an enabled trusted-client policy")
        collection = self.reader.known_collection(kind, collection)
        if any(isinstance(change, dict) and isinstance(change.get("fields"), dict)
               and "recurrence" in change["fields"] for change in changes):
            if (kind != "cal" or len(changes) != 1 or not
                    (changes[0].get("operation") == "create" or
                     (changes[0].get("operation") == "update" and changes[0].get("recurrence_scope") == "series"))):
                raise SafetyError("Prepare each recurring event creation or rescheduling as a separate Calendar proposal")
        prepared, identities = [], set()
        for change in changes:
            if not isinstance(change, dict) or set(change)-{"operation", "url", "fields", "irreversible_confirmed", "recurrence_scope", "occurrence_start"}:
                raise SafetyError("Invalid DAV change fields")
            operation = change.get("operation")
            if operation not in {"create", "update", "delete", "delete_occurrence"}:
                raise SafetyError("DAV operation must be create, update, or delete")
            recurrence_scope = change.get("recurrence_scope")
            if "recurrence_scope" in change and (kind != "cal" or
                    not ((recurrence_scope == "series" and operation in {"update", "delete"}) or
                         (recurrence_scope == "occurrence" and operation in {"update", "delete_occurrence"}))):
                raise SafetyError("Use an explicit supported series or occurrence operation")
            if (operation == "delete_occurrence" and recurrence_scope != "occurrence") or (
                    "occurrence_start" in change and recurrence_scope != "occurrence"):
                raise SafetyError("Occurrence changes require explicit occurrence scope and identity")
            if recurrence_scope == "occurrence" and (len(changes) != 1 or not isinstance(change.get("occurrence_start"), str)):
                raise SafetyError("Prepare one occurrence change with its original UTC start")
            if recurrence_scope == "series" and operation == "update":
                fields = change.get("fields")
                allowed = {"summary", "description", "location", "url", "free", "private"}
                schedule = {"start", "end", "timezone", "recurrence"}
                if (not isinstance(fields, dict) or not fields or set(fields) - allowed - schedule
                        or (set(fields) & schedule and not schedule <= set(fields))):
                    raise SafetyError("Whole-series rescheduling requires complete start, end, timezone and recurrence fields")
            if operation in {"delete", "delete_occurrence"} and change.get("irreversible_confirmed") is not True:
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
                if recurrence_scope in {"series", "occurrence"}:
                    from .calendar_recurrence import validate_series_deletion
                    validate_series_deletion(before['content'])
                else:
                    personal_event(before['content'])
            content = None
            timezone_binding = None
            occurrence_proof = None
            if operation != 'delete':
                if kind == 'cal':
                    timezone_definition = None
                    fields = change.get('fields')
                    if recurrence_scope == 'occurrence':
                        from .calendar_occurrence_proof import prove_occurrence
                        from .calendar_occurrence_changes import occurrence_content
                        occurrence_proof = prove_occurrence(self.reader, collection, url, before, change['occurrence_start'])
                        content = occurrence_content(before['content'], operation, change['occurrence_start'], fields)
                    if (isinstance(fields, dict) and 'recurrence' in fields
                            and isinstance(fields.get('timezone'), str) and fields['timezone'] not in {'UTC', 'Etc/UTC'}):
                        from .calendar_recurrence import calendar_timezone, recurrence_rule, zoned_datetime
                        from .calendar_timezone import validate_timezone_coverage, validate_wall_duration
                        recurrence_rule(fields)
                        timezone_definition = calendar_timezone(self.reader, collection, fields['timezone'])
                        timezone_binding = {'name': fields['timezone'], 'sha256': hashlib.sha256(timezone_definition.encode()).hexdigest(),
                                            'coverage': validate_timezone_coverage(timezone_definition, fields['timezone'],
                                                                                   zoned_datetime(fields['start'], fields['timezone'])),
                                            'duration': validate_wall_duration(timezone_definition, fields['timezone'],
                                                                               zoned_datetime(fields['start'], fields['timezone']),
                                                                               zoned_datetime(fields['end'], fields['timezone']))}
                    if recurrence_scope != 'occurrence':
                        content = event_content(change.get('fields'), before['content'] if before else None,
                                                recurring_master=recurrence_scope == 'series',
                                                timezone_definition=timezone_definition)
                else:
                    content = contact_content(change.get('fields'), before['content'] if before else None)
            item = {"operation": operation, "url": url, "fields": change.get('fields'),
                    "before": before, "content": content,
                    "deletion": ("irreversible exclusion of one occurrence using DAV PUT; private pre-change snapshot retained"
                                 if operation == 'delete_occurrence' else
                                 "irreversible DAV DELETE of the entire recurring series; private pre-change snapshot retained"
                                 if recurrence_scope == "series" and operation == 'delete' else
                                 "irreversible DAV DELETE; private pre-change snapshot retained" if operation=='delete' else None)}
            if recurrence_scope is not None:
                item["recurrence_scope"] = recurrence_scope
            if deletion_authorization == 'trusted-client':
                item['irreversible_confirmed'] = True
            if occurrence_proof is not None:
                item['occurrence_start'] = change['occurrence_start']
                item['occurrence_proof'] = occurrence_proof
            if timezone_binding is not None:
                item['timezone_binding'] = timezone_binding
            if (kind == 'cal' and isinstance(change.get('fields'), dict)
                    and 'recurrence' in change['fields']):
                item['recurrence_effects'] = [
                    'The rule applies to the entire series and continues indefinitely without a count or end.',
                    'Monthly or yearly dates that do not exist may be skipped.',
                    'Local occurrences in daylight-saving gaps may be skipped; future timezone rules can change.',
                    'Timezone comparisons are bounded and do not prove every future occurrence.',
                ]
                if operation == 'update':
                    item['recurrence_effects'].append('This replaces the schedule for the entire series, including past occurrences; count starts at the new first occurrence.')
            prepared.append(item)
        payload={"account":self.reader.account,"kind":kind,"collection":collection,"changes":prepared,
                 "external_notifications":"no attendee scheduling; changes may synchronize to devices and shared collections",
                 "recovery":"No protocol Trash. Snapshots remain in private local proposal files; provider recovery/import is separate and not guaranteed."}
        if temporary:
            payload['temporary_elevation'] = 'calendar-exact-batch-once'
            payload['temporary_elevation_effect'] = 'One approved exact Calendar batch; saved access mode remains unchanged.'
        if deletion_authorization == 'trusted-client':
            payload['calendar_deletion_authority'] = 'trusted-client-exact-v1'
            payload['calendar_deletion_authority_effect'] = ('The connected client must obtain the owner approval for this exact irreversible deletion; '
                                                             'this is not an independently verified human receipt.')
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
        candidate = self.store.proposal(plan_id)
        if candidate['kind'] not in {'calendar_change', 'contact_change'}:
            raise SafetyError('Not a calendar/contact proposal; use the matching service executor')
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
                if item.get('occurrence_proof'):
                    phase = 'occurrence_preflight'
                if item['before'] and self.resource(kind,item['url']) != item['before']:
                    raise SafetyError("DAV resource changed since human review; batch stopped")
                if item.get('occurrence_proof'):
                    from .calendar_occurrence_proof import prove_occurrence
                    if prove_occurrence(self.reader, payload['collection'], item['url'], item['before'], item['occurrence_start']) != item['occurrence_proof']:
                        raise SafetyError("Occurrence evidence changed since review; prepare a new proposal")
                    phase = 'identity_preflight'
                if item.get('timezone_binding'):
                    phase = "timezone_preflight"
                    from .calendar_recurrence import calendar_timezone, zoned_datetime
                    from .calendar_timezone import validate_timezone_coverage, validate_wall_duration
                    binding = item['timezone_binding']
                    definition = calendar_timezone(self.reader, payload['collection'], binding['name'])
                    coverage = validate_timezone_coverage(definition, binding['name'],
                                                          zoned_datetime(item['fields']['start'], binding['name']))
                    duration = validate_wall_duration(definition, binding['name'],
                                                      zoned_datetime(item['fields']['start'], binding['name']),
                                                      zoned_datetime(item['fields']['end'], binding['name']))
                    if (hashlib.sha256(definition.encode()).hexdigest() != binding['sha256']
                            or coverage != binding['coverage'] or duration != binding['duration']):
                        raise SafetyError("Calendar timezone changed since human review; prepare a new proposal")
                    phase = "identity_preflight"
            if plan.get("reconciliation_of"):
                phase="reconciled_absence_preflight"
                if kind != 'cal' or len(payload['changes']) != 1 or payload['changes'][0]['operation'] != 'create':
                    raise SafetyError("Invalid reconciled request")
                status, _, _ = self.reader.transport('GET', payload['changes'][0]['url'], '', 0)
                if status != 404:
                    raise SafetyError("Reconciled resource not definitely absent; no retry")
            phase="conditional_write"
            for item in payload['changes']:
                phase = "conditional_write"
                written = self.writer(kind,item)
                if kind == 'cal' and item.get('recurrence_scope') == 'occurrence':
                    phase = 'occurrence_readback'
                    try:
                        from .calendar_occurrence_changes import verify_occurrence_write
                        verify_occurrence_write(item['content'], self.resource(kind, item['url'])['content'])
                    except Exception:
                        completed.append(dict(written, status='write_returned_readback_unverified'))
                        raise
                    written = dict(written, verification='reviewed_occurrence_change_readback')
                elif (kind == 'cal' and ((item['operation'] == 'create'
                        and isinstance(item.get('fields'), dict) and 'recurrence' in item['fields'])
                        or (item['operation'] == 'update' and item.get('recurrence_scope') == 'series'))):
                    phase = "recurring_readback"
                    try:
                        from .calendar_recurrence import verify_created
                        verify_created(item['content'], self.resource(kind, item['url'])['content'])
                    except Exception:
                        completed.append(dict(written, status='write_returned_readback_unverified'))
                        raise
                    written = dict(written, verification='reviewed_recurring_event_readback')
                elif kind == 'cal' and item['operation'] == 'delete':
                    is_series = item.get('recurrence_scope') == 'series'
                    phase = "recurring_deletion_readback" if is_series else "calendar_deletion_readback"
                    try:
                        status, _, _ = self.reader.transport('GET', item['url'], '', 0)
                        if status != 404:
                            raise SafetyError("Deleted Calendar resource is not confirmed absent")
                    except Exception:
                        completed.append(dict(written, status='write_returned_readback_unverified'))
                        raise
                    written = dict(written, verification='series_resource_absent' if is_series else 'event_resource_absent')
                completed.append(written)
            result={"status":"applied","completed":completed,"do_not_retry":True}
        except Exception as exc:
            diagnostic={"phase":phase,"category":"http_refused" if isinstance(exc,DAVWriteRefused) else "safety_precondition" if isinstance(exc,SafetyError) else "transport_or_internal_error"}
            if isinstance(exc,DAVWriteRefused):diagnostic["http_status"]=exc.http_status
            if phase == 'timezone_preflight' and completed == []:
                from .core import digest
                result={"status":"calendar_timezone_refused_before_dispatch","completed":[],"do_not_retry":True,
                        "plan_sha256":digest(plan),"diagnostic":diagnostic,
                        "message":"No event write was attempted. Review a fresh proposal after resolving the timezone check."}
            elif phase == 'occurrence_preflight' and completed == []:
                from .core import digest
                result={"status":"calendar_occurrence_refused_before_dispatch","completed":[],"do_not_retry":True,
                        "plan_sha256":digest(plan),"diagnostic":diagnostic,
                        "message":"No event write was attempted. Review a fresh proposal after resolving the occurrence check."}
            else:
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
        """Prepare exact personal-event changes for approval; invitations are refused.

        Recurring creation is a separate one-event proposal: provide explicit
        start/end with offsets, timezone, and recurrence with frequency daily/weekly/
        monthly/yearly, optional interval, count or UTC until, and optional weekly
        weekdays MO..SU including the start day. Named-zone creation requires a
        matching supported timezone definition from the selected calendar; its
        sampled agreement and future limits are included in review. Named-zone
        events must start and end on the same day without overlapping a clock
        transition. Ambiguous or nonexistent initial local times refuse. For a supported single-component
        series without participants, exceptions, alarms or unsupported timezones,
        update summary/description/location/url/free/private with recurrence_scope=
        "series". To replace its whole schedule, provide start, end, timezone and
        recurrence together in a separate series update. This changes past and
        future occurrences; count starts from the new first occurrence. Delete a supported series with recurrence_scope="series" and
        irreversible_confirmed=true; the entire series is removed. For a single
        simple UTC occurrence, use recurrence_scope="occurrence", its original
        whole-second UTC occurrence_start, and an update of the same text fields.
        To exclude just that occurrence use operation="delete_occurrence" and
        irreversible_confirmed=true; this ordinary path requires a local owner receipt.
        Occurrence changes require a matching provider expansion and unchanged
        resource, have no attendees, alarms or prior exceptions, and are one per
        proposal. Future-only and individual-occurrence time edits are not supported.
        """
        return editor().prepare('cal',calendar_url,changes)

    @selected(calendar)
    @safe
    def calendar_prepare_once(calendar_url: str, changes: list[dict], deletion_authorization: str = 'local-receipt') -> dict:
        """Review one exact Calendar batch from read-only access without changing settings.

        Uses the same personal-event and bounded recurring-event rules as
        calendar_prepare_changes. For one irreversible deletion, only after the
        owner explicitly approves its exact target and consequences, an already
        enabled trusted-client policy may use deletion_authorization="trusted-client".
        This relies on the client to confirm the owner; it is not an independent
        human receipt. The default and strict mode require the local owner receipt.
        """
        return editor().prepare('cal',calendar_url,changes,temporary=True,deletion_authorization=deletion_authorization)

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
