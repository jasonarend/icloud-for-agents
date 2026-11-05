"""Prepare a bounded personal series split with exact history and remaining-count checks."""

from datetime import datetime, timedelta, timezone
import re
import hashlib
from zoneinfo import ZoneInfo

from .calendar_future_changes import truncate_before
from .calendar_recurrence import recurrence_rule, utc_datetime, zoned_datetime, validate_series_deletion, verify_created
from .core import SafetyError
from .dav_changes import event_content, prop, replace_properties, unfold

_CONTENT_FIELDS = {'summary', 'description', 'location', 'url', 'free', 'private'}
_TIME_FIELDS = {'start', 'end', 'timezone'}
_SCHEDULE_FIELDS = {'start', 'end', 'timezone', 'recurrence'}
_DAYS = ('MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU')


def _date(value):
    if not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z', value):
        raise SafetyError('Future content changes require UTC event timestamps')
    try:
        return datetime.strptime(value, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
    except ValueError:
        raise SafetyError('Future content changes require valid event timestamps') from None


def split_future(before, occurrence_start, fields, new_uid):
    """Return exact replacement resources; the caller must prove membership.

    This does no I/O. Creating the future resource and truncating the original
    cannot be atomic on CalDAV; the proposal must disclose that two-resource risk.
    """
    if (not isinstance(fields, dict) or not fields or set(fields) - _CONTENT_FIELDS - _SCHEDULE_FIELDS
            or not isinstance(new_uid, str) or not re.fullmatch(r'[0-9a-f]{32}', new_uid)):
        raise SafetyError('Future changes support content or paired same-date zoned time fields and a generated series identity')
    replace_schedule = 'recurrence' in fields
    if replace_schedule and not _SCHEDULE_FIELDS <= set(fields):
        raise SafetyError('Future schedule replacement requires complete start, end, timezone and recurrence')
    validate_series_deletion(before)
    verify_created(before, before)
    from .calendar_occurrence_changes import _resource, _line
    source = _resource(before)[2][None]
    values = source['values']
    first, end, zone = source['start'], source['end'], source['zone']
    marked_zone = values.get('X-ICLOUD-MCP-TIMEZONE', 'X-ICLOUD-MCP-TIMEZONE:' + zone).split(':', 1)[1]
    if marked_zone != zone and {marked_zone, zone} - {'UTC', 'Etc/UTC'}:
        raise SafetyError('Future series timezone metadata must match its actual event dates')
    if values.get('UID') == 'UID:' + new_uid:
        raise SafetyError('Future series must have a new event identity')
    original_target = utc_datetime(occurrence_start)
    target = original_target.astimezone(ZoneInfo(zone))
    target = zoned_datetime(target.isoformat(), zone)
    if original_target <= first.astimezone(timezone.utc):
        raise SafetyError('Use a whole-series content change at the first occurrence')
    rule = values.get('RRULE', '')
    if not rule.startswith('RRULE:'):
        raise SafetyError('Future content changes require one recurrence rule')
    pieces = rule[6:].split(';')
    if any(piece.count('=') != 1 for piece in pieces):
        raise SafetyError('Unsupported future recurrence rule')
    parsed = dict(piece.split('=') for piece in pieces)
    if (len(parsed) != len(pieces) or set(parsed) - {'FREQ', 'INTERVAL', 'COUNT', 'UNTIL', 'BYDAY'}
            or parsed.get('FREQ') not in {'DAILY', 'WEEKLY'} or ('COUNT' in parsed and 'UNTIL' in parsed)):
        raise SafetyError('Future content changes require a daily or weekly series with at most one COUNT or UNTIL')
    interval_value = parsed.get('INTERVAL', '1')
    if not re.fullmatch(r'[1-9][0-9]?', interval_value):
        raise SafetyError('Unsupported future recurrence interval')
    interval = int(interval_value)
    authored = {'frequency': parsed['FREQ'].lower(), 'interval': interval}
    count = None
    if 'COUNT' in parsed:
        if not re.fullmatch(r'[1-9][0-9]{0,3}', parsed['COUNT']):
            raise SafetyError('Invalid finite recurrence count')
        count = int(parsed['COUNT'])
        authored['count'] = count
    elif 'UNTIL' in parsed:
        until = _date(parsed['UNTIL'])
        if target > until:
            raise SafetyError('Selected future occurrence follows the original series end')
        authored['until'] = until.isoformat()
    weekdays = parsed.get('BYDAY')
    if weekdays is not None:
        authored['weekdays'] = weekdays.split(',')
    recurrence_rule({'start': first.isoformat(), 'end': end.isoformat(), 'timezone': zone, 'recurrence': authored})
    if target.time() != first.time():
        raise SafetyError('Future split identity must retain the original local wall time')
    if parsed['FREQ'] == 'DAILY':
        days = (target.date() - first.date()).days
        if days % interval:
            raise SafetyError('Selected future identity is outside the recurrence interval')
        preceding = days // interval
    else:
        days = sorted(_DAYS.index(day) for day in authored.get('weekdays', [_DAYS[first.weekday()]]))
        first_week = first.date() - timedelta(days=first.weekday())
        target_week = target.date() - timedelta(days=target.weekday())
        week_days = (target_week - first_week).days
        if week_days % (7 * interval) or target.weekday() not in days:
            raise SafetyError('Selected future identity is outside the weekly recurrence pattern')
        cycles = week_days // (7 * interval)
        preceding = (sum(first.weekday() <= day < target.weekday() for day in days) if cycles == 0
                     else sum(day >= first.weekday() for day in days) + (cycles - 1) * len(days)
                     + sum(day < target.weekday() for day in days))
    if preceding < 1 or (count is not None and preceding >= count):
        raise SafetyError('Selected future identity is outside the finite recurrence count')
    future_rule = dict(parsed)
    if count is not None:
        future_rule['COUNT'] = str(count - preceding)
    try:
        future_end = target + (end - first)
    except OverflowError:
        raise SafetyError('Future event end exceeds supported dates') from None
    chosen_start, chosen_end = target, future_end
    if ('start' in fields) != ('end' in fields) or ('timezone' in fields and 'start' not in fields):
        raise SafetyError('Future time changes require start and end together')
    if 'start' in fields:
        selected_zone = fields.get('timezone', zone)
        if not isinstance(selected_zone, str):
            raise SafetyError('Future schedule timezone must be an explicit IANA name')
        if selected_zone != zone and {selected_zone, zone} - {'UTC', 'Etc/UTC'}:
            raise SafetyError('Future time changes must retain the original IANA timezone')
        chosen_start, chosen_end = zoned_datetime(fields['start'], zone), zoned_datetime(fields['end'], zone)
        if not timedelta(0) < chosen_end - chosen_start <= timedelta(days=1) or chosen_end.date() != chosen_start.date():
            raise SafetyError('Future schedule requires a positive same-local-day interval')
        if replace_schedule:
            if chosen_start.date() < target.date():
                raise SafetyError('A replacement future schedule cannot start before the selected original local date')
        elif chosen_start.date() != target.date():
            raise SafetyError('Future time changes require the selected original local date; date changes need a complete replacement schedule')
    schedule_replacement = None
    if replace_schedule:
        new_rule = recurrence_rule({'start': chosen_start.isoformat(), 'end': chosen_end.isoformat(),
                                    'timezone': zone, 'recurrence': fields['recurrence']})
        future_rule = dict(piece.split('=') for piece in new_rule.split(';'))
        schedule_replacement = {
            'start': chosen_start.isoformat(), 'end': chosen_end.isoformat(), 'timezone': zone,
            'recurrence': fields['recurrence'], 'rrule': new_rule,
            'original_future_occurrence_start': target.isoformat(),
            'replaced_remaining_count': None if count is None else count - preceding,
            'original_end_condition_replaced': True,
            'count_starts_at_new_first_occurrence': True}
    zone_validation = None
    if zone not in {'UTC', 'Etc/UTC'}:
        from .calendar_timezone import validate_timezone_coverage, validate_wall_duration
        definition = source['timezone_definition']
        zone_validation = {
            'definition_sha256': hashlib.sha256(definition.encode()).hexdigest(),
            'target_coverage': validate_timezone_coverage(definition, zone, target),
            'target_duration': validate_wall_duration(definition, zone, target, future_end),
            'future_coverage': validate_timezone_coverage(definition, zone, chosen_start),
            'future_duration': validate_wall_duration(definition, zone, chosen_start, chosen_end)}
    changed_time = chosen_start != target or chosen_end != future_end
    if changed_time and 'UNTIL' in parsed and not replace_schedule:
        raise SafetyError('Future time changes on UNTIL-bounded series require a separate end-condition workflow')
    # Validate content independently: merely moving the first date to the split
    # boundary must not make an otherwise unchanged proposal look like an edit.
    content_fields = {key: value for key, value in fields.items() if key in _CONTENT_FIELDS}
    changed = event_content(content_fields, before, recurring_master=True) if content_fields else before
    changed_content = False
    if content_fields:
        try:
            verify_created(before, changed)
        except SafetyError:
            changed_content = True
    if not changed_content and not changed_time and not replace_schedule:
        raise SafetyError('Future proposal does not change the reviewed content or time')
    future = replace_properties(unfold(changed), {
        'UID': ['UID:' + new_uid],
        'DTSTAMP': ['DTSTAMP:' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')],
        'DTSTART': [_line('DTSTART', chosen_start, zone)],
        'DTEND': [_line('DTEND', chosen_end, zone)],
        'RRULE': ['RRULE:' + ';'.join(key + '=' + value for key, value in future_rule.items())],
        'CREATED': [], 'LAST-MODIFIED': [], 'SEQUENCE': [],
    }, 'END:VEVENT')
    original = truncate_before(before, occurrence_start)
    verify_created(future, future)
    verify_created(original, original)
    return {'original_content': original, 'future_content': future, 'future_uid': new_uid,
            'preceding_occurrences': preceding, 'remaining_count': int(future_rule['COUNT']) if 'COUNT' in future_rule else None,
            'original_until': parsed.get('UNTIL'), 'timezone': zone, 'timezone_validation': zone_validation,
            'schedule_replacement': schedule_replacement,
            'time_change': ({'original_start': target.isoformat(), 'original_end': future_end.isoformat(),
                             'new_start': chosen_start.isoformat(), 'new_end': chosen_end.isoformat(),
                             'recurrence_dates_unchanged': not replace_schedule} if changed_time else None),
            'effects': [('The future schedule and end condition are replaced by the complete reviewed new recurrence. Any COUNT starts at its new first occurrence; the original remaining count or end is not preserved.'
                         if replace_schedule else 'The future series retains its original end condition.' if count is not None or 'UNTIL' in parsed else
                         'The future series remains indefinite, as the original series was.'),
                        'A new event resource and UID hold the selected occurrence and later occurrences.',
                        'Earlier occurrences stay on the original resource and UID.',
                        ('The replacement may change all later recurrence dates, start/end times and ending. A later first date intentionally leaves a gap; earlier history and its times remain unchanged.'
                         if replace_schedule else 'Future occurrences keep their recurrence dates and use the reviewed new start/end times in their original timezone; past event times remain unchanged.'
                         if changed_time else 'Future occurrences retain their original start/end times.'),
                        ('The original IANA timezone and its provider definition are retained. Local wall time follows that zone across daylight saving; checks are bounded and do not prove all future timezone rules.'
                         if zone_validation is not None else 'UTC is retained; local daylight-saving changes do not shift these UTC recurrence times.'),
                        'The future resource is created and verified before the original is truncated.',
                        'CalDAV cannot make these two writes atomic: failure can leave duplicate future events.',
                        'A failure is retained as uncertain; there is no automatic replay, rollback or deletion.']}
