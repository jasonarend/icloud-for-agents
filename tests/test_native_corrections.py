"""Explicit known-ID update correction is scoped, one-shot and independently verified."""
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
from icloud_for_agents.native_reminders import RemindersCreateAdapter


class Reader:
    def __init__(self):
        self.item={'id':'task','source_id':'selected','list_id':'list','title':'Task','notes':'',
                   'priority':0,'due':None,'start':None,'completed':False,'url':None,
                   'has_recurrence':False,'has_alarms':False}
    def read(self,action,**fields):
        if action=='get':return {'item':copy.deepcopy(self.item)}
        if action=='lists':return {'lists':[{'id':'list','title':'Synthetic','source_id':'selected','writable':True}],'done':True}
        raise AssertionError(action)


class Adapter(RemindersCreateAdapter):
    backend_id='synthetic-v1'
    def __init__(self):self.reader=Reader();self.calls=0;self.fail=True
    def apply(self,item):
        self.calls+=1;self.reader.item.update(copy.deepcopy(item['fields']))
        if self.fail:self.reader.item['start']={'year':2026,'month':10,'day':28}
        return {'id':'task','source_id':'selected','list_id':'list'}


class NativeCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=PlanStore(Path(self.temp.name)/'runtime');self.adapter=Adapter()
        env={'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCESS':'write-enabled',
             'ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_APPROVAL_POLICY':'trusted-client',
             'ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'access_mode':'write-enabled','source_ids':['selected']}})}
        ctx=patch.dict(os.environ,env,clear=True);ctx.start();self.addCleanup(ctx.stop)
        self.engine=NativeChanges('reminders','synthetic@example.com',self.adapter,self.store)
        self.original=self.engine.prepare([{'operation':'update_reminder','source_id':'selected','id':'task',
            'fields':{'due':{'year':2026,'month':10,'day':28}}}])['plan_id']
        self.failure=self.engine.execute(self.original)
        self.assertEqual(self.failure['status'],'failed_or_uncertain')
        self.adapter.fail=False
    def correction(self):return self.engine.prepare_update_correction(self.original)
    def test_corrected_exact_state_preserves_both_original_audits(self):
        correction=self.correction();identifier=correction['plan_id']
        self.assertEqual(self.engine.execute(identifier)['status'],'applied')
        self.assertFalse(self.store._reconciliation_completed(self.original))
        self.assertEqual(self.engine.reconcile_correction(identifier)['status'],'verified_after_explicit_correction')
        self.assertEqual(self.store.read(self.store.path(self.original,'.result')),self.failure)
        self.assertTrue(self.store._reconciliation_completed(self.original))
        with self.assertRaises(SafetyError):self.engine.execute(identifier)
        self.assertEqual(self.adapter.calls,2)
    def test_strict_correction_still_requires_independent_receipt(self):
        os.environ['ICLOUD_APPROVAL_POLICY']='local-receipt';correction=self.correction()
        self.assertTrue(correction['local_receipt_required'])
        with self.assertRaises(FileNotFoundError):self.engine.execute(correction['plan_id'])
        self.assertEqual(self.adapter.calls,1)
    def test_second_correction_cannot_be_reserved(self):
        self.correction()
        with self.assertRaises(FileExistsError):self.correction()
    def test_changed_target_blocks_correction_before_consumption(self):
        correction=self.correction();self.adapter.reader.item['notes']='External edit'
        with self.assertRaises(SafetyError):self.engine.execute(correction['plan_id'])
        self.assertFalse(self.store.path(correction['plan_id'],'.started').exists())
    def test_unrelated_effects_cannot_be_cleared_by_correction(self):
        for key,value in [('list_id','other'),('completed',True),('has_recurrence',True)]:
            prior=self.adapter.reader.item[key];self.adapter.reader.item[key]=value
            with self.subTest(key=key),self.assertRaises(SafetyError):self.correction()
            self.adapter.reader.item[key]=prior
    def test_scope_and_other_uncertainty_still_block(self):
        correction=self.correction()
        other=self.store.prepare('notes_change',{'account':'synthetic@example.com','changes':[]})
        self.store.write_once(self.store.path(other['plan_id'],'.started'),{})
        with self.assertRaises(SafetyError):self.engine.execute(correction['plan_id'])
        self.assertEqual(self.adapter.calls,1)
    def test_altered_reserved_plan_cannot_bypass_uncertainty(self):
        correction=self.correction();identifier=correction['plan_id'];path=self.store.path(identifier,'.json')
        plan=self.store.read(path);plan['payload']['changes'][0]['id']='other';path.write_text(json.dumps(plan))
        with self.assertRaises(SafetyError):self.engine.execute(identifier)
        self.assertEqual(self.adapter.calls,1)
    def test_readback_reconciliation_cannot_precede_successful_correction(self):
        correction=self.correction()
        with self.assertRaises(FileNotFoundError):self.engine.reconcile_correction(correction['plan_id'])
        self.assertFalse(self.store._reconciliation_completed(self.original))

    def test_reviewed_before_state_rollback_is_distinct_from_original_success(self):
        os.environ['ICLOUD_MAIL_ACCESS']='read-only'
        os.environ['ICLOUD_NATIVE_CONFIG']=json.dumps({'reminders':{'access_mode':'read-only','source_ids':['selected']}})
        before=dict(os.environ);proposal=self.engine.prepare_update_correction(self.original,'before')
        self.assertEqual(proposal['review']['payload']['correction_target'],'before')
        self.assertEqual(self.engine.execute(proposal['plan_id'])['status'],'applied')
        status=self.engine.reconcile_correction(proposal['plan_id'])
        self.assertEqual(status['status'],'restored_before_after_explicit_correction');self.assertFalse(status['original_request_succeeded'])
        self.assertIsNone(self.adapter.reader.item['due']);self.assertIsNone(self.adapter.reader.item['start'])
        self.assertEqual(self.store.read(self.store.path(self.original,'.result')),self.failure)
        self.assertEqual(dict(os.environ),before)
        with self.assertRaises(SafetyError):self.engine.execute(self.original)
    def test_rollback_cannot_overwrite_unrequested_external_content(self):
        self.adapter.reader.item['notes']='External content'
        with self.assertRaises(SafetyError):self.engine.prepare_update_correction(self.original,'before')
        self.assertFalse(self.store.path(self.original,'.native-correction').exists())
    def test_unknown_correction_target_is_refused(self):
        with self.assertRaises(SafetyError):self.engine.prepare_update_correction(self.original,'anything')
        self.assertFalse(self.store.path(self.original,'.native-correction').exists())
    def test_readonly_requested_state_correction_remains_exact_and_replay_protected(self):
        os.environ['ICLOUD_MAIL_ACCESS']='read-only'
        proposal=self.correction();self.assertEqual(self.engine.execute(proposal['plan_id'])['status'],'applied')
        self.assertEqual(self.engine.reconcile_correction(proposal['plan_id'])['status'],'verified_after_explicit_correction')
        self.assertEqual(os.environ['ICLOUD_MAIL_ACCESS'],'read-only')
