"""Exact whole-resource operations preserve bounded personal recurrence exceptions."""
import os
import json
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.calendar_occurrence_changes import _resource, occurrence_content, verify_occurrence_write
from icloud_for_agents.calendar_series_exceptions import series_exception_content
from icloud_for_agents.core import SafetyError
from icloud_for_agents.dav import DAVReader
from icloud_for_agents.dav_changes import DAVChanges, event_content, unfold
from tests.test_calendar_timezone import CHICAGO


def resource(local=False):
    fields = {'summary': 'Synthetic master', 'location': 'Master room', 'description': 'Original details',
              'start': '2026-10-26T09:00:00-05:00' if local else '2026-10-26T14:00:00Z',
              'end': '2026-10-26T10:00:00-05:00' if local else '2026-10-26T15:00:00Z',
              'timezone': 'America/Chicago' if local else 'UTC',
              'recurrence': {'frequency': 'weekly', 'count': 6}}
    value = event_content(fields, timezone_definition=CHICAGO if local else None)
    target = '2026-11-09T15:00:00Z' if local else '2026-11-09T14:00:00Z'
    value = occurrence_content(value, 'update', target, {'summary': 'Customized occurrence', 'location': 'Override room'})
    excluded = '2026-11-16T15:00:00Z' if local else '2026-11-16T14:00:00Z'
    return occurrence_content(value, 'delete_occurrence', excluded)


class SeriesExceptionContentTests(unittest.TestCase):
    def test_requested_content_applies_to_all_components_and_retains_other_overrides_and_timing(self):
        for local in (False, True):
            before = resource(local)
            after = series_exception_content(before, {'summary': 'Shared whole-series title', 'private': True})
            original_parts, changed_parts = _resource(before)[2], _resource(after)[2]
            self.assertEqual(original_parts.keys(), changed_parts.keys())
            self.assertEqual(after.count('SUMMARY:Shared whole-series title'), 2)
            self.assertNotIn('SUMMARY:Customized occurrence', after)
            self.assertIn('LOCATION:Master room', after)
            self.assertIn('LOCATION:Override room', after)
            for identity, original in original_parts.items():
                changed = changed_parts[identity]
                self.assertEqual(original['start'], changed['start'])
                self.assertEqual(original['end'], changed['end'])
                self.assertEqual(original['exclusions'], changed['exclusions'])
                for key in set(original['values']) - {'SUMMARY', 'CLASS', 'DTSTAMP'}:
                    self.assertEqual(original['values'][key], changed['values'][key])
            verify_occurrence_write(after, after)

    def test_exdate_only_master_is_supported_and_schedule_or_unknown_fields_refuse(self):
        before = event_content({'summary': 'Synthetic', 'start': '2026-11-02T15:00:00Z',
            'end': '2026-11-02T16:00:00Z', 'timezone': 'UTC', 'recurrence': {'frequency': 'weekly', 'count': 3}})
        before = occurrence_content(before, 'delete_occurrence', '2026-11-09T15:00:00Z')
        after = series_exception_content(before, {'description': 'Whole-series details'})
        self.assertIn('EXDATE:20261109T150000Z', after)
        self.assertIn('DESCRIPTION:Whole-series details', after)
        for fields in ({'start': '2026-11-02T17:00:00Z', 'end': '2026-11-02T18:00:00Z'},
                       {'recurrence': {'frequency': 'daily'}}, {'unknown': 'value'}, {}):
            with self.subTest(fields=fields), self.assertRaises(SafetyError):
                series_exception_content(before, fields)

    def test_every_exception_and_exclusion_participates_in_readback(self):
        expected = series_exception_content(resource(), {'summary': 'Reviewed'})
        for observed in (expected.replace('LOCATION:Override room', 'LOCATION:Lost override'),
                         expected.replace('EXDATE:20261116T140000Z\r\n', ''),
                         expected.replace('RECURRENCE-ID:20261109T140000Z', 'RECURRENCE-ID:20261110T140000Z')):
            with self.subTest(observed=observed), self.assertRaises(SafetyError):
                verify_occurrence_write(expected, observed)


class SeriesExceptionExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = PlanStore(Path(self.temp.name) / 'plans')
        self.collection = 'https://caldav.icloud.com/synthetic/'
        self.url = self.collection + 'series.ics'
        self.value = resource()
        self.reader = DAVReader('synthetic@example.com', 'fake-only', Mock())
        self.reader.known_collection = lambda kind, collection: collection
        self.reader.transport = Mock(side_effect=self.read)
        self.writer = Mock(side_effect=self.write)
        self.editor = DAVChanges(self.reader, self.store, self.writer)
        env = patch.dict(os.environ, {'ICLOUD_APPROVAL_POLICY': 'trusted-client',
                                     'ICLOUD_MAIL_ACCOUNT': self.reader.account})
        env.start(); self.addCleanup(env.stop)

    def read(self, method, url, body, depth):
        if self.value is None:
            return 404, {}, b''
        return 200, {'etag': '"synthetic"'}, self.value.encode()

    def write(self, kind, item):
        self.value = None if item['operation'] == 'delete' else item['content']
        return {'url': item['url'], 'operation': item['operation'], 'status': 'applied'}

    def prepare(self, operation='update', **kwargs):
        change = {'operation': operation, 'recurrence_scope': 'series', 'url': self.url}
        if operation == 'update':
            change['fields'] = {'summary': 'Whole-series title'}
        else:
            change['irreversible_confirmed'] = True
        return self.editor.prepare('cal', self.collection, [change], temporary=True, **kwargs)

    def test_content_update_previews_all_components_and_verifies_complete_resource(self):
        proposal = self.prepare()
        marker = proposal['review']['payload']['changes'][0]['series_exception_update']
        self.assertEqual(marker['component_count'], 2)
        self.assertEqual(marker['fields_applied_to_every_component'], ['summary'])
        self.assertIn('including customized values', marker['effect'])
        self.writer.assert_not_called()
        result = self.editor.execute(proposal['plan_id'])
        self.assertEqual(result['status'], 'applied')
        self.assertEqual(result['completed'][0]['verification'], 'reviewed_whole_series_exception_content_readback')
        self.assertEqual(self.value.count('SUMMARY:Whole-series title'), 2)
        with self.assertRaises(SafetyError):
            self.editor.execute(proposal['plan_id'])

    def test_series_deletion_requires_selected_receipt_and_resource_absence(self):
        proposal = self.prepare('delete')
        self.assertTrue(proposal['local_receipt_required'])
        with self.assertRaises(FileNotFoundError):
            self.editor.execute(proposal['plan_id'])
        self.store.write_once(self.store.path(proposal['plan_id'], '.approved'),
            {'plan_id': proposal['plan_id'], 'sha256': proposal['sha256']})
        result = self.editor.execute(proposal['plan_id'])
        self.assertEqual(result['status'], 'applied')
        self.assertEqual(result['completed'][0]['verification'], 'series_resource_absent')
        self.assertIsNone(self.value)

    def test_explicit_trusted_deletion_of_bounded_exceptions_is_supported(self):
        proposal = self.prepare('delete', deletion_authorization='trusted-client')
        self.assertFalse(proposal['local_receipt_required'])
        self.assertEqual(self.editor.execute(proposal['plan_id'])['status'], 'applied')

    def test_invites_alarms_unknown_components_and_schedule_changes_refuse(self):
        original = self.value
        for extra in ('ATTENDEE:mailto:synthetic@example.invalid', 'ORGANIZER:mailto:owner@example.invalid',
                      'BEGIN:VALARM\r\nACTION:DISPLAY\r\nEND:VALARM', 'X-UNKNOWN:value'):
            self.value = original.replace('END:VEVENT', extra + '\r\nEND:VEVENT', 1)
            for operation in ('update', 'delete'):
                with self.subTest(extra=extra, operation=operation), self.assertRaises(SafetyError):
                    self.prepare(operation)
        self.value = original
        with self.assertRaises(SafetyError):
            self.editor.prepare('cal', self.collection, [{'operation': 'update', 'recurrence_scope': 'series',
                'url': self.url, 'fields': {'start': '2026-10-26T17:00:00Z', 'end': '2026-10-26T18:00:00Z',
                                          'timezone': 'UTC', 'recurrence': {'frequency': 'weekly', 'count': 6}}}])
        self.writer.assert_not_called()

    def failed_readback(self):
        proposal = self.prepare()
        expected = proposal['review']['payload']['changes'][0]['content']
        self.writer.side_effect = lambda kind, item: {'url': item['url'], 'operation': 'update', 'status': 'applied'}
        result = self.editor.execute(proposal['plan_id'])
        self.assertEqual(result['status'], 'failed_or_uncertain')
        self.assertEqual(result['diagnostic']['phase'], 'series_exception_readback')
        self.writer.reset_mock()
        return proposal, expected

    def test_exact_full_exception_readback_reconciles_without_mutation_or_replay(self):
        proposal, expected = self.failed_readback()
        identity = proposal['plan_id']
        original_bytes = {suffix: self.store.path(identity, suffix).read_bytes() for suffix in ('.json', '.started', '.result')}
        self.value = expected
        result = self.store.reconcile_calendar_series_update_readback(identity, self.editor)
        self.assertEqual(result['status'], 'calendar_series_update_verified_after_readback')
        self.assertTrue(self.store.operation_settled(identity))
        self.assertEqual(self.store.status(identity)['original_status'], 'failed_or_uncertain')
        record = self.store.read(self.store.path(identity, '.calendar-series-readback'))
        self.assertEqual(record['verification'], 'full_reviewed_series_exception_content_readback')
        self.writer.assert_not_called()
        for suffix, content in original_bytes.items():
            self.assertEqual(self.store.path(identity, suffix).read_bytes(), content)
        with self.assertRaises(SafetyError):
            self.editor.execute(identity)

    def test_missing_changed_exception_exclusion_or_wrong_phase_cannot_reconcile(self):
        proposal, expected = self.failed_readback()
        identity = proposal['plan_id']
        for changed in (expected.replace('LOCATION:Override room', 'LOCATION:Unknown override'),
                        expected.replace('EXDATE:20261116T140000Z\r\n', ''),
                        expected.replace('RECURRENCE-ID:20261109T140000Z', 'RECURRENCE-ID:20261110T140000Z')):
            self.value = changed
            with self.subTest(changed=changed), self.assertRaises(SafetyError):
                self.store.reconcile_calendar_series_update_readback(identity, self.editor)
            self.assertFalse(self.store.operation_settled(identity))
        self.value = expected
        result_path = self.store.path(identity, '.result')
        original = self.store.read(result_path)
        for changed in (dict(original, completed=[]),
                        dict(original, diagnostic={'phase': 'conditional_write', 'category': 'safety_precondition'})):
            result_path.write_text(json.dumps(changed))
            with self.assertRaises(SafetyError):
                self.store.reconcile_calendar_series_update_readback(identity, self.editor)
        result_path.write_text(json.dumps(original))
        with patch.dict(os.environ, {'ICLOUD_MAIL_ACCOUNT': 'other@example.com'}), self.assertRaises(SafetyError):
            self.store.reconcile_calendar_series_update_readback(identity, self.editor)
        self.writer.assert_not_called()

    def test_reconciliation_rejects_tampered_original_marker_hash_and_observation_record(self):
        proposal, expected = self.failed_readback()
        identity = proposal['plan_id']
        self.value = expected
        started_path = self.store.path(identity, '.started')
        started = self.store.read(started_path)
        started_path.write_text(json.dumps(dict(started, sha256='0' * 64)))
        with self.assertRaises(SafetyError):
            self.store.reconcile_calendar_series_update_readback(identity, self.editor)
        started_path.write_text(json.dumps(started))
        plan_path = self.store.path(identity, '.json')
        plan = self.store.read(plan_path)
        changed = deepcopy(plan)
        changed['payload']['changes'][0]['series_exception_update']['component_count'] = 999
        plan_path.write_text(json.dumps(changed))
        with self.assertRaises(SafetyError):
            self.store.reconcile_calendar_series_update_readback(identity, self.editor)
        plan_path.write_text(json.dumps(plan))
        self.store.reconcile_calendar_series_update_readback(identity, self.editor)
        record_path = self.store.path(identity, '.calendar-series-readback')
        record = self.store.read(record_path)
        changed = deepcopy(record)
        changed['observation']['content'] = changed['observation']['content'].replace('EXDATE:20261116T140000Z\r\n', '')
        record_path.write_text(json.dumps(changed))
        self.assertFalse(self.store.operation_settled(identity))
        self.writer.assert_not_called()

    def test_exception_preflight_refusal_settles_only_exact_authenticated_no_dispatch_result(self):
        proposal = self.prepare()
        identity = proposal['plan_id']
        plan_path = self.store.path(identity, '.json')
        plan = self.store.read(plan_path)
        plan['payload']['changes'][0]['series_exception_update']['component_count'] = 999
        plan_path.write_text(json.dumps(plan))
        result = self.editor.execute(identity)
        self.assertEqual(result['status'], 'calendar_series_exception_refused_before_dispatch')
        self.assertEqual(result['completed'], [])
        self.assertTrue(self.store.operation_settled(identity))
        self.writer.assert_not_called()
        result_path = self.store.path(identity, '.result')
        for changed in (dict(result, plan_sha256='0' * 64), dict(result, completed=[{'operation': 'update'}]),
                        dict(result, diagnostic={'phase': 'conditional_write', 'category': 'safety_precondition'})):
            result_path.write_text(json.dumps(changed))
            self.assertFalse(self.store.operation_settled(identity))
        result_path.write_text(json.dumps(result))
        started_path = self.store.path(identity, '.started')
        started = self.store.read(started_path)
        for changes in ({'authorization': 'model-approved'}, {'account': 'other@example.com'}, {'sha256': '0' * 64}):
            started_path.write_text(json.dumps(dict(started, **changes)))
            self.assertFalse(self.store.operation_settled(identity))
        started_path.write_text(json.dumps(started))
        self.assertTrue(self.store.operation_settled(identity))

    def test_omitted_exception_on_readback_or_failed_deletion_is_uncertain(self):
        original = self.value
        def bad_readback(kind, item):
            result = self.write(kind, item)
            self.value = original
            return result
        self.writer.side_effect = bad_readback
        proposal = self.prepare()
        result = self.editor.execute(proposal['plan_id'])
        self.assertEqual(result['status'], 'failed_or_uncertain')
        self.assertEqual(result['diagnostic']['phase'], 'series_exception_readback')
        self.assertFalse(self.store.operation_settled(proposal['plan_id']))
