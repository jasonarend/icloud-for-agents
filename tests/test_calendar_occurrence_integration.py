import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from icloud_for_agents.approvals import PlanStore, authorization_path
from icloud_for_agents.core import SafetyError
from icloud_for_agents.dav import DAVReader
from icloud_for_agents.dav_changes import DAVChanges, event_content


class OccurrenceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = PlanStore(Path(self.directory.name) / 'plans')
        self.collection = 'https://p01-caldav.icloud.com/synthetic/'
        self.url = self.collection + 'series.ics'
        self.value = event_content({'summary': 'Synthetic', 'start': '2026-11-02T15:00:00Z',
                                    'end': '2026-11-02T16:00:00Z', 'timezone': 'UTC',
                                    'recurrence': {'frequency': 'weekly', 'count': 3}})
        self.reader = DAVReader('synthetic@example.com', 'fake-only', Mock())
        self.reader.known_collection = lambda kind, collection: collection
        self.reader.transport = Mock(side_effect=lambda method, url, body, depth:
                                     (200, {'etag': '"synthetic"'}, self.value.encode()))
        self.writer = Mock(side_effect=self.write)
        self.editor = DAVChanges(self.reader, self.store, self.writer)
        self.proof = {'recurrence_id': '20261109T150000Z', 'membership': 'provider_attested_original_utc_instance'}
        self.proof_patch = patch('icloud_for_agents.calendar_occurrence_proof.prove_occurrence', return_value=self.proof)
        self.proof_mock = self.proof_patch.start()
        self.addCleanup(self.proof_patch.stop)
        self.env = patch.dict(os.environ, {'ICLOUD_APPROVAL_POLICY': 'trusted-client',
                                           'ICLOUD_MAIL_ACCOUNT': 'synthetic@example.com'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def write(self, kind, item):
        self.value = item['content']
        return {'operation': item['operation'], 'url': item['url'], 'status': 'applied'}

    def change(self, operation='update'):
        change = {'operation': operation, 'url': self.url, 'recurrence_scope': 'occurrence',
                  'occurrence_start': '2026-11-09T15:00:00Z'}
        if operation == 'update':
            change['fields'] = {'summary': 'Updated synthetic occurrence'}
        else:
            change['irreversible_confirmed'] = True
        return change

    def test_reviewed_occurrence_update_is_rechecked_and_verified(self):
        proposal = self.editor.prepare('cal', self.collection, [self.change()], temporary=True)
        item = proposal['review']['payload']['changes'][0]
        self.assertEqual(item['occurrence_proof'], self.proof)
        self.writer.assert_not_called()
        result = self.editor.execute(proposal['plan_id'])
        self.assertEqual(result['status'], 'applied')
        self.assertEqual(result['completed'][0]['verification'], 'reviewed_occurrence_change_readback')
        self.assertEqual(self.proof_mock.call_count, 2)
        self.assertIn('RECURRENCE-ID:20261109T150000Z', self.value)
        with self.assertRaises(SafetyError):
            self.editor.execute(proposal['plan_id'])

    def test_occurrence_deletion_requires_local_receipt_and_put_content(self):
        proposal = self.editor.prepare('cal', self.collection, [self.change('delete_occurrence')], temporary=True)
        self.assertEqual(authorization_path(self.store.proposal(proposal['plan_id'])), ('local-receipt', True))
        with self.assertRaises(FileNotFoundError):
            self.editor.execute(proposal['plan_id'])
        self.writer.assert_not_called()
        self.store.write_once(self.store.path(proposal['plan_id'], '.approved'),
                              {'plan_id': proposal['plan_id'], 'sha256': proposal['sha256']})
        result = self.editor.execute(proposal['plan_id'])
        self.assertEqual(result['status'], 'applied')
        self.assertIn('EXDATE:20261109T150000Z', self.value)
        self.assertIn('RRULE:', self.value)
        self.assertEqual(self.writer.call_args.args[1]['operation'], 'delete_occurrence')

    def test_changed_proof_refuses_before_write_and_permits_fresh_review(self):
        proposal = self.editor.prepare('cal', self.collection, [self.change()])
        self.proof_mock.return_value = dict(self.proof, recurrence_id='20261116T150000Z')
        result = self.editor.execute(proposal['plan_id'])
        self.assertEqual(result['status'], 'calendar_occurrence_refused_before_dispatch')
        self.assertTrue(self.store.operation_settled(proposal['plan_id']))
        self.writer.assert_not_called()
        with self.assertRaises(SafetyError):
            self.editor.execute(proposal['plan_id'])
        self.proof_mock.return_value = self.proof
        fresh = self.editor.prepare('cal', self.collection, [self.change()])
        self.assertEqual(self.editor.execute(fresh['plan_id'])['status'], 'applied')

    def test_readback_failure_after_write_is_uncertain_and_not_replayed(self):
        proposal = self.editor.prepare('cal', self.collection, [self.change()])
        def fail_readback(kind, item):
            self.reader.transport = Mock(return_value=(404, {}, b''))
            return {'operation': item['operation'], 'url': item['url'], 'status': 'applied'}
        self.writer.side_effect = fail_readback
        result = self.editor.execute(proposal['plan_id'])
        self.assertEqual(result['status'], 'failed_or_uncertain')
        self.assertEqual(result['diagnostic']['phase'], 'occurrence_readback')
        self.assertFalse(self.store.operation_settled(proposal['plan_id']))
        with self.assertRaises(SafetyError):
            self.editor.execute(proposal['plan_id'])
        self.assertEqual(self.writer.call_count, 1)

    def test_occurrence_operations_cannot_be_batched_or_mis_scoped(self):
        for changes in ([self.change(), self.change()],
                        [dict(self.change(), recurrence_scope='series')],
                        [dict(self.change('delete_occurrence'), irreversible_confirmed=False)],
                        [dict(self.change(), occurrence_start=None)]):
            with self.subTest(changes=changes), self.assertRaises(SafetyError):
                self.editor.prepare('cal', self.collection, changes)
        self.writer.assert_not_called()

    def test_occurrence_deletion_sends_conditional_put_not_series_delete(self):
        proposal = self.editor.prepare('cal', self.collection, [self.change('delete_occurrence')])
        item = proposal['review']['payload']['changes'][0]
        connection = Mock()
        connection.getresponse.return_value.status = 204
        with patch('icloud_for_agents.dav_changes.http.client.HTTPSConnection', return_value=connection):
            self.editor._write('cal', item)
        args, kwargs = connection.request.call_args
        self.assertEqual(args[0], 'PUT')
        self.assertEqual(kwargs['headers']['If-Match'], '"synthetic"')
        self.assertIn(b'EXDATE:20261109T150000Z', kwargs['body'])
