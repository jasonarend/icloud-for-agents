"""Bounded removal of a recurring series from one verified occurrence onward."""

from datetime import timedelta, timezone

from .calendar_recurrence import utc_datetime, validate_series_deletion, verify_created
from .core import SafetyError
from .dav_changes import replace_properties, unfold


def truncate_before(before, occurrence_start):
    """Keep earlier occurrences using a UTC cutoff while retaining the source zone.

    Membership must be proved through the provider before review and again before
    writing. Only one supported master without exceptions or alarms is admitted;
    this never splits the resource, issues DELETE, or guesses membership.
    """
    validate_series_deletion(before)
    verify_created(before, before)
    from .calendar_occurrence_changes import _resource, occurrence_source
    from .calendar_occurrence_proof import _master
    master = _resource(before)[2][None]
    supported = _master(before)
    target = utc_datetime(occurrence_start)
    if target <= master['start'].astimezone(timezone.utc):
        raise SafetyError('Use whole-series deletion to remove the first occurrence and all later ones')
    # Validate the target's local offset, embedded timezone coverage and duration.
    # This remains a candidate, not a substitute for provider membership proof.
    occurrence_source(before, occurrence_start)
    try:
        cutoff = target - timedelta(seconds=1)
    except OverflowError:
        raise SafetyError('Future deletion requires valid occurrence dates') from None
    pieces = supported['RRULE'].split(';')
    kept = [piece for piece in pieces if not piece.startswith(('COUNT=', 'UNTIL='))]
    kept.append('UNTIL=' + cutoff.strftime('%Y%m%dT%H%M%SZ'))
    result = replace_properties(unfold(before), {'RRULE': ['RRULE:' + ';'.join(kept)]}, 'END:VEVENT')
    verify_created(result, result)
    return result
