"""Future-occurrence deletion retains history and binds exact human approval."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.calendar_future_changes import truncate_before
from icloud_for_agents.core import SafetyError
from icloud_for_agents.dav import DAVReader
from icloud_for_agents.dav_changes import DAVChanges, event_content


class FutureDeletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = PlanStore(Path(self.temp.name) / 'plans')
        self.collection = 'https://caldav.icloud.com/synthetic/'
        self.url = self.collection + 'series.ics'
        self.value = event_content({'summary': 'Synthetic', 'start': '2026-11-02T15:00:00Z',
            'end': '2026-11-02T16:00:00Z', 'timezone': 'UTC',
            'recurrence': {'frequency': 'weekly', 'count': 10}})
        self.reader = DAVReader('synthetic@example.com', 'fake-only', Mock())
        self.reader.known_collection = lambda kind, collection: collection
        self.reader.transport = Mock(side_effect=lambda method, url, body, depth:
            (200, {'etag': '"synthetic"'}, self.value.encode()))
        self.writer = Mock(side_effect=self.write)
        self.editor = DAVChanges(self.reader, self.store, self.writer)
        self.proof = {'recurrence_id': '20261109T150000Z', 'membership': 'provider_attested_original_utc_instance'}
        self.proof_patch = patch('icloud_for_agents.calendar_occurrence_proof.prove_occurrence', return_value=self.proof)
        self.prove = self.proof_patch.start()
        self.addCleanup(self.proof_patch.stop)
        environment = patch.dict(os.environ, {'ICLOUD_APPROVAL_POLICY': 'trusted-client',
                                                 'ICLOUD_MAIL_ACCOUNT': 'synthetic@example.com'})
        environment.start(); self.addCleanup(environment.stop)

    def write(self, kind, item):
        self.value = item['content']
        return {'operation': item['operation'], 'url': item['url'], 'status': 'applied'}

    def change(self):
        return {'operation': 'delete_future_occurrences', 'url': self.url, 'recurrence_scope': 'future',
                'occurrence_start': '2026-11-09T15:00:00Z', 'irreversible_confirmed': True}

    def test_future_deletion_requires_receipt_and_conditional_put_preserving_prior_occurrences(self):
        proposal = self.editor.prepare('cal', self.collection, [self.change()], temporary=True)
        self.assertTrue(proposal['local_receipt_required'])
        item = proposal['review']['payload']['changes'][0]
        self.assertIn('UNTIL=20261109T145959Z', item['content'])
        self.assertNotIn('COUNT=10', item['content'])
        self.assertIn('DTSTART:20261102T150000Z', item['content'])
        with self.assertRaises(FileNotFoundError):
            self.editor.execute(proposal['plan_id'])
        self.writer.assert_not_called()
        self.store.write_once(self.store.path(proposal['plan_id'], '.approved'),
                              {'plan_id': proposal['plan_id'], 'sha256': proposal['sha256']})
        result = self.editor.execute(proposal['plan_id'])
        self.assertEqual(result['status'], 'applied')
        self.assertEqual(result['completed'][0]['verification'], 'reviewed_recurring_event_readback')
        self.assertEqual(self.prove.call_count, 2)
        with self.assertRaises(SafetyError):
            self.editor.execute(proposal['plan_id'])
        connection = Mock(); connection.getresponse.return_value.status = 204
        with patch('icloud_for_agents.dav_changes.http.client.HTTPSConnection', return_value=connection):
            self.editor._write('cal', item)
        self.assertEqual(connection.request.call_args.args[0], 'PUT')
        self.assertEqual(connection.request.call_args.kwargs['headers']['If-Match'], '"synthetic"')

    def test_explicit_target_and_unchanged_provider_proof_are_required(self):
        with self.assertRaises(SafetyError):
            self.editor.prepare('cal', self.collection, [dict(self.change(), irreversible_confirmed=False)])
        proposal = self.editor.prepare('cal', self.collection, [self.change()])
        self.store.write_once(self.store.path(proposal['plan_id'], '.approved'),
                              {'plan_id': proposal['plan_id'], 'sha256': proposal['sha256']})
        self.prove.return_value = {'recurrence_id': 'different'}
        result = self.editor.execute(proposal['plan_id'])
        self.assertEqual(result['status'], 'calendar_occurrence_refused_before_dispatch')
        self.assertTrue(self.store.operation_settled(proposal['plan_id']))
        self.writer.assert_not_called()

    def test_first_occurrence_and_existing_exceptions_are_refused(self):
        with self.assertRaises(SafetyError):
            truncate_before(self.value, '2026-11-02T15:00:00Z')
        with self.assertRaises(SafetyError):
            truncate_before(self.value.replace('END:VEVENT', 'EXDATE:20261109T150000Z\r\nEND:VEVENT'),
                            '2026-11-16T15:00:00Z')


if __name__ == '__main__':
    unittest.main()
