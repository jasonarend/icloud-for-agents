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
    def historical_completed(self):
        from contextlib import ExitStack
        from icloud_for_agents.native_protocol import mutation_request
        from icloud_for_agents.native_edit_recovery import NotesEditRecovery
        store=PlanStore(self.store.root/'historical');backend='notes-creation-v1:'+('a'*64)
        with patch.object(self.adapter,'backend_id',backend):
            engine=NativeChanges('notes',self.account,self.adapter,store)
            item=copy.deepcopy(self.plan['review']['payload']['changes'][0])
            original=store.prepare('notes_change',{'account':self.account,'context':engine.context(True),'changes':[item]})
            store.consume(original['plan_id'],self.account);store.finish(original['plan_id'],{'status':'failed_or_uncertain','phase':'mutation','attempted':[],'completed':[],'do_not_retry':True})
            legacy=mutation_request('notes',item);legacy['change'].pop('folder_guard')
            store.write_once(store.path(original['plan_id'],'.native-exchange-0'),{'plan_sha256':original['sha256'],'request_sha256':digest(legacy),'helper_fingerprint':'a'*64,'backend_id':backend})
            manager=NotesEditRecovery(self.account,store,self.adapter)
            with ExitStack() as stack:
                for module in ('native_protocol','native_edit_recovery','native_edit_observation'):
                    stack.enter_context(patch('icloud_for_agents.'+module+'.mutation_request',return_value=legacy))
                review=manager.prepare(original['plan_id'],'new');manager.execute(review['plan_id'])
        return store,original,review

    def test_completed_historical_edit_proof_survives_added_folder_guard(self):
        store,original,review=self.historical_completed()
        self.assertTrue(store.operation_settled(original['plan_id']))
        stored=store.read(store.path(review['plan_id'],'.json'))
        self.assertFalse(store._native_edit_recovery_for(original['plan_id'],stored))
        self.assertEqual(store.status(original['plan_id'])['status'],'observed_unchanged_note_accepted')
        with self.assertRaises(SafetyError):store.consume(original['plan_id'],self.account)

    def test_historical_edit_requires_original_transport_binding_and_completed_proof(self):
        store,original,review=self.historical_completed();identity=original['plan_id']
        binding_path=store.path(identity,'.native-exchange-0');binding=store.read(binding_path)
        for key in ('plan_sha256','request_sha256','helper_fingerprint','backend_id'):
            forged=dict(binding);forged[key]='invalid';binding_path.write_text(json.dumps(forged))
            self.assertFalse(store.operation_settled(identity))
        binding_path.write_text(json.dumps(binding));self.assertTrue(store.operation_settled(identity))
        store.path(identity,'.native-edit-recovery-result').unlink()
        self.assertFalse(store.operation_settled(identity))
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
