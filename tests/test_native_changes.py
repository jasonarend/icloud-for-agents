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
        self.assertEqual(result['status'],'stopped_before_dispatch');self.assertEqual(len(result['completed']),1)
        self.assertEqual(self.adapter.objects['two']['title'],'before')
    def test_known_creation_readback_reconciliation_preserves_audit_and_prevents_replay(self):
        self.adapter.operations.add('create_note')
        self.adapter.verify=lambda item,outcome:False
        plan=self.engine.prepare([{'operation':'create_note','id':'one','fields':{'title':'after'}}])
        self.assertEqual(self.engine.execute(plan['plan_id'])['status'],'failed_or_uncertain')
        original=self.store.read(self.store.path(plan['plan_id'],'.result'))
        self.adapter.verify=lambda item,outcome:self.adapter.objects[item['id']]==item['fields']
        resolved=self.engine.reconcile_created(plan['plan_id'])
        self.assertEqual(resolved['status'],'applied_after_readback_reconciliation')
        self.assertEqual(self.store.read(self.store.path(plan['plan_id'],'.result')),original)
        with self.assertRaises(SafetyError):self.engine.execute(plan['plan_id'])
        next_plan=self.proposal()
        self.assertEqual(self.engine.execute(next_plan['plan_id'])['status'],'applied')
        self.assertEqual(self.adapter.calls,['one','one'])

    def test_reconciliation_does_not_clear_unknown_or_unverified_effects(self):
        self.adapter.operations.add('create_note')
        self.adapter.verify=lambda item,outcome:False
        plan=self.engine.prepare([{'operation':'create_note','id':'one','fields':{'title':'after'}}])
        self.engine.execute(plan['plan_id'])
        with self.assertRaises(SafetyError):self.engine.reconcile_created(plan['plan_id'])
        self.assertFalse(self.store.path(plan['plan_id'],'.native-reconciled').exists())
        with patch.dict(os.environ,{'ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'source_ids':['other']}})}):
            with self.assertRaises(SafetyError):self.engine.reconcile_created(plan['plan_id'])

    def test_reconciliation_rejects_noncreation_even_with_known_identity(self):
        self.adapter.verify=lambda item,outcome:False
        plan=self.proposal();self.engine.execute(plan['plan_id'])
        self.adapter.verify=lambda item,outcome:True
        with self.assertRaises(SafetyError):self.engine.reconcile_created(plan['plan_id'])

    def test_permanent_delete_requires_receipt_even_in_trusted_mode(self):
        plan=self.engine.prepare([{'operation':'delete','id':'one'}])
        with self.assertRaises(FileNotFoundError):self.engine.execute(plan['plan_id'])
        self.assertEqual(self.adapter.calls,[])

    def refusal(self,item,**override):
        from icloud_for_agents.core import NativeOperationRefused,digest
        request={'protocol_version':1,'service':'notes','action':'apply','source_ids':[item['source_id']],'change':item}
        evidence={'mutation_dispatched':False,'request_sha256':digest(request),'helper_response_sha256':'a'*64}
        evidence.update(override)
        raise NativeOperationRefused('Synthetic helper refusal',evidence=evidence)
    def test_confirmed_pre_dispatch_refusal_is_not_applied_and_cannot_replay(self):
        original=self.adapter.apply;self.adapter.apply=self.refusal
        plan=self.proposal();result=self.engine.execute(plan['plan_id'])
        self.assertEqual(result['status'],'refused_without_dispatch');self.assertEqual(result['outcome'],'not_applied')
        self.assertEqual(result['completed'],[]);self.assertTrue(self.store.operation_settled(plan['plan_id']))
        self.assertEqual(self.adapter.objects['one']['title'],'before')
        with self.assertRaises(SafetyError):self.engine.execute(plan['plan_id'])
        self.adapter.apply=original
        fresh=self.proposal();self.assertEqual(self.engine.execute(fresh['plan_id'])['status'],'applied')
    def test_batch_refusal_reports_prior_verified_items_and_never_attempts_tail(self):
        original=self.adapter.apply
        self.adapter.apply=lambda item:self.refusal(item) if item['id']=='two' else original(item)
        plan=self.engine.prepare([{'operation':'update','id':name,'fields':{'title':'after'}} for name in ('one','two')])
        result=self.engine.execute(plan['plan_id'])
        self.assertEqual(result['status'],'refused_without_dispatch');self.assertEqual(result['outcome'],'partially_applied_then_refused')
        self.assertEqual(result['refused_index'],1);self.assertEqual(len(result['completed']),1)
        self.assertEqual(self.adapter.objects['one']['title'],'after');self.assertEqual(self.adapter.objects['two']['title'],'before')
        self.assertTrue(self.store.operation_settled(plan['plan_id']))
    def test_mismatched_or_after_dispatch_evidence_keeps_uncertainty(self):
        for override in ({'request_sha256':'b'*64},{'mutation_dispatched':True},{'helper_response_sha256':'g'*64}):
            # Isolated audit roots avoid clearing any preceding uncertain result.
            with tempfile.TemporaryDirectory() as directory:
                engine=NativeChanges('notes',self.account,self.adapter,PlanStore(Path(directory)/'runtime'))
                self.adapter.apply=lambda item:self.refusal(item,**override)
                plan=engine.prepare([{'operation':'update','id':'one','fields':{'title':'after'}}])
                result=engine.execute(plan['plan_id'])
                self.assertEqual(result['status'],'failed_or_uncertain');self.assertFalse(engine.store.operation_settled(plan['plan_id']))
    def test_corrupt_refusal_proof_does_not_release_admission(self):
        self.adapter.apply=self.refusal;plan=self.proposal();self.engine.execute(plan['plan_id'])
        path=self.store.path(plan['plan_id'],'.result');result=self.store.read(path)
        result['refusal_evidence']['request_sha256']='f'*64;path.write_text(json.dumps(result))
        self.assertFalse(self.store.operation_settled(plan['plan_id']))
        with self.assertRaises(SafetyError):self.store.status(plan['plan_id'])
