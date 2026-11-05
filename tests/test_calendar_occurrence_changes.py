import unittest

from icloud_for_agents.calendar_occurrence_changes import occurrence_content, verify_occurrence_write
from icloud_for_agents.core import SafetyError
from icloud_for_agents.dav_changes import event_content


class OccurrenceChangesTests(unittest.TestCase):
    def setUp(self):
        self.before = event_content({"summary": "Synthetic series", "start": "2026-11-02T15:00:00Z",
                                     "end": "2026-11-02T16:00:00Z", "timezone": "UTC",
                                     "recurrence": {"frequency": "weekly", "count": 4}})
        self.start = "2026-11-09T15:00:00Z"

    def test_one_occurrence_text_update_retains_the_original_master(self):
        value = occurrence_content(self.before, "update", self.start, {"summary": "Changed occurrence"})
        master = value.split("BEGIN:VEVENT", 2)[1].split("END:VEVENT", 1)[0]
        exception = value.split("BEGIN:VEVENT", 2)[2].split("END:VEVENT", 1)[0]
        self.assertIn("SUMMARY:Synthetic series", master)
        self.assertIn("RRULE:FREQ=WEEKLY;INTERVAL=1;COUNT=4", master)
        self.assertIn("SUMMARY:Changed occurrence", exception)
        self.assertIn("RECURRENCE-ID:20261109T150000Z", exception)
        self.assertIn("DTSTART:20261109T150000Z", exception)
        self.assertIn("DTEND:20261109T160000Z", exception)
        self.assertNotIn("RRULE:", exception)
        self.assertEqual(master.split("UID:")[1].splitlines()[0], exception.split("UID:")[1].splitlines()[0])
        verify_occurrence_write(value, value)

    def test_cancel_occurrence_retains_master_and_excludes_original_identity(self):
        value = occurrence_content(self.before, "delete_occurrence", self.start)
        self.assertEqual(value.count("BEGIN:VEVENT"), 1)
        self.assertIn("EXDATE:20261109T150000Z", value)
        self.assertIn("DTSTART:20261102T150000Z", value)
        self.assertIn("RRULE:", value)
        verify_occurrence_write(value, value)

    def test_existing_exceptions_components_and_unsupported_fields_refuse(self):
        for before in (self.before.replace("END:VEVENT", "BEGIN:VALARM\r\nEND:VALARM\r\nEND:VEVENT"),
                       self.before.replace("END:VEVENT", "ATTENDEE:mailto:synthetic@example.com\r\nEND:VEVENT"),
                       self.before.replace("END:VEVENT", "ATTACH:https://example.invalid/a\r\nEND:VEVENT")):
            with self.subTest(before=before), self.assertRaises(SafetyError):
                occurrence_content(before, "update", self.start, {"summary": "Changed"})
        for fields in ({"start": self.start}, {}, {"summary": "Changed", "recurrence": {}}):
            with self.subTest(fields=fields), self.assertRaises(SafetyError):
                occurrence_content(self.before, "update", self.start, fields)

    def test_changed_or_missing_occurrence_cannot_verify(self):
        value = occurrence_content(self.before, "update", self.start, {"summary": "Changed occurrence"})
        modified_values = [value.replace("RECURRENCE-ID:20261109T150000Z", "RECURRENCE-ID:20261116T150000Z"),
                           value.replace("SUMMARY:Changed occurrence", "SUMMARY:Unexpected"),
                           value.replace("RRULE:FREQ=WEEKLY", "RRULE:FREQ=DAILY"),
                           value.replace("END:VCALENDAR", "BEGIN:VEVENT\r\nEND:VEVENT\r\nEND:VCALENDAR")]
        for modified in modified_values:
            with self.subTest(modified=modified), self.assertRaises(SafetyError):
                verify_occurrence_write(value, modified)

    def test_first_occurrence_can_be_excluded_without_losing_dtstart(self):
        value = occurrence_content(self.before, "delete_occurrence", "2026-11-02T15:00:00Z")
        self.assertIn("DTSTART:20261102T150000Z", value)
        self.assertIn("EXDATE:20261102T150000Z", value)
