"""Known-ID observational move recovery; no native write or invented dispatch proof."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError,digest
from icloud_for_agents.native_move_recovery import NotesMoveRecovery

class Adapter:
    backend_id='synthetic-observation-v1';operations={'move_note'}
    def __init__(self):
        self.position='source';self.alias=None;self.calls=0
        self.note={'id':'note','title':'Synthetic','html':'<div>Synthetic</div><object>Native</object>','text':'Synthetic object','created':'2026-10-26','modified':'2026-10-26T00:00:00Z','locked':False,'shared':False,'truncated':False,'source_id':'selected','folder_id':'source'}
        self.attachments=[{'id':'asset','name':None,'note_id':'note','content_identifier':'native:asset'}];self.reader=self
    def private_folder(self,identity,source):
        if identity not in {'source','destination'} or source!='selected':raise SafetyError('Unselected fake folder')
        return {'id':identity,'title':identity,'source_id':source,'parent_id':source,'shared':False}
    def read(self,action,**fields):
        if action!='contains':raise AssertionError(action)
        return {'exists':self.alias if self.alias is not None else fields['folder_id']==self.position}
    def note_snapshot(self,item,**kwargs):
        self.calls+=1
        return {'folder':self.private_folder(self.position,'selected'),'note':dict(self.note,folder_id=self.position),'attachments':copy.deepcopy(self.attachments)}
    def apply(self,*args):raise AssertionError('Observation recovery must never mutate native data')

class NativeMoveRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.store=PlanStore(Path(temp.name)/'runtime');self.adapter=Adapter();self.account='synthetic@example.com'
        env={'ICLOUD_ENABLE_NOTES':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_MAIL_ACCOUNT':self.account,'ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'source_ids':['selected'],'access_mode':'read-only'}})}
        context=patch.dict(os.environ,env,clear=True);context.start();self.addCleanup(context.stop);self.recovery=NotesMoveRecovery(self.account,self.store,self.adapter)
        item={'operation':'move_note','source_id':'selected','folder_id':'source','id':'note','fields':{'destination_folder_id':'destination','title':'Synthetic'},'before':{'folder':self.adapter.private_folder('source','selected'),'destination':self.adapter.private_folder('destination','selected'),'note':copy.deepcopy(self.adapter.note),'attachments':copy.deepcopy(self.adapter.attachments)},'fidelity_verified':True,'effects':{}}
        self.original=self.store.prepare('notes_change',{'account':self.account,'context':self.recovery.engine.context(True),'changes':[item]});self.store.consume(self.original['plan_id'],self.account);self.result={'status':'failed_or_uncertain','phase':'mutation','attempted':[],'completed':[],'do_not_retry':True};self.store.finish(self.original['plan_id'],self.result)
        exchange=patch('icloud_for_agents.native_move_recovery.completed_exchange_evidence',side_effect=lambda req,**kwargs:{'request_id':'request-synthetic','request_sha256':digest(req),'helper_response_sha256':'a'*64,'helper_outcome':'error_response','mutation_dispatched':True});exchange.start();self.addCleanup(exchange.stop)
    def proposal(self):return self.recovery.prepare(self.original['plan_id'])
    def test_accept_source_observation_preserves_original_failure_and_never_replays(self):
        before=self.store.path(self.original['plan_id'],'.result').read_bytes();p=self.proposal();r=self.recovery.execute(p['plan_id'])
        self.assertEqual(r['location'],'source');self.assertFalse(r['native_data_changed']);self.assertFalse(r['causal_dispatch_proven'])
        self.assertEqual(self.store.path(self.original['plan_id'],'.result').read_bytes(),before)
        self.assertTrue(self.store.operation_settled(self.original['plan_id']));self.recovery.engine.assert_settled()
        self.assertEqual(self.store.status(self.original['plan_id'])['status'],'observed_known_move_state_accepted')
        with self.assertRaises(SafetyError):self.recovery.execute(p['plan_id'])
        with self.assertRaises(SafetyError):self.recovery.engine.execute(self.original['plan_id'])
    def test_accept_exact_destination_without_claiming_causal_dispatch(self):
        self.adapter.position='destination';p=self.proposal();r=self.recovery.execute(p['plan_id']);self.assertEqual(r['location'],'destination');self.assertFalse(r['causal_dispatch_proven'])
    def test_strict_policy_requires_independent_exact_receipt(self):
        os.environ['ICLOUD_APPROVAL_POLICY']='local-receipt';p=self.proposal()
        with self.assertRaises(FileNotFoundError):self.recovery.execute(p['plan_id'])
        self.assertFalse(self.store.path(p['plan_id'],'.started').exists())
        # Synthetic owner receipt fixture only; never a real human approval.
        self.store.write_once(self.store.path(p['plan_id'],'.approved'),{'plan_id':p['plan_id'],'sha256':p['sha256']})
        self.assertEqual(self.recovery.execute(p['plan_id'])['status'],'completed');self.assertTrue(self.store.operation_settled(self.original['plan_id']))
    def test_changed_content_attachments_or_ambiguous_presence_refuses(self):
        self.adapter.note['html']='Changed'
        with self.assertRaises(SafetyError):self.proposal()
        self.adapter.note['html']=self.original['review']['payload']['changes'][0]['before']['note']['html'];self.adapter.attachments=[]
        with self.assertRaises(SafetyError):self.proposal()
        self.adapter.attachments=copy.deepcopy(self.original['review']['payload']['changes'][0]['before']['attachments'])
        for alias in (True,False):
            self.adapter.alias=alias
            with self.assertRaises(SafetyError):self.proposal()
    def test_missing_terminal_response_never_unlocks_an_unknown_move(self):
        with patch('icloud_for_agents.native_move_recovery.completed_exchange_evidence',side_effect=SafetyError('No terminal response')):
            with self.assertRaises(SafetyError):self.proposal()
        self.assertFalse(self.store.operation_settled(self.original['plan_id']))
    def test_changed_location_or_backend_refuses_before_consumption_and_can_reprepare(self):
        p=self.proposal();self.adapter.position='destination'
        with self.assertRaises(SafetyError):self.recovery.execute(p['plan_id'])
        self.assertFalse(self.store.path(p['plan_id'],'.started').exists());fresh=self.proposal();self.adapter.backend_id='changed'
        with self.assertRaises(SafetyError):self.recovery.execute(fresh['plan_id'])
    def test_source_owner_and_nonmove_scope_cannot_be_rebound(self):
        with self.assertRaises(SafetyError):NotesMoveRecovery('other@example.com',self.store,self.adapter).prepare(self.original['plan_id'])
        config=json.dumps({'notes':{'source_ids':['other'],'access_mode':'read-only'}})
        with patch.dict(os.environ,{'ICLOUD_NATIVE_CONFIG':config}),self.assertRaises(SafetyError):self.proposal()
    def test_interrupted_proof_finalizes_without_reexecuting_native_work(self):
        p=self.proposal()
        with patch.object(self.recovery,'proof',side_effect=OSError('Synthetic final-proof interruption')),self.assertRaises(OSError):self.recovery.execute(p['plan_id'])
        self.assertFalse(self.store.operation_settled(self.original['plan_id']));self.assertEqual(self.recovery.finalize(p['plan_id'])['status'],'observed_known_move_state_accepted')
        self.assertEqual(self.recovery.finalize(p['plan_id'])['status'],'observed_known_move_state_accepted')
    def test_abandon_pending_or_interrupted_observation_retains_original_uncertainty(self):
        p=self.proposal();self.assertEqual(self.recovery.abandon(p['plan_id'])['status'],'abandoned')
        with self.assertRaises(SafetyError):self.recovery.execute(p['plan_id'])
        fresh=self.proposal();self.store.consume(fresh['plan_id'],self.account);self.recovery.abandon(fresh['plan_id'])
        self.assertTrue(self.store.operation_settled(fresh['plan_id']));self.assertFalse(self.store.operation_settled(self.original['plan_id']))
        self.assertEqual(self.store.status(fresh['plan_id'])['status'],'abandoned')
        latest=self.proposal();self.assertEqual(self.recovery.execute(latest['plan_id'])['status'],'completed')
    def test_failed_post_admission_read_does_not_accept_original(self):
        p=self.proposal();original=self.adapter.note_snapshot;calls=0
        def snapshot(*args,**kwargs):
            nonlocal calls
            calls+=1
            if calls==2:raise SafetyError('Synthetic read timeout after admission')
            return original(*args,**kwargs)
        with patch.object(self.adapter,'note_snapshot',side_effect=snapshot):r=self.recovery.execute(p['plan_id'])
        self.assertEqual(r['recovery_state'],'not_accepted');self.assertFalse(self.store.operation_settled(self.original['plan_id']))
        fresh=self.proposal();self.assertEqual(self.recovery.execute(fresh['plan_id'])['status'],'completed')

    def test_disabled_stale_methods_refuse_and_withdrawal_can_narrow_removed_source_scope(self):
        p=self.proposal()
        with patch.dict(os.environ,{'ICLOUD_ENABLE_NOTES':'0'}):
            for method,args in [(self.recovery.prepare,[self.original['plan_id']]),(self.recovery.execute,[p['plan_id']]),(self.recovery.finalize,[p['plan_id']]),(self.recovery.abandon,[p['plan_id']])]:
                with self.assertRaises(SafetyError):method(*args)
        with patch.dict(os.environ,{'ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'source_ids':[],'access_mode':'read-only'}})}):
            self.assertEqual(self.recovery.abandon(p['plan_id'])['status'],'abandoned')

    def test_observed_source_identity_is_not_inferred_from_matching_content(self):
        self.adapter.note['source_id']='unselected'
        with self.assertRaises(SafetyError):self.proposal()
