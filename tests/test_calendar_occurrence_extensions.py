"""Local-zone and repeated exception changes use synthetic resources only."""
from datetime import datetime, timedelta, timezone
import hashlib
import unittest
from unittest.mock import Mock
from xml.sax.saxutils import escape

from icloud_for_agents.calendar_occurrence_changes import (occurrence_content, occurrence_source,
    validate_occurrence_resource, verify_occurrence_write)
from icloud_for_agents.calendar_occurrence_proof import prove_occurrence
from icloud_for_agents.core import SafetyError
from icloud_for_agents.dav_changes import event_content, unfold
from test_calendar_timezone import CHICAGO

COLLECTION = 'https://caldav.icloud.com/synthetic/'
URL = COLLECTION + 'series.ics'


def series(local=False):
    return event_content({'summary': 'Synthetic series',
        'start': '2026-10-26T09:00:00-05:00' if local else '2026-10-26T14:00:00Z',
        'end': '2026-10-26T10:00:00-05:00' if local else '2026-10-26T15:00:00Z',
        'timezone': 'America/Chicago' if local else 'UTC',
        'recurrence': {'frequency': 'weekly', 'count': 6}}, timezone_definition=CHICAGO if local else None)


def expansion(raw, original):
    master, source, master_values, source_values = occurrence_source(raw, original)
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Synthetic//EN', 'BEGIN:VEVENT']
    ignored = {'RRULE', 'RECURRENCE-ID', 'DTSTART', 'DTEND', 'DTSTAMP'}
    lines.extend(name + ':' + value for name, value in source_values.items() if name not in ignored)
    lines.extend(['DTSTAMP:20261101T000000Z', 'DTSTART:' + source_values['DTSTART'],
                  'DTEND:' + source_values['DTEND'],
                  'RECURRENCE-ID:' + datetime.fromisoformat(original.replace('Z', '+00:00')).strftime('%Y%m%dT%H%M%SZ'),
                  'END:VEVENT', 'END:VCALENDAR', ''])
    return '\r\n'.join(lines)


def proof(raw, original, projection=None):
    snapshot = {'etag': '"synthetic"', 'content': raw, 'sha256': hashlib.sha256(raw.encode()).hexdigest()}
    value = expansion(raw, original) if projection is None else projection
    xml = ('<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">'
           '<d:response><d:href>' + URL + '</d:href><d:propstat><d:prop><d:getetag>"synthetic"</d:getetag>'
           '<cal:calendar-data>' + escape(value) + '</cal:calendar-data></d:prop>'
           '<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>').encode()
    reader = Mock()
    reader.known_collection.return_value = COLLECTION
    reader.transport.side_effect = [(207, {}, xml), (200, {'etag': '"synthetic"'}, raw.encode())]
    result = prove_occurrence(reader, COLLECTION, URL, snapshot, original)
    return result, reader


class OccurrenceExtensionTests(unittest.TestCase):
    def test_named_zone_across_dst_preserves_local_wall_time_and_identity(self):
        raw = series(True)
        original = '2026-11-09T15:00:00Z'
        evidence, _ = proof(raw, original)
        self.assertEqual(evidence['start'], '20261109T150000Z')
        changed = occurrence_content(raw, 'update', original, {'summary': 'Second occurrence'})
        self.assertIn('RECURRENCE-ID;TZID=America/Chicago:20261109T090000', unfold(changed))
        self.assertEqual(changed.count('BEGIN:VTIMEZONE'), 1)
        self.assertIn('DTSTART;TZID=America/Chicago:20261109T090000', unfold(changed))
        verify_occurrence_write(changed, changed)
        verify_occurrence_write(changed, changed.replace('RECURRENCE-ID;TZID=America/Chicago:20261109T090000', 'RECURRENCE-ID:20261109T150000Z'))
        evidence, _ = proof(changed, original)
        self.assertEqual(evidence['start'], '20261109T150000Z')

    def test_apple_local_projection_is_normalized_only_with_exact_source_zone_and_metadata(self):
        raw = series(True)
        original = '2026-11-09T15:00:00Z'
        value = expansion(raw, original)
        value = value.replace('VERSION:2.0\r\n', '')
        value = value.replace('PRODID:-//Synthetic//EN\r\n', '')
        value = value.replace('BEGIN:VEVENT', 'X-EXPANDED:True\r\n'
            'X-MASTER-DTSTART:20261026T090000\r\n'
            'X-MASTER-RRULE:FREQ=WEEKLY\\;INTERVAL=1\\;COUNT=6\r\nBEGIN:VEVENT')
        value = value.replace('DTSTART:20261109T150000Z', 'DTSTART;TZID=America/Chicago:20261109T090000')
        value = value.replace('DTEND:20261109T160000Z', 'DTEND;TZID=America/Chicago:20261109T100000')
        value = value.replace('END:VEVENT', 'DTSTAMP:20261102T000000Z\r\nEND:VEVENT')
        observed, _ = proof(raw, original, value)
        canonical, _ = proof(raw, original)
        self.assertEqual(observed['start'], '20261109T150000Z')
        self.assertEqual(observed['selected_sha256'], canonical['selected_sha256'])
        self.assertEqual(observed['projection_sha256'], canonical['projection_sha256'])
        for changed in (value.replace('TZID=America/Chicago:', 'TZID=America/Denver:'),
                        value.replace('DTSTART;TZID=America/Chicago:20261109T090000', 'DTSTART:20261109T090000'),
                        value.replace('X-EXPANDED:True', 'X-EXPANDED:False'),
                        value.replace('X-MASTER-DTSTART:20261026T090000', 'X-MASTER-DTSTART:20261026T100000'),
                        value.replace('X-MASTER-RRULE:FREQ=WEEKLY\\;INTERVAL=1\\;COUNT=6',
                                      'X-MASTER-RRULE:FREQ=WEEKLY\\;INTERVAL=1\\;COUNT=7'),
                        value.replace('DTEND;TZID=America/Chicago:20261109T100000', 'DTEND;TZID=America/Chicago:20261109T110000'),
                        value.replace('DTSTART;TZID=America/Chicago:20261109T090000', 'DTSTART;TZID=America/Chicago:20261101T013000'),
                        value.replace('BEGIN:VEVENT', 'BEGIN:VEVENT\r\nDTSTART:20261109T150000Z'),
                        value.replace('X-EXPANDED:True\r\n', 'VERSION:2.0\r\n')):
            with self.subTest(changed=changed), self.assertRaises(SafetyError):
                proof(raw, original, changed)

    def test_successive_exceptions_and_deletions_preserve_other_instances(self):
        first = occurrence_content(series(), 'update', '2026-11-02T14:00:00Z', {'summary': 'First override'})
        second = occurrence_content(first, 'update', '2026-11-09T14:00:00Z', {'location': 'Second override'})
        self.assertEqual(second.count('BEGIN:VEVENT'), 3)
        self.assertIn('SUMMARY:First override', unfold(second))
        self.assertIn('LOCATION:Second override', unfold(second))
        third = occurrence_content(second, 'update', '2026-11-02T14:00:00Z', {'description': 'Updated again'})
        self.assertEqual(third.count('BEGIN:VEVENT'), 3)
        self.assertIn('SUMMARY:First override', unfold(third))
        self.assertIn('DESCRIPTION:Updated again', unfold(third))
        fourth = occurrence_content(third, 'delete_occurrence', '2026-11-02T14:00:00Z')
        self.assertEqual(fourth.count('BEGIN:VEVENT'), 2)
        self.assertNotIn('SUMMARY:First override', unfold(fourth))
        self.assertIn('LOCATION:Second override', unfold(fourth))
        self.assertIn('EXDATE:20261102T140000Z', unfold(fourth))
        proof(fourth, '2026-11-09T14:00:00Z')
        with self.assertRaises(SafetyError):
            occurrence_content(fourth, 'update', '2026-11-02T14:00:00Z', {'summary': 'Cannot silently restore'})
        verify_occurrence_write(fourth, fourth)

    def test_moved_exception_uses_original_identity_and_current_query_window(self):
        original = '2026-11-09T15:00:00Z'
        moved = occurrence_content(series(True), 'update', original,
            {'start': '2026-11-10T11:00:00-06:00', 'end': '2026-11-10T12:30:00-06:00',
             'timezone': 'America/Chicago', 'summary': 'Moved'})
        evidence, reader = proof(moved, original)
        self.assertEqual(evidence['recurrence_id'], '20261109T150000Z')
        self.assertEqual(evidence['start'], '20261110T170000Z')
        self.assertEqual(evidence['window_start'], '20261110T170000Z')
        self.assertIn('start="20261110T170000Z"', reader.transport.call_args_list[0].args[2])
        altered = expansion(moved, original).replace('SUMMARY:Moved', 'SUMMARY:Unexpected')
        with self.assertRaises(SafetyError):
            proof(moved, original, altered)
        updated = occurrence_content(moved, 'update', original, {'description': 'After moving'})
        self.assertIn('DTSTART;TZID=America/Chicago:20261110T110000', unfold(updated))
        self.assertIn('RECURRENCE-ID;TZID=America/Chicago:20261109T090000', unfold(updated))

    def test_apple_moved_local_exception_projection_retains_original_identity(self):
        original = '2026-11-09T15:00:00Z'
        moved = occurrence_content(series(True), 'update', original,
            {'start': '2026-11-09T10:00:00-06:00', 'end': '2026-11-09T11:00:00-06:00', 'summary': 'Moved'})
        value = expansion(moved, original).replace('VERSION:2.0\r\n', '').replace('PRODID:-//Synthetic//EN\r\n', '')
        value = value.replace('BEGIN:VEVENT', 'X-EXPANDED:True\r\nX-MASTER-DTSTART:20261026T090000\r\n'
            'X-MASTER-RRULE:FREQ=WEEKLY\\;INTERVAL=1\\;COUNT=6\r\nBEGIN:VEVENT')
        value = value.replace('DTSTART:20261109T160000Z', 'DTSTART;TZID=America/Chicago:20261109T100000')
        value = value.replace('DTEND:20261109T170000Z', 'DTEND;TZID=America/Chicago:20261109T110000')
        value = value.replace('RECURRENCE-ID:20261109T150000Z', 'RECURRENCE-ID;TZID=America/Chicago:20261109T090000')
        value = value.replace('END:VEVENT', 'X-RECURRENCE-EXCEPTION:True\r\nEND:VEVENT')
        observed, _ = proof(moved, original, value)
        self.assertEqual(observed['recurrence_id'], '20261109T150000Z')
        self.assertEqual(observed['start'], '20261109T160000Z')
        for changed in (value.replace('X-RECURRENCE-EXCEPTION:True', 'X-RECURRENCE-EXCEPTION:False'),
                        value.replace('RECURRENCE-ID;TZID=America/Chicago:', 'RECURRENCE-ID;TZID=America/Denver:'),
                        value.replace('RECURRENCE-ID;TZID=America/Chicago:20261109T090000',
                                      'RECURRENCE-ID;TZID=America/Chicago:20261109T080000'),
                        value.replace('X-EXPANDED:True\r\n', 'VERSION:2.0\r\n')):
            with self.subTest(changed=changed), self.assertRaises(SafetyError):
                proof(moved, original, changed)

    def test_dst_gaps_folds_changed_timezone_and_partial_reschedule_refuse(self):
        original = '2026-11-09T15:00:00Z'
        for fields in ({'start': '2026-11-01T01:30:00-05:00', 'end': '2026-11-01T02:30:00-06:00'},
                       {'start': '2027-03-14T02:30:00-06:00', 'end': '2027-03-14T03:30:00-05:00'},
                       {'start': '2026-11-10T11:00:00-06:00'},
                       {'start': '2026-11-10T11:00:00-06:00', 'end': '2026-11-10T12:00:00-06:00', 'timezone': 'America/Denver'}):
            with self.subTest(fields=fields), self.assertRaises(SafetyError):
                occurrence_content(series(True), 'update', original, fields)

    def test_supported_monthly_and_yearly_rule_shapes_share_authoring_validation(self):
        base = series()
        for rule in ('FREQ=MONTHLY;INTERVAL=1;COUNT=6;BYMONTHDAY=26,-1',
                     'FREQ=MONTHLY;INTERVAL=1;COUNT=6;BYDAY=-1MO',
                     'FREQ=YEARLY;INTERVAL=1;COUNT=6;BYMONTH=10,11;BYMONTHDAY=26',
                     'FREQ=YEARLY;INTERVAL=1;COUNT=6;BYMONTH=10;BYDAY=-1MO'):
            raw = base.replace('FREQ=WEEKLY;INTERVAL=1;COUNT=6', rule)
            with self.subTest(rule=rule):
                evidence, _ = proof(raw, '2026-10-26T14:00:00Z')
                self.assertEqual(evidence['recurrence_id'], '20261026T140000Z')
        for rule in ('FREQ=MONTHLY;BYDAY=1MO,-1MO', 'FREQ=YEARLY;BYMONTH=0',
                     'FREQ=YEARLY;BYMONTH=13', 'FREQ=MONTHLY;BYMONTHDAY=0',
                     'FREQ=MONTHLY;BYMONTHDAY=32', 'FREQ=WEEKLY;BYDAY=-1MO',
                     'FREQ=YEARLY;BYMONTH=10,10', 'FREQ=MONTHLY;BYDAY=-1MO;BYMONTHDAY=26'):
            raw = base.replace('FREQ=WEEKLY;INTERVAL=1;COUNT=6', rule)
            with self.subTest(rule=rule), self.assertRaises(SafetyError):
                proof(raw, '2026-10-26T14:00:00Z')

    def test_duplicate_or_conflicting_exception_identity_refuses(self):
        raw = occurrence_content(series(), 'update', '2026-11-09T14:00:00Z', {'summary': 'Override'})
        event = raw[raw.rindex('BEGIN:VEVENT'):raw.rindex('END:VEVENT') + len('END:VEVENT\r\n')]
        for altered in (raw.replace('END:VCALENDAR', event + 'END:VCALENDAR'),
                        raw.replace('RRULE:', 'EXDATE:20261109T140000Z\r\nRRULE:'),
                        raw.replace('RECURRENCE-ID:', 'RECURRENCE-ID;RANGE=THISANDFUTURE:')):
            with self.subTest(altered=altered), self.assertRaises(SafetyError):
                validate_occurrence_resource(altered)

    def test_readback_verifies_preserved_siblings_and_exclusions(self):
        raw = occurrence_content(series(), 'update', '2026-11-02T14:00:00Z', {'summary': 'First'})
        expected = occurrence_content(raw, 'update', '2026-11-09T14:00:00Z', {'summary': 'Second'})
        with self.assertRaises(SafetyError):
            verify_occurrence_write(expected, expected.replace('SUMMARY:First', 'SUMMARY:Lost sibling'))
        expected = occurrence_content(expected, 'delete_occurrence', '2026-11-16T14:00:00Z')
        with self.assertRaises(SafetyError):
            verify_occurrence_write(expected, expected.replace('EXDATE:20261116T140000Z\r\n', ''))
