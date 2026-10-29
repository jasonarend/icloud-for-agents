"""Custom grocery categories preserve task state and exact approval/evidence scopes."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from test_native_grocery_mode import Reader as ModeReader
from test_native_reminder_moves import Reader as TaskReader,SOURCE,ORIGIN,DEST,ROOT
from icloud_for_agents import native_grocery_categories as module
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError,digest
from icloud_for_agents.native_access import require_write
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.native_grants import NativeGrants
from icloud_for_agents.native_protocol import mutation_request
from icloud_for_agents.native_reminders import RemindersCreateAdapter
class Reader(ModeReader):
 def __init__(self):
  super().__init__();self.enabled=True;row=copy.deepcopy(TaskReader().items[ROOT]);self.items={ROOT:row};self.parents={ROOT:None};self.flags={ROOT:False};self.order=[ROOT]
 def read(self,action,**fields):
  if action=='list_order':
   value=self.read('list_hierarchy',**fields);return dict(value,ordered_ids=list(self.order))
  return super().read(action,**fields)
class Adapter(RemindersCreateAdapter):
 backend_id='synthetic-grocery-category'
 def __init__(self):self.reader=Reader();self.calls=[]
 def apply(self,item):
  require_write('reminders',item);self.calls.append(item['operation']);self.reader.sections=sorted(self.reader.sections+[{'id':item['id'],'title':item['fields']['title']}],key=lambda v:v['id']);return {'source_id':item['source_id'],'id':item['id'],'list_id':item['list_id'],'saved_locally':True}
class GroceryCategoryTests(unittest.TestCase):
 def setUp(self):
  t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.store=PlanStore(Path(t.name)/'runtime');self.adapter=Adapter();self.engine=NativeChanges('reminders','synthetic@example.com',self.adapter,self.store)
  p=patch.dict(os.environ,{'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'source_ids':[SOURCE],'access_mode':'read-only','advanced_backend':'reminderkit','helper_path':'/synthetic/helper'}})},clear=True);p.start();self.addCleanup(p.stop)
 def change(self,title='Synthetic custom grocery category'):return {'operation':'create_grocery_category','source_id':SOURCE,'list_id':ORIGIN,'fields':{'title':title}}
 def scope(self):return {'scenario':'Synthetic custom grocery organization','rules':[{'operation':'create_grocery_category','source_id':SOURCE,'container_id':ORIGIN,'fields':['title'],'destinations':[]}],'max_batch':1,'max_24h':2,'expires':None}
 def test_once_preassigned_category_and_source_content_order_preserved(self):
  before=copy.deepcopy(self.adapter.reader.items)
  with self.assertRaises(SafetyError):self.engine.prepare([self.change()])
  p=self.engine.prepare_once([self.change()]);item=p['review']['payload']['changes'][0];self.assertEqual(mutation_request('reminders',item)['change']['before'],item['before']);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');self.assertEqual(self.adapter.reader.items,before);self.assertEqual(self.adapter.reader.order,[ROOT]);self.engine.assert_settled()
  with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
 def test_exact_scope_quota_and_revocation(self):
  m=NativeGrants(self.engine);g=m.prepare(self.scope())['plan_id'];m.activate(g)
  with self.assertRaises(SafetyError):self.engine.prepare_with_grant(g,[dict(self.change(),list_id=DEST)])
  for title in('Synthetic grocery one','Synthetic grocery two'):
   p=self.engine.prepare_with_grant(g,[self.change(title)]);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');self.assertEqual(self.store.read(self.store.path(p['plan_id'],'.native-grant-use'))['count'],1)
  p=self.engine.prepare_with_grant(g,[self.change('Over quota')])
  with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
  m.revoke(g)
  with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
 def test_strict_synthetic_receipt_and_duplicate_or_bad_title_refusal(self):
  for title in('',True,'\n','\ud800','a'*1001):
   with self.assertRaises(SafetyError):self.engine.prepare_once([self.change(title)])
  os.environ['ICLOUD_APPROVAL_POLICY']='local-receipt';p=self.engine.prepare_once([self.change()])
  with self.assertRaises(FileNotFoundError):self.engine.execute(p['plan_id'])
  self.store.write_once(self.store.path(p['plan_id'],'.approved'),{'plan_id':p['plan_id'],'sha256':p['sha256']});self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied')
  with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
 def test_ordinary_shared_shopping_completed_recurring_and_nested_source_refuse(self):
  for key,value in(('enabled',False),('shared',True),('shopping',True)):
   old=getattr(self.adapter.reader,key);setattr(self.adapter.reader,key,value)
   with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
   setattr(self.adapter.reader,key,old)
  for key,value in(('completed',True),('has_recurrence',True)):
   old=self.adapter.reader.items[ROOT][key];self.adapter.reader.items[ROOT][key]=value
   with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
   self.adapter.reader.items[ROOT][key]=old
  self.adapter.reader.parents[ROOT]=DEST
  with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
  self.assertEqual(self.adapter.calls,[])
 def test_drift_blocks_dispatch_and_body_loss_or_wrong_category_readback_refuses(self):
  p=self.engine.prepare_once([self.change()]);self.adapter.reader.items[ROOT]['notes']='Concurrent text'
  with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
  self.assertFalse(self.store.path(p['plan_id'],'.started').exists());p=self.engine.prepare_once([self.change()]);item=p['review']['payload']['changes'][0];self.engine.execute(p['plan_id']);value=module.expected_observation(self.adapter,item)
  for mutate in(lambda v:v['snapshot']['items'][0]['item'].__setitem__('notes','lost'),lambda v:v['snapshot']['sections']['sections'][0].__setitem__('title','wrong'),lambda v:v['snapshot']['features'].__setitem__('shared',True)):
   bad=copy.deepcopy(value);mutate(bad);self.assertFalse(module.record_matches(item,bad))
 def test_terminal_error_expected_readback_retains_original_failure_and_no_replay(self):
  from icloud_for_agents.native_reminder_exchange import begin,finish
  apply=self.adapter.apply
  def uncertain(item):apply(item);finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic terminal error')
  p=self.engine.prepare_once([self.change()])
  with patch.object(self.adapter,'apply',side_effect=uncertain):self.engine.execute(p['plan_id'])
  original=self.store.path(p['plan_id'],'.result').read_bytes();self.engine.reconcile_known_changes(p['plan_id']);self.engine.assert_settled();self.assertEqual(self.store.path(p['plan_id'],'.result').read_bytes(),original);self.assertEqual(len(self.adapter.calls),1)
  path=self.store.path(p['plan_id'],'.native-lifecycle-readback');v=self.store.read(path);v['observed']['snapshot']['items'][0]['item']['notes']='forged';v['observed_sha256']=digest(v['observed']);path.write_text(json.dumps(v));self.assertFalse(self.store._native_lifecycle_readback_completed(p['plan_id']))
 def test_unchanged_error_review_cannot_use_legacy_missing_evidence_or_replay(self):
  from icloud_for_agents.native_reminder_exchange import begin,finish
  from icloud_for_agents.native_reminder_recovery import ReminderObservationRecovery
  def unchanged(item):finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic unchanged terminal error')
  p=self.engine.prepare_once([self.change()])
  with patch.object(self.adapter,'apply',side_effect=unchanged):self.engine.execute(p['plan_id'])
  manager=ReminderObservationRecovery(self.engine.account,self.store,self.adapter)
  with self.assertRaises(SafetyError):manager.prepare(p['plan_id'],legacy_missing_terminal=True)
  q=manager.prepare(p['plan_id']);self.assertIn('not proven absent globally',q['review']['payload']['effects']['grocery_category_creation_coverage']);manager.execute(q['plan_id']);self.engine.assert_settled()
  with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
