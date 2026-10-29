"""Text attachment authority, byte preservation, quotas and no-replay recovery."""
import base64,copy,hashlib,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from test_native_reminder_moves import Reader as BaseReader,SOURCE,ORIGIN,DEST,ROOT,CHILD
from icloud_for_agents import native_reminder_file_create as module
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_access import require_write
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.native_grants import NativeGrants
from icloud_for_agents.native_protocol import mutation_request
from icloud_for_agents.native_reminders import RemindersCreateAdapter
class Reader(BaseReader):
 def __init__(self):
  super().__init__();self.parents[CHILD]=None;self.flags[CHILD]=False;self.attachments={ROOT:[],CHILD:[]};self.raw={};self.grocery=False;self.sections=[]
  for item in self.items.values():item['modified']=200.0
 def read(self,action,**fields):
  if action=='list_order':
   result=super().read('list_hierarchy',**fields);result['ordered_ids']=[v['id']for v in result['nodes']];return result
  if action=='list_sections':return {'protocol_version':1,'source_id':SOURCE,'list_id':fields['list_id'],'sections':copy.deepcopy(self.sections),'data_mutated':False,'ordering_verified':False,'membership_verified':False}
  result=super().read(action,**fields)
  if action=='list_features':result['grocery_categorization']=self.grocery
  if action=='get'and fields.get('include_native_attachments'):
   v=result['item'];v['native_metadata']={**{k:v[k]for k in('id','source_id','list_id')},'backend':'reminderkit-attachments-v1','attachments':copy.deepcopy(self.attachments[v['id']]),'attachment_metadata_complete':False,'coverage':['attachments'],'native_metadata_complete':False,'observation_consistency':'separate_provider_reads'}
  return result
class Adapter(RemindersCreateAdapter):
 backend_id='synthetic-text-file'
 def __init__(self):self.reader=Reader();self.calls=[]
 def apply(self,item):
  require_write('reminders',item);self.calls.append(item['id']);request=mutation_request('reminders',item)['change'];assert request['attachment_id']==item['attachment_id']and request['before']==item['before'];raw=module.data(item['fields']);self.reader.raw[item['attachment_id']]=raw;self.reader.attachments[item['id']]=[{'id':item['attachment_id'],'kind':'file','uti':'public.plain-text'}];self.reader.items[item['id']]['modified']=201.0;return {k:item[k]for k in('id','source_id','list_id','attachment_id')}|{'saved_locally':True}
class TextFileCreateTests(unittest.TestCase):
 def setUp(self):
  t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.store=PlanStore(Path(t.name)/'runtime');self.adapter=Adapter();self.engine=NativeChanges('reminders','synthetic@example.com',self.adapter,self.store)
  env={'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'source_ids':[SOURCE],'access_mode':'read-only','advanced_backend':'reminderkit','helper_path':'/synthetic/helper'}})}
  p=patch.dict(os.environ,env,clear=True);p.start();self.addCleanup(p.stop)
  def export(identity,attachment,maximum,*,reader):
   raw=reader.raw[attachment];return {'id':identity,'source_id':SOURCE,'list_id':reader.items[identity]['list_id'],'attachment_id':attachment,'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'uti':'public.plain-text','kind':'file','data_mutated':False}
  p=patch.object(module,'export',side_effect=export);p.start();self.addCleanup(p.stop)
 def change(self,identity=ROOT,raw='Synthetic text 🚀\n'.encode()):return {'operation':'add_file_attachment','source_id':SOURCE,'id':identity,'fields':{'uti':'public.plain-text','data_base64':base64.b64encode(raw).decode(),'allow_mcp_only':True}}
 def scope(self):return {'scenario':'Synthetic text files','rules':[{'operation':'add_file_attachment','source_id':SOURCE,'container_id':ORIGIN,'fields':['uti','data_base64','allow_mcp_only'],'destinations':[]}],'max_batch':2,'max_24h':4,'expires':None}
 def test_once_exact_bytes_preassigned_id_and_bystander_preservation(self):
  before=copy.deepcopy(self.adapter.reader.items[CHILD]);p=self.engine.prepare_once([self.change()]);item=p['review']['payload']['changes'][0];self.assertEqual(item['size'],len('Synthetic text 🚀\n'.encode()));self.assertIn('temporary',item['effects']['dispatch']);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');self.assertEqual(self.adapter.reader.items[CHILD],before);self.engine.assert_settled()
  with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
  with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
 def test_grants_charge_two_objects_without_trusting_effects_and_revoke(self):
  m=NativeGrants(self.engine);scope=self.scope();scope['max_batch']=1;g=m.prepare(scope)['plan_id'];m.activate(g)
  with self.assertRaisesRegex(SafetyError,'native-object bound'):self.engine.prepare_with_grant(g,[self.change()])
  m.revoke(g);scope=self.scope();g=m.prepare(scope)['plan_id'];m.activate(g)
  with self.assertRaises(SafetyError):self.engine.prepare_with_grant(g,[dict(self.change(),source_id=DEST)])
  p=self.engine.prepare_with_grant(g,[self.change()]);item=copy.deepcopy(p['review']['payload']['changes'][0]);item['effects']['quota_objects']=0;self.assertEqual(m.batch_count([item]),2);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');self.assertEqual(self.store.read(self.store.path(p['plan_id'],'.native-grant-use'))['count'],2)
  pending=self.engine.prepare_with_grant(g,[self.change(CHILD)]);m.revoke(g)
  with self.assertRaises(SafetyError):self.engine.execute(pending['plan_id'])
  self.assertEqual(self.adapter.calls,[ROOT])
 def test_strict_synthetic_receipt_and_no_implicit_authority(self):
  with self.assertRaises(SafetyError):self.engine.prepare([self.change()])
  os.environ['ICLOUD_APPROVAL_POLICY']='local-receipt';p=self.engine.prepare_once([self.change()])
  with self.assertRaises(FileNotFoundError):self.engine.execute(p['plan_id'])
  self.assertEqual(self.adapter.calls,[]);self.store.write_once(self.store.path(p['plan_id'],'.approved'),{'plan_id':p['plan_id'],'sha256':p['sha256']});self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied')
 def test_invalid_bytes_type_extra_path_and_scope_refuse(self):
  for raw in(b'',b'\0',b'\xff',b'a'*(module.MAX_BYTES+1)):
   with self.assertRaises(SafetyError):self.engine.prepare_once([self.change(raw=raw)])
  for fields in({'uti':'image/png','data_base64':'YQ=='},{'uti':'public.plain-text','data_base64':'YQ==\n'},{'uti':'public.plain-text','data_base64':'YR=='},{'uti':'public.plain-text','data_base64':'YQ==','path':'/synthetic/file'}):
   with self.assertRaises(SafetyError):self.engine.prepare_once([dict(self.change(),fields=dict(fields,allow_mcp_only=True))])
  for ack in(False,1,None):
   with self.assertRaises(SafetyError):self.engine.prepare_once([dict(self.change(),fields=dict(self.change()['fields'],allow_mcp_only=ack))])
  with self.assertRaises(SafetyError):self.engine.prepare_once([dict(self.change(),attachment_id=DEST)])
  for key in('grocery','shared'):
   setattr(self.adapter.reader,key,True)
   with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
   setattr(self.adapter.reader,key,False)
  self.adapter.reader.sections=[{'id':DEST,'title':'Synthetic'}]
  with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
 def test_drift_before_dispatch_and_wrong_saved_bytes_or_metadata_never_verify(self):
  p=self.engine.prepare_once([self.change()]);self.adapter.reader.items[CHILD]['notes']='Concurrent edit'
  with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
  self.assertFalse(self.store.path(p['plan_id'],'.started').exists());p=self.engine.prepare_once([self.change()]);item=p['review']['payload']['changes'][0];self.engine.execute(p['plan_id']);v=module.expected_observation(self.adapter,item);self.assertTrue(module.record_matches(item,v))
  for path in('bytes','bystander','attachment'):
   bad=copy.deepcopy(v)
   if path=='bytes':bad['file']['sha256']='0'*64
   elif path=='bystander':bad['snapshot']['items'][1]['item']['notes']='Lost body'
   else:bad['snapshot']['items'][0]['attachments']['attachments'][0]['id']=DEST
   self.assertFalse(module.record_matches(item,bad))
 def test_retained_error_expected_state_reconciles_without_replay(self):
  from icloud_for_agents.native_reminder_exchange import begin,finish
  apply=self.adapter.apply
  def uncertain(item):apply(item);finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic retained error')
  with patch.object(self.adapter,'apply',side_effect=uncertain):p=self.engine.prepare_once([self.change()]);self.engine.execute(p['plan_id'])
  original=self.store.path(p['plan_id'],'.result').read_bytes();self.assertEqual(self.engine.reconcile_known_changes(p['plan_id'])['status'],'applied_after_readback_reconciliation');self.assertEqual(original,self.store.path(p['plan_id'],'.result').read_bytes());self.engine.assert_settled();self.assertEqual(self.adapter.calls,[ROOT])
 def test_unchanged_error_requires_review_and_retains_temporary_file_uncertainty(self):
  from icloud_for_agents.native_reminder_exchange import begin,finish
  from icloud_for_agents.native_reminder_recovery import ReminderObservationRecovery
  def unchanged(item):finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic unchanged error')
  with patch.object(self.adapter,'apply',side_effect=unchanged):p=self.engine.prepare_once([self.change()]);self.engine.execute(p['plan_id'])
  original=self.store.path(p['plan_id'],'.result').read_bytes();m=ReminderObservationRecovery(self.engine.account,self.store,self.adapter)
  with self.assertRaises(SafetyError):m.prepare(p['plan_id'],legacy_missing_terminal=True)
  review=m.prepare(p['plan_id']);self.assertIn('temporary files',review['review']['payload']['effects']['file_insertion_coverage']);self.assertEqual(m.execute(review['plan_id'])['recovery_state'],'observed_unchanged_reminder_accepted_after_review');self.engine.assert_settled();self.assertEqual(self.adapter.calls,[]);self.assertEqual(original,self.store.path(p['plan_id'],'.result').read_bytes())
