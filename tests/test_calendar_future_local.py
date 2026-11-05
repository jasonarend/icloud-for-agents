"""Future local-zone operations retain wall-time recurrence across DST."""
import unittest

from icloud_for_agents.calendar_future_changes import truncate_before
from icloud_for_agents.calendar_future_split import split_future
from icloud_for_agents.calendar_occurrence_changes import _resource, occurrence_content
from icloud_for_agents.core import SafetyError
from icloud_for_agents.dav_changes import event_content, unfold
from test_calendar_timezone import CHICAGO

UID = 'b' * 32


def local_series(rule=None, start='2026-10-26T09:00:00-05:00', end='2026-10-26T10:00:00-05:00'):
    return event_content({'summary': 'Synthetic local series', 'start': start, 'end': end,
        'timezone': 'America/Chicago', 'recurrence': rule or {'frequency': 'weekly', 'count': 6}},
        timezone_definition=CHICAGO)


class LocalFutureChangesTests(unittest.TestCase):
    def test_split_across_dst_keeps_local_wall_time_count_phase_and_full_provider_timezone(self):
        before = local_series()
        split = split_future(before, '2026-11-09T15:00:00Z', {'summary': 'Future local title'}, UID)
        self.assertEqual(split['preceding_occurrences'], 2)
        self.assertEqual(split['remaining_count'], 4)
        self.assertEqual(split['timezone'], 'America/Chicago')
        self.assertFalse(split['timezone_validation']['future_coverage']['all_recurrence_instants_verified'])
        self.assertIn('DTSTART;TZID=America/Chicago:20261026T090000', unfold(split['original_content']))
        self.assertIn('UNTIL=20261109T145959Z', split['original_content'])
        self.assertIn('DTSTART;TZID=America/Chicago:20261109T090000', unfold(split['future_content']))
        self.assertIn('DTEND;TZID=America/Chicago:20261109T100000', unfold(split['future_content']))
        self.assertIn('COUNT=4', split['future_content'])
        for content in (split['original_content'], split['future_content']):
            self.assertEqual(_resource(content)[1], _resource(before)[1])
            self.assertEqual(content.count('BEGIN:VTIMEZONE'), 1)
        self.assertIn('daylight saving', ' '.join(split['effects']))

    def test_same_local_date_time_shift_uses_matching_offset_and_keeps_weekly_interval_phase(self):
        before = local_series({'frequency': 'weekly', 'interval': 2, 'weekdays': ['MO', 'WE', 'FR'], 'count': 10},
                              start='2026-10-28T09:00:00-05:00', end='2026-10-28T10:00:00-05:00')
        split = split_future(before, '2026-11-11T15:00:00Z', {'start': '2026-11-11T11:00:00-06:00',
            'end': '2026-11-11T12:30:00-06:00', 'timezone': 'America/Chicago'}, UID)
        self.assertEqual(split['preceding_occurrences'], 3)
        self.assertEqual(split['remaining_count'], 7)
        self.assertIn('FREQ=WEEKLY;INTERVAL=2;COUNT=7;BYDAY=MO,WE,FR', split['future_content'])
        self.assertIn('DTSTART;TZID=America/Chicago:20261111T110000', unfold(split['future_content']))
        self.assertEqual(split['time_change']['original_start'], '2026-11-11T09:00:00-06:00')
        self.assertEqual(split['time_change']['new_start'], '2026-11-11T11:00:00-06:00')
        self.assertIn('DTSTART;TZID=America/Chicago:20261028T090000', unfold(split['original_content']))

    def test_indefinite_local_series_remains_indefinite_and_retains_zone(self):
        split = split_future(local_series({'frequency': 'daily'}), '2026-11-09T15:00:00Z',
                             {'description': 'Future details'}, UID)
        self.assertIsNone(split['remaining_count'])
        self.assertNotIn('UNTIL=', '\n'.join(line for line in unfold(split['future_content'])
                                              if line.startswith('RRULE:FREQ=DAILY')))
        self.assertIn('remains indefinite', ' '.join(split['effects']))
        self.assertIn('DTSTART;TZID=America/Chicago:20261109T090000', unfold(split['future_content']))

    def test_future_deletion_uses_exact_utc_cutoff_without_changing_local_history_or_zone(self):
        before = local_series()
        after = truncate_before(before, '2026-11-09T15:00:00Z')
        self.assertIn('UNTIL=20261109T145959Z', after)
        self.assertIn('DTSTART;TZID=America/Chicago:20261026T090000', unfold(after))
        self.assertEqual(_resource(after)[1], _resource(before)[1])
        self.assertNotIn('COUNT=6', after)
        monthly = local_series({'frequency': 'monthly', 'ordinal_weekday': {'weekday': 'MO', 'ordinal': 1}, 'count': 6},
            start='2026-11-02T09:00:00-06:00', end='2026-11-02T10:00:00-06:00')
        truncated = truncate_before(monthly, '2026-12-07T15:00:00Z')
        self.assertIn('BYDAY=1MO', truncated)
        self.assertIn('UNTIL=20261207T145959Z', truncated)

    def test_gap_fold_wrong_offset_zone_and_date_changes_refuse(self):
        daily = local_series({'frequency': 'daily'})
        cases = (
            ('2026-11-09T15:00:00Z', {'start': '2026-11-09T11:00:00-05:00', 'end': '2026-11-09T12:00:00-05:00'}),
            ('2026-11-09T15:00:00Z', {'start': '2026-11-09T11:00:00-06:00', 'end': '2026-11-09T12:00:00-06:00', 'timezone': 'America/Denver'}),
            ('2026-11-09T15:00:00Z', {'start': '2026-11-10T11:00:00-06:00', 'end': '2026-11-10T12:00:00-06:00'}),
            ('2026-11-01T15:00:00Z', {'start': '2026-11-01T01:30:00-05:00', 'end': '2026-11-01T02:30:00-06:00'}),
            ('2027-03-14T14:00:00Z', {'start': '2027-03-14T02:30:00-06:00', 'end': '2027-03-14T03:30:00-05:00'}),
            ('2026-11-09T14:00:00Z', {'summary': 'Wrong original wall time'}))
        for target, fields in cases:
            with self.subTest(target=target, fields=fields), self.assertRaises(SafetyError):
                split_future(daily, target, fields, UID)

    def test_until_time_shift_and_unsupported_definition_or_exceptions_refuse(self):
        bounded = local_series({'frequency': 'weekly', 'until': '2026-12-14T15:00:00Z'})
        split = split_future(bounded, '2026-11-09T15:00:00Z', {'summary': 'Content-only until'}, UID)
        self.assertIn('UNTIL=20261214T150000Z', split['future_content'])
        with self.assertRaises(SafetyError):
            split_future(bounded, '2026-11-09T15:00:00Z', {'start': '2026-11-09T11:00:00-06:00',
                'end': '2026-11-09T12:00:00-06:00'}, UID)
        before = local_series()
        altered = before.replace('TZOFFSETTO:-0500', 'TZOFFSETTO:-0400')
        with self.assertRaises(SafetyError):
            split_future(altered, '2026-11-09T15:00:00Z', {'summary': 'Unsupported zone'}, UID)
        excluded = occurrence_content(before, 'delete_occurrence', '2026-11-09T15:00:00Z')
        with self.assertRaises(SafetyError):
            split_future(excluded, '2026-11-16T15:00:00Z', {'summary': 'Unsupported exceptions'}, UID)
        with self.assertRaises(SafetyError):
            truncate_before(excluded, '2026-11-16T15:00:00Z')
