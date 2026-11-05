"""Explicit future schedule replacements retain history and use reviewed new rules."""
import unittest
from icloud_for_agents.calendar_future_split import split_future
from icloud_for_agents.calendar_occurrence_changes import _resource
from icloud_for_agents.core import SafetyError
from icloud_for_agents.dav_changes import unfold
from test_calendar_future_split import sample, UID
from test_calendar_future_local import local_series


class FutureScheduleReplacementTests(unittest.TestCase):
    def test_shifted_date_and_new_count_replace_only_future_schedule(self):
        before = sample()
        fields = {'start': '2026-11-18T17:00:00Z', 'end': '2026-11-18T18:30:00Z', 'timezone': 'UTC',
                  'recurrence': {'frequency': 'weekly', 'weekdays': ['WE'], 'count': 5}}
        split = split_future(before, '2026-11-16T15:00:00Z', fields, UID)
        self.assertEqual(split['preceding_occurrences'], 2)
        self.assertEqual(split['schedule_replacement']['replaced_remaining_count'], 8)
        self.assertEqual(split['remaining_count'], 5)
        self.assertIn('DTSTART:20261102T150000Z', unfold(split['original_content']))
        self.assertIn('UNTIL=20261116T145959Z', split['original_content'])
        self.assertIn('DTSTART:20261118T170000Z', unfold(split['future_content']))
        self.assertIn('DTEND:20261118T183000Z', unfold(split['future_content']))
        self.assertIn('FREQ=WEEKLY;INTERVAL=1;COUNT=5;BYDAY=WE', split['future_content'])
        self.assertTrue(split['schedule_replacement']['original_end_condition_replaced'])
        self.assertTrue(split['schedule_replacement']['count_starts_at_new_first_occurrence'])
        self.assertFalse(split['time_change']['recurrence_dates_unchanged'])
        self.assertIn('intentionally leaves a gap', ' '.join(split['effects']))

    def test_old_until_can_be_replaced_by_a_new_monthly_pattern_count_until_or_indefinite(self):
        before = sample({'frequency': 'daily', 'until': '2026-12-01T15:00:00Z'})
        fields = {'start': '2026-12-09T17:00:00Z', 'end': '2026-12-09T18:00:00Z', 'timezone': 'UTC',
                  'recurrence': {'frequency': 'monthly', 'ordinal_weekday': {'weekday': 'WE', 'ordinal': 2}, 'count': 3}}
        counted = split_future(before, '2026-11-09T15:00:00Z', fields, UID)
        self.assertIn('FREQ=MONTHLY;INTERVAL=1;COUNT=3;BYDAY=2WE', counted['future_content'])
        self.assertNotIn('UNTIL=20261201T150000Z', counted['future_content'])
        fields['recurrence'] = {'frequency': 'daily', 'until': '2027-02-01T23:59:00Z'}
        until = split_future(before, '2026-11-09T15:00:00Z', fields, UID)
        self.assertIn('UNTIL=20270201T235900Z', until['future_content'])
        self.assertIsNone(until['remaining_count'])
        fields['recurrence'] = {'frequency': 'weekly'}
        indefinite = split_future(before, '2026-11-09T15:00:00Z', fields, UID)
        self.assertNotIn('COUNT=', indefinite['future_content'])
        self.assertNotIn('UNTIL=', indefinite['future_content'])
        self.assertEqual(indefinite['schedule_replacement']['recurrence'], {'frequency': 'weekly'})

    def test_local_new_date_and_rule_preserve_zone_provider_definition_and_original_history(self):
        before = local_series()
        fields = {'start': '2026-11-12T11:00:00-06:00', 'end': '2026-11-12T12:00:00-06:00',
                  'timezone': 'America/Chicago', 'recurrence': {'frequency': 'monthly', 'month_days': [12], 'count': 7}}
        split = split_future(before, '2026-11-09T15:00:00Z', fields, UID)
        self.assertEqual(split['remaining_count'], 7)
        self.assertEqual(split['schedule_replacement']['replaced_remaining_count'], 4)
        self.assertIn('DTSTART;TZID=America/Chicago:20261112T110000', unfold(split['future_content']))
        self.assertIn('FREQ=MONTHLY;INTERVAL=1;COUNT=7;BYMONTHDAY=12', split['future_content'])
        self.assertEqual(_resource(before)[1], _resource(split['future_content'])[1])
        self.assertIn('DTSTART;TZID=America/Chicago:20261026T090000', unfold(split['original_content']))
        self.assertIn('UNTIL=20261109T145959Z', split['original_content'])
        self.assertIsNotNone(split['timezone_validation'])

    def test_complete_new_schedule_is_required_and_cannot_shift_into_original_history(self):
        fields = {'start': '2026-11-18T17:00:00Z', 'end': '2026-11-18T18:00:00Z', 'timezone': 'UTC',
                  'recurrence': {'frequency': 'weekly', 'count': 5}}
        for missing in ('start', 'end', 'timezone'):
            incomplete = {key: value for key, value in fields.items() if key != missing}
            with self.subTest(missing=missing), self.assertRaises(SafetyError):
                split_future(sample(), '2026-11-16T15:00:00Z', incomplete, UID)
        bad = [dict(fields, start='2026-11-15T17:00:00Z', end='2026-11-15T18:00:00Z'),
               dict(fields, timezone='America/Chicago'), dict(fields, timezone=[]),
               dict(fields, end='2026-11-19T01:00:00Z'),
               dict(fields, recurrence={'frequency': 'weekly', 'count': 5, 'until': '2027-01-01T00:00:00Z'}),
               dict(fields, recurrence={'frequency': 'monthly', 'month_days': [19], 'count': 5}),
               dict(fields, recurrence={'frequency': 'weekly', 'count': True})]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(SafetyError):
                split_future(sample(), '2026-11-16T15:00:00Z', value, UID)

    def test_local_schedule_replacement_rejects_gap_fold_and_offset_mismatch(self):
        fields = {'start': '2027-03-14T02:30:00-06:00', 'end': '2027-03-14T03:30:00-05:00',
                  'timezone': 'America/Chicago', 'recurrence': {'frequency': 'daily', 'count': 5}}
        for times in (('2027-03-14T02:30:00-06:00', '2027-03-14T03:30:00-05:00'),
                      ('2026-11-01T01:30:00-05:00', '2026-11-01T02:30:00-06:00'),
                      ('2026-11-12T11:00:00-05:00', '2026-11-12T12:00:00-05:00')):
            value = dict(fields, start=times[0], end=times[1])
            with self.subTest(times=times), self.assertRaises(SafetyError):
                split_future(local_series(), '2026-11-02T15:00:00Z', value, UID)
