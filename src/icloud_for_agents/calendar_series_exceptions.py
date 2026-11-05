"""Apply a reviewed whole-series content change to bounded existing exceptions."""

from .calendar_occurrence_changes import _resource, verify_occurrence_write
from .core import SafetyError
from .dav_changes import event_content, prop, unfold

_FIELDS = {'summary', 'description', 'location', 'url', 'free', 'private'}
_RECURRENCE = {'RRULE', 'EXDATE', 'RECURRENCE-ID'}


def series_exception_review(before, fields):
    """Describe the exact component scope shared by preview and reconciliation."""
    if not isinstance(fields, dict) or not fields or set(fields) - _FIELDS:
        raise SafetyError('A series with exceptions supports explicit whole-series content fields only')
    parts = _resource(before)[2]
    if len(parts) == 1 and not parts[None]['exclusions']:
        raise SafetyError('Use the ordinary whole-series workflow for a master without exceptions')
    return {'component_count': len(parts), 'fields_applied_to_every_component': sorted(fields),
            'effect': 'The selected fields replace existing values on the master and every detached exception, including customized values. Other fields, occurrence times, identities and exclusions remain unchanged.'}


def series_exception_content(before, fields):
    """Update requested fields on every component while retaining all timing.

    A whole-series content edit deliberately replaces the selected properties on
    the master and every detached exception, including prior customized values.
    Other overrides, UIDs, starts/ends, exclusions and recurrence identities stay.
    """
    series_exception_review(before, fields)
    outside, zone_lines, parts = _resource(before)
    changed_events = []
    for item in parts.values():
        recurrence = [line for line in item['lines'] if prop(line) in _RECURRENCE]
        ordinary = [line for line in item['lines'] if prop(line) not in _RECURRENCE]
        standalone = '\r\n'.join(['BEGIN:VCALENDAR', *outside, *ordinary, 'END:VCALENDAR', ''])
        updated = unfold(event_content(fields, standalone))
        event = updated[updated.index('BEGIN:VEVENT'):updated.index('END:VEVENT') + 1]
        event[-1:-1] = recurrence
        changed_events.extend(event)
    result = '\r\n'.join(['BEGIN:VCALENDAR', *outside, *zone_lines, *changed_events, 'END:VCALENDAR', ''])
    _resource(result)
    verify_occurrence_write(result, result)
    return result
