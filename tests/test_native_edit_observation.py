"""Uncertain edit inspection never mutates or settles the original plan."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError,digest
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.native_edit_observation import inspect_uncertain_note_edit

class Adapter:
    backend_id='synthetic-observation-v1';operations={'append_note'}
    def __init__(self):
        self.note={'id':'note','title':'Synthetic','html':'<div>Synthetic</div>','text':'Synthetic','created':'2026-10-26','modified':'2026-10-26T00:00:00Z','locked':False,'shared':False,'truncated':False,'source_id':'selected','folder_id':'old'}
        self.attachments=[]
    def note_snapshot(self,item,**kwargs):
        return {'note':dict(self.note,folder_id=item['folder_id']),'attachments':copy.deepcopy(self.attachments)}
    def apply(self,*args):raise AssertionError('Read-only observation must not mutate')
class EditObservationTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.store=PlanStore(Path(temp.name)/'runtime');self.adapter=Adapter();self.account='synthetic@example.com'
        env={'ICLOUD_ENABLE_NOTES':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_MAIL_ACCOUNT':self.account,'ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'source_ids':['selected'],'access_mode':'read-only'}})}
        context=patch.dict(os.environ,env,clear=True);context.start();self.addCleanup(context.stop);self.engine=NativeChanges('notes',self.account,self.adapter,self.store)
        item={'operation':'append_note','source_id':'selected','folder_id':'old','id':'note','fields':{'title':'Synthetic','html':'<div>Synthetic</div><div>new</div>'},'before':{'note':copy.deepcopy(self.adapter.note),'attachments':[]},'fidelity_verified':True,'effects':{}}
        self.plan=self.store.prepare('notes_change',{'account':self.account,'context':self.engine.context(True),'changes':[item]});self.store.consume(self.plan['plan_id'],self.account);self.store.finish(self.plan['plan_id'],{'status':'failed_or_uncertain','phase':'mutation','attempted':[],'completed':[],'do_not_retry':True})
        exchange=patch('icloud_for_agents.native_edit_observation.completed_exchange_evidence',return_value={'helper_outcome':'error_response','mutation_dispatched':True});exchange.start();self.addCleanup(exchange.stop)
    def inspect(self,folder='new'):return inspect_uncertain_note_edit(self.engine,self.plan['plan_id'],folder)
    def test_exact_observation_keeps_uncertainty_and_exposes_no_body(self):
        result=self.inspect();self.assertTrue(result['observable_content_matches_before']);self.assertTrue(result['original_uncertainty_retained']);self.assertFalse(result['causal_dispatch_proven']);self.assertNotIn('html',result);self.assertFalse(self.store.operation_settled(self.plan['plan_id']))
        self.adapter.note['html']='Changed';self.assertFalse(self.inspect()['observable_content_matches_before']);self.assertFalse(self.store.operation_settled(self.plan['plan_id']))
    def test_identity_scope_terminal_response_and_backend_fail_closed(self):
        self.adapter.note['source_id']='foreign'
        with self.assertRaises(SafetyError):self.inspect()
        self.adapter.note['source_id']='selected';self.adapter.backend_id='changed'
        with self.assertRaises(SafetyError):self.inspect()
        self.adapter.backend_id='synthetic-observation-v1'
        with patch('icloud_for_agents.native_edit_observation.completed_exchange_evidence',side_effect=SafetyError('No terminal response')),self.assertRaises(SafetyError):self.inspect()
        self.assertFalse(self.store.operation_settled(self.plan['plan_id']))

class EditRecoveryTests(EditObservationTests):
    def setUp(self):
        super().setUp()
        from icloud_for_agents.native_edit_recovery import NotesEditRecovery
        self.recovery=NotesEditRecovery(self.account,self.store,self.adapter)
    def proposal(self):return self.recovery.prepare(self.plan['plan_id'],'new')
    def test_reviewed_acceptance_preserves_original_result_without_replay(self):
        original=self.store.path(self.plan['plan_id'],'.result').read_bytes();p=self.proposal();r=self.recovery.execute(p['plan_id'])
        self.assertFalse(r['native_data_changed']);self.assertFalse(r['causal_dispatch_proven']);self.assertTrue(self.store.operation_settled(self.plan['plan_id']))
        self.assertEqual(self.store.path(self.plan['plan_id'],'.result').read_bytes(),original);self.assertEqual(self.store.status(self.plan['plan_id'])['status'],'observed_unchanged_note_accepted')
        self.engine.assert_settled()
        with self.assertRaises(SafetyError):self.recovery.execute(p['plan_id'])
    def test_strict_policy_requires_exact_receipt(self):
        os.environ['ICLOUD_APPROVAL_POLICY']='local-receipt';p=self.proposal()
        with self.assertRaises(FileNotFoundError):self.recovery.execute(p['plan_id'])
        self.store.write_once(self.store.path(p['plan_id'],'.approved'),{'plan_id':p['plan_id'],'sha256':p['sha256']})
        self.assertEqual(self.recovery.execute(p['plan_id'])['status'],'completed');self.assertTrue(self.store.operation_settled(self.plan['plan_id']))
    def test_changed_content_backend_and_response_refuse_without_settling(self):
        p=self.proposal();self.adapter.note['html']='Changed'
        with self.assertRaises(SafetyError):self.recovery.execute(p['plan_id'])
        self.adapter.note['html']='<div>Synthetic</div>';self.adapter.backend_id='changed'
        with self.assertRaises(SafetyError):self.recovery.execute(p['plan_id'])
        self.adapter.backend_id='synthetic-observation-v1'
        with patch('icloud_for_agents.native_edit_observation.completed_exchange_evidence',side_effect=SafetyError('No response')),self.assertRaises(SafetyError):self.recovery.execute(p['plan_id'])
        self.assertFalse(self.store.operation_settled(self.plan['plan_id']))
    def test_interrupted_finalize_and_abandon_preserve_proof_rules(self):
        p=self.proposal();self.recovery.abandon(p['plan_id']);self.assertFalse(self.store.operation_settled(self.plan['plan_id']))
        with self.assertRaises(SafetyError):self.recovery.execute(p['plan_id'])
        fresh=self.proposal()
        with patch.object(self.recovery,'proof',side_effect=OSError('Interrupted')),self.assertRaises(OSError):self.recovery.execute(fresh['plan_id'])
        self.assertFalse(self.store.operation_settled(self.plan['plan_id']));self.assertEqual(self.recovery.finalize(fresh['plan_id'])['status'],'observed_unchanged_note_accepted')
        self.assertEqual(self.recovery.finalize(fresh['plan_id'])['status'],'observed_unchanged_note_accepted')
    def test_unavailable_post_admission_does_not_accept(self):
        p=self.proposal();original=self.adapter.note_snapshot;calls=0
        def snapshot(*args,**kwargs):
            nonlocal calls
            calls+=1
            if calls==2:raise SafetyError('Synthetic read timeout')
            return original(*args,**kwargs)
        with patch.object(self.adapter,'note_snapshot',side_effect=snapshot):result=self.recovery.execute(p['plan_id'])
        self.assertEqual(result['recovery_state'],'not_accepted');self.assertFalse(self.store.operation_settled(self.plan['plan_id']))
    def test_foreign_scope_and_forged_observation_are_rejected(self):
        with patch.dict(os.environ,{'ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'source_ids':['foreign'],'access_mode':'read-only'}})}),self.assertRaises(SafetyError):self.proposal()
        proposal=self.proposal();plan=self.store.read(self.store.path(proposal['plan_id'],'.json'))
        for key,value in [('original_id','other'),('request_sha256','0'*64),('operation','other')]:
            forged=copy.deepcopy(plan);forged['payload'][key]=value
            self.assertFalse(self.store._native_edit_recovery_for(self.plan['plan_id'],forged))
        for key,value in [('observable_content_matches_before',False),('source_id','foreign'),('terminal_exchange_sha256','invalid'),('data_mutated',True)]:
            forged=copy.deepcopy(plan);forged['payload']['observed'][key]=value
            self.assertFalse(self.store._native_edit_recovery_for(self.plan['plan_id'],forged))
