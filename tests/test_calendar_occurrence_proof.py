"""Exact-resource occurrence proofs use synthetic transport only."""

from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import unittest
from unittest.mock import Mock
from xml.sax.saxutils import escape

from icloud_for_agents.calendar_occurrence_proof import prove_occurrence
from icloud_for_agents.core import SafetyError


COLLECTION = "https://caldav.icloud.com/synthetic/"
URL = COLLECTION + "event.ics"
MASTER = "\r\n".join([
    "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Synthetic//EN", "BEGIN:VEVENT", "UID:synthetic",
    "DTSTART:20261102T150000Z", "DTEND:20261102T160000Z", "DTSTAMP:20261101T000000Z",
    "RRULE:FREQ=WEEKLY;INTERVAL=1;COUNT=3", "SUMMARY:Synthetic", "END:VEVENT", "END:VCALENDAR", ""])


def projection(start="20261109T150000Z", *, identity=True):
    end = (datetime.strptime(start, "%Y%m%dT%H%M%SZ") + timedelta(hours=1)).strftime("%Y%m%dT%H%M%SZ")
    value = MASTER.replace("RRULE:FREQ=WEEKLY;INTERVAL=1;COUNT=3\r\n", "")
    value = value.replace("DTSTART:20261102T150000Z", "DTSTART:" + start).replace("DTEND:20261102T160000Z", "DTEND:" + end)
    if identity:
        value = value.replace("END:VEVENT", "RECURRENCE-ID:" + start + "\r\nEND:VEVENT")
    return value


def response(value=None, etag='"synthetic"'):
    return ('<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">'
            '<d:response><d:href>' + URL + '</d:href><d:propstat><d:prop><d:getetag>' + escape(etag) +
            '</d:getetag><cal:calendar-data>' + escape(projection() if value is None else value) +
            '</cal:calendar-data></d:prop><d:status>HTTP/1.1 200 OK</d:status>'
            '</d:propstat></d:response></d:multistatus>').encode()


def apple_projection():
    return (projection().replace("VERSION:2.0\r\n", "")
            .replace("BEGIN:VEVENT", "X-EXPANDED:True\r\nX-MASTER-DTSTART:20261102T150000Z\r\n"
                     "X-MASTER-RRULE:FREQ=WEEKLY\\;INTERVAL=1\\;COUNT=3\r\nBEGIN:VEVENT")
            .replace("END:VEVENT", "DTSTAMP:20261103T000000Z\r\nEND:VEVENT"))


class OccurrenceProofTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {"etag": '"synthetic"', "content": MASTER,
                         "sha256": hashlib.sha256(MASTER.encode()).hexdigest()}
        self.reader = Mock()
        self.reader.known_collection.return_value = COLLECTION
        self.reader.transport.side_effect = [
            (207, {}, response()), (200, {"etag": self.snapshot["etag"]}, MASTER.encode())]

    def prove(self, start="2026-11-09T15:00:00Z", **kwargs):
        return prove_occurrence(self.reader, kwargs.get("collection", COLLECTION), kwargs.get("url", URL),
                                kwargs.get("snapshot", self.snapshot), start)

    def test_later_instance_has_repeatable_serializable_snapshot_bound_proof(self):
        proof = self.prove()
        self.setUp()
        self.assertEqual(proof, self.prove())
        self.assertEqual(json.loads(json.dumps(proof)), proof)
        self.assertEqual(proof["recurrence_id"], "20261109T150000Z")
        self.assertEqual(proof["window_end"], "20261109T150001Z")
        self.assertEqual(proof["snapshot_sha256"], self.snapshot["sha256"])
        self.assertFalse(proof["recurrence_completeness_verified"])
        call = self.reader.transport.call_args_list[0]
        self.assertEqual(call.args[:2], ("REPORT", COLLECTION))
        self.assertIn("<cal:calendar-query", call.args[2])
        self.assertIn("<d:getetag/>", call.args[2])
        self.assertIn('<cal:time-range start="20261109T150000Z" end="20261109T150001Z"/>', call.args[2])
        self.assertNotIn("<d:href>", call.args[2])
        self.assertEqual(self.reader.transport.call_args_list[1].args, ("GET", URL, "", 0))

    def test_initial_instance_may_omit_recurrence_id(self):
        for identity in (False, True):
            self.reader.transport.side_effect = [(207, {}, response(projection("20261102T150000Z", identity=identity))),
                                                  (200, {"etag": '"synthetic"'}, MASTER.encode())]
            self.assertEqual(self.prove("2026-11-02T15:00:00Z")["start"], "20261102T150000Z")

    def test_apple_metadata_and_two_timestamps_produce_stable_proof(self):
        proofs = []
        for stamp in ("20261103T000000Z", "20261104T000000Z"):
            value = apple_projection().replace("20261103T000000Z", stamp)
            self.reader.transport.side_effect = [(207, {}, response(value)),
                                                  (200, {"etag": '"synthetic"'}, MASTER.encode())]
            proofs.append(self.prove())
        self.assertEqual(proofs[0], proofs[1])

    def test_apple_projection_metadata_and_timestamp_ambiguities_refuse(self):
        original = apple_projection()
        cases = [original.replace("X-EXPANDED:True", "X-EXPANDED:False"),
                 original.replace("X-MASTER-DTSTART:20261102T150000Z", "X-MASTER-DTSTART:20261102T160000Z"),
                 original.replace("COUNT=3", "COUNT=4"),
                 original.replace("X-EXPANDED:True\r\n", ""),
                 original.replace("X-EXPANDED:True", "X-EXPANDED:True\r\nX-EXPANDED:True"),
                 original.replace("BEGIN:VEVENT", "X-UNKNOWN:True\r\nBEGIN:VEVENT"),
                 original.replace("DTSTAMP:20261103T000000Z", "DTSTAMP:invalid"),
                 original.replace("DTSTAMP:20261103T000000Z", "DTSTAMP:20261103T000000Z\r\nDTSTAMP:20261104T000000Z"),
                 original.replace("END:VEVENT", "CREATED:20261101T000000Z\r\nCREATED:20261102T000000Z\r\nEND:VEVENT"),
                 original.replace("END:VEVENT", "SEQUENCE:-1\r\nEND:VEVENT"),
                 original.replace("END:VEVENT", "UID:synthetic\r\nEND:VEVENT"),
                 original.replace("END:VEVENT", "RRULE:FREQ=WEEKLY\r\nEND:VEVENT"),
                 MASTER]
        for value in cases:
            with self.subTest(value=value):
                self.reader.transport.side_effect = [(207, {}, response(value))]
                with self.assertRaises(SafetyError):
                    self.prove()

    def test_unrelated_query_rows_are_not_used_as_proof(self):
        own = response(apple_projection()).decode()
        unrelated = response(projection().replace("UID:synthetic", "UID:other")).decode()
        unrelated = unrelated[unrelated.index("<d:response>"):unrelated.index("</d:response>") + len("</d:response>")]
        unrelated = unrelated.replace(URL, COLLECTION + "other.ics")
        for xml in (own.replace("</d:multistatus>", unrelated + "</d:multistatus>"),
                    own.replace("<d:response>", unrelated + "<d:response>", 1)):
            self.reader.transport.side_effect = [(207, {}, xml.encode()), (200, {"etag": '"synthetic"'}, MASTER.encode())]
            self.assertEqual(self.prove()["uid"], "synthetic")
        for xml in (own.replace("</d:multistatus>", unrelated.replace("other.ics", "event.ics") + "</d:multistatus>"),
                    own.replace(URL, COLLECTION + "other.ics"),
                    own.replace("</d:multistatus>", unrelated * 200 + "</d:multistatus>"),
                    own.replace(URL, "/synthetic/sub/../event.ics")):
            self.reader.transport.side_effect = [(207, {}, xml.encode())]
            with self.assertRaises(SafetyError):
                self.prove()

    def test_later_instance_cannot_omit_original_identity(self):
        self.reader.transport.side_effect = [(207, {}, response(projection(identity=False)))]
        with self.assertRaises(SafetyError):
            self.prove()

    def test_projection_identity_duration_and_content_mismatches_are_refused(self):
        original = projection()
        cases = [original.replace("UID:synthetic", "UID:other"),
                 original.replace("RECURRENCE-ID:20261109T150000Z", "RECURRENCE-ID:20261109T140000Z"),
                 original.replace("DTEND:20261109T160000Z", "DTEND:20261109T170000Z"),
                 original.replace("SUMMARY:Synthetic", "SUMMARY:Other"),
                 projection("20261116T150000Z"),
                 original.replace("DTSTART:20261109T150000Z", "DTSTART:20261109T150000"),
                 original.replace("RECURRENCE-ID:", "RECURRENCE-ID;RANGE=THISANDFUTURE:"),
                 original.replace("END:VEVENT", "UID:synthetic\r\nEND:VEVENT"),
                 original.replace("END:VEVENT", "ATTENDEE:mailto:other@example.com\r\nEND:VEVENT"),
                 original.replace("END:VEVENT", "RRULE:FREQ=WEEKLY\r\nEND:VEVENT"),
                 original.replace("END:VEVENT", "BEGIN:VALARM\r\nACTION:DISPLAY\r\nEND:VALARM\r\nEND:VEVENT")]
        for value in cases:
            with self.subTest(value=value):
                self.reader.transport.side_effect = [(207, {}, response(value))]
                with self.assertRaises(SafetyError):
                    self.prove()

    def test_duplicate_projected_instances_are_refused(self):
        value = projection()
        event = value[value.index("BEGIN:VEVENT"):value.index("END:VEVENT") + len("END:VEVENT\r\n")]
        self.reader.transport.side_effect = [(207, {}, response(value.replace("END:VCALENDAR", event + "END:VCALENDAR")))]
        with self.assertRaises(SafetyError):
            self.prove()

    def test_projection_component_and_byte_limits_are_enforced(self):
        value = projection()
        event = value[value.index("BEGIN:VEVENT"):value.index("END:VEVENT") + len("END:VEVENT\r\n")]
        for altered in (value.replace("END:VCALENDAR", event * 200 + "END:VCALENDAR"),
                        value.replace("SUMMARY:Synthetic", "SUMMARY:" + "x" * 262144)):
            self.reader.transport.side_effect = [(207, {}, response(altered))]
            with self.assertRaises(SafetyError):
                self.prove()

    def test_unsupported_or_ambiguous_master_rules_are_refused(self):
        for rule in ("FREQ=HOURLY", "FREQ=WEEKLY;BYHOUR=15", "FREQ=WEEKLY;COUNT=3;COUNT=4",
                     "FREQ=WEEKLY;COUNT=3;UNTIL=20261201T000000Z", "FREQ=WEEKLY;INTERVAL=0",
                     "FREQ=WEEKLY;BYDAY=TU", "FREQ=WEEKLY;BYDAY=MO,MO"):
            snapshot = deepcopy(self.snapshot)
            snapshot["content"] = MASTER.replace("FREQ=WEEKLY;INTERVAL=1;COUNT=3", rule)
            snapshot["sha256"] = hashlib.sha256(snapshot["content"].encode()).hexdigest()
            with self.assertRaises(SafetyError):
                self.prove(snapshot=snapshot)

    def test_strict_multiget_response_rejects_ambiguity_errors_and_injection(self):
        xml = response().decode()
        cases = [xml.replace(URL, COLLECTION + "other.ics"),
                 xml.replace("</d:href>", "</d:href><d:href>" + URL + "</d:href>"),
                 xml.replace("<d:propstat>", "<d:status>HTTP/1.1 403 Forbidden</d:status><d:propstat>"),
                 xml.replace("HTTP/1.1 200 OK", "HTTP/1.1 404 Not Found"),
                 xml.replace("</d:getetag>", "<d:href>other</d:href></d:getetag>"),
                 xml.replace("</cal:calendar-data>", "<d:href>other</d:href></cal:calendar-data>"),
                 xml.replace("</d:getetag>", '</d:getetag><d:getetag>"synthetic"</d:getetag>'),
                 xml.replace("</d:response>", "</d:response><d:response/>"),
                 xml.replace("</d:propstat>", "</d:propstat><d:propstat/>"),
                 xml.replace("</d:status>", "</d:status><d:status>HTTP/1.1 200 OK</d:status>"),
                 xml.replace("</d:prop>", "<d:unknown/></d:prop>"),
                 '<!DOCTYPE x [<!ENTITY x "bad">]>' + xml,
                 "<not-xml"]
        for value in cases:
            with self.subTest(value=value):
                self.reader.transport.side_effect = [(207, {}, value.encode())]
                with self.assertRaises(SafetyError):
                    self.prove()

    def test_report_etag_must_match_full_snapshot(self):
        for etag in ('"other"', 'W/"synthetic"'):
            self.reader.transport.side_effect = [(207, {}, response(etag=etag))]
            with self.assertRaises(SafetyError):
                self.prove()

    def test_redirect_and_transport_status_are_not_followed(self):
        for status in (200, 301, 302, 307, 308, 403, 404):
            self.reader.transport.reset_mock()
            self.reader.transport.side_effect = [(status, {"location": URL}, response())]
            with self.assertRaises(SafetyError):
                self.prove()
            self.reader.transport.assert_called_once()

    def test_post_projection_snapshot_races_and_redirects_are_refused(self):
        for current in ((200, {"etag": '"other"'}, MASTER.encode()),
                        (200, {"etag": '"synthetic"'}, MASTER.replace("Synthetic", "Changed").encode()),
                        (200, {"etag": '"synthetic"'}, MASTER.encode() + b"x"),
                        (302, {"location": URL}, b""), (404, {}, b"")):
            self.reader.transport.side_effect = [(207, {}, response()), current]
            with self.assertRaises(SafetyError):
                self.prove()

    def test_unsupported_master_properties_and_components_are_refused_before_report(self):
        for extra in ("ATTENDEE:mailto:other@example.com", "ORGANIZER:mailto:owner@example.com", "EXDATE:20261109T150000Z",
                      "RDATE:20261110T150000Z", "RECURRENCE-ID:20261102T150000Z", "SUMMARY:Duplicate",
                      "BEGIN:VALARM\r\nACTION:DISPLAY\r\nEND:VALARM", "X-UNKNOWN:value"):
            snapshot = deepcopy(self.snapshot)
            snapshot["content"] = MASTER.replace("END:VEVENT", extra + "\r\nEND:VEVENT")
            snapshot["sha256"] = hashlib.sha256(snapshot["content"].encode()).hexdigest()
            self.reader.transport.reset_mock()
            with self.assertRaises(SafetyError):
                self.prove(snapshot=snapshot)
            self.reader.transport.assert_not_called()

    def test_malformed_snapshot_and_unsafe_resource_are_refused(self):
        for changes in ({"etag": 'W/"synthetic"'}, {"etag": '"bad\x00etag"'},
                        {"sha256": "wrong"}, {"content": MASTER + "bad"}):
            with self.assertRaises(SafetyError):
                self.prove(snapshot=dict(self.snapshot, **changes))
        for url in (COLLECTION + "../event.ics", COLLECTION + "%2eevent.ics", COLLECTION + "foo%2fbar.ics",
                    "https://caldav.icloud.com/other/event.ics", "https://example.com/event.ics"):
            with self.assertRaises(SafetyError):
                self.prove(url=url)

    def test_target_requires_whole_second_utc_not_before_master(self):
        for start in ("2026-11-01T15:00:00Z", "2026-11-09T15:00:00", "2026-11-09T15:00:00.1Z",
                      "2026-11-09T15:00:00-06:00", "9999-12-31T23:59:59Z"):
            with self.assertRaises(SafetyError):
                self.prove(start)


if __name__ == "__main__":
    unittest.main()
