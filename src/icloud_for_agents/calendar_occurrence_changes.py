"""Build and verify narrowly scoped UTC recurrence exceptions."""

from datetime import datetime, timezone
import re

from .core import SafetyError
from .calendar_recurrence import utc_datetime, validate_series_deletion, verify_created
from .dav_changes import event_content, prop, replace_properties, unfold


EVENT_PROPERTIES = {
    "UID", "DTSTAMP", "DTSTART", "DTEND", "RRULE", "SUMMARY", "DESCRIPTION",
    "LOCATION", "URL", "TRANSP", "CLASS", "STATUS", "CREATED", "LAST-MODIFIED",
    "SEQUENCE", "X-ICLOUD-MCP-TIMEZONE",
}


def _utc_ical(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", value):
        raise SafetyError("Occurrence changes require exact UTC event timestamps")
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        raise SafetyError("Occurrence changes require exact UTC event timestamps") from None


def _parts(raw):
    """Split a plain VCALENDAR into one or two events and validate each."""
    lines = unfold(raw)
    if not lines or lines[0] != "BEGIN:VCALENDAR" or lines[-1] != "END:VCALENDAR":
        raise SafetyError("Occurrence readback requires a canonical calendar resource")
    outside, events, current = [], [], None
    for line in lines[1:-1]:
        if line == "BEGIN:VEVENT" and current is None:
            current = [line]
        elif line == "END:VEVENT" and current is not None:
            current.append(line)
            events.append(current)
            current = None
        elif current is None:
            if prop(line) in {"BEGIN", "END"}:
                raise SafetyError("Unexpected calendar component in occurrence readback")
            outside.append(line)
        else:
            if prop(line) in {"BEGIN", "END"}:
                raise SafetyError("Nested event component in occurrence readback")
            current.append(line)
    if current is not None or not 1 <= len(events) <= 2:
        raise SafetyError("Occurrence readback has an unexpected number of events")
    result = {}
    for event in events:
        identity = [line for line in event if prop(line) == "RECURRENCE-ID"]
        if len(identity) > 1 or (identity and not re.fullmatch(r"RECURRENCE-ID:[0-9]{8}T[0-9]{6}Z", identity[0])):
            raise SafetyError("Occurrence readback has an ambiguous recurrence identity")
        key = identity[0] if identity else "master"
        if key in result:
            raise SafetyError("Occurrence readback has duplicate recurrence identities")
        calendar = "\r\n".join(["BEGIN:VCALENDAR", *outside, *event, "END:VCALENDAR", ""])
        verify_created(calendar, calendar)
        result[key] = calendar
    if "master" not in result:
        raise SafetyError("Occurrence readback is missing its master event")
    return result


def occurrence_content(before, operation, occurrence_start, fields=None):
    """Return the full reviewed resource with one UTC exception applied."""
    if operation not in {"update", "delete_occurrence"}:
        raise SafetyError("Unsupported occurrence operation")
    validate_series_deletion(before)
    parts = _parts(before)
    if set(parts) != {"master"}:
        raise SafetyError("A series with existing exceptions needs a separate workflow")
    lines = unfold(before)
    start, end = lines.index("BEGIN:VEVENT"), lines.index("END:VEVENT")
    properties = lines[start + 1:end]
    if any(prop(line) not in EVENT_PROPERTIES for line in properties):
        raise SafetyError("Occurrence changes require a supported simple event")
    timestamps = {}
    for name in ("DTSTART", "DTEND"):
        matches = [line for line in properties if prop(line) == name]
        if len(matches) != 1 or not matches[0].startswith(name + ":"):
            raise SafetyError("Occurrence changes require UTC start and end")
        timestamps[name] = _utc_ical(matches[0][len(name) + 1:])
    duration = timestamps["DTEND"] - timestamps["DTSTART"]
    if not 0 < duration.total_seconds() <= 86400:
        raise SafetyError("Occurrence duration must be positive and at most one day")
    original = utc_datetime(occurrence_start)
    recurrence_id = original.strftime("%Y%m%dT%H%M%SZ")
    if operation == "delete_occurrence":
        if fields is not None:
            raise SafetyError("Occurrence deletion does not accept replacement fields")
        return replace_properties(lines, {"EXDATE": ["EXDATE:" + recurrence_id]}, "END:VEVENT")
    if not isinstance(fields, dict) or not fields or set(fields) - {"summary", "description", "location", "url", "free", "private"}:
        raise SafetyError("Occurrence updates support only explicit text, URL, free or private fields")
    updated = event_content(fields, before, recurring_master=True)
    replacement = {
        "RRULE": [],
        "RECURRENCE-ID": ["RECURRENCE-ID:" + recurrence_id],
        "DTSTART": ["DTSTART:" + recurrence_id],
        "DTEND": ["DTEND:" + (original + duration).strftime("%Y%m%dT%H%M%SZ")],
    }
    detached = unfold(replace_properties(unfold(updated), replacement, "END:VEVENT"))
    first, last = detached.index("BEGIN:VEVENT"), detached.index("END:VEVENT")
    result = lines[:-1] + detached[first:last + 1] + ["END:VCALENDAR"]
    value = "\r\n".join(result) + "\r\n"
    _parts(value)
    return value


def verify_occurrence_write(expected, observed):
    """Compare each master/exception with the exact reviewed change."""
    expected_parts, observed_parts = _parts(expected), _parts(observed)
    if expected_parts.keys() != observed_parts.keys():
        raise SafetyError("Occurrence readback does not match the reviewed identities")
    for key in expected_parts:
        verify_created(expected_parts[key], observed_parts[key])
