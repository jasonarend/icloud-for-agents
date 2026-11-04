"""Strict extraction of provider-supplied, supported VTIMEZONE definitions.

The provider supplies the transition rules. This module never discovers a URL,
evaluates calendar content or infers future rules from sampled UTC offsets.
Callers must separately bind the provider, definition and applicable date scope.
"""

import calendar
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .core import SafetyError


MAX_BYTES = 65536
MAX_LINES = 256
WEEKDAYS = {name: index for index, name in enumerate(("MO", "TU", "WE", "TH", "FR", "SA", "SU"))}


def _refuse():
    raise SafetyError("Provider timezone definition is malformed, ambiguous or unsupported")


def _offset(value):
    if not re.fullmatch(r"[+-][0-9]{4}(?:[0-9]{2})?", value):
        _refuse()
    hours, minutes = int(value[1:3]), int(value[3:5])
    seconds = int(value[5:7]) if len(value) == 7 else 0
    if hours > 23 or minutes > 59 or seconds > 59:
        _refuse()
    total = hours * 3600 + minutes * 60 + seconds
    if value[0] == "-" and total == 0:
        _refuse()
    return total if value[0] == "+" else -total


def _start(value):
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}", value):
        _refuse()
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%S")
    except ValueError:
        _refuse()


def _observance(values):
    required = {"DTSTART", "TZOFFSETFROM", "TZOFFSETTO"}
    if not required <= values.keys() or values.keys() - required - {"RRULE", "TZNAME"}:
        _refuse()
    start = _start(values["DTSTART"])
    offset_from, offset_to = _offset(values["TZOFFSETFROM"]), _offset(values["TZOFFSETTO"])
    if "TZNAME" in values and not re.fullmatch(r"[A-Za-z0-9 _+\-]{1,64}", values["TZNAME"]):
        _refuse()
    rule = values.get("RRULE")
    if rule is None:
        if offset_from != offset_to:
            _refuse()
        return offset_from, offset_to, False
    pieces = rule.upper().split(";")
    if any(piece.count("=") != 1 for piece in pieces):
        _refuse()
    parts = dict(piece.split("=", 1) for piece in pieces)
    if (len(parts) != len(pieces) or not {"FREQ", "BYMONTH", "BYDAY"} <= parts.keys()
            or parts.keys() - {"FREQ", "BYMONTH", "BYDAY", "INTERVAL"}
            or parts["FREQ"] != "YEARLY" or parts.get("INTERVAL", "1") != "1"
            or not re.fullmatch(r"(?:[1-9]|1[0-2])", parts["BYMONTH"])):
        _refuse()
    day = re.fullmatch(r"(-?[1-5])(MO|TU|WE|TH|FR|SA|SU)", parts["BYDAY"])
    if day is None:
        _refuse()
    ordinal, weekday = int(day[1]), WEEKDAYS[day[2]]
    actual_ordinal = ((start.day - 1) // 7 + 1 if ordinal > 0 else
                      -((calendar.monthrange(start.year, start.month)[1] - start.day) // 7 + 1))
    if (start.month != int(parts["BYMONTH"]) or start.weekday() != weekday
            or actual_ordinal != ordinal or offset_from == offset_to):
        _refuse()
    return offset_from, offset_to, True


def extract_timezone_definition(raw: str, timezone_name: str) -> list[str]:
    """Return one supported unfolded VTIMEZONE, refusing unrelated content.

    Accepted definitions contain either a fixed-offset STANDARD observance or
    one STANDARD and one DAYLIGHT with reciprocal offsets and open-ended yearly
    month/ordinal-weekday rules. Finite historical observances may precede both
    modern origins; matching X-LIC-LOCATION metadata is retained. Unknown forms refuse.
    """
    if (not isinstance(timezone_name, str) or not 1 <= len(timezone_name) <= 255
            or not re.fullmatch(r"[A-Za-z0-9_+.-]+(?:/[A-Za-z0-9_+.-]+)*", timezone_name)
            or any(part in {".", ".."} for part in timezone_name.split("/"))):
        _refuse()
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        _refuse()
    if not isinstance(raw, str):
        _refuse()
    try:
        if not 1 <= len(raw.encode("utf-8")) <= MAX_BYTES:
            _refuse()
    except UnicodeError:
        _refuse()
    text = raw.replace("\r\n", "\n")
    if ("\r" in text or any(ord(c) < 32 and c not in "\n\t" for c in text)
            or any(127 <= ord(c) <= 159 for c in text)):
        _refuse()
    lines = re.sub(r"\n[ \t]", "", text).split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines or len(lines) > MAX_LINES:
        _refuse()
    stack, result, root, timezone, observations = [], [], {}, {}, []
    seen_calendar = seen_timezone = False
    current = None
    for line in lines:
        if not line or len(line.encode("utf-8")) > 4096 or ":" not in line or "\t" in line:
            _refuse()
        key, value = line.split(":", 1)
        key = key.upper()
        # Parameters are unnecessary in this supported timezone subset.
        if not re.fullmatch(r"[A-Z-]+", key) or not value:
            _refuse()
        if key == "BEGIN":
            component = value.upper()
            allowed = ((not stack and not seen_calendar and component == "VCALENDAR") or
                       (stack == ["VCALENDAR"] and not seen_timezone and component == "VTIMEZONE") or
                       (stack == ["VCALENDAR", "VTIMEZONE"] and component in {"STANDARD", "DAYLIGHT"}
                        and len(observations) < 40))
            if not allowed:
                _refuse()
            stack.append(component)
            if component == "VCALENDAR":
                seen_calendar = True
            elif component == "VTIMEZONE":
                seen_timezone = True
            else:
                current = {}
                observations.append((component, current))
            if len(stack) > 1:
                result.append("BEGIN:" + component)
        elif key == "END":
            component = value.upper()
            if not stack or stack[-1] != component:
                _refuse()
            if len(stack) > 1:
                result.append("END:" + component)
            stack.pop()
            current = None
        else:
            if stack == ["VCALENDAR"]:
                target, allowed = root, {"VERSION", "PRODID", "CALSCALE"}
            elif stack == ["VCALENDAR", "VTIMEZONE"]:
                target, allowed = timezone, {"TZID", "X-LIC-LOCATION"}
            elif len(stack) == 3 and current is not None:
                target, allowed = current, {"DTSTART", "TZOFFSETFROM", "TZOFFSETTO", "RRULE", "RDATE", "TZNAME"}
            else:
                _refuse()
            repeated_date = key == "RDATE" and len(stack) == 3 and current is not None
            if key not in allowed or (key in target and not repeated_date):
                _refuse()
            target[key] = target[key] + "," + value if repeated_date and key in target else value
            if len(stack) > 1:
                result.append(key + ":" + value)
    if (stack or not seen_calendar or not seen_timezone or root.get("VERSION") != "2.0"
            or root.get("CALSCALE", "GREGORIAN").upper() != "GREGORIAN"
            or timezone.get("TZID") != timezone_name
            or timezone.get("X-LIC-LOCATION", timezone_name) != timezone_name):
        _refuse()
    _partition_observances(observations)
    return result


def _definition_components(lines):
    result, current = [], None
    for line in lines:
        name, value = line.split(":", 1)
        if name == "BEGIN" and value in {"STANDARD", "DAYLIGHT"}:
            current = {}
            result.append((value, current))
        elif name == "END" and value in {"STANDARD", "DAYLIGHT"}:
            current = None
        elif current is not None:
            current[name] = current[name] + "," + value if name == "RDATE" and name in current else value
    return result


def _origin(values):
    try:
        return (_start(values["DTSTART"]) - timedelta(seconds=_offset(values["TZOFFSETFROM"]))).replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError):
        _refuse()


def _historical_transitions(values):
    required = {"DTSTART", "TZOFFSETFROM", "TZOFFSETTO"}
    if not required <= values.keys() or values.keys() - required - {"RRULE", "RDATE", "TZNAME"}:
        _refuse()
    if "TZNAME" in values and not re.fullmatch(r"[A-Za-z0-9 _+\-]{1,64}", values["TZNAME"]):
        _refuse()
    start = _start(values["DTSTART"])
    before, after = _offset(values["TZOFFSETFROM"]), _offset(values["TZOFFSETTO"])
    dates = {start}
    if "RDATE" in values:
        items = values["RDATE"].split(",")
        if not 1 <= len(items) <= 128:
            _refuse()
        dates.update(_start(item) for item in items)
    if "RRULE" in values:
        pieces = values["RRULE"].upper().split(";")
        if any(piece.count("=") != 1 for piece in pieces):
            _refuse()
        parts = dict(piece.split("=", 1) for piece in pieces)
        if (len(parts) != len(pieces) or not {"FREQ", "UNTIL", "BYMONTH"} <= parts.keys()
                or parts.keys() - {"FREQ", "UNTIL", "BYMONTH", "BYDAY", "BYMONTHDAY", "INTERVAL"}
                or parts["FREQ"] != "YEARLY" or parts.get("INTERVAL", "1") != "1"
                or ("BYDAY" in parts) == ("BYMONTHDAY" in parts)
                or not re.fullmatch(r"(?:[1-9]|1[0-2])", parts["BYMONTH"])
                or not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", parts["UNTIL"])):
            _refuse()
        until = _start(parts["UNTIL"][:-1]).replace(tzinfo=timezone.utc)
        if until < _origin(values):
            _refuse()
        if "BYDAY" in parts and not re.fullmatch(r"(-?[1-5])(MO|TU|WE|TH|FR|SA|SU)", parts["BYDAY"]):
            _refuse()
        if "BYMONTHDAY" in parts and not re.fullmatch(r"(?:[1-9]|[12][0-9]|3[01])", parts["BYMONTHDAY"]):
            _refuse()
        if until.year - start.year > 2048:
            _refuse()
        for year in range(start.year, min(9999, until.year + 1) + 1):
            if "BYDAY" in parts:
                candidate_values = dict(values, RRULE=";".join(k + "=" + v for k, v in parts.items() if k != "UNTIL"))
                try:
                    local, instant, _, _ = _yearly_transition(candidate_values, year)
                except SafetyError:
                    # A fifth weekday need not exist in every historical year.
                    continue
            else:
                try:
                    local = datetime(year, int(parts["BYMONTH"]), int(parts["BYMONTHDAY"]), start.hour, start.minute, start.second)
                except ValueError:
                    continue
                instant = (local - timedelta(seconds=before)).replace(tzinfo=timezone.utc)
            if local >= start and instant <= until:
                dates.add(local)
    result = []
    for local in dates:
        try:
            result.append(((local - timedelta(seconds=before)).replace(tzinfo=timezone.utc), before, after))
        except (ValueError, OverflowError):
            _refuse()
    return result


def _partition_observances(components):
    # A single fixed definition retains its original strict subset. Historical
    # enrichment is admitted only around an unchanged open-ended modern pair.
    if len(components) == 1 and components[0][0] == "STANDARD":
        if _observance(components[0][1])[2]:
            _refuse()
        return {"STANDARD": components[0][1]}
    modern, historical = {}, []
    for kind, values in components:
        if "RRULE" in values and not re.search(r"(?:^|;)UNTIL=", values["RRULE"], re.I):
            _observance(values)
            if kind in modern:
                _refuse()
            modern[kind] = values
        else:
            historical.append(values)
    if set(modern) != {"STANDARD", "DAYLIGHT"}:
        _refuse()
    standard, daylight = _observance(modern["STANDARD"]), _observance(modern["DAYLIGHT"])
    if not standard[2] or not daylight[2] or standard[:2] != daylight[1::-1]:
        _refuse()
    first = min((_origin(values), _offset(values["TZOFFSETFROM"])) for values in modern.values())
    transitions = []
    for values in historical:
        if "RRULE" in values:
            match = re.search(r"(?:^|;)UNTIL=([0-9]{8}T[0-9]{6}Z)(?:;|$)", values["RRULE"], re.I)
            if match is None or _start(match[1][:-1]).replace(tzinfo=timezone.utc) >= first[0]:
                _refuse()
        added = _historical_transitions(values)
        if any(instant >= first[0] for instant, _, _ in added) or len(transitions) + len(added) > 4096:
            _refuse()
        transitions.extend(added)
    transitions.sort()
    if (any(current[0] <= previous[0] or current[1] != previous[2]
            for previous, current in zip(transitions, transitions[1:]))
            or (transitions and transitions[-1][2] != first[1])):
        _refuse()
    return modern


def _definition_observances(lines):
    """Only modern rules may determine future coverage and duration evidence."""
    return _partition_observances(_definition_components(lines))


def timezone_verification_key(raw: str, timezone_name: str) -> tuple:
    """Normalize comparison evidence only; never use this key as write content.

    Valid finite historical additions precede both modern rule origins in UTC.
    Exact modern property sets, including their DTSTART and offsets, remain in
    the comparison. No external metadata or unknown properties are ignored.
    """
    lines = extract_timezone_definition(raw, timezone_name)
    modern = _definition_observances(lines)
    components = []
    for kind, values in sorted(modern.items()):
        canonical = dict(values)
        if "RRULE" in canonical:
            canonical["RRULE"] = ";".join(sorted(canonical["RRULE"].upper().split(";")))
        components.append((kind, tuple(sorted(canonical.items()))))
    return timezone_name, tuple(components)


def validate_timezone_event_start(raw: str, timezone_name: str, local_start: str):
    """Require the event to start after both modern observances became active."""
    modern = _definition_observances(extract_timezone_definition(raw, timezone_name))
    wall = _start(local_start)
    if any(wall < _start(values["DTSTART"]) for values in modern.values()):
        _refuse()
    zone = ZoneInfo(timezone_name)
    supplied = wall.replace(tzinfo=zone)
    try:
        instant = supplied.astimezone(timezone.utc)
        if (supplied.utcoffset() != wall.replace(tzinfo=zone, fold=1).utcoffset()
                or instant.astimezone(zone).replace(tzinfo=None) != wall
                or any(instant < _origin(values) for values in modern.values())):
            _refuse()
    except (ValueError, OverflowError):
        _refuse()

def _yearly_transition(values, year):
    start = _start(values["DTSTART"])
    parts = dict(piece.split("=", 1) for piece in values["RRULE"].upper().split(";"))
    month = int(parts["BYMONTH"])
    matched = re.fullmatch(r"(-?[1-5])(MO|TU|WE|TH|FR|SA|SU)", parts["BYDAY"])
    ordinal, weekday = int(matched[1]), WEEKDAYS[matched[2]]
    last_day = calendar.monthrange(year, month)[1]
    if ordinal > 0:
        first_weekday = datetime(year, month, 1).weekday()
        day = 1 + (weekday - first_weekday) % 7 + (ordinal - 1) * 7
    else:
        last_weekday = datetime(year, month, last_day).weekday()
        day = last_day - (last_weekday - weekday) % 7 + (ordinal + 1) * 7
    if not 1 <= day <= last_day:
        raise SafetyError("Provider timezone rule has no transition in a checked year")
    local = datetime(year, month, day, start.hour, start.minute, start.second)
    offset_from, offset_to = _offset(values["TZOFFSETFROM"]), _offset(values["TZOFFSETTO"])
    instant = (local - timedelta(seconds=offset_from)).replace(tzinfo=timezone.utc)
    return local, instant, offset_from, offset_to


def validate_timezone_coverage(raw: str, timezone_name: str, start_datetime: datetime) -> dict:
    """Check bounded agreement with ZoneInfo, with explicit sampling limits.

    Every provider yearly transition in the start year through the next five
    calendar years is checked immediately before and after its UTC boundary.
    Daily UTC offset probes and the exact supplied start cover the returned
    interval. These observations do not prove a complete transition inventory,
    every recurrence instant, or agreement beyond the checked interval.
    """
    lines = extract_timezone_definition(raw, timezone_name)
    if not isinstance(start_datetime, datetime):
        raise SafetyError("Timezone validation requires an explicit aware start datetime")
    try:
        if start_datetime.tzinfo is None or start_datetime.utcoffset() is None:
            raise ValueError()
        start_utc = start_datetime.astimezone(timezone.utc)
        zone = ZoneInfo(timezone_name)
        local_start = start_utc.astimezone(zone).replace(tzinfo=None)
        start_year, end_year = local_start.year, local_start.year + 5
        end_utc = datetime(end_year + 1, 1, 1, tzinfo=timezone.utc)
    except (ValueError, OverflowError):
        raise SafetyError("Timezone validation requires a bounded aware date interval") from None
    observations = _definition_observances(lines)
    if any(local_start < _start(values["DTSTART"]) for values in observations.values()):
        raise SafetyError("Requested event precedes the provider timezone rule coverage")

    transitions = []
    checked_boundaries = 0
    for values in observations.values():
        if "RRULE" not in values:
            continue
        origin = _start(values["DTSTART"])
        for year in range(max(1, start_year - 1), end_year + 1):
            try:
                local, instant, offset_from, offset_to = _yearly_transition(values, year)
            except (ValueError, OverflowError):
                raise SafetyError("Provider timezone transition exceeds the checked date range") from None
            if local < origin:
                continue
            transitions.append((instant, offset_from, offset_to))
            if year >= start_year:
                try:
                    before = (instant - timedelta(seconds=1)).astimezone(zone).utcoffset()
                    after = instant.astimezone(zone).utcoffset()
                except (ValueError, OverflowError):
                    raise SafetyError("Provider timezone boundary exceeds the checked date range") from None
                if before != timedelta(seconds=offset_from) or after != timedelta(seconds=offset_to):
                    raise SafetyError("Provider timezone transition disagrees with the local timezone database")
                checked_boundaries += 1
    transitions.sort()
    if any(current[0] <= previous[0] or current[1] != previous[2]
           for previous, current in zip(transitions, transitions[1:])):
        raise SafetyError("Provider timezone transitions overlap or have inconsistent offsets")
    instants = [value[0] for value in transitions]
    fixed_offset = _offset(observations["STANDARD"]["TZOFFSETTO"])

    def check_probe(instant):
        if transitions:
            index = bisect_right(instants, instant) - 1
            expected = transitions[index][2] if index >= 0 else transitions[0][1]
        else:
            expected = fixed_offset
        try:
            observed = instant.astimezone(zone).utcoffset()
        except (ValueError, OverflowError):
            raise SafetyError("Provider timezone probe exceeds the checked date range") from None
        if observed != timedelta(seconds=expected):
            raise SafetyError("Provider timezone offset disagrees with the local timezone database")

    check_probe(start_utc)
    # UTC noon avoids ambiguous local probe timestamps and includes every season.
    probe = datetime(start_utc.year, start_utc.month, start_utc.day, 12, tzinfo=timezone.utc)
    if probe < start_utc:
        probe += timedelta(days=1)
    daily_probes = 0
    while probe < end_utc:
        check_probe(probe)
        daily_probes += 1
        probe += timedelta(days=1)
    check_probe(end_utc - timedelta(seconds=1))
    return {
        "checked_start": start_utc.isoformat(),
        "checked_end_exclusive": end_utc.isoformat(),
        "transition_start_year": start_year,
        "transition_end_year": end_year,
        "provider_transition_boundaries_checked": checked_boundaries,
        "daily_offset_probes_checked": daily_probes,
        "exact_start_and_end_probes_checked": True,
        "agreement": "provider_transition_boundaries_and_sampled_offsets",
        "complete_transition_inventory_proven": False,
        "all_recurrence_instants_verified": False,
        "indefinite_future_verified": False,
    }


def validate_wall_duration(raw: str, timezone_name: str,
                           start_datetime: datetime, end_datetime: datetime) -> dict:
    """Admit same-day durations clear of all provider transition clock intervals.

    Recurring DTEND uses an exact elapsed duration. This conservative subset
    refuses wall-time intervals touching a provider's clock-change interval,
    without trying to decide which recurrence dates will encounter that change.
    """
    lines = extract_timezone_definition(raw, timezone_name)
    zone = ZoneInfo(timezone_name)
    local = []
    try:
        for supplied in (start_datetime, end_datetime):
            if not isinstance(supplied, datetime) or supplied.tzinfo is None or supplied.microsecond:
                raise ValueError()
            value = supplied.astimezone(zone)
            wall = value.replace(tzinfo=None)
            if (supplied.utcoffset() is None or supplied.utcoffset() != value.utcoffset()
                    or supplied.replace(tzinfo=None) != wall
                    or wall.replace(tzinfo=zone, fold=0).utcoffset() != wall.replace(tzinfo=zone, fold=1).utcoffset()):
                raise ValueError()
            local.append(value)
        start, end = local
        if (start.date() != end.date() or start.utcoffset() != end.utcoffset()
                or start.astimezone(timezone.utc) >= end.astimezone(timezone.utc)):
            raise ValueError()
    except (ValueError, TypeError, OverflowError):
        raise SafetyError("Recurring duration requires an unambiguous positive same-day interval with unchanged offset") from None
    checked = 0
    for values in _definition_observances(lines).values():
        offset_from, offset_to, changes = _observance(values)
        if not changes:
            continue
        transition = _start(values["DTSTART"])
        try:
            changed_wall = transition + timedelta(seconds=offset_to - offset_from)
        except (ValueError, OverflowError):
            raise SafetyError("Provider timezone duration boundary exceeds supported dates") from None
        if changed_wall.date() != transition.date():
            raise SafetyError("Recurring duration does not support date-crossing timezone transitions")
        first, last = sorted((transition.time(), changed_wall.time()))
        # Include both boundaries: a transition date may make either one
        # nonexistent or ambiguous, so this subset refuses even exact contact.
        if start.time().replace(tzinfo=None) <= last and end.time().replace(tzinfo=None) >= first:
            raise SafetyError("Recurring wall-time duration touches a provider clock-change interval")
        checked += 1
    return {
        "same_local_date": True,
        "initial_offset_unchanged": True,
        "elapsed_seconds": int((end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds()),
        "provider_transition_clock_intervals_checked": checked,
        "validation": "same_day_interval_clear_of_provider_transition_clocks",
        "recurrence_dates_evaluated": False,
    }
