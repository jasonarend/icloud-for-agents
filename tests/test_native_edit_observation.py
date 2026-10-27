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
