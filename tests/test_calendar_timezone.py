"""Provider timezone parsing uses synthetic definitions and no native data."""

import unittest
from datetime import datetime, timezone

from icloud_for_agents.calendar_timezone import extract_timezone_definition, validate_timezone_coverage, validate_wall_duration
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
