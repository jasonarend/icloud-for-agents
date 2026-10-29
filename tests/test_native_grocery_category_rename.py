"""Exact grocery category naming preserves membership and scoped authority."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from test_native_grocery_categories import Reader as BaseReader,SOURCE,ORIGIN,DEST,ROOT
from icloud_for_agents import native_grocery_category_rename as module
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_access import require_write
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.native_grants import NativeGrants
from icloud_for_agents.native_protocol import mutation_request
from icloud_for_agents.native_reminders import RemindersCreateAdapter
CATEGORY='77777777-7777-4777-8777-777777777777';OTHER='88888888-8888-4888-8888-888888888888'
class Reader(BaseReader):
 def __init__(self):super().__init__();self.sections=[{'id':CATEGORY,'title':'Synthetic produce'},{'id':OTHER,'title':'Synthetic pantry'}]
 def read(self,action,**fields):
  result=super().read(action,**fields)
  if action=='list_sections'and fields.get('list_id')==DEST:result['sections']=[]
  if action=='list_order'and fields.get('list_id')==DEST:result['ordered_ids']=[]
  if action=='get'and fields.get('include_native_metadata'):result['item']['native_metadata'].update(section=copy.deepcopy(self.sections[0]),section_status='provider_reported')
  return result
class Adapter(RemindersCreateAdapter):
 backend_id='synthetic-grocery-category-rename'
 def __init__(self):self.reader=Reader();self.calls=[]
 def apply(self,item):
  require_write('reminders',item);assert mutation_request('reminders',item)['change']['before']==item['before'];self.calls.append(item['id']);next(v for v in self.reader.sections if v['id']==item['id'])['title']=item['fields']['title'];return {'source_id':item['source_id'],'list_id':item['list_id'],'id':item['id'],'saved_locally':True}
class GroceryCategoryRenameTests(unittest.TestCase):
 def setUp(self):
  t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.store=PlanStore(Path(t.name)/'runtime');self.adapter=Adapter();self.engine=NativeChanges('reminders','synthetic@example.com',self.adapter,self.store)
  p=patch.dict(os.environ,{'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'source_ids':[SOURCE],'access_mode':'read-only','advanced_backend':'reminderkit','helper_path':'/synthetic/helper'}})},clear=True);p.start();self.addCleanup(p.stop)
 def change(self,title='Synthetic fresh produce'):return {'operation':'rename_grocery_category','source_id':SOURCE,'list_id':ORIGIN,'id':CATEGORY,'fields':{'title':title}}
 def scope(self,targets=None):return {'scenario':'Synthetic grocery category naming','rules':[{'operation':'rename_grocery_category','source_id':SOURCE,'container_id':ORIGIN,'fields':['title'],'destinations':targets or [CATEGORY]}],'max_batch':1,'max_24h':2,'expires':None}
 def test_once_round_trip_preserves_tasks_membership_order_and_categories(self):
  before=copy.deepcopy(self.adapter.reader.items)
  for title in('Synthetic fresh produce','Synthetic produce'):
   p=self.engine.prepare_once([self.change(title)]);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');self.assertEqual(self.adapter.reader.items,before);self.engine.assert_settled()
   with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
 def test_exact_category_grant_quota_and_revocation(self):
  m=NativeGrants(self.engine);g=m.prepare(self.scope())['plan_id'];m.activate(g)
  with self.assertRaises(SafetyError):self.engine.prepare_with_grant(g,[dict(self.change(),id=OTHER)])
  for title in('Synthetic one','Synthetic two'):
   p=self.engine.prepare_with_grant(g,[self.change(title)]);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');self.assertEqual(self.store.read(self.store.path(p['plan_id'],'.native-grant-use'))['count'],1)
  pending=self.engine.prepare_with_grant(g,[self.change('Synthetic over cap')])
  with self.assertRaises(SafetyError):self.engine.execute(pending['plan_id'])
  m.revoke(g)
  with self.assertRaises(SafetyError):self.engine.execute(pending['plan_id'])
 def test_invalid_foreign_noop_duplicate_titles_and_strict_approval(self):
  for change in(self.change('Synthetic produce'),self.change('Synthetic pantry'),self.change(''),self.change('\n'),self.change('\ud800'),dict(self.change(),id=DEST),dict(self.change(),list_id=DEST),dict(self.change(),source_id=DEST),dict(self.change(),fields={'title':'New','extra':True})):
   with self.assertRaises(SafetyError):self.engine.prepare_once([change])
  os.environ['ICLOUD_APPROVAL_POLICY']='local-receipt';p=self.engine.prepare_once([self.change()])
  with self.assertRaises(FileNotFoundError):self.engine.execute(p['plan_id'])
  self.store.write_once(self.store.path(p['plan_id'],'.approved'),{'plan_id':p['plan_id'],'sha256':p['sha256']});self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied')
 def test_ordinary_shared_shopping_completed_recurring_and_nested_refuse(self):
  for key,value in(('enabled',False),('shared',True),('shopping',True)):
   old=getattr(self.adapter.reader,key);setattr(self.adapter.reader,key,value)
   with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
   setattr(self.adapter.reader,key,old)
  for key in('completed','has_recurrence'):
   self.adapter.reader.items[ROOT][key]=True
   with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
   self.adapter.reader.items[ROOT][key]=False
  self.adapter.reader.parents[ROOT]=DEST
  with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
 def test_changed_task_category_inventory_and_readback_refuse(self):
  for mode in('body','category'):
   state=copy.deepcopy(self.adapter.reader.__dict__);p=self.engine.prepare_once([self.change()])
   if mode=='body':self.adapter.reader.items[ROOT]['notes']='Concurrent edit'
   else:self.adapter.reader.sections[0]['title']='Concurrent category edit'
   with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
   self.assertFalse(self.store.path(p['plan_id'],'.started').exists());self.adapter.reader.__dict__.update(state)
  p=self.engine.prepare_once([self.change()]);item=p['review']['payload']['changes'][0];self.engine.execute(p['plan_id']);value=module.expected_observation(self.adapter,item);self.assertTrue(module.record_matches(item,value));bad=copy.deepcopy(value);bad['snapshot']['items'][0]['item']['notes']='Wrong content';self.assertFalse(module.record_matches(item,bad));bad=copy.deepcopy(value);bad['snapshot']['items'][0]['native_metadata']['section']['id']=OTHER;self.assertFalse(module.record_matches(item,bad))
 def test_retained_error_expected_state_settles_without_replay(self):
  from icloud_for_agents.native_reminder_exchange import begin,finish
  apply=self.adapter.apply
  def uncertain(item):apply(item);finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic retained error')
  with patch.object(self.adapter,'apply',side_effect=uncertain):p=self.engine.prepare_once([self.change()]);self.engine.execute(p['plan_id'])
  original=self.store.path(p['plan_id'],'.result').read_bytes();self.assertEqual(self.engine.reconcile_known_changes(p['plan_id'])['status'],'applied_after_readback_reconciliation');self.assertEqual(original,self.store.path(p['plan_id'],'.result').read_bytes());self.engine.assert_settled();self.assertEqual(self.adapter.calls,[CATEGORY])
 def test_unchanged_error_review_refuses_legacy_and_preserves_failure(self):
  from icloud_for_agents.native_reminder_exchange import begin,finish
  from icloud_for_agents.native_reminder_recovery import ReminderObservationRecovery
  def unchanged(item):finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic unchanged error')
  with patch.object(self.adapter,'apply',side_effect=unchanged):p=self.engine.prepare_once([self.change()]);self.engine.execute(p['plan_id'])
  original=self.store.path(p['plan_id'],'.result').read_bytes();m=ReminderObservationRecovery(self.engine.account,self.store,self.adapter)
  with self.assertRaises(SafetyError):m.prepare(p['plan_id'],legacy_missing_terminal=True)
  review=m.prepare(p['plan_id']);self.assertEqual(m.execute(review['plan_id'])['recovery_state'],'observed_unchanged_reminder_accepted_after_review');self.engine.assert_settled();self.assertEqual(self.adapter.calls,[]);self.assertEqual(original,self.store.path(p['plan_id'],'.result').read_bytes())
