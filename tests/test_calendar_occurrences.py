"""Bounded CalDAV server-expansion contracts using synthetic responses only."""
import unittest
from unittest.mock import Mock
from xml.sax.saxutils import escape

from icloud_for_agents.core import SafetyError
from icloud_for_agents.dav import DAVReader, expanded_event_count


COLLECTION = 'https://p01-caldav.icloud.com/synthetic/'
START = '2026-11-01T00:00:00Z'
END = '2026-11-03T00:00:00Z'
EVENT = ('BEGIN:VEVENT\r\nUID:synthetic-series\r\n'
         'RECURRENCE-ID:20261102T140000Z\r\nDTSTART:20261102T150000Z\r\n'
         'DTEND:20261102T160000Z\r\nSUMMARY:Synthetic moved occurrence\r\nEND:VEVENT\r\n')


def calendar(events=EVENT):
    return 'BEGIN:VCALENDAR\r\nVERSION:2.0\r\n' + events + 'END:VCALENDAR\r\n'


def response(values):
    rows = []
    for index, value in enumerate(values):
        rows.append('<d:response><d:href>' + COLLECTION + str(index) + '.ics</d:href>'
                    '<d:propstat><d:prop><cal:calendar-data>' + escape(value) +
                    '</cal:calendar-data></d:prop><d:status>HTTP/1.1 200 OK</d:status>'
                    '</d:propstat></d:response>')
    return ('<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">' +
            ''.join(rows) + '</d:multistatus>').encode()


class CalendarOccurrenceTests(unittest.TestCase):
    def reader(self, values):
        transport = Mock(return_value=(207, {}, response(values)))
        reader = DAVReader('synthetic@example.com', 'fake-only', transport)
        reader.known_collection = Mock(return_value=COLLECTION)
        return reader, transport

    def test_default_retains_raw_series_without_requesting_expansion(self):
        raw = calendar(EVENT.replace('RECURRENCE-ID:20261102T140000Z\r\n', 'RRULE:FREQ=DAILY\r\n'))
        reader, transport = self.reader([raw])
        result = reader.events(COLLECTION, START, END)
        self.assertEqual(result['events'][0]['icalendar'], raw.replace('\r\n', '\n'))
        self.assertNotIn('<cal:expand', transport.call_args.args[2])
        self.assertNotIn('occurrence_count', result)
        self.assertEqual(result['recurrence'], 'raw server-matched resources; not client-expanded occurrences')

    def test_expansion_is_requested_with_utc_bounds_and_projection_identity(self):
        raw = calendar()
        reader, transport = self.reader([raw])
        result = reader.events(COLLECTION, '2026-11-01T00:00:00-05:00', '2026-11-03T00:00:00-06:00', True)
        self.assertIn('<cal:expand start="20261101T050000Z" end="20261103T060000Z"/>', transport.call_args.args[2])
        self.assertEqual(transport.call_args.args[0], 'REPORT')
        self.assertEqual(result['occurrence_count'], 1)
        self.assertIn('not an editable series', result['recurrence'])
        self.assertIn('not independently verified', result['expansion'])
        self.assertEqual(result['events'][0], {'url': COLLECTION + '0.ics', 'icalendar': raw.replace('\r\n', '\n'), 'untrusted_content': True})

    def test_flag_is_a_boolean_and_invalid_flag_never_discovers_or_requests(self):
        reader, transport = self.reader([])
        for flag in (None, 0, 1, 'true', [], {}):
            with self.assertRaises(SafetyError):
                reader.events(COLLECTION, START, END, flag)
        reader.known_collection.assert_not_called()
        transport.assert_not_called()

    def test_expansion_keeps_existing_time_and_byte_bounds(self):
        reader, transport = self.reader([])
        with self.assertRaises(SafetyError):
            reader.events(COLLECTION, START, '2027-01-01T00:00:00Z', True)
        transport.assert_not_called()
        reader, _ = self.reader([calendar(EVENT.replace('Synthetic moved occurrence', 'x' * 262145))])
        with self.assertRaises(SafetyError):
            reader.events(COLLECTION, START, END, True)

    def test_occurrence_cap_applies_within_one_resource_and_across_resources(self):
        reader, _ = self.reader([calendar(EVENT * 200)])
        self.assertEqual(reader.events(COLLECTION, START, END, True)['occurrence_count'], 200)
        for values in ([calendar(EVENT * 201)], [calendar(EVENT * 100), calendar(EVENT * 101)]):
            reader, _ = self.reader(values)
            with self.assertRaises(SafetyError):
                reader.events(COLLECTION, START, END, True)

    def test_empty_query_is_explicitly_an_empty_server_projection(self):
        reader, _ = self.reader([])
        result = reader.events(COLLECTION, START, END, True)
        self.assertEqual(result['events'], [])
        self.assertEqual(result['occurrence_count'], 0)

    def test_ignored_expansion_or_embedded_timezone_refuses(self):
        for property_line in ('RRULE:FREQ=DAILY', 'RDATE:20261102T150000Z', 'EXDATE:20261102T150000Z', 'EXRULE:FREQ=DAILY'):
            reader, _ = self.reader([calendar(EVENT.replace('SUMMARY:', property_line + '\r\nSUMMARY:'))])
            with self.assertRaises(SafetyError):
                reader.events(COLLECTION, START, END, True)
        with self.assertRaises(SafetyError):
            expanded_event_count(calendar('BEGIN:VTIMEZONE\r\nEND:VTIMEZONE\r\n' + EVENT))

    def test_unbalanced_duplicate_or_unsupported_components_refuse(self):
        for raw in (EVENT, calendar('').replace('VERSION:2.0\r\n', ''), calendar() + calendar(),
                    calendar().replace('END:VEVENT\r\n', ''), calendar() + 'SUMMARY:outside\r\n',
                    calendar().replace('BEGIN:VEVENT', 'BEGIN:VTODO'), calendar().replace('SUMMARY:', '\r\nSUMMARY:'),
                    calendar().replace('BEGIN:VEVENT', 'BEGIN;X-INVALID=1:VEVENT')):
            with self.assertRaises(SafetyError, msg=raw):
                expanded_event_count(raw)

    def test_nested_alarm_and_folded_property_are_preserved(self):
        raw = calendar(EVENT.replace('END:VEVENT', 'BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:Long\r\n folded\r\nEND:VALARM\r\nEND:VEVENT'))
        reader, _ = self.reader([raw])
        result = reader.events(COLLECTION, START, END, True)
        self.assertEqual(result['events'][0]['icalendar'], raw.replace('\r\n', '\n'))
        self.assertEqual(result['occurrence_count'], 1)

    def test_missing_or_failed_calendar_data_cannot_be_reported_as_empty(self):
        for raw in (response([calendar()]).replace(b'HTTP/1.1 200 OK', b'HTTP/1.1 403 Forbidden'),
                    response([calendar()]).replace(b'cal:calendar-data', b'd:getetag'),
                    response([calendar()]).replace(b'</d:prop>', b'<cal:calendar-data/></d:prop>'),
                    response([calendar()]).replace(b'<d:propstat>', b'<d:status>HTTP/1.1 403 Forbidden</d:status><d:propstat>')):
            transport = Mock(return_value=(207, {}, raw))
            reader = DAVReader('synthetic@example.com', 'fake-only', transport)
            reader.known_collection = lambda kind, url: COLLECTION
            with self.assertRaises(SafetyError):
                reader.events(COLLECTION, START, END, True)

    def test_wrong_response_or_foreign_resource_refuses(self):
        for raw in (b'<d:error xmlns:d="DAV:"/>',
                    response([calendar()]).replace((COLLECTION + '0.ics').encode(), b'https://p01-caldav.icloud.com/other/0.ics'),
                    response([calendar()]).replace(b'0.ics</d:href>', b'%2e%2e/0.ics</d:href>')):
            reader = DAVReader('synthetic@example.com', 'fake-only', Mock(return_value=(207, {}, raw)))
            reader.known_collection = lambda kind, url: COLLECTION
            with self.assertRaises(SafetyError):
                reader.events(COLLECTION, START, END, True)
