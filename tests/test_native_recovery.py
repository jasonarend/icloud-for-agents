"""Reviewed Notes association preserves uncertainty, identity provenance and approval policy."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.native_notes import NotesCreateAdapter
from icloud_for_agents.native_recovery import NotesCreationRecovery
from icloud_for_agents.notes_content import project_html


class Reader:
    def __init__(self):
        self.folder={'id':'folder','source_id':'selected','title':'Synthetic','parent_id':'selected','shared':False}
        self.note=None;self.duplicate=False;self.attachments=[];self.calls=[];self.bad_repair=False
    def read(self,action,**fields):
        if action=='folder_get':return {'folder':copy.deepcopy(self.folder)}
        if action=='get':return {'item':copy.deepcopy(self.note)}
        if action=='query':
            notes=[{'id':self.note['id'],'title':self.note['title']}]
            if self.duplicate:notes.append({'id':'other','title':self.note['title']})
            return {'items':notes,'done':True}
        if action=='attachments':return {'attachments':copy.deepcopy(self.attachments),'done':True,'locked':False}
        raise AssertionError(action)
    def _dispatch(self,request,config):
        change=request['change'];assert change['operation']=='repair_note_body'
        self.calls.append('repair');self.note['html']='<div>Spec</div>'+('' if self.bad_repair else change['fields']['html'])
        self.note['text']=project_html(self.note['html'])['text'];self.note['modified']='new'
        return {'result':{'id':'candidate','source_id':'selected','folder_id':'folder'}}


class Adapter(NotesCreateAdapter):
    backend_id='synthetic-notes-v1'
    def __init__(self):self.reader=Reader();self.full=False
    def apply(self,item):
        self.reader.calls.append('create')
        html='<div>Spec</div>'+(item['fields']['html'] if self.full else '')
        self.reader.note={'id':'candidate','source_id':'selected','folder_id':'folder','title':'Spec','html':html,
            'text':project_html(html)['text'],'locked':False,'shared':False,'truncated':False,'modified':None}
        raise SafetyError('No returned identity after dispatch')


class NativeRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=PlanStore(Path(self.tmp.name)/'runtime');self.adapter=Adapter()
        ctx=patch.dict(os.environ,{'ICLOUD_ENABLE_NOTES':'1','ICLOUD_MAIL_ACCESS':'write-enabled',
            'ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_APPROVAL_POLICY':'trusted-client',
            'ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'access_mode':'write-enabled','source_ids':['selected']}})},clear=True)
        ctx.start();self.addCleanup(ctx.stop)
        self.engine=NativeChanges('notes','synthetic@example.com',self.adapter,self.store)
        self.recovery=NotesCreationRecovery('synthetic@example.com',self.store,self.adapter)
    def fail(self,full=False):
        self.adapter.full=full
        original=self.engine.prepare([{'operation':'create_note','source_id':'selected','folder_id':'folder',
            'fields':{'title':'Spec','body':'Original requested body'}}])['plan_id']
        self.failure=self.engine.execute(original);self.assertEqual(self.failure['phase'],'mutation')
        return original
    def test_accept_is_explicit_association_not_claimed_creation_identity(self):
        original=self.fail(full=True);proposal=self.recovery.prepare(original,'candidate','accept_observed_creation')
        self.assertIn('not a provider-returned',proposal['review']['payload']['effects']['identity_provenance'])
        result=self.recovery.execute(proposal['plan_id'])
        self.assertEqual(result['status'],'completed');self.assertFalse(result['native_data_changed'])
        self.assertEqual(self.store.status(original)['status'],'observed_state_accepted_after_review')
        self.assertEqual(self.store.read(self.store.path(original,'.result')),self.failure)
        self.assertEqual(self.adapter.reader.calls,['create'])
        self.engine.assert_settled()
        with self.assertRaises(SafetyError):self.engine.execute(original)
    def test_partial_candidate_cannot_be_accepted_as_success(self):
        original=self.fail()
        with self.assertRaises(SafetyError):self.recovery.prepare(original,'candidate','accept_observed_creation')
        self.assertFalse(self.store.path(original,'.native-recovery').exists())
    def test_repair_changes_only_exact_title_only_candidate_without_creating_another(self):
        original=self.fail();proposal=self.recovery.prepare(original,'candidate','repair_title_only_candidate')
        result=self.recovery.execute(proposal['plan_id'])
        self.assertTrue(result['native_data_changed']);self.assertEqual(self.adapter.reader.calls,['create','repair'])
        self.assertEqual(project_html(self.adapter.reader.note['html'])['text'],'Spec Original requested body')
        self.assertEqual(self.store.read(self.store.path(original,'.result')),self.failure)
        self.assertTrue(self.store._reconciliation_completed(original))
    def test_strict_policy_requires_independent_receipt(self):
        original=self.fail();os.environ['ICLOUD_APPROVAL_POLICY']='local-receipt'
        proposal=self.recovery.prepare(original,'candidate','repair_title_only_candidate')
        self.assertTrue(proposal['local_receipt_required'])
        with self.assertRaises(FileNotFoundError):self.recovery.execute(proposal['plan_id'])
        self.assertEqual(self.adapter.reader.calls,['create'])
    def test_existing_native_content_attachments_and_ambiguity_are_not_repairable(self):
        original=self.fail();reader=self.adapter.reader
        for key,value in [('html','<div>Spec</div><div>Existing content</div>'),('locked',True),
                          ('shared',True),('folder_id','other'),('source_id','other'),('truncated',True)]:
            previous=reader.note[key];reader.note[key]=value
            with self.subTest(key=key),self.assertRaises(SafetyError):self.recovery.prepare(original,'candidate','repair_title_only_candidate')
            reader.note[key]=previous
        reader.attachments=[{'id':'attachment'}]
        with self.assertRaises(SafetyError):self.recovery.prepare(original,'candidate','repair_title_only_candidate')
        reader.attachments=[];reader.duplicate=True
        with self.assertRaises(SafetyError):self.recovery.prepare(original,'candidate','repair_title_only_candidate')
    def test_changed_candidate_backend_scope_or_policy_cannot_consume_review(self):
        original=self.fail();proposal=self.recovery.prepare(original,'candidate','repair_title_only_candidate')
        self.adapter.reader.note['modified']='changed'
        with self.assertRaises(SafetyError):self.recovery.execute(proposal['plan_id'])
        self.adapter.reader.note['modified']=None;self.adapter.backend_id='new-helper'
        with self.assertRaises(SafetyError):self.recovery.execute(proposal['plan_id'])
        self.assertFalse(self.store.path(proposal['plan_id'],'.started').exists())
    def test_no_second_reservation_and_no_replay(self):
        original=self.fail(full=True);proposal=self.recovery.prepare(original,'candidate','accept_observed_creation')
        with self.assertRaises(FileExistsError):self.recovery.prepare(original,'candidate','accept_observed_creation')
        self.recovery.execute(proposal['plan_id'])
        with self.assertRaises(SafetyError):self.recovery.execute(proposal['plan_id'])
    def test_other_unfinished_work_remains_blocking(self):
        original=self.fail(full=True);proposal=self.recovery.prepare(original,'candidate','accept_observed_creation')
        other=self.store.prepare('reminders_change',{'account':'synthetic@example.com','changes':[]})
        self.store.write_once(self.store.path(other['plan_id'],'.started'),{})
        with self.assertRaises(SafetyError):self.recovery.execute(proposal['plan_id'])
        self.assertFalse(self.store._reconciliation_completed(original))
    def test_failed_repair_verification_never_clears_original(self):
        original=self.fail();proposal=self.recovery.prepare(original,'candidate','repair_title_only_candidate');self.adapter.reader.bad_repair=True
        result=self.recovery.execute(proposal['plan_id'])
        self.assertEqual(result['status'],'failed_or_uncertain')
        self.assertFalse(self.store._reconciliation_completed(original))
        with self.assertRaises(SafetyError):self.engine.assert_settled()

    def test_interrupted_final_proof_can_finish_readonly_without_replaying(self):
        original=self.fail(full=True);proposal=self.recovery.prepare(original,'candidate','accept_observed_creation')
        self.recovery.execute(proposal['plan_id'])
        # Model a crash after successful result persistence, before proof creation.
        self.store.path(original,'.native-recovery-result').unlink()
        self.assertFalse(self.store._reconciliation_completed(original))
        self.assertEqual(self.recovery.finalize(proposal['plan_id'])['status'],'observed_state_accepted_after_review')
        self.assertEqual(self.adapter.reader.calls,['create'])
        self.assertEqual(self.recovery.finalize(proposal['plan_id'])['status'],'observed_state_accepted_after_review')
    def test_interrupted_proof_does_not_accept_subsequent_external_edits(self):
        original=self.fail(full=True);proposal=self.recovery.prepare(original,'candidate','accept_observed_creation')
        self.recovery.execute(proposal['plan_id']);self.store.path(original,'.native-recovery-result').unlink()
        self.adapter.reader.note['modified']='external'
        with self.assertRaises(SafetyError):self.recovery.finalize(proposal['plan_id'])
        self.assertFalse(self.store._reconciliation_completed(original))
