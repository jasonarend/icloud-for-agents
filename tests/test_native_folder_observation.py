"""Synthetic retained-folder failure recovery. Never calls a native writer."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError,digest
from icloud_for_agents.native_move_recovery import NotesMoveRecovery
from icloud_for_agents.native_folder_observation import observation_matches,validate_item
from icloud_for_agents.native_notes import NotesCreateAdapter
U='00000000-0000-0000-0000-000000000001';S=f'x-coredata://{U}/ICAccount/p1';A=f'x-coredata://{U}/ICFolder/p10';B=f'x-coredata://{U}/ICFolder/p11';D=f'x-coredata://{U}/ICFolder/p12'
def role(identity,title):return {'protocol_version':1,'folder_id':identity,'source_id':S,'title':title,'storage_kind':'regular','native_folder_type':0,'marked_for_deletion':False,'has_smart_query':False,'revision':1,'parent_pk':None,'backend':'notes-store-role-v1','schema_sha256':'a'*64,'default_folder_status':'requires independent Notes scripting check','content_read':False,'database_writes':False}
def folder(identity,title):return {'id':identity,'title':title,'source_id':S,'parent_id':S,'shared':False}
def inventory(value):return {'folders':[dict(value,is_default=False)],'notes':[],'siblings':[]}
def item():
 a=folder(A,'Empty source');b=folder(B,'Empty target')
 return {'operation':'move_folder','id':A,'folder_id':A,'source_id':S,'fields':{'title':a['title'],'destination_folder_id':B},'folder_guard':'verified_store_roles','fidelity_verified':True,'effects':{},'before':{'folder':a,'destination':b,'source_inventory':inventory(a),'destination_inventory':inventory(b),'roles':[role(A,a['title']),role(B,b['title'])],'roots':[{k:v[k]for k in ('id','title','source_id','parent_id')}for v in (a,b)],'default_folder_id':D}}
class Adapter:
 backend_id='synthetic-folder-observation';operations=set()
 def __init__(self):self.reader=self;self.roles={v['folder_id']:v for v in item()['before']['roles']};self.roles[A].update(parent_pk=11,marked_for_deletion=True,storage_kind='special_smart_or_deleted',revision=2);self.default=D
 def read(self,action,**fields):
  if action=='default_folder':return {'folder':{'id':self.default,'source_id':S}}
  if action=='folder_role':return copy.deepcopy(self.roles[fields['folder_id']])
  raise AssertionError(action)
 def apply(self,*args):raise AssertionError('No writer allowed')
class FolderObservationTests(unittest.TestCase):
 def setUp(self):
  tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.store=PlanStore(Path(tmp.name)/'runtime');self.adapter=Adapter();self.account='synthetic@example.com'
  env={'ICLOUD_ENABLE_NOTES':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_MAIL_ACCOUNT':self.account,'ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'source_ids':[S],'access_mode':'read-only','metadata_backend':'notes-store-readonly'}})}
  for context in (patch.dict(os.environ,env,clear=True),patch('icloud_for_agents.native_folder_observation.empty_folder_membership',return_value={'folder_id':A,'source_id':S,'live_notes':0,'live_child_folders':0,'content_read':False,'database_writes':False}),patch('icloud_for_agents.native_move_recovery.completed_exchange_evidence',side_effect=lambda request,**kw:{'request_sha256':digest(request),'helper_response_sha256':'a'*64,'helper_outcome':'error_response','mutation_dispatched':True})):
   context.start();self.addCleanup(context.stop)
  self.recovery=NotesMoveRecovery(self.account,self.store,self.adapter);self.original=self.store.prepare('notes_change',{'account':self.account,'context':self.recovery.engine.context(True),'changes':[item()]});self.store.consume(self.original['plan_id'],self.account);self.store.finish(self.original['plan_id'],{'status':'failed_or_uncertain','phase':'mutation','attempted':[],'completed':[],'do_not_retry':True})
 def prepare(self):return self.recovery.prepare(self.original['plan_id'])
 def test_deleted_empty_observation_preserves_failure_and_never_claims_restore(self):
  before=self.store.path(self.original['plan_id'],'.result').read_bytes();p=self.prepare();self.assertIn('possible marked-deleted',p['review']['payload']['effects']['decision']);r=self.recovery.execute(p['plan_id']);self.assertEqual(r['location'],'marked_deleted');self.assertFalse(r['native_data_changed']);self.assertFalse(r['causal_dispatch_proven']);self.assertTrue(self.store.operation_settled(self.original['plan_id']));self.assertEqual(self.store.path(self.original['plan_id'],'.result').read_bytes(),before);self.recovery.engine.assert_settled()
  with self.assertRaises(SafetyError):self.recovery.engine.execute(self.original['plan_id'])
  self.assertNotIn('move_folder',NotesCreateAdapter.operations)
 def test_strict_requires_exact_independent_receipt(self):
  os.environ['ICLOUD_APPROVAL_POLICY']='local-receipt';p=self.prepare()
  with self.assertRaises(FileNotFoundError):self.recovery.execute(p['plan_id'])
  # Synthetic policy fixture in isolated temporary runtime only.
  self.store.write_once(self.store.path(p['plan_id'],'.approved'),{'plan_id':p['plan_id'],'sha256':p['sha256']});self.assertEqual(self.recovery.execute(p['plan_id'])['location'],'marked_deleted');self.assertTrue(self.store.operation_settled(self.original['plan_id']))
 def test_original_source_or_destination_live_observation_has_no_success_claim(self):
  for parent,location in ((None,'source'),(11,'destination')):
   self.adapter.roles[A].update(parent_pk=parent,marked_for_deletion=False,storage_kind='regular');p=self.prepare();self.assertEqual(p['review']['payload']['observed']['location'],location);self.recovery.abandon(p['plan_id'])
 def test_outside_parent_shared_smart_deleted_bystander_and_content_refuse(self):
  saved=copy.deepcopy(self.adapter.roles)
  for identity,field,value in ((A,'parent_pk',99),(A,'title','Changed'),(A,'native_folder_type',1),(B,'marked_for_deletion',True),(B,'revision',0),(A,'schema_sha256','b'*64)):
   self.adapter.roles=copy.deepcopy(saved);self.adapter.roles[identity][field]=value
   with self.subTest(field=field),self.assertRaises(SafetyError):self.prepare()
  self.adapter.roles=saved
  with patch('icloud_for_agents.native_folder_observation.empty_folder_membership',side_effect=SafetyError('Live native child')),self.assertRaises(SafetyError):self.prepare()
 def test_destination_revision_can_advance_but_is_bound_at_acceptance(self):
  self.adapter.roles[B]['revision']=2;p=self.prepare();self.adapter.roles[B]['revision']=3
  with self.assertRaises(SafetyError):self.recovery.execute(p['plan_id'])
  self.assertFalse(self.store.path(p['plan_id'],'.started').exists())
 def test_opt_in_default_change_scope_disable_and_missing_terminal_refuse(self):
  saved=os.environ['ICLOUD_NATIVE_CONFIG'];os.environ['ICLOUD_NATIVE_CONFIG']=json.dumps({'notes':{'source_ids':[S]}})
  with self.assertRaises(SafetyError):self.prepare()
  os.environ['ICLOUD_NATIVE_CONFIG']=saved;self.adapter.default=A
  with self.assertRaises(SafetyError):self.prepare()
  self.adapter.default=D
  with patch('icloud_for_agents.native_move_recovery.completed_exchange_evidence',side_effect=SafetyError('Missing terminal')),self.assertRaises(SafetyError):self.prepare()
  with patch.dict(os.environ,{'ICLOUD_ENABLE_NOTES':'0'}),self.assertRaises(SafetyError):self.prepare()
 def test_drift_before_consumption_and_interrupted_proof(self):
  p=self.prepare();self.adapter.roles[A]['revision']=3
  with self.assertRaises(SafetyError):self.recovery.execute(p['plan_id'])
  self.assertFalse(self.store.path(p['plan_id'],'.started').exists());fresh=self.prepare()
  with patch.object(self.recovery,'proof',side_effect=OSError('Interrupted')),self.assertRaises(OSError):self.recovery.execute(fresh['plan_id'])
  self.assertFalse(self.store.operation_settled(self.original['plan_id']));self.recovery.finalize(fresh['plan_id']);self.assertTrue(self.store.operation_settled(self.original['plan_id']))
 def test_proof_cannot_forge_content_empty_location_or_roles(self):
  p=self.prepare();observed=p['review']['payload']['observed']
  for path,value in ((('location',),'source'),(('empty_membership','live_notes'),1),(('folder_id',),B),(('before_sha256',),'b'*64),(('coverage',),'All restored')):
   bad=copy.deepcopy(observed);target=bad
   for key in path[:-1]:target=target[key]
   target[path[-1]]=value;self.assertFalse(observation_matches(item(),bad))
 def test_partial_or_rich_old_proposal_cannot_enter_compatibility(self):
  for mutate in (lambda v:v['before']['source_inventory']['notes'].append({'id':'note','folder_id':A}),lambda v:v['before']['source_inventory']['folders'].append(folder(B,'child')),lambda v:v['before'].__setitem__('default_folder_id',A),lambda v:v['fields'].__setitem__('destination_folder_id',A),lambda v:v['before'].__delitem__('roles')):
   bad=item();mutate(bad)
   with self.assertRaises(SafetyError):validate_item(bad)
