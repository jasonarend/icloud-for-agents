"""Two-resource future splits use synthetic finite calendars and fault injection."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.calendar_future_split import split_future
from icloud_for_agents.core import SafetyError, digest
from icloud_for_agents.dav import DAVReader
from icloud_for_agents.dav_changes import DAVChanges, DAVWriteRefused, event_content, unfold

UID = 'a' * 32


def sample(recurrence=None, start='2026-11-02T15:00:00Z', end='2026-11-02T16:00:00Z'):
    return event_content({'summary': 'Synthetic', 'description': 'Keep prior history', 'location': 'Original room',
                          'url': 'https://example.invalid/synthetic', 'free': True, 'private': True,
                          'start': start, 'end': end, 'timezone': 'UTC',
                          'recurrence': recurrence or {'frequency': 'weekly', 'count': 10}})


class FutureSplitContentTests(unittest.TestCase):
    def test_count_is_partitioned_and_unedited_properties_and_history_are_preserved(self):
        before = sample()
        split = split_future(before, '2026-11-16T15:00:00Z', {'summary': 'Future title'}, UID)
        self.assertEqual(split['preceding_occurrences'], 2)
        self.assertEqual(split['remaining_count'], 8)
        original, future = map(unfold, (split['original_content'], split['future_content']))
        self.assertIn('RRULE:FREQ=WEEKLY;INTERVAL=1;UNTIL=20261116T145959Z', original)
        self.assertIn('SUMMARY:Synthetic', original)
        self.assertIn('DTSTART:20261102T150000Z', original)
        self.assertIn('UID:' + UID, future)
        self.assertIn('DTSTART:20261116T150000Z', future)
        self.assertIn('DTEND:20261116T160000Z', future)
        self.assertIn('RRULE:FREQ=WEEKLY;INTERVAL=1;COUNT=8', future)
        self.assertIn('SUMMARY:Future title', future)
        for line in ('DESCRIPTION:Keep prior history', 'LOCATION:Original room',
                     'URL:https://example.invalid/synthetic', 'TRANSP:TRANSPARENT', 'CLASS:PRIVATE'):
            self.assertIn(line, original)
            self.assertIn(line, future)

    def test_until_is_not_extended_and_daily_interval_phase_is_preserved(self):
        before = sample({'frequency': 'daily', 'interval': 2, 'until': '2026-12-01T15:00:00Z'})
        split = split_future(before, '2026-11-08T15:00:00Z', {'description': 'Future details'}, UID)
        self.assertEqual(split['preceding_occurrences'], 3)
        self.assertIsNone(split['remaining_count'])
        self.assertEqual(split['original_until'], '20261201T150000Z')
        self.assertIn('RRULE:FREQ=DAILY;INTERVAL=2;UNTIL=20261201T150000Z', unfold(split['future_content']))
        with self.assertRaises(SafetyError):
            split_future(before, '2026-11-09T15:00:00Z', {'summary': 'Off phase'}, UID)
        with self.assertRaises(SafetyError):
            split_future(before, '2026-12-02T15:00:00Z', {'summary': 'Past until'}, UID)

    def test_weekly_multi_day_count_uses_first_partial_week_and_interval_phase(self):
        before = sample({'frequency': 'weekly', 'interval': 2, 'weekdays': ['MO', 'WE', 'FR'], 'count': 10},
                        start='2026-11-04T15:00:00Z', end='2026-11-04T16:00:00Z')
        split = split_future(before, '2026-11-18T15:00:00Z', {'location': 'Future room'}, UID)
        self.assertEqual(split['preceding_occurrences'], 3)
        self.assertEqual(split['remaining_count'], 7)
        self.assertIn('BYDAY=MO,WE,FR', split['future_content'])
        with self.assertRaises(SafetyError):
            split_future(before, '2026-11-11T15:00:00Z', {'summary': 'Off week'}, UID)

    def test_indefinite_series_keeps_its_unchanged_future_recurrence(self):
        before = sample({'frequency': 'weekly', 'interval': 2, 'weekdays': ['MO', 'WE']})
        split = split_future(before, '2026-11-16T15:00:00Z', {'summary': 'Future title'}, UID)
        self.assertIsNone(split['remaining_count'])
        self.assertIsNone(split['original_until'])
        self.assertIn('RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE', unfold(split['future_content']))
        self.assertIn('UNTIL=20261116T145959Z', split['original_content'])
        self.assertIn('remains indefinite', ' '.join(split['effects']))

    def test_future_time_change_preserves_count_pattern_and_past_schedule(self):
        before = sample({'frequency': 'weekly', 'interval': 2, 'weekdays': ['MO', 'WE', 'FR'], 'count': 10},
                        start='2026-11-04T15:00:00Z', end='2026-11-04T16:00:00Z')
        split = split_future(before, '2026-11-18T15:00:00Z',
                             {'start': '2026-11-18T23:00:00Z', 'end': '2026-11-18T23:30:00Z', 'timezone': 'UTC'}, UID)
        self.assertEqual(split['preceding_occurrences'], 3)
        self.assertEqual(split['remaining_count'], 7)
        self.assertIn('DTSTART:20261104T150000Z', unfold(split['original_content']))
        self.assertIn('DTEND:20261104T160000Z', unfold(split['original_content']))
        self.assertIn('UNTIL=20261118T145959Z', split['original_content'])
        self.assertIn('DTSTART:20261118T230000Z', unfold(split['future_content']))
        self.assertIn('DTEND:20261118T233000Z', unfold(split['future_content']))
        self.assertIn('FREQ=WEEKLY;INTERVAL=2;COUNT=7;BYDAY=MO,WE,FR', split['future_content'])
        self.assertEqual(split['time_change']['original_start'], '2026-11-18T15:00:00+00:00')
        self.assertEqual(split['time_change']['new_start'], '2026-11-18T23:00:00+00:00')
        self.assertTrue(split['time_change']['recurrence_dates_unchanged'])
        self.assertIn('past event times remain unchanged', ' '.join(split['effects']))

    def test_daily_indefinite_utc_time_change_is_independent_of_local_dst(self):
        before = sample({'frequency': 'daily'})
        split = split_future(before, '2027-03-14T15:00:00Z',
                             {'start': '2027-03-14T02:00:00Z', 'end': '2027-03-14T03:00:00Z'}, UID)
        self.assertIn('RRULE:FREQ=DAILY;INTERVAL=1', unfold(split['future_content']))
        self.assertNotIn('COUNT=', split['future_content'])
        self.assertNotIn('UNTIL=', split['future_content'])
        self.assertIn('DTSTART:20270314T020000Z', unfold(split['future_content']))
        self.assertIn('DTSTART:20261102T150000Z', unfold(split['original_content']))

    def test_future_time_changes_refuse_date_shifts_zones_partial_intervals_and_until(self):
        before = sample()
        target = '2026-11-09T15:00:00Z'
        cases = ({'start': '2026-11-10T17:00:00Z', 'end': '2026-11-10T18:00:00Z'},
                 {'start': '2026-11-09T23:00:00Z', 'end': '2026-11-10T00:00:00Z'},
                 {'start': '2026-11-09T18:00:00Z', 'end': '2026-11-09T17:00:00Z'},
                 {'start': '2026-11-09T17:00:00Z'},
                 {'start': '2026-11-09T17:00:00Z', 'end': '2026-11-09T18:00:00Z', 'timezone': 'America/Chicago'},
                 {'start': '2026-11-09T17:00:00-06:00', 'end': '2026-11-09T18:00:00-06:00'},
                 {'start': '2026-11-09T15:00:00Z', 'end': '2026-11-09T16:00:00Z'},
                 {'start': '2026-11-09T17:00:00.5Z', 'end': '2026-11-09T18:00:00Z'})
        for fields in cases:
            with self.subTest(fields=fields), self.assertRaises(SafetyError):
                split_future(before, target, fields, UID)
        finite = sample({'frequency': 'daily', 'until': '2026-12-01T15:00:00Z'})
        with self.assertRaises(SafetyError):
            split_future(finite, target, {'start': '2026-11-09T17:00:00Z', 'end': '2026-11-09T18:00:00Z'}, UID)
        unchanged = split_future(finite, target, {'summary': 'Content-only change',
            'start': target, 'end': '2026-11-09T16:00:00Z'}, UID)
        self.assertIsNone(unchanged['time_change'])
        self.assertIn('UNTIL=20261201T150000Z', unchanged['future_content'])

    def test_unsupported_changes_are_refused(self):
        before = sample()
        for fields in ({}, {'summary': 'Synthetic'}, {'start': '2026-11-09T17:00:00Z'},
                       {'recurrence': {'frequency': 'daily'}}, {'timezone': 'America/Chicago'}):
            with self.subTest(fields=fields), self.assertRaises(SafetyError):
                split_future(before, '2026-11-09T15:00:00Z', fields, UID)
        cases = (sample({'frequency': 'monthly', 'count': 10}),
                 before.replace('END:VEVENT', 'EXDATE:20261109T150000Z\r\nEND:VEVENT'),
                 before.replace('END:VEVENT', 'BEGIN:VALARM\r\nACTION:DISPLAY\r\nEND:VALARM\r\nEND:VEVENT'),
                 before.replace('END:VEVENT', 'ATTENDEE:mailto:synthetic@example.invalid\r\nEND:VEVENT'))
        for value in cases:
            with self.subTest(value=value), self.assertRaises(SafetyError):
                split_future(value, '2026-11-09T15:00:00Z', {'summary': 'Future'}, UID)
        for target in ('2026-11-02T15:00:00Z', '2026-11-09T16:00:00Z', '2027-11-09T15:00:00Z'):
            with self.subTest(target=target), self.assertRaises(SafetyError):
                split_future(before, target, {'summary': 'Future'}, UID)


class FutureSplitExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = PlanStore(Path(self.temp.name) / 'plans')
        self.collection = 'https://caldav.icloud.com/synthetic/'
        self.url = self.collection + 'original.ics'
        self.original = sample()
        self.resources = {self.url: self.original}
        self.reader = DAVReader('synthetic@example.com', 'fake-only', Mock())
        self.reader.known_collection = lambda kind, collection: collection
        self.reader.transport = Mock(side_effect=self.read)
        self.writer = Mock(side_effect=self.write)
        self.editor = DAVChanges(self.reader, self.store, self.writer)
        self.proof = {'recurrence_id': '20261116T150000Z', 'membership': 'synthetic provider assertion'}
        self.patcher = patch('icloud_for_agents.calendar_occurrence_proof.prove_occurrence', return_value=self.proof)
        self.prove = self.patcher.start(); self.addCleanup(self.patcher.stop)
        env = patch.dict(os.environ, {'ICLOUD_APPROVAL_POLICY': 'trusted-client',
                                     'ICLOUD_MAIL_ACCOUNT': self.reader.account})
        env.start(); self.addCleanup(env.stop)

    def read(self, method, url, body, depth):
        if url not in self.resources:
            return 404, {}, b''
        return 200, {'etag': '"synthetic"'}, self.resources[url].encode()

    def write(self, kind, item):
        self.resources[item['url']] = item['content']
        return {'operation': item['operation'], 'url': item['url'], 'status': 'applied'}

    def proposal(self):
        return self.editor.prepare('cal', self.collection, [{'operation': 'update', 'url': self.url,
            'recurrence_scope': 'future', 'occurrence_start': '2026-11-16T15:00:00Z',
            'fields': {'summary': 'Future title'}}], temporary=True)

    def test_exact_review_then_create_verify_truncate_and_verify_both(self):
        proposal = self.proposal()
        self.writer.assert_not_called()
        item = proposal['review']['payload']['changes'][0]
        self.assertEqual(self.resources[self.url], self.original)
        new = item['future_split']['new_resource']
        self.assertNotEqual(new['url'], self.url)
        self.assertIn('failure can leave duplicate', ' '.join(item['future_split']['effects']))
        result = self.editor.execute(proposal['plan_id'])
        self.assertEqual(result['status'], 'applied')
        self.assertEqual([call.args[1]['operation'] for call in self.writer.call_args_list], ['create', 'update'])
        self.assertEqual(self.resources[self.url], item['content'])
        self.assertEqual(self.resources[new['url']], new['content'])
        self.assertEqual(self.prove.call_count, 3)
        self.assertEqual(len(result['completed']), 2)
        self.assertTrue(self.store.operation_settled(proposal['plan_id']))
        with self.assertRaises(SafetyError):
            self.editor.execute(proposal['plan_id'])

    def test_local_receipt_is_required_by_default_and_still_uses_two_reviewed_writes(self):
        with patch.dict(os.environ, {'ICLOUD_APPROVAL_POLICY': 'local-receipt'}):
            proposal = self.proposal()
            with self.assertRaises(FileNotFoundError):
                self.editor.execute(proposal['plan_id'])
            self.writer.assert_not_called()
            self.store.write_once(self.store.path(proposal['plan_id'], '.approved'),
                {'plan_id': proposal['plan_id'], 'sha256': proposal['sha256']})
            self.assertEqual(self.editor.execute(proposal['plan_id'])['status'], 'applied')

    def test_changed_proof_or_existing_new_url_stops_before_dispatch_and_settles(self):
        for fault in ('proof', 'new_url'):
            proposal = self.proposal()
            new = proposal['review']['payload']['changes'][0]['future_split']['new_resource']
            if fault == 'proof':
                self.prove.return_value = {'changed': True}
            else:
                self.resources[new['url']] = 'Other existing resource'
            result = self.editor.execute(proposal['plan_id'])
            self.assertIn(result['status'], {'calendar_occurrence_refused_before_dispatch',
                                            'calendar_future_split_refused_before_dispatch'})
            self.assertTrue(self.store.operation_settled(proposal['plan_id']))
            self.writer.assert_not_called()
            self.assertEqual(self.resources[self.url], self.original)
            self.prove.return_value = self.proof

    def test_failed_creation_or_new_readback_never_truncates_original(self):
        proposal = self.proposal()
        self.writer.side_effect = OSError('Synthetic lost reply')
        result = self.editor.execute(proposal['plan_id'])
        self.assertEqual(result['status'], 'failed_or_uncertain')
        self.assertEqual(result['diagnostic']['phase'], 'future_split_create')
        self.assertEqual(self.resources[self.url], self.original)
        self.assertFalse(self.store.operation_settled(proposal['plan_id']))
        # A fresh proposal is not permission to repeat an uncertain creation.
        fresh = self.proposal()
        for policy in ('trusted-client', 'local-receipt'):
            with patch.dict(os.environ, {'ICLOUD_APPROVAL_POLICY': policy}):
                if policy == 'local-receipt':
                    self.store.write_once(self.store.path(fresh['plan_id'], '.approved'),
                        {'plan_id': fresh['plan_id'], 'sha256': fresh['sha256']})
                with self.assertRaises(SafetyError):
                    self.editor.execute(fresh['plan_id'])
        self.assertEqual(self.writer.call_count, 1)

    def test_new_series_readback_or_original_race_leaves_explicit_uncertainty_without_rollback(self):
        for fault in ('new_readback', 'original_race', 'truncate_refusal'):
            # Separate accounts/runtime per subcase avoid intentionally retained uncertainty.
            self.store = PlanStore(Path(self.temp.name) / fault)
            self.editor = DAVChanges(self.reader, self.store, self.writer)
            self.resources = {self.url: self.original}
            self.writer.reset_mock()
            def failing_write(kind, item):
                if item['operation'] == 'create':
                    result = self.write(kind, item)
                    if fault == 'new_readback':
                        self.resources[item['url']] = item['content'].replace('SUMMARY:Future title', 'SUMMARY:Changed')
                    if fault == 'original_race':
                        self.resources[self.url] = self.original.replace('SUMMARY:Synthetic', 'SUMMARY:Externally changed')
                    return result
                raise DAVWriteRefused(412)
            self.writer.side_effect = failing_write
            proposal = self.proposal()
            new = proposal['review']['payload']['changes'][0]['future_split']['new_resource']
            result = self.editor.execute(proposal['plan_id'])
            self.assertEqual(result['status'], 'failed_or_uncertain')
            self.assertFalse(self.store.operation_settled(proposal['plan_id']))
            self.assertIn(new['url'], self.resources)
            self.assertFalse(any(call.args[1]['operation'] == 'delete' for call in self.writer.call_args_list))
            if fault != 'original_race':
                self.assertEqual(self.resources[self.url], self.original)
            self.assertEqual(self.writer.call_count, 2 if fault == 'truncate_refusal' else 1)

    def test_readback_failure_after_truncation_preserves_both_attempts_and_blocks_replay(self):
        for fault in ('original_readback', 'future_final_readback'):
            self.store = PlanStore(Path(self.temp.name) / fault)
            self.editor = DAVChanges(self.reader, self.store, self.writer)
            self.resources = {self.url: self.original}
            self.writer.reset_mock()
            def faulty_write(kind, item):
                result = self.write(kind, item)
                if item['operation'] == 'update':
                    if fault == 'original_readback':
                        self.resources[self.url] = item['content'].replace('SUMMARY:Synthetic', 'SUMMARY:Unexpected original')
                    else:
                        for url in self.resources:
                            if url != self.url:
                                self.resources[url] = self.resources[url].replace('SUMMARY:Future title', 'SUMMARY:Unexpected future')
                return result
            self.writer.side_effect = faulty_write
            proposal = self.proposal()
            result = self.editor.execute(proposal['plan_id'])
            self.assertEqual(result['status'], 'failed_or_uncertain')
            self.assertEqual(len(result['completed']), 2)
            self.assertEqual(self.writer.call_count, 2)
            self.assertFalse(self.store.operation_settled(proposal['plan_id']))
            with self.assertRaises(SafetyError):
                self.editor.execute(proposal['plan_id'])

    def test_reviewed_content_or_partition_tampering_refuses_before_any_dispatch(self):
        proposal = self.proposal()
        plan = self.store.read(self.store.path(proposal['plan_id'], '.json'))
        plan['payload']['changes'][0]['future_split']['remaining_count'] = 999
        self.store.path(proposal['plan_id'], '.json').write_text(__import__('json').dumps(plan))
        result = self.editor.execute(proposal['plan_id'])
        self.assertEqual(result['status'], 'calendar_future_split_refused_before_dispatch')
        self.assertTrue(self.store.operation_settled(proposal['plan_id']))
        self.writer.assert_not_called()

    def test_http_creation_and_truncation_use_exclusive_and_original_etag_conditions(self):
        item = self.proposal()['review']['payload']['changes'][0]
        connection = Mock(); connection.getresponse.return_value.status = 204
        with patch('icloud_for_agents.dav_changes.http.client.HTTPSConnection', return_value=connection):
            self.editor._write('cal', item['future_split']['new_resource'])
            self.assertEqual(connection.request.call_args.kwargs['headers']['If-None-Match'], '*')
            self.editor._write('cal', item)
            self.assertEqual(connection.request.call_args.kwargs['headers']['If-Match'], '"synthetic"')
