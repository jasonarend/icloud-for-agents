"""Bounded recurrence authoring for non-invitation Calendar events."""

from datetime import datetime, timezone
from collections import Counter
from calendar import monthrange
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .core import SafetyError


FREQUENCIES = {"daily": "DAILY", "weekly": "WEEKLY", "monthly": "MONTHLY", "yearly": "YEARLY"}
WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")


def calendar_timezone(reader, collection, timezone_name):
    """Read the selected calendar's exact timezone definition without changing it."""
    from .dav import NS
    from .calendar_timezone import extract_timezone_definition

    final, root = reader.propfind("cal", collection, "<cal:calendar-timezone/>")
    if root.tag != "{DAV:}multistatus":
        raise SafetyError("Calendar did not return timezone properties")
    rows = root.findall("d:response", NS)
    if (len(rows) != 1 or len(rows[0].findall("d:href", NS)) != 1
            or rows[0].find("d:status", NS) is not None
            or reader.href(rows[0], "d:href", final, "cal").rstrip("/") != collection.rstrip("/")):
        raise SafetyError("Timezone definition does not match the selected calendar")
    found = []
    parts = rows[0].findall("d:propstat", NS)
    if len(parts) != 1:
        raise SafetyError("Calendar timezone response is missing or ambiguous")
    for part in parts:
        element = part.find("d:prop/cal:calendar-timezone", NS)
        if element is None:
            continue
        status = part.findtext("d:status", default="", namespaces=NS)
        if (len(part.findall("d:status", NS)) != 1 or len(part.findall("d:prop", NS)) != 1
                or not re.fullmatch(r"HTTP/\d(?:\.\d)? 200(?: .*)?", status)
                or element.text is None or len(element) != 0):
            raise SafetyError("Calendar did not provide a usable timezone definition")
        found.append(element.text)
    if len(found) != 1 or len(rows[0].findall(".//cal:calendar-timezone", NS)) != 1:
        raise SafetyError("Calendar timezone definition is missing or ambiguous")
    extract_timezone_definition(found[0], timezone_name)
    return found[0]


def utc_datetime(value):
    """Require explicit, whole-second UTC rather than silently shifting a local series."""
    if not isinstance(value, str) or len(value) > 100:
        raise SafetyError("Recurring dates require explicit whole-second UTC timestamps")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() != timezone.utc.utcoffset(result) or result.microsecond:
            raise ValueError()
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        raise SafetyError("Recurring dates require explicit whole-second UTC timestamps") from None


def zoned_datetime(value, timezone_name):
    """Match a supplied offset and wall time against an explicit IANA zone."""
    if (not isinstance(value, str) or len(value) > 100 or not isinstance(timezone_name, str)
            or not re.fullmatch(r"[A-Za-z0-9_+/-]{1,100}", timezone_name)):
        raise SafetyError("Recurring dates require an explicit timezone and offset")
    try:
        zone = ZoneInfo(timezone_name)
        supplied = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if supplied.tzinfo is None or supplied.microsecond:
            raise ValueError()
        local = supplied.astimezone(zone)
        if (local.replace(tzinfo=None) != supplied.replace(tzinfo=None)
                or local.utcoffset() != supplied.utcoffset()):
            raise ValueError()
        wall = local.replace(tzinfo=None)
        if wall.replace(tzinfo=zone, fold=0).utcoffset() != wall.replace(tzinfo=zone, fold=1).utcoffset():
            raise ValueError()
        return local
    except (ValueError, TypeError, OverflowError, ZoneInfoNotFoundError):
        raise SafetyError("Recurring date and offset must match the named IANA timezone") from None


def recurrence_rule(fields):
    """Build an explicit bounded RRULE; reject unknown or ambiguous combinations."""
    if not isinstance(fields, dict) or not isinstance(fields.get("timezone"), str):
        raise SafetyError("Recurring events require an explicit IANA timezone")
    start = zoned_datetime(fields.get("start"), fields["timezone"])
    zoned_datetime(fields.get("end"), fields["timezone"])
    rule = fields.get("recurrence")
    if not isinstance(rule, dict) or set(rule) - {"frequency", "interval", "count", "until", "weekdays", "month_days", "months", "ordinal_weekday"}:
        raise SafetyError("Use an explicit supported recurrence rule")
    frequency = rule.get("frequency")
    if not isinstance(frequency, str) or frequency not in FREQUENCIES:
        raise SafetyError("Recurrence frequency must be daily, weekly, monthly or yearly")
    interval = rule.get("interval", 1)
    if type(interval) is not int or not 1 <= interval <= 99:
        raise SafetyError("Recurrence interval must be an integer from 1 to 99")
    parts = ["FREQ=" + FREQUENCIES[frequency], "INTERVAL=" + str(interval)]
    count = rule.get("count")
    until = rule.get("until")
    if count is not None and until is not None:
        raise SafetyError("Choose a recurrence count or an end timestamp, not both")
    if count is not None:
        if type(count) is not int or not 1 <= count <= 1000:
            raise SafetyError("Recurrence count must be an integer from 1 to 1000")
        parts.append("COUNT=" + str(count))
    if until is not None:
        end = utc_datetime(until)
        if end < start:
            raise SafetyError("Recurrence end must not precede the first occurrence")
        parts.append("UNTIL=" + end.strftime("%Y%m%dT%H%M%SZ"))
    weekdays = rule.get("weekdays")
    if weekdays is not None:
        if (frequency != "weekly" or not isinstance(weekdays, list) or not 1 <= len(weekdays) <= 7
                or any(not isinstance(day, str) or day not in WEEKDAYS for day in weekdays)
                or len(set(weekdays)) != len(weekdays) or WEEKDAYS[start.weekday()] not in weekdays):
            raise SafetyError("Weekly weekdays must be distinct MO..SU values including the first occurrence")
        parts.append("BYDAY=" + ",".join(day for day in WEEKDAYS if day in weekdays))
    months = rule.get("months")
    if months is not None:
        if (frequency != "yearly" or not isinstance(months, list) or not 1 <= len(months) <= 12
                or any(type(month) is not int or not 1 <= month <= 12 for month in months)
                or len(set(months)) != len(months) or start.month not in months):
            raise SafetyError("Yearly months must be distinct months including the first occurrence")
        parts.append("BYMONTH=" + ",".join(str(month) for month in sorted(months)))
    month_days = rule.get("month_days")
    ordinal = rule.get("ordinal_weekday")
    if month_days is not None:
        if (frequency not in {"monthly", "yearly"} or not isinstance(month_days, list)
                or not 1 <= len(month_days) <= 31 or any(type(day) is not int or day == 0 or not -31 <= day <= 31 for day in month_days)
                or len(set(month_days)) != len(month_days) or ordinal is not None):
            raise SafetyError("Month days must be distinct nonzero values from -31 to 31 without an ordinal weekday")
        last_day = monthrange(start.year, start.month)[1]
        if start.day not in month_days and start.day - last_day - 1 not in month_days:
            raise SafetyError("Month days must include the first occurrence")
        if frequency == "yearly" and months is None:
            parts.append("BYMONTH=" + str(start.month))
        parts.append("BYMONTHDAY=" + ",".join(str(day) for day in sorted(month_days)))
    if ordinal is not None:
        if (frequency not in {"monthly", "yearly"} or not isinstance(ordinal, dict)
                or set(ordinal) != {"weekday", "ordinal"} or ordinal.get("weekday") not in WEEKDAYS
                or type(ordinal.get("ordinal")) is not int or ordinal["ordinal"] == 0
                or not -5 <= ordinal["ordinal"] <= 5 or weekdays is not None):
            raise SafetyError("Use one ordinal weekday from first through fifth or last through fifth-last")
        # Yearly ordinal BYDAY without BYMONTH means ordinal within a year,
        # rather than within the starting month. Make the latter explicit.
        if frequency == "yearly" and months is None:
            parts.append("BYMONTH=" + str(start.month))
        index = (start.day - 1) // 7 + 1
        reverse_index = -((monthrange(start.year, start.month)[1] - start.day) // 7 + 1)
        if ordinal["weekday"] != WEEKDAYS[start.weekday()] or ordinal["ordinal"] not in {index, reverse_index}:
            raise SafetyError("Ordinal weekday must include the first occurrence")
        parts.append("BYDAY=" + str(ordinal["ordinal"]) + ordinal["weekday"])
    return ";".join(parts)


def detach_timezone(lines):
    """Extract and validate at most one provider timezone before event checks."""
    normalized = [line.upper() for line in lines]
    starts = [index for index, line in enumerate(normalized) if line == "BEGIN:VTIMEZONE"]
    ends = [index for index, line in enumerate(normalized) if line == "END:VTIMEZONE"]
    if not starts and not ends:
        return lines, ()
    event_starts = [index for index, line in enumerate(normalized) if line == "BEGIN:VEVENT"]
    event_ends = [index for index, line in enumerate(normalized) if line == "END:VEVENT"]
    if (len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]
            or len(event_starts) != 1 or len(event_ends) != 1
            or not (ends[0] < event_starts[0] or starts[0] > event_ends[0])
            or starts[0] == 0 or ends[0] >= len(lines) - 1):
        raise SafetyError("Calendar contains ambiguous timezone components")
    component = lines[starts[0]:ends[0] + 1]
    identifiers = [line[5:] for line in component if line.upper().startswith("TZID:")]
    if len(identifiers) != 1:
        raise SafetyError("Calendar timezone identity is missing or ambiguous")
    from .calendar_timezone import extract_timezone_definition
    wrapped = "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", *component, "END:VCALENDAR", ""])
    validated = extract_timezone_definition(wrapped, identifiers[0])
    return lines[:starts[0]] + lines[ends[0] + 1:], tuple(validated)


def verify_created(expected, observed):
    """Compare an authored recurring event with exact provider readback.

    Only provider-managed timestamps and sequence may be added or changed. An
    unfamiliar component or property refuses verification instead of being lost.
    """
    from .dav_changes import prop, unfold

    def event_lines(raw):
        lines = unfold(raw)
        lines, timezone_component = detach_timezone(lines)
        normalized = [line.upper() for line in lines]
        if (not lines or any(":" not in line for line in lines)
                or normalized[0] != "BEGIN:VCALENDAR" or normalized[-1] != "END:VCALENDAR"
                or normalized.count("BEGIN:VCALENDAR") != 1 or normalized.count("END:VCALENDAR") != 1
                or normalized.count("BEGIN:VEVENT") != 1 or normalized.count("END:VEVENT") != 1):
            raise SafetyError("Recurring event readback contains an unexpected component structure")
        start, end = normalized.index("BEGIN:VEVENT"), normalized.index("END:VEVENT")
        if start >= end:
            raise SafetyError("Recurring event readback contains malformed components")
        outside = lines[1:start] + lines[end + 1:-1]
        if (sum(line.upper() == "VERSION:2.0" for line in outside) != 1
                or any(prop(line) not in {"VERSION", "PRODID", "CALSCALE"} for line in outside)
                or any(prop(line) == "VERSION" and line.upper() != "VERSION:2.0" for line in outside)
                or any(prop(line) == "CALSCALE" and line.upper() != "CALSCALE:GREGORIAN" for line in outside)
                or any(sum(prop(line) == name for line in outside) > 1 for name in {"VERSION", "PRODID", "CALSCALE"})):
            raise SafetyError("Recurring event readback contains an unexpected calendar component")
        # Preserve alarms byte-for-byte while comparing event properties. A
        # provider change to an alarm remains an uncertain readback, and no
        # nested or unfamiliar component is silently discarded.
        raw_event, alarms, alarm = [], [], None
        for line in lines[start + 1:end]:
            boundary = line.upper()
            if boundary == "BEGIN:VALARM" and alarm is None:
                alarm = [line]
            elif boundary == "END:VALARM" and alarm is not None:
                alarm.append(line)
                alarms.append(tuple(alarm))
                alarm = None
            elif prop(line) in {"BEGIN", "END"}:
                raise SafetyError("Recurring event readback contains an unsupported nested component")
            elif alarm is not None:
                alarm.append(line)
            else:
                raw_event.append(line)
        if alarm is not None:
            raise SafetyError("Recurring event readback contains an unbalanced alarm")
        raw_names = [prop(line) for line in raw_event]
        if len(raw_names) != len(set(raw_names)):
            raise SafetyError("Recurring event readback contains duplicate properties")
        uids = [line[4:] for line in raw_event if line.startswith("UID:")]
        if (len(uids) != 1 or not 1 <= len(uids[0]) <= 255 or not uids[0].strip()
                or any(ord(character) < 32 or ord(character) == 127 for character in uids[0])):
            raise SafetyError("Recurring event readback needs one valid event identity")
        event = []
        for line in raw_event:
            name = prop(line)
            if name in {"DTSTAMP", "CREATED", "LAST-MODIFIED", "SEQUENCE"}:
                continue
            if name == "RRULE":
                if not line.startswith("RRULE:"):
                    raise SafetyError("Recurring event readback contains an unsupported rule")
                pieces = line[6:].split(";")
                if any(piece.count("=") != 1 for piece in pieces):
                    raise SafetyError("Recurring event readback contains an invalid rule")
                keys = [piece.split("=", 1)[0] for piece in pieces]
                if (len(keys) != len(set(keys)) or
                        any(not re.fullmatch(r"[A-Z0-9-]+", key) for key in keys) or
                        any(not piece.split("=", 1)[1] for piece in pieces)):
                    raise SafetyError("Recurring event readback contains an ambiguous rule")
                # RFC 5545 rule-part ordering is not significant. Apple may
                # reorder parts without changing the reviewed recurrence.
                line = "RRULE:" + ";".join(sorted(pieces))
            event.append(line)
        if timezone_component:
            from .calendar_timezone import timezone_verification_key, validate_timezone_event_start
            identifiers = [line[5:] for line in timezone_component if line.startswith("TZID:")]
            if len(identifiers) != 1:
                raise SafetyError("Recurring event readback has an ambiguous timezone identity")
            name = identifiers[0]
            starts = [line for line in raw_event if line.startswith("DTSTART;TZID=" + name + ":")]
            ends = [line for line in raw_event if line.startswith("DTEND;TZID=" + name + ":")]
            if len(starts) != 1 or len(ends) != 1:
                raise SafetyError("Recurring event readback does not use its validated timezone")
            wrapped = "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", *timezone_component, "END:VCALENDAR", ""])
            validate_timezone_event_start(wrapped, name, starts[0].split(":", 1)[1])
            timezone_component = timezone_verification_key(wrapped, name)
        return Counter(event), timezone_component, tuple(alarms)

    if event_lines(expected) != event_lines(observed):
        raise SafetyError("Recurring event readback does not match the reviewed event")


def validate_series_deletion(raw):
    """Admit one master without participants or exceptions for whole-series deletion."""
    from .dav_changes import prop, unfold

    lines = unfold(raw)
    lines, _ = detach_timezone(lines)
    normalized = [line.upper() for line in lines]
    if (not lines or any(":" not in line for line in lines)
            or normalized[0] != "BEGIN:VCALENDAR" or normalized[-1] != "END:VCALENDAR"
            or normalized.count("BEGIN:VCALENDAR") != 1 or normalized.count("END:VCALENDAR") != 1
            or normalized.count("BEGIN:VEVENT") != 1 or normalized.count("END:VEVENT") != 1):
        raise SafetyError("Whole-series deletion requires exactly one ordinary recurring event")
    if any(prop(line) in {"ATTENDEE", "ORGANIZER", "METHOD", "RDATE", "EXDATE", "EXRULE", "RECURRENCE-ID"}
           for line in lines):
        raise SafetyError("Invitations and recurring events with exceptions need a separate workflow")
    start, end = normalized.index("BEGIN:VEVENT"), normalized.index("END:VEVENT")
    if start >= end:
        raise SafetyError("Whole-series deletion requires an ordered recurring event")
    stack = []
    rules = 0
    for line in lines:
        name = prop(line)
        if name in {"BEGIN", "END"}:
            boundary = line.upper()
            if name == "BEGIN":
                allowed = ((not stack and boundary == "BEGIN:VCALENDAR") or
                           (stack == ["VCALENDAR"] and boundary == "BEGIN:VEVENT") or
                           (stack == ["VCALENDAR", "VEVENT"] and boundary == "BEGIN:VALARM"))
                if not allowed:
                    raise SafetyError("Whole-series deletion cannot include other calendar components")
                stack.append(boundary.split(":", 1)[1])
            elif not stack or boundary != "END:" + stack.pop():
                raise SafetyError("Unbalanced recurring calendar resource")
        elif not stack:
            raise SafetyError("Calendar properties outside the series resource")
        elif stack == ["VCALENDAR", "VEVENT"] and name == "RRULE":
            rules += 1
    if stack:
        raise SafetyError("Unbalanced recurring calendar resource")
    if rules != 1:
        raise SafetyError("Whole-series deletion requires exactly one master recurrence rule")
