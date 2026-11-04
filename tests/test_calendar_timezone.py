"""Provider timezone parsing uses synthetic definitions and no native data."""

import unittest
from datetime import datetime, timezone

from icloud_for_agents.calendar_timezone import (extract_timezone_definition, validate_timezone_coverage,
                                                validate_wall_duration, timezone_verification_key,
                                                validate_timezone_event_start)
from icloud_for_agents.core import SafetyError


CHICAGO = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Synthetic//EN
CALSCALE:GREGORIAN
BEGIN:VTIMEZONE
TZID:America/Chicago
BEGIN:DAYLIGHT
TZOFFSETFROM:-0600
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
DTSTART:20070311T020000
TZNAME:CDT
TZOFFSETTO:-0500
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:-0500
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
DTSTART:20071104T020000
TZNAME:CST
TZOFFSETTO:-0600
END:STANDARD
END:VTIMEZONE
END:VCALENDAR
"""

FIXED = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTIMEZONE
TZID:Etc/UTC
BEGIN:STANDARD
DTSTART:19700101T000000
TZOFFSETFROM:+0000
TZOFFSETTO:+0000
TZNAME:UTC
END:STANDARD
END:VTIMEZONE
END:VCALENDAR
"""

HISTORY = """BEGIN:STANDARD
DTSTART:18831118T120000
TZOFFSETFROM:-055036
TZOFFSETTO:-0600
TZNAME:CST
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:20060402T020000
TZOFFSETFROM:-0600
TZOFFSETTO:-0500
RRULE:FREQ=YEARLY;BYMONTH=4;BYDAY=1SU;UNTIL=20060402T080000Z
TZNAME:CDT
END:DAYLIGHT
BEGIN:STANDARD
DTSTART:20061029T020000
TZOFFSETFROM:-0500
TZOFFSETTO:-0600
RDATE:20061029T020000
TZNAME:CST
END:STANDARD
"""


def with_history(history=HISTORY):
    return CHICAGO.replace("TZID:America/Chicago\n", "TZID:America/Chicago\nX-LIC-LOCATION:America/Chicago\n" + history)


class HistoricalTimezoneTests(unittest.TestCase):
    def test_retains_full_history_but_normalizes_only_verification_evidence(self):
        expanded = with_history()
        lines = extract_timezone_definition(expanded, "America/Chicago")
        self.assertIn("DTSTART:18831118T120000", lines)
        self.assertIn("TZOFFSETFROM:-055036", lines)
        self.assertIn("X-LIC-LOCATION:America/Chicago", lines)
        self.assertEqual(lines.count("BEGIN:STANDARD"), 3)
        self.assertEqual(timezone_verification_key(expanded, "America/Chicago"),
                         timezone_verification_key(CHICAGO, "America/Chicago"))
        start = datetime.fromisoformat("2026-11-09T09:00:00-06:00")
        self.assertEqual(validate_timezone_coverage(expanded, "America/Chicago", start),
                         validate_timezone_coverage(CHICAGO, "America/Chicago", start))

    def test_twenty_one_historical_observances_remain_bounded_and_preserved(self):
        additional = "".join("BEGIN:STANDARD\nDTSTART:" + str(year) + "0101T000000\n"
                             "TZOFFSETFROM:-0600\nTZOFFSETTO:-0600\nTZNAME:CST\nEND:STANDARD\n"
                             for year in range(1884, 1902))
        expanded = with_history(HISTORY + additional)
        lines = extract_timezone_definition(expanded, "America/Chicago")
        self.assertEqual(lines.count("BEGIN:STANDARD") + lines.count("BEGIN:DAYLIGHT"), 23)
        self.assertEqual(timezone_verification_key(expanded, "America/Chicago"),
                         timezone_verification_key(CHICAGO, "America/Chicago"))

    def test_finite_month_day_history_is_validated_without_inventing_rules(self):
        additional = ("BEGIN:STANDARD\nDTSTART:19000101T000000\nTZOFFSETFROM:-0600\nTZOFFSETTO:-0600\n"
                      "RRULE:FREQ=YEARLY;BYMONTH=1;BYMONTHDAY=1;UNTIL=19020101T060000Z\nEND:STANDARD\n")
        self.assertEqual(timezone_verification_key(with_history(HISTORY + additional), "America/Chicago"),
                         timezone_verification_key(CHICAGO, "America/Chicago"))

    def test_repeated_historical_rdates_are_bounded_and_preserved(self):
        additional = ("BEGIN:STANDARD\nDTSTART:19000101T000000\nTZOFFSETFROM:-0600\nTZOFFSETTO:-0600\n"
                      "RDATE:19200101T000000\nRDATE:19420101T000000\nRDATE:19460101T000000\n"
                      "RDATE:19670101T000000\nEND:STANDARD\n")
        expanded = with_history(HISTORY + additional)
        lines = extract_timezone_definition(expanded, "America/Chicago")
        for value in ("19200101T000000", "19420101T000000", "19460101T000000", "19670101T000000"):
            self.assertIn("RDATE:" + value, lines)
        self.assertEqual(timezone_verification_key(expanded, "America/Chicago"),
                         timezone_verification_key(CHICAGO, "America/Chicago"))
        # A repeated identical onset within one component is a set duplicate;
        # competing components at the same UTC instant are ambiguous.
        duplicate_date = additional.replace("RDATE:19200101T000000", "RDATE:19000101T000000\nRDATE:19200101T000000")
        extract_timezone_definition(with_history(HISTORY + duplicate_date), "America/Chicago")
        for extra in (additional + additional,
                      additional.replace("RDATE:19670101T000000", "RDATE:20260101T000000"),
                      additional.replace("RDATE:19200101T000000", "RDATE:" + ",".join(["19200101T000000"] * 129))):
            with self.assertRaises(SafetyError):
                extract_timezone_definition(with_history(HISTORY + extra), "America/Chicago")

    def test_modern_rule_part_order_is_not_a_semantic_change(self):
        reordered = with_history().replace("FREQ=YEARLY;BYMONTH=3;BYDAY=2SU", "BYDAY=2SU;BYMONTH=3;FREQ=YEARLY")
        self.assertEqual(timezone_verification_key(reordered, "America/Chicago"),
                         timezone_verification_key(CHICAGO, "America/Chicago"))

    def test_historical_dtstart_can_be_a_one_off_before_later_yearly_onsets(self):
        additional = ("BEGIN:DAYLIGHT\nDTSTART:19360405T020000\nTZOFFSETFROM:-0600\nTZOFFSETTO:-0500\n"
                      "RRULE:FREQ=YEARLY;UNTIL=19410406T080000Z;BYMONTH=4;BYDAY=1SU\nEND:DAYLIGHT\n"
                      "BEGIN:STANDARD\nDTSTART:19361115T020000\nTZOFFSETFROM:-0500\nTZOFFSETTO:-0600\n"
                      "RRULE:FREQ=YEARLY;UNTIL=19410928T070000Z;BYMONTH=9;BYDAY=-1SU\nEND:STANDARD\n")
        expanded = with_history(HISTORY + additional)
        self.assertIn("DTSTART:19361115T020000", extract_timezone_definition(expanded, "America/Chicago"))
        self.assertEqual(timezone_verification_key(expanded, "America/Chicago"),
                         timezone_verification_key(CHICAGO, "America/Chicago"))

    def test_future_modern_properties_are_never_ignored(self):
        original = timezone_verification_key(CHICAGO, "America/Chicago")
        for changed in (CHICAGO.replace("BYMONTH=3;BYDAY=2SU", "BYMONTH=3;BYDAY=1SU").replace("20070311", "20070304"),
                        CHICAGO.replace("20070311T020000", "20070311T030000"),
                        CHICAGO.replace("TZNAME:CDT", "TZNAME:SUMMER")):
            self.assertNotEqual(timezone_verification_key(changed, "America/Chicago"), original)

    def test_history_cannot_reach_modern_cutoff_or_hide_later_onsets(self):
        extras = [
            "BEGIN:STANDARD\nDTSTART:20070311T020000\nTZOFFSETFROM:-0600\nTZOFFSETTO:-0600\nEND:STANDARD\n",
            # Earlier local time still occurs later in UTC: compare instants.
            "BEGIN:STANDARD\nDTSTART:20070311T010000\nTZOFFSETFROM:-0800\nTZOFFSETTO:-0600\nEND:STANDARD\n",
            "BEGIN:STANDARD\nDTSTART:20060101T000000\nTZOFFSETFROM:-0600\nTZOFFSETTO:-0600\nRDATE:20260101T000000\nEND:STANDARD\n",
        ]
        for history in [HISTORY + item for item in extras] + [
                HISTORY.replace("UNTIL=20060402T080000Z", "UNTIL=20070311T080000Z"),
                HISTORY.replace("UNTIL=20060402T080000Z", "COUNT=2"),
                HISTORY.replace(";UNTIL=20060402T080000Z", ""),
                HISTORY.replace("UNTIL=20060402T080000Z", "UNTIL=20060402T080000"),
                HISTORY.replace("RDATE:20061029T020000", "RDATE:20061029T020000Z"),
                HISTORY + HISTORY,
                HISTORY.replace("TZOFFSETFROM:-0600\nTZOFFSETTO:-0500", "TZOFFSETFROM:-0400\nTZOFFSETTO:-0500"),
                HISTORY.replace("BYMONTH=4", "BYMONTH=4;BYMONTH=5")]:
            with self.subTest(history=history), self.assertRaises(SafetyError):
                extract_timezone_definition(with_history(history), "America/Chicago")

    def test_timezone_metadata_and_unknown_properties_refuse(self):
        for value in (with_history().replace("X-LIC-LOCATION:America/Chicago", "X-LIC-LOCATION:Europe/Berlin"),
                      with_history().replace("X-LIC-LOCATION:America/Chicago", "X-LIC-LOCATION:America/Chicago\nX-LIC-LOCATION:America/Chicago"),
                      with_history().replace("X-LIC-LOCATION:America/Chicago", "TZURL:https://example.invalid/zone"),
                      with_history().replace("RDATE:20061029T020000", "RDATE:20061029T020000\nATTENDEE:mailto:other@example.invalid")):
            with self.subTest(value=value), self.assertRaises(SafetyError):
                extract_timezone_definition(value, "America/Chicago")

    def test_comparison_domain_requires_both_modern_origins_to_be_active(self):
        validate_timezone_event_start(with_history(), "America/Chicago", "20261109T090000")
        for value in ("20061030T090000", "20070312T090000", "20261101T013000", "20260308T023000"):
            with self.subTest(value=value), self.assertRaises(SafetyError):
                validate_timezone_event_start(with_history(), "America/Chicago", value)


class CalendarTimezoneTests(unittest.TestCase):
    def test_extracts_only_complete_supported_timezone(self):
        result = extract_timezone_definition(CHICAGO, "America/Chicago")
        self.assertEqual(result[0], "BEGIN:VTIMEZONE")
        self.assertEqual(result[-1], "END:VTIMEZONE")
        self.assertIn("TZID:America/Chicago", result)
        self.assertIn("RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU", result)
        self.assertNotIn("BEGIN:VCALENDAR", result)
        self.assertNotIn("PRODID:-//Synthetic//EN", result)

    def test_folded_lines_and_crlf_are_unfolded(self):
        folded = CHICAGO.replace("RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU", "RRULE:FREQ=YEARLY;BYMONTH=3;\n BYDAY=2SU")
        self.assertEqual(extract_timezone_definition(folded.replace("\n", "\r\n"), "America/Chicago"),
                         extract_timezone_definition(CHICAGO, "America/Chicago"))

    def test_component_names_are_case_insensitive(self):
        value = CHICAGO.replace("BEGIN:", "begin:").replace("END:", "end:")
        self.assertEqual(extract_timezone_definition(value, "America/Chicago"),
                         extract_timezone_definition(CHICAGO, "America/Chicago"))

    def test_fixed_offset_standard_is_supported(self):
        result = extract_timezone_definition(FIXED, "Etc/UTC")
        self.assertIn("TZOFFSETTO:+0000", result)
        self.assertNotIn("RRULE", "\n".join(result))

    def test_last_weekday_and_half_hour_rules_preserve_provider_values(self):
        berlin = (CHICAGO.replace("America/Chicago", "Europe/Berlin")
                  .replace("-0600", "+0100").replace("-0500", "+0200")
                  .replace("BYMONTH=3;BYDAY=2SU", "BYMONTH=3;BYDAY=-1SU")
                  .replace("BYMONTH=11;BYDAY=1SU", "BYMONTH=10;BYDAY=-1SU")
                  .replace("20070311T020000", "20070325T020000")
                  .replace("20071104T020000", "20071028T030000"))
        self.assertIn("RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU",
                      extract_timezone_definition(berlin, "Europe/Berlin"))
        lord_howe = (CHICAGO.replace("America/Chicago", "Australia/Lord_Howe")
                     .replace("-0600", "+1030").replace("-0500", "+1100")
                     .replace("BYMONTH=3;BYDAY=2SU", "BYMONTH=10;BYDAY=1SU")
                     .replace("BYMONTH=11;BYDAY=1SU", "BYMONTH=4;BYDAY=1SU")
                     .replace("20070311T020000", "20071007T020000")
                     .replace("20071104T020000", "20080406T020000"))
        self.assertIn("TZOFFSETFROM:+1030", extract_timezone_definition(lord_howe, "Australia/Lord_Howe"))

    def test_timezone_identity_is_exact_known_and_safe(self):
        for zone in ("Europe/Berlin", "America/Unknown", "America/Chicago\nATTENDEE:x", "../America/Chicago", "", None, []):
            with self.subTest(zone=zone), self.assertRaises(SafetyError):
                extract_timezone_definition(CHICAGO, zone)

    def test_embedded_events_and_scheduling_properties_refuse(self):
        for value in (
            CHICAGO.replace("END:VCALENDAR", "begin:vevent\nUID:other\nend:vevent\nEND:VCALENDAR"),
            CHICAGO.replace("TZID:America/Chicago", "TZID:America/Chicago\nATTENDEE:mailto:other@example.com"),
            CHICAGO.replace("VERSION:2.0", "VERSION:2.0\nMETHOD:REQUEST"),
            CHICAGO.replace("TZNAME:CDT", "TZNAME:CDT\nBEGIN:VALARM\nACTION:EMAIL\nEND:VALARM"),
            CHICAGO.replace("END:VTIMEZONE", "TZURL:https://example.com/timezone\nEND:VTIMEZONE"),
        ):
            with self.subTest(value=value), self.assertRaises(SafetyError):
                extract_timezone_definition(value, "America/Chicago")

    def test_duplicate_or_extra_components_and_properties_refuse(self):
        for value in (
            CHICAGO + CHICAGO,
            CHICAGO.replace("TZID:America/Chicago", "TZID:America/Chicago\nTZID:America/Chicago"),
            CHICAGO.replace("VERSION:2.0", "VERSION:2.0\nVERSION:2.0"),
            CHICAGO.replace("TZNAME:CST", "TZNAME:CST\nTZOFFSETTO:-0600"),
            CHICAGO.replace("END:VTIMEZONE", "BEGIN:STANDARD\nEND:STANDARD\nEND:VTIMEZONE"),
            CHICAGO.replace("END:DAYLIGHT", "END:STANDARD"),
            CHICAGO.replace("BEGIN:DAYLIGHT", "BEGIN;X=1:DAYLIGHT"),
            CHICAGO.replace("END:VCALENDAR", "END:VCALENDAR\nTZID:America/Chicago"),
        ):
            with self.subTest(value=value), self.assertRaises(SafetyError):
                extract_timezone_definition(value, "America/Chicago")

    def test_finite_unknown_or_invalid_transition_rules_refuse(self):
        for rule in (
            "FREQ=YEARLY;BYMONTH=3;BYDAY=2SU;UNTIL=20300310T080000Z",
            "FREQ=YEARLY;BYMONTH=3;BYDAY=2SU;COUNT=10",
            "FREQ=MONTHLY;BYMONTH=3;BYDAY=2SU",
            "FREQ=YEARLY;BYMONTH=13;BYDAY=2SU",
            "FREQ=YEARLY;BYMONTH=3;BYDAY=0SU",
            "FREQ=YEARLY;BYMONTH=3;BYDAY=2XX",
            "FREQ=YEARLY;BYMONTH=3;BYDAY=1SU",
            "FREQ=YEARLY;BYMONTH=3;BYDAY=2SU;BYMONTH=3",
            "FREQ=YEARLY;BYMONTH=3;BYDAY=2SU;INTERVAL=2",
            "FREQ=YEARLY;BYMONTH=3;BYDAY=2SU;X-CUSTOM=1",
        ):
            value = CHICAGO.replace("FREQ=YEARLY;BYMONTH=3;BYDAY=2SU", rule)
            with self.subTest(rule=rule), self.assertRaises(SafetyError):
                extract_timezone_definition(value, "America/Chicago")

    def test_invalid_dates_offsets_and_inconsistent_pair_refuse(self):
        for value in (
            CHICAGO.replace("20070311T020000", "20070230T020000"),
            CHICAGO.replace("20070311T020000", "20070311T020000Z"),
            CHICAGO.replace("TZOFFSETFROM:-0600", "TZOFFSETFROM:-2400"),
            CHICAGO.replace("TZOFFSETFROM:-0600", "TZOFFSETFROM:-0660"),
            CHICAGO.replace("TZOFFSETTO:-0500", "TZOFFSETTO:-0400"),
            CHICAGO.replace("TZNAME:CDT", "TZNAME:https://example.com"),
            FIXED.replace("TZOFFSETFROM:+0000", "TZOFFSETFROM:-0000").replace("TZID:Etc/UTC", "TZID:America/Chicago"),
        ):
            with self.subTest(value=value), self.assertRaises(SafetyError):
                extract_timezone_definition(value, "America/Chicago")

    def test_missing_fixed_or_missing_rule_pairs_refuse(self):
        for value in (CHICAGO.replace("RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU\n", ""),
                      CHICAGO.replace("TZOFFSETFROM:-0600\n", ""),
                      CHICAGO.replace("VERSION:2.0", "VERSION:1.0"),
                      CHICAGO.replace("CALSCALE:GREGORIAN", "CALSCALE:OTHER")):
            with self.subTest(value=value), self.assertRaises(SafetyError):
                extract_timezone_definition(value, "America/Chicago")

    def test_controls_and_resource_bounds_refuse(self):
        for value in (None, CHICAGO.replace("CST", "C\x00ST"), CHICAGO.replace("CST", "C\x85ST"),
                      CHICAGO.replace("\n", "\r"), CHICAGO.replace("CST", "C\tST"),
                      CHICAGO + "x" * 65536, CHICAGO.replace("CDT", "x" * 4097)):
            with self.subTest(value=value), self.assertRaises(SafetyError):
                extract_timezone_definition(value, "America/Chicago")


class CalendarTimezoneCoverageTests(unittest.TestCase):
    def test_chicago_transition_boundaries_and_daily_probes_agree(self):
        result = validate_timezone_coverage(CHICAGO, "America/Chicago", datetime(2026, 11, 4, 15, tzinfo=timezone.utc))
        self.assertEqual(result["transition_start_year"], 2026)
        self.assertEqual(result["transition_end_year"], 2031)
        self.assertEqual(result["provider_transition_boundaries_checked"], 12)
        self.assertGreater(result["daily_offset_probes_checked"], 1800)
        self.assertEqual(result["checked_start"], "2026-11-04T15:00:00+00:00")
        self.assertEqual(result["checked_end_exclusive"], "2032-01-01T00:00:00+00:00")
        self.assertFalse(result["all_recurrence_instants_verified"])
        self.assertFalse(result["indefinite_future_verified"])
        self.assertFalse(result["complete_transition_inventory_proven"])

    def test_fixed_utc_agrees_across_seasonal_daily_probes(self):
        result = validate_timezone_coverage(FIXED, "Etc/UTC", datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(result["provider_transition_boundaries_checked"], 0)
        self.assertGreater(result["daily_offset_probes_checked"], 2100)

    def test_berlin_and_lord_howe_transition_boundaries_agree(self):
        berlin = (CHICAGO.replace("America/Chicago", "Europe/Berlin")
                  .replace("-0600", "+0100").replace("-0500", "+0200")
                  .replace("BYMONTH=3;BYDAY=2SU", "BYMONTH=3;BYDAY=-1SU")
                  .replace("BYMONTH=11;BYDAY=1SU", "BYMONTH=10;BYDAY=-1SU")
                  .replace("20070311T020000", "20070325T020000")
                  .replace("20071104T020000", "20071028T030000"))
        lord_howe = (CHICAGO.replace("America/Chicago", "Australia/Lord_Howe")
                     .replace("-0600", "+1030").replace("-0500", "+1100")
                     .replace("BYMONTH=3;BYDAY=2SU", "BYMONTH=10;BYDAY=1SU")
                     .replace("BYMONTH=11;BYDAY=1SU", "BYMONTH=4;BYDAY=1SU")
                     .replace("20070311T020000", "20071007T020000")
                     .replace("20071104T020000", "20080406T020000"))
        for definition, name in ((berlin, "Europe/Berlin"), (lord_howe, "Australia/Lord_Howe")):
            with self.subTest(name=name):
                result = validate_timezone_coverage(definition, name, datetime(2026, 11, 4, tzinfo=timezone.utc))
                self.assertEqual(result["provider_transition_boundaries_checked"], 12)

    def test_structurally_valid_wrong_offsets_refuse(self):
        shifted = CHICAGO.replace("-0600", "-0700").replace("-0500", "-0600")
        self.assertTrue(extract_timezone_definition(shifted, "America/Chicago"))
        with self.assertRaises(SafetyError):
            validate_timezone_coverage(shifted, "America/Chicago", datetime(2026, 11, 4, tzinfo=timezone.utc))

    def test_structurally_valid_wrong_transition_date_refuses(self):
        shifted = (CHICAGO.replace("BYMONTH=3;BYDAY=2SU", "BYMONTH=3;BYDAY=1SU")
                   .replace("20070311T020000", "20070304T020000"))
        self.assertTrue(extract_timezone_definition(shifted, "America/Chicago"))
        with self.assertRaises(SafetyError):
            validate_timezone_coverage(shifted, "America/Chicago", datetime(2026, 11, 4, tzinfo=timezone.utc))

    def test_transition_local_clock_time_is_checked_as_utc_boundary(self):
        shifted = CHICAGO.replace("20070311T020000", "20070311T030000")
        self.assertTrue(extract_timezone_definition(shifted, "America/Chicago"))
        with self.assertRaises(SafetyError):
            validate_timezone_coverage(shifted, "America/Chicago", datetime(2026, 11, 4, tzinfo=timezone.utc))

    def test_fixed_definition_cannot_impersonate_a_dst_zone(self):
        fake = FIXED.replace("TZID:Etc/UTC", "TZID:America/Chicago")
        self.assertTrue(extract_timezone_definition(fake, "America/Chicago"))
        with self.assertRaises(SafetyError):
            validate_timezone_coverage(fake, "America/Chicago", datetime(2026, 11, 4, tzinfo=timezone.utc))

    def test_start_before_any_provider_rule_refuses(self):
        for start in (datetime(2006, 1, 1, tzinfo=timezone.utc), datetime(2007, 11, 3, 12, tzinfo=timezone.utc)):
            with self.subTest(start=start), self.assertRaises(SafetyError):
                validate_timezone_coverage(CHICAGO, "America/Chicago", start)
        with self.assertRaises(SafetyError):
            validate_timezone_coverage(FIXED, "Etc/UTC", datetime(1969, 1, 1, tzinfo=timezone.utc))

    def test_naive_unknown_or_unbounded_start_refuses(self):
        for start in (None, "2026-11-04", datetime(2026, 11, 4), datetime(9999, 1, 1, tzinfo=timezone.utc)):
            with self.subTest(start=start), self.assertRaises(SafetyError):
                validate_timezone_coverage(CHICAGO, "America/Chicago", start)


class CalendarWallDurationTests(unittest.TestCase):
    def check(self, start, end, definition=CHICAGO, name="America/Chicago"):
        return validate_wall_duration(definition, name, datetime.fromisoformat(start), datetime.fromisoformat(end))

    def test_normal_local_morning_duration_is_explicit(self):
        result = self.check("2026-11-04T09:00:00-06:00", "2026-11-04T10:00:00-06:00")
        self.assertEqual(result["elapsed_seconds"], 3600)
        self.assertEqual(result["provider_transition_clock_intervals_checked"], 2)
        self.assertFalse(result["recurrence_dates_evaluated"])

    def test_initial_clock_change_and_overnight_intervals_refuse(self):
        for start, end in (("2026-03-08T01:00:00-06:00", "2026-03-08T04:00:00-05:00"),
                           ("2026-11-01T00:00:00-05:00", "2026-11-01T03:00:00-06:00"),
                           ("2026-11-04T23:00:00-06:00", "2026-11-05T01:00:00-06:00")):
            with self.subTest(start=start, end=end), self.assertRaises(SafetyError):
                self.check(start, end)

    def test_future_clock_change_interval_refuses_on_an_ordinary_start_date(self):
        for start, end in (("2026-11-04T01:30:00-06:00", "2026-11-04T02:30:00-06:00"),
                           ("2026-11-04T02:15:00-06:00", "2026-11-04T03:15:00-06:00"),
                           ("2026-11-04T01:30:00-06:00", "2026-11-04T03:30:00-06:00")):
            with self.subTest(start=start, end=end), self.assertRaises(SafetyError):
                self.check(start, end)

    def test_touching_exact_transition_boundaries_is_conservatively_refused(self):
        for start, end in (("2026-11-04T00:30:00-06:00", "2026-11-04T01:00:00-06:00"),
                           ("2026-11-04T03:00:00-06:00", "2026-11-04T04:00:00-06:00")):
            with self.subTest(start=start, end=end), self.assertRaises(SafetyError):
                self.check(start, end)

    def test_fixed_zone_does_not_invent_clock_change_restrictions(self):
        result = self.check("2026-11-04T01:00:00+00:00", "2026-11-04T02:00:00+00:00", FIXED, "Etc/UTC")
        self.assertEqual(result["provider_transition_clock_intervals_checked"], 0)
        self.assertEqual(result["elapsed_seconds"], 3600)

    def test_offset_mismatch_nonpositive_and_ambiguous_times_refuse(self):
        for start, end in (("2026-11-04T09:00:00+00:00", "2026-11-04T10:00:00+00:00"),
                           ("2026-11-04T10:00:00-06:00", "2026-11-04T09:00:00-06:00"),
                           ("2026-11-04T09:00:00-06:00", "2026-11-04T09:00:00-06:00"),
                           ("2026-11-01T01:15:00-05:00", "2026-11-01T01:30:00-05:00"),
                           ("2026-03-08T02:15:00-06:00", "2026-03-08T02:30:00-06:00")):
            with self.subTest(start=start, end=end), self.assertRaises(SafetyError):
                self.check(start, end)


if __name__ == "__main__":
    unittest.main()
