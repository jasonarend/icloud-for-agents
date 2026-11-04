import unittest
import os
from pathlib import Path
import tempfile
from unittest.mock import Mock, patch
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.calendar_recurrence import calendar_timezone, recurrence_rule, validate_series_deletion, verify_created
from icloud_for_agents.core import SafetyError
from icloud_for_agents.dav import DAVReader
from icloud_for_agents.dav_changes import DAVChanges, event_content
from test_calendar_timezone import CHICAGO


class RecurrenceRuleTests(unittest.TestCase):
    def setUp(self):
        self.fields = {
            "start": "2026-11-02T15:00:00Z",
            "end": "2026-11-02T16:00:00Z",
            "timezone": "UTC",
            "recurrence": {"frequency": "weekly", "interval": 2, "weekdays": ["WE", "MO"], "count": 5},
        }

    def test_weekly_rule_is_canonical_and_explicit(self):
        self.assertEqual(recurrence_rule(self.fields), "FREQ=WEEKLY;INTERVAL=2;COUNT=5;BYDAY=MO,WE")

    def test_until_is_utc_and_inclusive(self):
        self.fields["recurrence"] = {"frequency": "daily", "until": "2026-11-10T15:00:00+00:00"}
        self.assertEqual(recurrence_rule(self.fields), "FREQ=DAILY;INTERVAL=1;UNTIL=20261110T150000Z")

    def test_local_wall_time_requires_a_matching_provider_definition(self):
        fields = dict(self.fields, timezone="America/Chicago", start="2026-11-02T09:00:00-06:00",
                      end="2026-11-02T10:00:00-06:00", summary="Synthetic")
        with self.assertRaises(SafetyError):
            event_content(fields)
        for zone, start in (("UTC", "2026-11-02T09:00:00-06:00"),
                            ("UTC", "2026-11-02T15:00:00")):
            fields = dict(self.fields, timezone=zone, start=start)
            with self.subTest(fields=fields), self.assertRaises(SafetyError):
                recurrence_rule(fields)

    def test_refuses_ambiguous_or_unsupported_rules(self):
        invalid = [
            {"frequency": "weekly", "weekdays": ["TU"]},
            {"frequency": "weekly", "weekdays": ["MO", "MO"]},
            {"frequency": "daily", "weekdays": ["MO"]},
            {"frequency": "weekly", "interval": True},
            {"frequency": "weekly", "count": 0},
            {"frequency": "weekly", "count": 3, "until": "2026-11-30T15:00:00Z"},
            {"frequency": "weekly", "until": "2026-10-01T15:00:00Z"},
            {"frequency": "weekly", "extra": "ATTENDEE:bad"},
        ]
        for rule in invalid:
            with self.subTest(rule=rule), self.assertRaises(SafetyError):
                recurrence_rule(dict(self.fields, recurrence=rule))

    def test_dst_gap_and_ambiguous_initial_time_are_refused(self):
        for start in ("2026-03-08T02:30:00-06:00", "2026-11-01T01:30:00-05:00",
                      "2026-11-01T01:30:00-06:00"):
            with self.subTest(start=start), self.assertRaises(SafetyError):
                recurrence_rule(dict(self.fields, timezone="America/Chicago", start=start,
                                     end="2026-11-02T10:00:00-06:00"))

    def test_named_zone_creation_preserves_local_wall_time_and_provider_definition(self):
        fields = dict(self.fields, timezone="America/Chicago", start="2026-10-26T09:00:00-05:00",
                      end="2026-10-26T10:00:00-05:00", summary="Synthetic")
        value = event_content(fields, timezone_definition=CHICAGO)
        self.assertIn("DTSTART;TZID=America/Chicago:20261026T090000\r\n", value)
        self.assertIn("DTEND;TZID=America/Chicago:20261026T100000\r\n", value)
        self.assertIn("BEGIN:VTIMEZONE\r\nTZID:America/Chicago\r\n", value)
        self.assertIn("RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU", value)
        verify_created(value, value)
        validate_series_deletion(value)
        updated = event_content({"summary": "Updated synthetic"}, value, recurring_master=True)
        self.assertIn("SUMMARY:Updated synthetic", updated)
        self.assertIn("DTSTART;TZID=America/Chicago:20261026T090000", updated)
        verify_created(updated, updated)
        with self.assertRaises(SafetyError):
            verify_created(value, value.replace("BYMONTH=3;BYDAY=2SU", "BYMONTH=3;BYDAY=1SU"))

    def test_named_zone_creation_refuses_wall_duration_that_touches_dst_transition(self):
        fields = dict(self.fields, timezone="America/Chicago", start="2026-11-02T01:00:00-06:00",
                      end="2026-11-02T03:30:00-06:00", summary="Synthetic")
        with self.assertRaises(SafetyError):
            event_content(fields, timezone_definition=CHICAGO)

    def test_series_rescheduling_replaces_rule_time_and_timezone_without_losing_identity(self):
        before = event_content(dict(self.fields, summary="Synthetic", location="Original location"))
        fields = {"start": "2026-10-26T09:00:00-05:00", "end": "2026-10-26T10:00:00-05:00",
                  "timezone": "America/Chicago", "recurrence": {"frequency": "weekly", "count": 3}}
        named = event_content(fields, before, recurring_master=True, timezone_definition=CHICAGO)
        self.assertEqual(named.count("BEGIN:VTIMEZONE"), 1)
        self.assertIn("DTSTART;TZID=America/Chicago:20261026T090000", named)
        self.assertIn("RRULE:FREQ=WEEKLY;INTERVAL=1;COUNT=3", named)
        self.assertIn("SUMMARY:Synthetic", named)
        self.assertIn("LOCATION:Original location", named)
        self.assertEqual(before.split("UID:")[1].splitlines()[0], named.split("UID:")[1].splitlines()[0])
        named_again = event_content(fields, named, recurring_master=True, timezone_definition=CHICAGO)
        self.assertEqual(named_again.count("BEGIN:VTIMEZONE"), 1)
        utc_fields = {"start": "2026-11-09T16:00:00Z", "end": "2026-11-09T17:00:00Z",
                      "timezone": "UTC", "recurrence": {"frequency": "daily", "count": 4}}
        utc = event_content(utc_fields, named, recurring_master=True)
        self.assertNotIn("VTIMEZONE", utc)
        self.assertIn("DTSTART:20261109T160000Z", utc)
        self.assertIn("RRULE:FREQ=DAILY;INTERVAL=1;COUNT=4", utc)
        self.assertIn("LOCATION:Original location", utc)
        verify_created(utc, utc)
        with self.assertRaises(SafetyError):
            verify_created(utc, utc.replace("COUNT=4", "COUNT=5"))

    def test_series_rescheduling_refuses_unknown_properties_and_existing_exceptions(self):
        before = event_content(dict(self.fields, summary="Synthetic"))
        fields = {"start": "2026-11-09T16:00:00Z", "end": "2026-11-09T17:00:00Z",
                  "timezone": "UTC", "recurrence": {"frequency": "daily", "count": 4}}
        for extra in ("EXDATE:20261116T150000Z", "ATTENDEE:mailto:synthetic@example.com",
                      "ATTACH:https://example.invalid/synthetic"):
            with self.subTest(extra=extra), self.assertRaises(SafetyError):
                event_content(fields, before.replace("END:VEVENT", extra + "\r\nEND:VEVENT"), recurring_master=True)

    def test_timezone_component_cannot_be_nested_inside_an_event(self):
        fields = dict(self.fields, timezone="America/Chicago", start="2026-10-26T09:00:00-05:00",
                      end="2026-10-26T10:00:00-05:00", summary="Synthetic")
        value = event_content(fields, timezone_definition=CHICAGO)
        start, end = value.index("BEGIN:VTIMEZONE"), value.index("END:VTIMEZONE") + len("END:VTIMEZONE\r\n")
        timezone_component = value[start:end]
        altered = (value[:start] + value[end:]).replace("END:VEVENT", timezone_component + "END:VEVENT")
        with self.assertRaises(SafetyError):
            verify_created(value, altered)
        with self.assertRaises(SafetyError):
            validate_series_deletion(altered)

    def test_changed_selected_calendar_timezone_stops_before_write(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PlanStore(Path(directory) / "plans")
            collection = "https://p01-caldav.icloud.com/calendar/"
            reader = DAVReader("synthetic@example.com", "fake-only", Mock())
            reader.known_collection = lambda kind, target: target

            def response(definition):
                xml = ('<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">'
                       '<d:response><d:href>' + collection + '</d:href><d:propstat><d:prop>'
                       '<cal:calendar-timezone>' + escape(definition) + '</cal:calendar-timezone>'
                       '</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>')
                return collection, ElementTree.fromstring(xml)

            reader.propfind = Mock(side_effect=[response(CHICAGO), response(CHICAGO.replace("TZNAME:CDT", "TZNAME:SUMMER"))])
            writer = Mock(return_value={"status": "applied"})
            editor = DAVChanges(reader, store, writer)
            fields = dict(self.fields, timezone="America/Chicago", start="2026-10-26T09:00:00-05:00",
                          end="2026-10-26T10:00:00-05:00", summary="Synthetic")
            proposal = editor.prepare("cal", collection, [{"operation": "create", "fields": fields}])
            store.write_once(store.path(proposal["plan_id"], ".approved"),
                             {"plan_id": proposal["plan_id"], "sha256": proposal["sha256"]})
            result = editor.execute(proposal["plan_id"])
            self.assertEqual(result["status"], "calendar_timezone_refused_before_dispatch")
            self.assertEqual(result["diagnostic"]["phase"], "timezone_preflight")
            self.assertTrue(store.operation_settled(proposal["plan_id"]))
            writer.assert_not_called()
            with self.assertRaises(SafetyError):
                editor.execute(proposal["plan_id"])
            with patch.dict(os.environ, {"ICLOUD_APPROVAL_POLICY": "trusted-client", "ICLOUD_MAIL_ACCOUNT": "synthetic@example.com"}):
                fresh = editor.prepare("cal", collection, [{"operation": "create", "fields": {
                    "summary": "Fresh synthetic", "start": "2026-11-09T16:00:00Z", "end": "2026-11-09T17:00:00Z"}}])
                self.assertEqual(editor.execute(fresh["plan_id"])["status"], "applied")

    def test_missing_named_zone_dates_refuse_before_fetching_timezone(self):
        with tempfile.TemporaryDirectory() as directory:
            reader = DAVReader("synthetic@example.com", "fake-only", Mock())
            reader.known_collection = lambda kind, target: target
            reader.propfind = Mock()
            editor = DAVChanges(reader, PlanStore(Path(directory) / "plans"), Mock())
            fields = {"summary": "Synthetic", "timezone": "America/Chicago",
                      "recurrence": {"frequency": "weekly"}}
            with self.assertRaises(SafetyError):
                editor.prepare("cal", "https://p01-caldav.icloud.com/calendar/",
                               [{"operation": "create", "fields": fields}])
            reader.propfind.assert_not_called()

    def test_ambiguous_or_contradictory_timezone_responses_are_refused(self):
        collection = "https://p01-caldav.icloud.com/calendar/"
        xml = ('<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">'
               '<d:response><d:href>' + collection + '</d:href><d:propstat><d:prop>'
               '<cal:calendar-timezone>' + escape(CHICAGO) + '</cal:calendar-timezone>'
               '</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>')
        for modified in (xml.replace('<d:propstat>', '<d:status>HTTP/1.1 403 Forbidden</d:status><d:propstat>'),
                         xml.replace('</d:href>', '</d:href><d:href>' + collection + 'other</d:href>'),
                         xml.replace('</cal:calendar-timezone>', '<d:href>other</d:href></cal:calendar-timezone>'),
                         xml.replace('HTTP/1.1 200 OK', 'HTTP/1.1 404 Not Found')):
            reader = DAVReader("synthetic@example.com", "fake-only", Mock())
            reader.propfind = Mock(return_value=(collection, ElementTree.fromstring(modified)))
            with self.subTest(modified=modified), self.assertRaises(SafetyError):
                calendar_timezone(reader, collection, "America/Chicago")

    def test_timezone_refusal_cannot_clear_a_dispatched_or_unbound_write(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PlanStore(Path(directory) / "plans")
            plan = store.prepare("calendar_change", {"account": "synthetic@example.com", "changes": [{
                "operation": "create", "fields": {"recurrence": {}}, "timezone_binding": {"name": "America/Chicago"}}]})
            store.write_once(store.path(plan["plan_id"], ".started"),
                             {"sha256": plan["sha256"], "account": "synthetic@example.com"})
            store.write_once(store.path(plan["plan_id"], ".result"), {
                "status": "calendar_timezone_refused_before_dispatch", "completed": [], "do_not_retry": True,
                "plan_sha256": plan["sha256"], "diagnostic": {"phase": "conditional_write"}})
            self.assertFalse(store.operation_settled(plan["plan_id"]))

    def test_named_zone_writer_timeout_is_uncertain_not_preflight_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PlanStore(Path(directory) / "plans")
            collection = "https://p01-caldav.icloud.com/calendar/"
            reader = DAVReader("synthetic@example.com", "fake-only", Mock())
            reader.known_collection = lambda kind, target: target
            xml = ('<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">'
                   '<d:response><d:href>' + collection + '</d:href><d:propstat><d:prop>'
                   '<cal:calendar-timezone>' + escape(CHICAGO) + '</cal:calendar-timezone>'
                   '</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>')
            reader.propfind = Mock(return_value=(collection, ElementTree.fromstring(xml)))
            writer = Mock(side_effect=TimeoutError("synthetic timeout"))
            editor = DAVChanges(reader, store, writer)
            fields = dict(self.fields, timezone="America/Chicago", start="2026-10-26T09:00:00-05:00",
                          end="2026-10-26T10:00:00-05:00", summary="Synthetic")
            proposal = editor.prepare("cal", collection, [{"operation": "create", "fields": fields}])
            store.write_once(store.path(proposal["plan_id"], ".approved"),
                             {"plan_id": proposal["plan_id"], "sha256": proposal["sha256"]})
            result = editor.execute(proposal["plan_id"])
            self.assertEqual(result["status"], "failed_or_uncertain")
            self.assertEqual(result["diagnostic"]["phase"], "conditional_write")
            self.assertFalse(store.operation_settled(proposal["plan_id"]))
            writer.assert_called_once()

    def test_created_series_contains_reviewable_rule_and_no_invitation(self):
        value = event_content(dict(self.fields, summary="Synthetic recurrence"))
        self.assertIn("RRULE:FREQ=WEEKLY;INTERVAL=2;COUNT=5;BYDAY=MO,WE\r\n", value)
        self.assertIn("DTSTART:20261102T150000Z\r\n", value)
        self.assertNotIn("ATTENDEE", value)
        self.assertNotIn("ORGANIZER", value)

    def test_readback_cannot_claim_success_for_changed_rule_or_extra_components(self):
        value = event_content(dict(self.fields, summary="Synthetic recurrence"))
        verify_created(value, value.replace("END:VEVENT", "CREATED:20261101T000000Z\r\nEND:VEVENT"))
        for modified in (value.replace("COUNT=5", "COUNT=6"),
                         value.replace("END:VEVENT", "ATTENDEE:mailto:other@example.com\r\nEND:VEVENT"),
                         value.replace("END:VCALENDAR", "METHOD:REQUEST\r\nEND:VCALENDAR"),
                         value.replace("VERSION:2.0", "VERSION:1.0"),
                         value.replace("END:VCALENDAR", "begin:vevent\r\nUID:other\r\nend:vevent\r\nEND:VCALENDAR"),
                         value.replace("END:VEVENT", "BEGIN:VALARM\r\nEND:VALARM\r\nEND:VEVENT")):
            with self.subTest(modified=modified), self.assertRaises(SafetyError):
                verify_created(value, modified)

    def test_series_readback_and_rescheduling_require_nonempty_uid(self):
        before = event_content(dict(self.fields, summary="Synthetic"))
        uid = next(line for line in before.splitlines() if line.startswith("UID:"))
        fields = {"start": "2026-11-09T16:00:00Z", "end": "2026-11-09T17:00:00Z",
                  "timezone": "UTC", "recurrence": {"frequency": "daily", "count": 4}}
        for replacement in ("", "UID:", "UID:   ", "UID;X-EXTRA=1:synthetic"):
            modified = before.replace(uid, replacement)
            with self.subTest(replacement=replacement):
                with self.assertRaises(SafetyError):
                    verify_created(modified, modified)
                with self.assertRaises(SafetyError):
                    event_content(fields, modified, recurring_master=True)

    def test_creation_is_separately_reviewed_and_requires_verified_readback(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PlanStore(Path(directory) / "plans")
            collection = "https://p01-caldav.icloud.com/calendar/"
            transport = Mock()
            reader = DAVReader("synthetic@example.com", "fake-only", transport)
            reader.known_collection = lambda kind, url: url
            writer = Mock(return_value={"status": "applied"})
            editor = DAVChanges(reader, store, writer)
            change = {"operation": "create", "fields": dict(self.fields, summary="Synthetic recurrence")}
            proposal = editor.prepare("cal", collection, [change])
            item = proposal["review"]["payload"]["changes"][0]
            self.assertIn("RRULE:", item["content"])
            self.assertTrue(any("continues indefinitely" in effect for effect in item["recurrence_effects"]))
            self.assertTrue(any("daylight-saving gaps" in effect for effect in item["recurrence_effects"]))
            writer.assert_not_called()
            transport.return_value = (200, {"etag": '"synthetic"'}, item["content"].encode())
            store.write_once(store.path(proposal["plan_id"], ".approved"),
                             {"plan_id": proposal["plan_id"], "sha256": proposal["sha256"]})
            result = editor.execute(proposal["plan_id"])
            self.assertEqual(result["status"], "applied")
            self.assertEqual(result["completed"][0]["verification"], "reviewed_recurring_event_readback")
            with self.assertRaises(SafetyError):
                editor.execute(proposal["plan_id"])

    def test_missing_readback_remains_uncertain_and_cannot_be_replayed(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PlanStore(Path(directory) / "plans")
            collection = "https://p01-caldav.icloud.com/calendar/"
            reader = DAVReader("synthetic@example.com", "fake-only", Mock(return_value=(404, {}, b"")))
            reader.known_collection = lambda kind, url: url
            writer = Mock(return_value={"status": "applied"})
            editor = DAVChanges(reader, store, writer)
            proposal = editor.prepare("cal", collection, [{"operation": "create", "fields": dict(self.fields, summary="Synthetic")}])
            store.write_once(store.path(proposal["plan_id"], ".approved"),
                             {"plan_id": proposal["plan_id"], "sha256": proposal["sha256"]})
            result = editor.execute(proposal["plan_id"])
            self.assertEqual(result["status"], "failed_or_uncertain")
            self.assertEqual(result["diagnostic"]["phase"], "recurring_readback")
            self.assertEqual(result["completed"][0]["status"], "write_returned_readback_unverified")
            with self.assertRaises(SafetyError):
                editor.execute(proposal["plan_id"])
            self.assertEqual(writer.call_count, 1)

    def test_series_deletion_refuses_other_events_exceptions_and_invitees(self):
        value = event_content(dict(self.fields, summary="Synthetic recurrence"))
        validate_series_deletion(value)
        for modified in (value.replace("END:VCALENDAR", "begin:vtodo\r\nUID:other\r\nend:vtodo\r\nEND:VCALENDAR"),
                         value.replace("END:VEVENT", "EXDATE:20261109T150000Z\r\nEND:VEVENT"),
                         value.replace("END:VEVENT", "ATTENDEE:mailto:other@example.com\r\nEND:VEVENT"),
                         value.replace("BEGIN:VEVENT", "BEGIN:VTIMEZONE\r\nEND:VTIMEZONE\r\nBEGIN:VEVENT"),
                         value.replace("RRULE:", "RDATE:")):
            with self.subTest(modified=modified), self.assertRaises(SafetyError):
                validate_series_deletion(modified)

    def test_series_update_requires_scope_preserves_rule_and_verifies_readback(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PlanStore(Path(directory) / "plans")
            collection = "https://p01-caldav.icloud.com/calendar/"
            value = event_content(dict(self.fields, summary="Synthetic recurrence"))
            transport = Mock(return_value=(200, {"etag": '"synthetic"'}, value.encode()))
            reader = DAVReader("synthetic@example.com", "fake-only", transport)
            reader.known_collection = lambda kind, target: target
            writer = Mock(return_value={"status": "applied"})
            editor = DAVChanges(reader, store, writer)
            change = {"operation": "update", "url": collection + "synthetic.ics", "fields": {"summary": "Updated"}}
            with self.assertRaises(SafetyError):
                editor.prepare("cal", collection, [change])
            proposal = editor.prepare("cal", collection, [dict(change, recurrence_scope="series")])
            item = proposal["review"]["payload"]["changes"][0]
            self.assertIn("RRULE:FREQ=WEEKLY;INTERVAL=2;COUNT=5;BYDAY=MO,WE", item["content"])
            self.assertIn("SUMMARY:Updated", item["content"])
            self.assertIsNone(item["deletion"])
            with self.assertRaises(SafetyError):
                editor.prepare("cal", collection, [dict(change, recurrence_scope="series", fields={"start": self.fields["start"], "end": self.fields["end"]})])
            store.write_once(store.path(proposal["plan_id"], ".approved"),
                             {"plan_id": proposal["plan_id"], "sha256": proposal["sha256"]})
            transport.side_effect = [(200, {"etag": '"synthetic"'}, value.encode()),
                                     (200, {"etag": '"new"'}, item["content"].encode())]
            result = editor.execute(proposal["plan_id"])
            self.assertEqual(result["status"], "applied")
            self.assertEqual(result["completed"][0]["verification"], "reviewed_recurring_event_readback")

    def test_series_rescheduling_requires_complete_replacement_and_binds_review(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PlanStore(Path(directory) / "plans")
            collection = "https://p01-caldav.icloud.com/calendar/"
            value = event_content(dict(self.fields, summary="Synthetic recurrence"))
            transport = Mock(return_value=(200, {"etag": '"synthetic"'}, value.encode()))
            reader = DAVReader("synthetic@example.com", "fake-only", transport)
            reader.known_collection = lambda kind, target: target
            writer = Mock(return_value={"status": "applied"})
            editor = DAVChanges(reader, store, writer)
            fields = {"start": "2026-11-09T16:00:00Z", "end": "2026-11-09T17:00:00Z",
                      "timezone": "UTC", "recurrence": {"frequency": "daily", "count": 3}}
            change = {"operation": "update", "url": collection + "synthetic.ics",
                      "recurrence_scope": "series", "fields": fields}
            for missing in fields:
                with self.subTest(missing=missing), self.assertRaises(SafetyError):
                    editor.prepare("cal", collection, [dict(change, fields={k: v for k, v in fields.items() if k != missing})])
            with self.assertRaises(SafetyError):
                editor.prepare("cal", collection, [change, change])
            proposal = editor.prepare("cal", collection, [change])
            item = proposal["review"]["payload"]["changes"][0]
            self.assertTrue(any("past occurrences" in effect for effect in item["recurrence_effects"]))
            self.assertTrue(any("count starts" in effect for effect in item["recurrence_effects"]))
            self.assertIn("RRULE:FREQ=DAILY;INTERVAL=1;COUNT=3", item["content"])
            store.write_once(store.path(proposal["plan_id"], ".approved"),
                             {"plan_id": proposal["plan_id"], "sha256": proposal["sha256"]})
            transport.side_effect = [(200, {"etag": '"synthetic"'}, value.encode()),
                                     (200, {"etag": '"new"'}, item["content"].encode())]
            result = editor.execute(proposal["plan_id"])
            self.assertEqual(result["status"], "applied")
            self.assertEqual(result["completed"][0]["verification"], "reviewed_recurring_event_readback")

    def test_named_zone_series_reschedule_timezone_change_refuses_before_write(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PlanStore(Path(directory) / "plans")
            collection = "https://p01-caldav.icloud.com/calendar/"
            value = event_content(dict(self.fields, summary="Synthetic recurrence"))
            reader = DAVReader("synthetic@example.com", "fake-only", Mock(return_value=(200, {"etag": '"synthetic"'}, value.encode())))
            reader.known_collection = lambda kind, target: target
            def response(definition):
                xml = ('<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">'
                       '<d:response><d:href>' + collection + '</d:href><d:propstat><d:prop>'
                       '<cal:calendar-timezone>' + escape(definition) + '</cal:calendar-timezone>'
                       '</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>')
                return collection, ElementTree.fromstring(xml)
            reader.propfind = Mock(side_effect=[response(CHICAGO), response(CHICAGO.replace("TZNAME:CDT", "TZNAME:SUMMER"))])
            writer = Mock(return_value={"status": "applied"})
            editor = DAVChanges(reader, store, writer)
            fields = {"start": "2026-10-26T09:00:00-05:00", "end": "2026-10-26T10:00:00-05:00",
                      "timezone": "America/Chicago", "recurrence": {"frequency": "weekly", "count": 3}}
            proposal = editor.prepare("cal", collection, [{"operation": "update", "url": collection + "synthetic.ics",
                                      "recurrence_scope": "series", "fields": fields}])
            self.assertEqual(proposal["review"]["payload"]["changes"][0]["timezone_binding"]["name"], "America/Chicago")
            store.write_once(store.path(proposal["plan_id"], ".approved"),
                             {"plan_id": proposal["plan_id"], "sha256": proposal["sha256"]})
            result = editor.execute(proposal["plan_id"])
            self.assertEqual(result["status"], "calendar_timezone_refused_before_dispatch")
            self.assertTrue(store.operation_settled(proposal["plan_id"]))
            writer.assert_not_called()

    def test_series_update_refuses_noncanonical_event_boundaries(self):
        value = event_content(dict(self.fields, summary="Synthetic recurrence"))
        for modified in (value.replace("BEGIN:VEVENT", "begin:vevent"),
                         value.replace("END:VEVENT", "end:vevent")):
            with self.subTest(modified=modified), self.assertRaises(SafetyError):
                event_content({"summary": "Updated"}, modified, recurring_master=True)

    def test_whole_series_deletion_requires_explicit_scope_receipt_and_absence(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PlanStore(Path(directory) / "plans")
            collection = "https://p01-caldav.icloud.com/calendar/"
            url = collection + "synthetic.ics"
            value = event_content(dict(self.fields, summary="Synthetic recurrence"))
            transport = Mock(return_value=(200, {"etag": '"synthetic"'}, value.encode()))
            reader = DAVReader("synthetic@example.com", "fake-only", transport)
            reader.known_collection = lambda kind, target: target
            writer = Mock(return_value={"status": "applied"})
            editor = DAVChanges(reader, store, writer)
            change = {"operation": "delete", "url": url, "irreversible_confirmed": True}
            with self.assertRaises(SafetyError):
                editor.prepare("cal", collection, [change])
            proposal = editor.prepare("cal", collection, [dict(change, recurrence_scope="series")])
            item = proposal["review"]["payload"]["changes"][0]
            self.assertIn("entire recurring series", item["deletion"])
            with self.assertRaises(FileNotFoundError):
                editor.execute(proposal["plan_id"])
            writer.assert_not_called()
            store.write_once(store.path(proposal["plan_id"], ".approved"),
                             {"plan_id": proposal["plan_id"], "sha256": proposal["sha256"]})
            transport.side_effect = [(200, {"etag": '"synthetic"'}, value.encode()), (404, {}, b"")]
            result = editor.execute(proposal["plan_id"])
            self.assertEqual(result["status"], "applied")
            self.assertEqual(result["completed"][0]["verification"], "series_resource_absent")

    def test_series_deletion_with_existing_resource_after_write_remains_uncertain(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PlanStore(Path(directory) / "plans")
            collection = "https://p01-caldav.icloud.com/calendar/"
            value = event_content(dict(self.fields, summary="Synthetic recurrence"))
            transport = Mock(return_value=(200, {"etag": '"synthetic"'}, value.encode()))
            reader = DAVReader("synthetic@example.com", "fake-only", transport)
            reader.known_collection = lambda kind, target: target
            writer = Mock(return_value={"status": "applied"})
            editor = DAVChanges(reader, store, writer)
            proposal = editor.prepare("cal", collection, [{"operation": "delete", "url": collection + "synthetic.ics",
                "irreversible_confirmed": True, "recurrence_scope": "series"}])
            store.write_once(store.path(proposal["plan_id"], ".approved"),
                             {"plan_id": proposal["plan_id"], "sha256": proposal["sha256"]})
            result = editor.execute(proposal["plan_id"])
            self.assertEqual(result["status"], "failed_or_uncertain")
            self.assertEqual(result["diagnostic"]["phase"], "recurring_deletion_readback")
            with self.assertRaises(SafetyError):
                editor.execute(proposal["plan_id"])
            writer.assert_called_once()


if __name__ == "__main__":
    unittest.main()
