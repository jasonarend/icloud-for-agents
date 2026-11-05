"""Build and verify bounded recurrence exceptions without losing other instances."""

from datetime import datetime, timedelta, timezone
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .core import SafetyError
from .calendar_recurrence import utc_datetime, verify_created, zoned_datetime
from .dav_changes import event_content, prop, replace_properties, unfold


EVENT_PROPERTIES = {
    "UID", "DTSTAMP", "DTSTART", "DTEND", "RRULE", "SUMMARY", "DESCRIPTION",
    "LOCATION", "URL", "TRANSP", "CLASS", "STATUS", "CREATED", "LAST-MODIFIED",
    "SEQUENCE", "X-ICLOUD-MCP-TIMEZONE",
}


def _date_line(line, name, zone=None):
    match = re.fullmatch(name + r"(?:;TZID=([A-Za-z0-9_+/-]{1,100}))?:([0-9]{8}T[0-9]{6}Z?)", line)
    if not match:
        raise SafetyError("Occurrence dates require explicit UTC or supported named-zone timestamps")
    timezone_name, value = match.groups()
    try:
        if timezone_name:
            if value.endswith('Z') or (zone is not None and timezone_name != zone):
                raise ValueError()
            wall = datetime.strptime(value, '%Y%m%dT%H%M%S')
            tz = ZoneInfo(timezone_name)
            local = wall.replace(tzinfo=tz)
            # Reject gaps and folds instead of choosing one interpretation.
            local = zoned_datetime(local.isoformat(), timezone_name)
            return local, timezone_name
        if not value.endswith('Z'):
            raise ValueError()
        return datetime.strptime(value, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc), 'UTC'
    except (ValueError, OverflowError, ZoneInfoNotFoundError):
        raise SafetyError("Occurrence date is invalid or ambiguous") from None


def _line(name, value, zone):
    if zone in {'UTC', 'Etc/UTC'}:
        return name + ':' + value.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    return name + ';TZID=' + zone + ':' + value.astimezone(ZoneInfo(zone)).strftime('%Y%m%dT%H%M%S')


def _resource(raw):
    if (not isinstance(raw, str) or any((ord(c) < 32 and c not in '\r\n\t')
                                      or 127 <= ord(c) <= 159 for c in raw)):
        raise SafetyError('Occurrence resource contains invalid control characters')
    try:
        lines = unfold(raw)
    except UnicodeError:
        raise SafetyError('Occurrence resource has invalid text encoding') from None
    if len(lines) > 4096:
        raise SafetyError('Occurrence resource exceeds bounded line count')
    if not lines or lines[0] != 'BEGIN:VCALENDAR' or lines[-1] != 'END:VCALENDAR':
        raise SafetyError('Occurrence changes require a canonical calendar resource')
    outside, events, current, zone_lines = [], [], None, []
    in_zone = False
    for line in lines[1:-1]:
        if line == 'BEGIN:VTIMEZONE' and current is None and not in_zone:
            if zone_lines:
                raise SafetyError('Occurrence changes require one timezone definition')
            in_zone = True
            zone_lines.append(line)
        elif in_zone:
            zone_lines.append(line)
            if line == 'END:VTIMEZONE':
                in_zone = False
        elif line == 'BEGIN:VEVENT' and current is None:
            current = [line]
        elif line == 'END:VEVENT' and current is not None:
            current.append(line)
            events.append(current)
            current = None
        elif current is None:
            if prop(line) not in {'VERSION', 'PRODID', 'CALSCALE'}:
                raise SafetyError('Unsupported calendar component in occurrence resource')
            outside.append(line)
        else:
            if prop(line) in {'BEGIN', 'END'}:
                raise SafetyError('Nested event components require a separate workflow')
            current.append(line)
    if current is not None or in_zone or not 1 <= len(events) <= 101:
        raise SafetyError('Occurrence resource exceeds its bounded event structure')
    timezone_definition = '\r\n'.join(['BEGIN:VCALENDAR', 'VERSION:2.0', *zone_lines, 'END:VCALENDAR', ''])
    result, identities = {}, set()
    uid = None
    for event in events:
        names = [prop(line) for line in event[1:-1]]
        if (any(name not in EVENT_PROPERTIES | {'RECURRENCE-ID', 'EXDATE'} for name in names)
                or any(names.count(name) > 1 for name in set(names) - {'EXDATE'})):
            raise SafetyError('Occurrence changes require supported unambiguous event properties')
        values = {prop(line): line for line in event[1:-1] if prop(line) != 'EXDATE'}
        if not {'UID', 'DTSTART', 'DTEND'} <= set(values):
            raise SafetyError('Occurrence event lacks exact identity and timing')
        start, zone = _date_line(values['DTSTART'], 'DTSTART')
        end, end_zone = _date_line(values['DTEND'], 'DTEND', zone)
        if zone != end_zone or not timedelta(0) < end - start <= timedelta(days=1):
            raise SafetyError('Occurrence duration must be positive and at most one day')
        identity = None
        if 'RECURRENCE-ID' in values:
            original, _ = _date_line(values['RECURRENCE-ID'], 'RECURRENCE-ID', zone)
            identity = original.astimezone(timezone.utc)
            if 'RRULE' in values or 'EXDATE' in names:
                raise SafetyError('Detached occurrences cannot contain recurrence rules or exclusions')
        elif 'RRULE' not in values:
            raise SafetyError('Occurrence resource needs one recurring master')
        if identity in identities:
            raise SafetyError('Duplicate occurrence identity')
        identities.add(identity)
        current_uid = values['UID']
        if uid is not None and current_uid != uid:
            raise SafetyError('Occurrence resource has inconsistent event UIDs')
        uid = current_uid
        exclusions = set()
        for exclusion in (line for line in event[1:-1] if prop(line) == 'EXDATE'):
            left, right = exclusion.split(':', 1)
            for value in right.split(','):
                date, _ = _date_line(left + ':' + value, 'EXDATE', zone)
                stamp = date.astimezone(timezone.utc)
                if stamp in exclusions:
                    raise SafetyError('Duplicate excluded occurrence')
                exclusions.add(stamp)
        # Verify the full outer timezone and ordinary single-event structure.
        # Multiple EXDATE properties are normalized only for comparison.
        normalized = [line for line in event if prop(line) not in {'EXDATE', 'RECURRENCE-ID'}]
        if identity is not None:
            normalized.insert(-1, 'RECURRENCE-ID:' + identity.strftime('%Y%m%dT%H%M%SZ'))
        if exclusions:
            normalized.insert(-1, 'EXDATE:' + ','.join(v.strftime('%Y%m%dT%H%M%SZ') for v in sorted(exclusions)))
        calendar = '\r\n'.join(['BEGIN:VCALENDAR', *outside, *zone_lines, *normalized, 'END:VCALENDAR', ''])
        verify_created(calendar, calendar)
        if zone not in {'UTC', 'Etc/UTC'}:
            from .calendar_timezone import validate_wall_duration, validate_timezone_coverage
            validate_timezone_coverage(timezone_definition, zone, start)
            validate_wall_duration(timezone_definition, zone, start, end)
        result[identity] = {'lines': event, 'calendar': calendar, 'start': start, 'end': end,
                            'zone': zone, 'values': values, 'exclusions': exclusions,
                            'timezone_definition': timezone_definition}
    if None not in result:
        raise SafetyError('Occurrence resource is missing its master')
    master = result[None]
    if master['exclusions'] & (set(result) - {None}):
        raise SafetyError('An occurrence cannot be both excluded and detached')
    for key, item in result.items():
        if key is not None and item['zone'] != master['zone']:
            raise SafetyError('Detached occurrence must retain its master timezone')
    return outside, zone_lines, result


def validate_occurrence_resource(raw):
    """Validate one bounded personal master and its supported detached exceptions."""
    _resource(raw)


def _parts(raw):
    return {('master' if key is None else key.isoformat()): item['calendar']
            for key, item in _resource(raw)[2].items()}


def occurrence_source(raw, occurrence_start):
    """Return normalized source timing/content for one original UTC identity."""
    _, _, parts = _resource(raw)
    master = parts[None]
    original = utc_datetime(occurrence_start)
    if original < master['start'].astimezone(timezone.utc) or original in master['exclusions']:
        raise SafetyError('Occurrence is before the series or already excluded')
    source = parts.get(original)
    if source is None:
        local = original.astimezone(ZoneInfo(master['zone']))
        local = zoned_datetime(local.isoformat(), master['zone'])
        wall_duration = master['end'].replace(tzinfo=None) - master['start'].replace(tzinfo=None)
        try:
            local_end = local + wall_duration
            local_end = zoned_datetime(local_end.isoformat(), master['zone'])
        except (OverflowError, ValueError):
            raise SafetyError('Occurrence end exceeds supported dates') from None
        if master['zone'] not in {'UTC', 'Etc/UTC'}:
            from .calendar_timezone import validate_wall_duration, validate_timezone_coverage
            validate_timezone_coverage(master['timezone_definition'], master['zone'], local)
            validate_wall_duration(master['timezone_definition'], master['zone'], local, local_end)
        source = dict(master, start=local, end=local_end)
    def normalized(item):
        values = {name: line.split(':', 1)[1] for name, line in item['values'].items()}
        values['DTSTART'] = item['start'].astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        values['DTEND'] = item['end'].astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        values.pop('RECURRENCE-ID', None)
        return values
    return master, source, normalized(master), normalized(source)


def occurrence_content(before, operation, occurrence_start, fields=None):
    """Apply one reviewed exception while preserving every other event verbatim."""
    if operation not in {'update', 'delete_occurrence'}:
        raise SafetyError('Unsupported occurrence operation')
    outside, zone_lines, parts = _resource(before)
    master, source, _, _ = occurrence_source(before, occurrence_start)
    original = utc_datetime(occurrence_start)
    zone = master['zone']
    if operation == 'delete_occurrence':
        if fields is not None:
            raise SafetyError('Occurrence deletion does not accept replacement fields')
        exclusions = master['exclusions'] | {original}
        exclusion_lines = [_line('EXDATE', value, zone) for value in sorted(exclusions)]
        exclusion_name = exclusion_lines[0].split(':', 1)[0]
        updated_master = unfold(replace_properties(master['lines'], {'EXDATE': [
            exclusion_name + ':' + ','.join(line.split(':', 1)[1] for line in exclusion_lines)]}, 'END:VEVENT'))
        remaining = [item['lines'] for key, item in parts.items() if key not in {None, original}]
        events = [updated_master, *remaining]
    else:
        allowed = {'summary', 'description', 'location', 'url', 'free', 'private', 'start', 'end', 'timezone'}
        if not isinstance(fields, dict) or not fields or set(fields) - allowed:
            raise SafetyError('Occurrence updates require explicit supported content or start/end fields')
        if ('start' in fields) != ('end' in fields) or ('timezone' in fields and 'start' not in fields):
            raise SafetyError('Occurrence rescheduling requires start and end together')
        chosen_start, chosen_end = source['start'], source['end']
        if 'start' in fields:
            if fields.get('timezone', zone) != zone:
                raise SafetyError('An occurrence must retain its master timezone')
            chosen_start = zoned_datetime(fields['start'], zone)
            chosen_end = zoned_datetime(fields['end'], zone)
            if not timedelta(0) < chosen_end - chosen_start <= timedelta(days=1):
                raise SafetyError('Occurrence duration must be positive and at most one day')
        # Update text through the existing field validator on a standalone event.
        base = [line for line in source['lines'] if prop(line) not in {'RRULE', 'EXDATE', 'RECURRENCE-ID'}]
        body = '\r\n'.join(['BEGIN:VCALENDAR', *outside, *base, 'END:VCALENDAR', ''])
        text_fields = {key: value for key, value in fields.items() if key not in {'start', 'end', 'timezone'}}
        if text_fields:
            # recurring_master enforces a rule, while personal_event rejects only
            # recurrence properties. The detached base has neither.
            body = event_content(text_fields, body)
        detached = unfold(body)
        detached = detached[detached.index('BEGIN:VEVENT'):detached.index('END:VEVENT') + 1]
        replacements = {'RECURRENCE-ID': [_line('RECURRENCE-ID', original, zone)],
                        'DTSTART': [_line('DTSTART', chosen_start, zone)],
                        'DTEND': [_line('DTEND', chosen_end, zone)]}
        detached = unfold(replace_properties(detached, replacements, 'END:VEVENT'))
        events = [master['lines'], *(item['lines'] for key, item in parts.items() if key not in {None, original}), detached]
    value = '\r\n'.join(['BEGIN:VCALENDAR', *outside, *zone_lines,
                          *(line for event in events for line in event), 'END:VCALENDAR', ''])
    _resource(value)
    return value


def verify_occurrence_write(expected, observed):
    """Compare every master/exception to the complete reviewed resource."""
    expected_parts, observed_parts = _parts(expected), _parts(observed)
    if expected_parts.keys() != observed_parts.keys():
        raise SafetyError('Occurrence readback does not match the reviewed identities')
    for key in expected_parts:
        verify_created(expected_parts[key], observed_parts[key])
