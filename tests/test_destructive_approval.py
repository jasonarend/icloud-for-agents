"""Generic irreversible-operation approval regressions; no private cleanup hooks."""
import os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import digest

class DestructiveApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=PlanStore(Path(self.tmp.name)/'runtime',clock=lambda:1000)
        self.account='synthetic@example.com'
        env=patch.dict(os.environ,{'ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_MAIL_ACCOUNT':self.account},clear=True);env.start();self.addCleanup(env.stop)
    def proposal(self,kind='contact_change'):
        return self.store.prepare(kind,{'account':self.account,'changes':[{'operation':'delete','url':'https://contacts.icloud.com/synthetic/test.vcf'}]})
    def test_contact_delete_needs_local_receipt(self):
        p=self.proposal()
        with self.assertRaises(FileNotFoundError):self.store.consume(p['plan_id'],self.account)
    def test_calendar_delete_needs_local_receipt(self):
        p=self.proposal('calendar_change')
        with self.assertRaises(FileNotFoundError):self.store.consume(p['plan_id'],self.account)
    def test_model_boolean_is_not_receipt(self):
        p=self.proposal();self.store.write_once(self.store.path(p['plan_id'],'.client-confirmed-test-cleanup'),{'approved':True})
        with self.assertRaises(FileNotFoundError):self.store.consume(p['plan_id'],self.account)
    def test_old_maintenance_record_does_not_authorize_delete(self):
        p=self.proposal();self.store.write_once(self.store.path(p['plan_id'],'.client-confirmed-test-cleanup'),{'plan_id':p['plan_id'],'plan_sha256':p['sha256'],'account':self.account})
        with self.assertRaises(FileNotFoundError):self.store.consume(p['plan_id'],self.account)
    def test_no_private_starred_override_method(self):
        from icloud_for_agents.service import MailService
        self.assertFalse(hasattr(MailService,'execute_exact_starred_trash'))
    def test_genuine_exact_receipt_is_still_accepted(self):
        p=self.proposal();self.store.write_once(self.store.path(p['plan_id'],'.approved'),{'plan_id':p['plan_id'],'sha256':p['sha256']})
        self.store.consume(p['plan_id'],self.account)
    def test_wrong_receipt_hash_is_refused(self):
        p=self.proposal();self.store.write_once(self.store.path(p['plan_id'],'.approved'),{'plan_id':p['plan_id'],'sha256':'0'*64})
        with self.assertRaises(Exception):self.store.consume(p['plan_id'],self.account)
