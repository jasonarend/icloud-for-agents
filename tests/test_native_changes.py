"""Exact native plans, strict/trusted authorization and failure reconciliation."""
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


class Adapter:
    backend_id='synthetic-native-v1'
    operations={'update','delete'}
    def __init__(self):self.objects={'one':{'title':'before'},'two':{'title':'before'}};self.calls=[]
    def prepare(self,change):
        return dict(change,source_id=change.get('source_id','selected'),
                    before=copy.deepcopy(self.objects.get(change['id'])),
                    fidelity_verified=change.get('fidelity_verified',True),effects={})
    def snapshot(self,item):return copy.deepcopy(self.objects.get(item['id']))
    def apply(self,item):
        self.calls.append(item['id']);self.objects[item['id']]=dict(item.get('fields',{}))
        return {'id':item['id'],'saved_locally':True}
    def verify(self,item,outcome):return self.objects[item['id']]==item.get('fields',{})


class NativeChangesTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=PlanStore(Path(self.tmp.name)/'runtime')
        self.adapter=Adapter();self.account='synthetic@example.com'
        env={'ICLOUD_ENABLE_NOTES':'1','ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCESS':'write-enabled',
             'ICLOUD_MAIL_ACCOUNT':self.account,'ICLOUD_APPROVAL_POLICY':'trusted-client',
             'ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'access_mode':'write-enabled','source_ids':['selected']},
                                               'reminders':{'access_mode':'write-enabled','source_ids':['selected']}})}
        self.context=patch.dict(os.environ,env,clear=True);self.context.start();self.addCleanup(self.context.stop)
        self.engine=NativeChanges('notes',self.account,self.adapter,self.store)
    def proposal(self,**fields):
        return self.engine.prepare([dict(operation='update',id='one',fields={'title':'after'},**fields)])
    def test_trusted_exact_plan_readback_and_replay(self):
        plan=self.proposal();result=self.engine.execute(plan['plan_id'])
        self.assertEqual(result['status'],'applied');self.assertEqual(result['sync_status'],'unconfirmed')
        self.assertFalse(self.store.path(plan['plan_id'],'.approved').exists())
        with self.assertRaises(SafetyError):self.engine.execute(plan['plan_id'])
        self.assertEqual(self.adapter.calls,['one'])
    def test_strict_requires_independent_receipt(self):
        os.environ['ICLOUD_APPROVAL_POLICY']='local-receipt';plan=self.proposal()
        with self.assertRaises(FileNotFoundError):self.engine.execute(plan['plan_id'])
        self.assertEqual(self.adapter.calls,[])
        self.store.write_once(self.store.path(plan['plan_id'],'.approved'),{'plan_id':plan['plan_id'],'sha256':plan['sha256']})
        self.assertEqual(self.engine.execute(plan['plan_id'])['status'],'applied')
    def test_scope_revocation_backend_and_policy_changes_before_consumption(self):
        for setting,value in [('ICLOUD_ENABLE_NOTES','0'),('ICLOUD_MAIL_ACCESS','read-only'),
                              ('ICLOUD_APPROVAL_POLICY','local-receipt'),
                              ('ICLOUD_NATIVE_CONFIG',json.dumps({'notes':{'access_mode':'write-enabled','source_ids':['other']}}))]:
            plan=self.proposal()
            with patch.dict(os.environ,{setting:value}),self.assertRaises(SafetyError):self.engine.execute(plan['plan_id'])
            self.assertFalse(self.store.path(plan['plan_id'],'.started').exists())
        plan=self.proposal();self.adapter.backend_id='changed'
        with self.assertRaises(SafetyError):self.engine.execute(plan['plan_id'])
    def test_changed_object_refused_before_consumption(self):
        plan=self.proposal();self.adapter.objects['one']['title']='external edit'
        with self.assertRaises(SafetyError):self.engine.execute(plan['plan_id'])
        self.assertEqual(self.adapter.calls,[])
        self.assertFalse(self.store.path(plan['plan_id'],'.started').exists())
    def test_scope_and_fidelity_refused_during_prepare(self):
        for extra in ({'source_id':'other'},{'fidelity_verified':False}):
            with self.assertRaises(SafetyError):self.proposal(**extra)
        self.assertFalse(list(self.store.root.glob('*.json')))
    def test_wrong_service_does_not_consume(self):
        plan=self.proposal();other=NativeChanges('reminders',self.account,self.adapter,self.store)
        with self.assertRaises(SafetyError):other.execute(plan['plan_id'])
        self.assertFalse(self.store.path(plan['plan_id'],'.started').exists())
    def test_failed_readback_blocks_followup_even_under_strict_policy(self):
        self.adapter.verify=lambda item,outcome:False
        first=self.proposal();result=self.engine.execute(first['plan_id'])
        self.assertEqual(result['status'],'failed_or_uncertain')
        self.assertEqual(result['attempted'][0]['result']['id'],'one')
        self.assertTrue(self.store.path(first['plan_id'],'.attempt-0').exists())
        os.environ['ICLOUD_APPROVAL_POLICY']='local-receipt';second=self.proposal()
        with self.assertRaises(SafetyError):self.engine.execute(second['plan_id'])
        self.assertEqual(self.adapter.calls,['one'])
    def test_partial_batch_stops_when_access_revoked(self):
        original=self.adapter.apply
        def apply(item):
            result=original(item);os.environ['ICLOUD_ENABLE_NOTES']='0';return result
        self.adapter.apply=apply
        plan=self.engine.prepare([{'operation':'update','id':id,'fields':{'title':'after'}} for id in ('one','two')])
        result=self.engine.execute(plan['plan_id'])
        self.assertEqual(result['status'],'failed_or_uncertain');self.assertEqual(len(result['completed']),1)
        self.assertEqual(self.adapter.objects['two']['title'],'before')
    def test_permanent_delete_requires_receipt_even_in_trusted_mode(self):
        plan=self.engine.prepare([{'operation':'delete','id':'one'}])
        with self.assertRaises(FileNotFoundError):self.engine.execute(plan['plan_id'])
        self.assertEqual(self.adapter.calls,[])
