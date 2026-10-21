import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError
from icloud_for_agents.local_config import child_environment

class ApprovalPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=PlanStore(Path(self.tmp.name)/'runtime',clock=lambda:1000)
        self.account='synthetic@example.com'
    def plan(self,kind='contact_change',op='create',account=None):
        return self.store.prepare(kind,{'account':account or self.account,'changes':[{'operation':op}]})['plan_id']
    def env(self,**extra):
        return patch.dict(os.environ,dict(ICLOUD_APPROVAL_POLICY='trusted-client',ICLOUD_MAIL_ACCOUNT=self.account,**extra),clear=True)
    def test_default_requires_receipt_and_does_not_start(self):
        p=self.plan()
        with patch.dict(os.environ,{},clear=True),self.assertRaises(FileNotFoundError):self.store.consume(p,self.account)
        self.assertFalse(self.store.path(p,'.started').exists())
    def test_optin_exact_account_no_fabricated_receipt_and_replay(self):
        p=self.plan()
        with self.env():self.store.consume(p,self.account)
        self.assertFalse(self.store.path(p,'.approved').exists())
        self.assertIn('not an independent',self.store.read(self.store.path(p,'.started'))['authorization'])
        with self.env(),self.assertRaises(SafetyError):self.store.consume(p,self.account)
    def test_wrong_account_and_invalid_mode(self):
        for env in [{'ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_MAIL_ACCOUNT':'other@example.com'}, {'ICLOUD_APPROVAL_POLICY':'yes'}]:
            with patch.dict(os.environ,env,clear=True),self.assertRaises(SafetyError):self.store.consume(self.plan(),self.account)
        with self.env(),self.assertRaises(SafetyError):self.store.consume(self.plan(account='other@example.com'),self.account)
    def test_irreversible_dav_delete_still_requires_receipt(self):
        p=self.plan(op='delete')
        with self.env(),self.assertRaises(FileNotFoundError):self.store.consume(p,self.account)
    def test_unfinished_uncertain_and_completed_admission(self):
        first=self.plan();second=self.plan()
        with self.env():
            self.store.consume(first,self.account)
            with self.assertRaises(SafetyError):self.store.consume(second,self.account)
            self.store.finish(first,{'status':'failed_or_uncertain'})
            with self.assertRaises(SafetyError):self.store.consume(second,self.account)
    def test_completed_allows_next_exact_plan(self):
        first=self.plan();second=self.plan()
        with self.env():
            self.store.consume(first,self.account);self.store.finish(first,{'status':'applied'})
            self.store.consume(second,self.account)
    def test_child_does_not_inherit_approval_optin(self):
        settings=dict(account=self.account,apple_keychain_service='synthetic',access_mode='read-only',dav_reads=False)
        self.assertEqual(child_environment(settings,{'ICLOUD_APPROVAL_POLICY':'trusted-client'})['ICLOUD_APPROVAL_POLICY'],'local-receipt')
        settings['approval_policy']='trusted-client'
        self.assertEqual(child_environment(settings,{})['ICLOUD_APPROVAL_POLICY'],'trusted-client')
    def test_partial_or_unknown_result_blocks_followup(self):
        p=self.plan();other=self.plan()
        with self.env():
            self.store.consume(p,self.account);self.store.finish(p,{'status':'partially_sent'})
            with self.assertRaises(SafetyError):self.store.consume(other,self.account)
    def test_concurrent_admission_lock_fails_without_starting(self):
        import fcntl
        self.store.ready();path=self.store.root/'approval-policy.lock'
        fd=os.open(path,os.O_CREAT|os.O_RDWR,0o600)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);p=self.plan()
            with self.env(),self.assertRaises(BlockingIOError):self.store.consume(p,self.account)
            self.assertFalse(self.store.path(p,'.started').exists())
        finally:os.close(fd)

class ReconciliationTests(ApprovalPolicyTests):
    def failed_calendar(self):
        from icloud_for_agents.core import digest
        payload={'account':self.account,'kind':'cal','changes':[{'operation':'create','url':'https://p01-caldav.icloud.com/cal/test.ics','content':'BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:synthetic-uid\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n'}]}
        p=self.store.prepare('calendar_change',payload)
        with self.env():self.store.consume(p['plan_id'],self.account)
        self.store.finish(p['plan_id'],{'status':'failed_or_uncertain','completed':[]})
        e=dict(checked_at=1000,url=payload['changes'][0]['url'],http_status=404,uid='synthetic-uid',bounded_uid_matches=0,scope='Synthetic bounded day query',material_fix='rfc6638-schedule-reply-delete-only',request_sha256=digest(payload))
        return p,e
    def test_one_retry_preserves_original_and_cannot_repeat(self):
        p,e=self.failed_calendar();original=self.store.read(self.store.path(p['plan_id'],'.result'))
        with self.env():
            retry=self.store.prepare_no_effect_retry(p['plan_id'],e)
            self.store.consume(retry['plan_id'],self.account)
            with self.assertRaises((FileExistsError,SafetyError)):self.store.prepare_no_effect_retry(p['plan_id'],e)
            with self.assertRaises(SafetyError):self.store.consume(retry['plan_id'],self.account)
        self.assertEqual(self.store.read(self.store.path(p['plan_id'],'.result')),original)
        self.assertEqual(retry['review']['payload'],p['review']['payload'])
    def test_existing_stale_wrong_identity_or_request_cannot_reconcile(self):
        p,e=self.failed_calendar()
        for change in ({'http_status':200},{'checked_at':900},{'url':'https://p01-caldav.icloud.com/other.ics'},{'uid':'other'},{'bounded_uid_matches':1},{'request_sha256':'wrong'}):
            with self.env(),self.assertRaises(SafetyError):self.store.prepare_no_effect_retry(p['plan_id'],dict(e,**change))
    def test_retry_proof_expiry_blocks_admission(self):
        p,e=self.failed_calendar()
        with self.env():
            retry=self.store.prepare_no_effect_retry(p['plan_id'],e);self.store.clock=lambda:1061
            with self.assertRaises(SafetyError):self.store.consume(retry['plan_id'],self.account)
    def test_only_verified_retry_completion_unblocks_followup(self):
        p,e=self.failed_calendar()
        with self.env():
            retry=self.store.prepare_no_effect_retry(p['plan_id'],e);self.store.consume(retry['plan_id'],self.account)
            self.store.finish(retry['plan_id'],{'status':'applied','completed':[{'url':e['url'],'operation':'create','status':'applied'}]})
            other=self.plan()
            with self.assertRaises(SafetyError):self.store.consume(other,self.account)
            proof=dict(url=e['url'],uid=e['uid'],resource_sha256='a'*64,all_fields_verified=True,checked_at=1000)
            with self.assertRaises(SafetyError):self.store.complete_reconciliation(retry['plan_id'],dict(proof,uid='wrong'))
            self.store.complete_reconciliation(retry['plan_id'],proof)
            self.store.consume(other,self.account)


class ApprovalMetadataTests(unittest.TestCase):
    def test_metadata_does_not_invent_terminal_requirement_in_trusted_mode(self):
        with tempfile.TemporaryDirectory() as t,patch.dict(os.environ,{'ICLOUD_APPROVAL_POLICY':'trusted-client'},clear=True):
            store=PlanStore(Path(t)/'runtime');p=store.prepare('send',{'account':'synthetic@example.com'})
            self.assertFalse(p['local_receipt_required']);self.assertIsNone(p['operator_command'])
            self.assertEqual(store.status(p['plan_id'])['status'],'awaiting_client_authorized_execution')
    def test_standing_metadata_still_requires_execution_scope_validation(self):
        with tempfile.TemporaryDirectory() as t,patch.dict(os.environ,{'ICLOUD_CLEANUP_GRANT_ID':'c'*32},clear=True):
            store=PlanStore(Path(t)/'runtime');p=store.prepare('trash',{'account':'synthetic@example.com'})
            self.assertEqual(store.status(p['plan_id'])['status'],'awaiting_standing_scope_validation')
    def test_irreversible_delete_metadata_stays_strict(self):
        with tempfile.TemporaryDirectory() as t,patch.dict(os.environ,{'ICLOUD_APPROVAL_POLICY':'trusted-client'},clear=True):
            p=PlanStore(Path(t)/'runtime').prepare('contact_change',{'account':'synthetic@example.com','changes':[{'operation':'delete'}]})
            self.assertTrue(p['local_receipt_required'])
