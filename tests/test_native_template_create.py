"""Exact source copies, affected-object permission accounting and retained uncertainty."""
import copy,json,os,tempfile,unittest,uuid
from pathlib import Path
from unittest.mock import patch
from test_native_reminder_moves import Reader as BaseReader,SOURCE,ORIGIN,DEST,ROOT,CHILD
from icloud_for_agents import native_template_create as module
from icloud_for_agents.core import SafetyError,digest
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.native_reminders import RemindersCreateAdapter
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.native_grants import NativeGrants
from icloud_for_agents.native_access import require_write
from icloud_for_agents.native_protocol import mutation_request
SAVED='66666666-6666-4666-8666-666666666666'
class Reader(BaseReader):
 def __init__(self):
  super().__init__();self.items.pop(CHILD);self.parents.pop(CHILD);self.items[ROOT]['notes']='';self.templates={};self.saved={};self.incomplete=False
 def read(self,action,**fields):
  if action=='templates':
   rows=sorted([{'id':k,'title':v['title']}for k,v in self.templates.items()],key=lambda v:v['id']);return {'protocol_version':1,'source_id':SOURCE,'backend':'reminderkit-templates-v1','templates':rows,'next_offset':len(rows),'done':not self.incomplete,'data_mutated':False,'template_contents_complete':False,'pagination_consistency':'live_offset_may_shift'}
  if action=='template_metadata':return copy.deepcopy(self.templates[fields['id']])
  if action=='template_sections':return {'protocol_version':1,'source_id':SOURCE,'id':fields['id'],'sections':[],'sections_complete':False,'ordering_verified':False,'membership_verified':False,'data_mutated':False,'backend':'reminderkit-template-sections-v1'}
  if action=='list_sections':return {'protocol_version':1,'source_id':SOURCE,'list_id':fields['list_id'],'sections':[],'data_mutated':False}
  return super().read(action,**fields)
 def outline(self,source,identity,**kw):return {'title':self.templates[identity]['title'],'items':[{'item_id':v['item_id'],'title':v['title'],'priority':v['priority'],'parent_item_id':None}for v in self.saved[identity]],'done':True,'total_items':len(self.saved[identity])}
 def details(self,source,identity,item,**kw):
  value=next(v for v in self.saved[identity]if v['item_id']==item)
  return dict(value,source_id=source,template_id=identity,parent_item_id=None,representation_complete=False,restorable=False,native_checklist_items_omitted=0,omitted_metadata_keys=[],native_metadata_uninterpreted={'flagged':0,'recurrenceRules':[],'alarmTriggers':{'dateTriggers':[],'timeIntervalTriggers':[],'locationTriggers':[],'vehicleTriggers':[]}})
class Adapter(RemindersCreateAdapter):
 backend_id='synthetic-template-create'
 def __init__(self):self.reader=Reader();self.calls=[]
 def apply(self,item):
  require_write('reminders',item);self.calls.append(copy.deepcopy(item));self.reader.templates[item['id']]={'protocol_version':1,'source_id':SOURCE,'id':item['id'],'title':item['fields']['title'],'minimum_supported_version':0,'configuration':{'source_list_id':ORIGIN,'save_completed':False},'public_link_present':False,'public_link_included':False,'public_link':None,'data_mutated':False,'backend':'reminderkit-template-metadata-v1'}
  self.reader.saved[item['id']]=[dict(item_id=str(uuid.uuid5(uuid.NAMESPACE_URL,item['id']+v['item']['id'])).upper(),**{k:v['item'][k]for k in('title','notes','priority')})for v in item['before']['items']]
  return {'source_id':SOURCE,'id':item['id'],'saved_locally':True}
class TemplateCreateTests(unittest.TestCase):
 def setUp(self):
  self.adapter=Adapter();t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.store=PlanStore(Path(t.name)/'runtime')
  env={'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'source_ids':[SOURCE],'access_mode':'read-only','advanced_backend':'reminderkit','metadata_backend':'reminders-store-readonly','helper_path':'/synthetic/helper'}})}
  for p in(patch.dict(os.environ,env,clear=True),patch.object(module,'outline',side_effect=self.adapter.reader.outline),patch.object(module,'item_details',side_effect=self.adapter.reader.details)):p.start();self.addCleanup(p.stop)
  self.engine=NativeChanges('reminders','synthetic@example.com',self.adapter,self.store)
 def change(self,title='Synthetic template copy'):return {'operation':'create_template','source_id':SOURCE,'list_id':ORIGIN,'fields':{'title':title}}
 def scope(self):return {'scenario':'Synthetic exact list template copies','rules':[{'operation':'create_template','source_id':SOURCE,'container_id':ORIGIN,'fields':['title'],'destinations':[]}],'max_batch':2,'max_24h':4,'expires':None}
 def test_once_preassigned_identity_protocol_content_and_unchanged_source(self):
  before=copy.deepcopy(self.adapter.reader.items)
  with self.assertRaises(SafetyError):self.engine.prepare([self.change()])
  p=self.engine.prepare_once([self.change()]);item=p['review']['payload']['changes'][0];uuid.UUID(item['id']);self.assertEqual(item['effects']['quota_objects'],2);self.assertEqual(mutation_request('reminders',item)['change']['before'],item['before']);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');self.assertEqual(self.adapter.reader.items,before);self.engine.assert_settled()
  with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
 def test_strict_synthetic_receipt_and_service_selection(self):
  os.environ['ICLOUD_APPROVAL_POLICY']='local-receipt';p=self.engine.prepare_once([self.change()])
  with self.assertRaises(FileNotFoundError):self.engine.execute(p['plan_id'])
  self.store.write_once(self.store.path(p['plan_id'],'.approved'),{'plan_id':p['plan_id'],'sha256':p['sha256']});self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied')
  with patch.dict(os.environ,{'ICLOUD_ENABLE_REMINDERS':'0'}),self.assertRaises(SafetyError):self.engine.prepare_once([self.change('Disabled')])
 def test_exact_scope_quota_charges_template_and_saved_items_and_revocation(self):
  m=NativeGrants(self.engine);g=m.prepare(self.scope())['plan_id'];m.activate(g)
  with self.assertRaises(SafetyError):self.engine.prepare_with_grant(g,[dict(self.change(),list_id=DEST)])
  for title in('Synthetic first','Synthetic second'):
   p=self.engine.prepare_with_grant(g,[self.change(title)]);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');self.assertEqual(self.store.read(self.store.path(p['plan_id'],'.native-grant-use'))['count'],2)
  p=self.engine.prepare_with_grant(g,[self.change('Synthetic over quota')])
  with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
  m.revoke(g)
  with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
 def test_invalid_titles_duplicate_template_incomplete_source_rich_scheduling_and_flags_refuse(self):
  for title in('',True,'\n','\ud800','a'*1001):
   with self.assertRaises(SafetyError):self.engine.prepare_once([self.change(title)])
  for key,value in(('completed',True),('notes','Body not admitted yet'),('due',{}),('url','https://example.invalid'),('has_recurrence',True),('alarms',[{}])):
   before=copy.deepcopy(self.adapter.reader.items);self.adapter.reader.items[ROOT][key]=value
   with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
   self.adapter.reader.items=before
  self.adapter.reader.flags[ROOT]=True
  with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
  self.adapter.reader.flags[ROOT]=False;self.adapter.reader.incomplete=True
  with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
  self.assertEqual(self.adapter.calls,[])
 def test_drift_prevents_dispatch_and_quota_does_not_trust_effects(self):
  p=self.engine.prepare_once([self.change()]);self.adapter.reader.items[ROOT]['modified']+=1
  with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
  self.assertFalse(self.store.path(p['plan_id'],'.started').exists());self.assertEqual(self.adapter.calls,[])
  item=p['review']['payload']['changes'][0];item['effects']['quota_objects']=1;self.assertEqual(module.charge(item),2);item['before']['items']*=2
  with self.assertRaises(SafetyError):module.charge(item)
 def test_content_and_source_readback_forgery_refuse(self):
  p=self.engine.prepare_once([self.change()]);item=p['review']['payload']['changes'][0];self.engine.execute(p['plan_id']);value=module.expected_observation(self.adapter,item);self.assertTrue(module.record_matches(item,value))
  for modify in(lambda v:v['details'][0].__setitem__('notes','lost or changed'),lambda v:v['details'][0].__setitem__('template_id',DEST),lambda v:v['metadata'].__setitem__('public_link_present',True),lambda v:v['snapshot']['items'][0]['item'].__setitem__('modified',999),lambda v:v['details'][0]['native_metadata_uninterpreted']['alarmTriggers'].__setitem__('dateTriggers',[1])):
   bad=copy.deepcopy(value);modify(bad);self.assertFalse(module.record_matches(item,bad))
 def test_terminal_error_can_observe_expected_state_without_replay_and_forgery_refuses(self):
  from icloud_for_agents.native_reminder_exchange import begin,finish
  apply=self.adapter.apply
  def uncertain(item):apply(item);finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic terminal error')
  p=self.engine.prepare_once([self.change()])
  with patch.object(self.adapter,'apply',side_effect=uncertain):self.engine.execute(p['plan_id'])
  original=self.store.path(p['plan_id'],'.result').read_bytes();self.assertEqual(self.engine.reconcile_known_changes(p['plan_id'])['status'],'applied_after_readback_reconciliation');self.engine.assert_settled();self.assertEqual(len(self.adapter.calls),1)
  path=self.store.path(p['plan_id'],'.native-lifecycle-readback');v=self.store.read(path);v['observed']['details'][0]['notes']='forged';v['observed_sha256']=digest(v['observed']);path.write_text(json.dumps(v));self.assertFalse(self.store._native_lifecycle_readback_completed(p['plan_id']));self.assertEqual(self.store.path(p['plan_id'],'.result').read_bytes(),original)
 def test_unchanged_terminal_error_retained_review_no_legacy_and_no_replay(self):
  from icloud_for_agents.native_reminder_exchange import begin,finish
  from icloud_for_agents.native_reminder_recovery import ReminderObservationRecovery
  def uncertain(item):finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic unchanged terminal error')
  p=self.engine.prepare_once([self.change()])
  with patch.object(self.adapter,'apply',side_effect=uncertain):self.engine.execute(p['plan_id'])
  r=ReminderObservationRecovery(self.engine.account,self.store,self.adapter)
  with self.assertRaises(SafetyError):r.prepare(p['plan_id'],legacy_missing_terminal=True)
  q=r.prepare(p['plan_id']);self.assertIn('not proven absent globally',q['review']['payload']['effects']['template_creation_coverage']);r.execute(q['plan_id']);self.engine.assert_settled()
  with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])

 def test_many_root_copies_charge_every_object_and_refuse_over_verified_bound(self):
  reader=self.adapter.reader;base=copy.deepcopy(reader.items[ROOT]);reader.items={};reader.parents={};reader.flags={}
  for n in range(19):
   key=str(uuid.uuid5(uuid.NAMESPACE_URL,'synthetic-template-source-'+str(n))).upper();reader.items[key]=dict(base,id=key,title='Synthetic source '+str(n));reader.parents[key]=None;reader.flags[key]=False
  p=self.engine.prepare_once([self.change()]);self.assertEqual(p['review']['payload']['changes'][0]['effects']['quota_objects'],20);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');self.engine.assert_settled()
  key=str(uuid.uuid5(uuid.NAMESPACE_URL,'synthetic-template-over-bound')).upper();reader.items[key]=dict(base,id=key,title='Synthetic over bound');reader.parents[key]=None;reader.flags[key]=False
  with self.assertRaises(SafetyError):self.engine.prepare_once([self.change('Too large')])
