"""Wrong-service execution must not consume or strand another service's plan."""
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError
from icloud_for_agents.dav_changes import DAVChanges
from icloud_for_agents.service import MailService


class PlanDispatchTests(unittest.TestCase):
    def test_mail_refuses_dav_before_receipt_or_started_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            store=PlanStore(Path(directory)/'runtime')
            for kind in ('calendar_change','contact_change'):
                plan=store.prepare(kind,{'account':'synthetic@example.com','changes':[]})
                owner=MailService('synthetic@example.com','synthetic',store=store)
                with patch.object(owner,'_send',side_effect=AssertionError('No send')):
                    with self.assertRaisesRegex(SafetyError,'Not a Mail proposal'):owner.execute(plan['plan_id'])
                self.assertFalse(store.path(plan['plan_id'],'.started').exists())
                self.assertEqual(store.proposal(plan['plan_id'])['kind'],kind)

    def test_dav_refuses_mail_before_receipt_or_started_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            store=PlanStore(Path(directory)/'runtime')
            for kind in ('send','trash'):
                plan=store.prepare(kind,{'account':'synthetic@example.com'})
                editor=DAVChanges(SimpleNamespace(account='synthetic@example.com'),store)
                with self.assertRaisesRegex(SafetyError,'Not a calendar/contact proposal'):editor.execute(plan['plan_id'])
                self.assertFalse(store.path(plan['plan_id'],'.started').exists())
                self.assertEqual(store.proposal(plan['plan_id'])['kind'],kind)
