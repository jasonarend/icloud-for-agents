"""Grocery category assignment preserves root content and exact scoped authority."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import test_native_reminder_moves as f
from test_native_reminder_order import Reader as OrderReader,ANCHOR
from icloud_for_agents.native_reminders import RemindersCreateAdapter
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.native_grants import NativeGrants
from icloud_for_agents.native_access import require_write
from icloud_for_agents.native_protocol import mutation_request
from icloud_for_agents.native_grocery_assignment import expected_observation,record_matches
from icloud_for_agents.native_reminder_section_store import memberships
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError
FIRST='77777777-7777-4777-8777-777777777777';SECOND='88888888-8888-4888-8888-888888888888'

class Reader(OrderReader):
 def __init__(self):
  super().__init__();self.parents={v:None for v in self.items};self.grocery=True;self.shopping=False;self.groups={f.ROOT:None,f.CHILD:None,ANCHOR:FIRST};self.sections=[{'id':FIRST,'title':'Synthetic first'},{'id':SECOND,'title':'Synthetic second'}]
  self.map={'schema_sha256':'synthetic-schema','list_revision':1,'value':{'minimumSupportedVersion':20230430,'memberships':[{'memberID':ANCHOR,'groupID':FIRST,'modifiedOn':100.0}]}}
 def read(self,action,**fields):
  if action=='list_sections':return {'protocol_version':1,'source_id':f.SOURCE,'list_id':f.ORIGIN,'sections':copy.deepcopy(self.sections),'data_mutated':False,'membership_verified':False,'ordering_verified':False}
  result=super().read(action,**fields)
  if action=='list_features':result.update(grocery_categorization=self.grocery,grocery_shopping_session=self.shopping)
  if action=='get' and fields.get('include_native_metadata'):
   group=self.groups[fields['id']];result['item']['native_metadata'].update(section=next((v for v in self.sections if v['id']==group),None),section_status='provider_reported' if group else 'provider_not_reported')
  return result
class Adapter(RemindersCreateAdapter):
 backend_id='synthetic-reminder-section'
 def __init__(self):self.reader=Reader();self.calls=[]
 def apply(self,item):
  require_write('reminders',item);wire=mutation_request('reminders',item)['change'];assert wire['before']==item['before'];self.calls.append(item['id']);target=item['fields']['category_id'];self.reader.groups[item['id']]=target;rows=self.reader.map['value']['memberships'];old=next((v for v in rows if v['memberID']==item['id']),None);new={'memberID':item['id'],'modifiedOn':(old or {}).get('modifiedOn',0)+1}
  if target:new['groupID']=target
  self.reader.map['value']['memberships']=sorted([v for v in rows if v['memberID']!=item['id']]+[new],key=lambda v:v['memberID']);self.reader.map['list_revision']+=1
  return {'id':item['id'],'source_id':item['source_id'],'list_id':item['list_id']}
class GroceryAssignmentTests(unittest.TestCase):
 def setUp(self):
  t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.adapter=Adapter();self.store=PlanStore(Path(t.name)/'runtime')
  p=patch.dict(os.environ,{'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'source_ids':[f.SOURCE],'access_mode':'read-only','advanced_backend':'reminderkit','metadata_backend':'reminders-store-readonly','helper_path':'/synthetic/helper'}})},clear=True);p.start();self.addCleanup(p.stop)
  for target in ('icloud_for_agents.native_grocery_assignment.read_store','icloud_for_agents.native_reminder_section_store.read'):
   p=patch(target,side_effect=lambda source,list_id:copy.deepcopy(self.adapter.reader.map));p.start();self.addCleanup(p.stop)
  self.engine=NativeChanges('reminders','synthetic@example.com',self.adapter,self.store)
 def change(self,section=SECOND,**extra):return dict(operation='set_grocery_category',source_id=f.SOURCE,id=ANCHOR,fields={'category_id':section},**extra)
 def scope(self,destinations=None):return {'scenario':'Synthetic section organization','rules':[{'operation':'set_grocery_category','source_id':f.SOURCE,'container_id':f.ORIGIN,'fields':['category_id'],'destinations':destinations or [FIRST,SECOND,f.ORIGIN]}],'max_batch':1,'max_24h':3,'expires':None}
 def test_once_between_remove_and_restore_preserves_all_content_graph_and_native_order(self):
  before=copy.deepcopy(self.adapter.reader.items);order=list(self.adapter.reader.order);parents=dict(self.adapter.reader.parents)
  for group in (SECOND,None,FIRST):
   p=self.engine.prepare_once([self.change(group)]);self.assertEqual(p['review']['payload']['changes'][0]['effects']['quota_objects'],1);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied')
   with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
  self.assertEqual(before,self.adapter.reader.items);self.assertEqual(order,self.adapter.reader.order);self.assertEqual(parents,self.adapter.reader.parents)
 def test_grant_exact_sections_root_removal_quota_and_revocation(self):
  m=NativeGrants(self.engine);g=m.prepare(self.scope([SECOND]))['plan_id'];m.activate(g)
  with self.assertRaises(SafetyError):self.engine.prepare_with_grant(g,[self.change(None)])
  p=self.engine.prepare_with_grant(g,[self.change()]);m.revoke(g)
  with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
  g=m.prepare(self.scope())['plan_id'];m.activate(g)
  for group in (SECOND,None,FIRST):
   p=self.engine.prepare_with_grant(g,[self.change(group)]);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied')
  p=self.engine.prepare_with_grant(g,[self.change()])
  with self.assertRaisesRegex(SafetyError,'24-hour'):self.engine.execute(p['plan_id'])
  m.revoke(g)
 def test_foreign_sections_recurring_completed_parent_and_child_targets_refuse(self):
  for change in (self.change(FIRST),self.change(f.DEST),dict(self.change(),source_id=f.DEST),dict(self.change(),fields={'category_id':SECOND,'title':'extra'})):
   with self.assertRaises(SafetyError):self.engine.prepare_once([change])
  for key in ('completed','has_recurrence'):
   self.adapter.reader.items[ANCHOR][key]=True
   with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
   self.adapter.reader.items[ANCHOR][key]=False
  self.adapter.reader.shared=True
  with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
  self.assertEqual(self.adapter.calls,[])
 def test_stale_bystander_map_section_inventory_and_order_refuse_before_dispatch(self):
  for mutation in ('body','map','section','order'):
   before=copy.deepcopy(self.adapter.reader.__dict__);p=self.engine.prepare_once([self.change()])
   if mutation=='body':self.adapter.reader.items[f.CHILD]['notes']='Concurrent edit'
   elif mutation=='map':self.adapter.reader.map['list_revision']+=1
   elif mutation=='section':self.adapter.reader.sections[0]['title']='Concurrent section rename'
   else:self.adapter.reader.order.reverse()
   with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
   self.assertFalse(self.store.path(p['plan_id'],'.started').exists());self.adapter.reader.__dict__.update(before)
  self.assertEqual(self.adapter.calls,[])
 def test_readback_refuses_bystander_timestamp_content_order_map_loss_and_wrong_target(self):
  p=self.engine.prepare_once([self.change()]);item=p['review']['payload']['changes'][0];self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');observed=expected_observation(self.adapter,item);self.assertTrue(record_matches(item,observed))
  for key,value in [('notes','Unexpected body'),('modified',999)]:
   bad=copy.deepcopy(observed);bad['snapshot']['items'][0]['item'][key]=value;self.assertFalse(record_matches(item,bad))
  bad=copy.deepcopy(observed);bad['snapshot']['source_graph']['ordered_ids'].reverse();self.assertFalse(record_matches(item,bad))
  bad=copy.deepcopy(observed);bad['snapshot']['membership_store']['value']['memberships'][0]['modifiedOn']=100;self.assertFalse(record_matches(item,bad))
  self.adapter.reader.map['value']['memberships'].append({'memberID':f.ROOT,'groupID':f.DEST,'modifiedOn':1})
  with self.assertRaises(SafetyError):expected_observation(self.adapter,item)
 def test_known_id_and_retained_terminal_error_recovery_do_not_replay(self):
  from icloud_for_agents.native_reminder_exchange import begin,finish
  with patch.object(self.adapter,'verify',return_value=False):p=self.engine.prepare_once([self.change()]);self.engine.execute(p['plan_id'])
  self.assertEqual(self.engine.reconcile_known_changes(p['plan_id'])['status'],'applied_after_readback_reconciliation');apply=self.adapter.apply
  def uncertain(item):apply(item);finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic terminal error')
  with patch.object(self.adapter,'apply',side_effect=uncertain):p=self.engine.prepare_once([self.change(FIRST)]);self.engine.execute(p['plan_id'])
  original=self.store.path(p['plan_id'],'.result').read_bytes();self.assertEqual(self.engine.reconcile_known_changes(p['plan_id'])['status'],'applied_after_readback_reconciliation');self.assertEqual(original,self.store.path(p['plan_id'],'.result').read_bytes());self.engine.assert_settled();self.assertEqual(len(self.adapter.calls),2)
 def test_grocery_roles_hierarchy_and_strict_approval_refuse(self):
  for key,value in (('grocery',False),('shopping',True)):
   old=getattr(self.adapter.reader,key);setattr(self.adapter.reader,key,value)
   with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
   setattr(self.adapter.reader,key,old)
  self.adapter.reader.parents[f.CHILD]=f.ROOT
  with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
  self.adapter.reader.parents[f.CHILD]=None
  os.environ['ICLOUD_APPROVAL_POLICY']='local-receipt';p=self.engine.prepare_once([self.change()])
  with self.assertRaises(FileNotFoundError):self.engine.execute(p['plan_id'])
  self.store.write_once(self.store.path(p['plan_id'],'.approved'),{'plan_id':p['plan_id'],'sha256':p['sha256']});self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied')
 def test_unchanged_terminal_error_requires_review_retained_exchange_and_never_replays(self):
  from icloud_for_agents.native_reminder_exchange import begin,finish
  from icloud_for_agents.native_reminder_recovery import ReminderObservationRecovery
  def unchanged(item):finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic unchanged error')
  with patch.object(self.adapter,'apply',side_effect=unchanged):p=self.engine.prepare_once([self.change()]);self.engine.execute(p['plan_id'])
  original=self.store.path(p['plan_id'],'.result').read_bytes();manager=ReminderObservationRecovery(self.engine.account,self.store,self.adapter)
  with self.assertRaises(SafetyError):manager.prepare(p['plan_id'],legacy_missing_terminal=True)
  review=manager.prepare(p['plan_id']);self.assertEqual(manager.execute(review['plan_id'])['recovery_state'],'observed_unchanged_reminder_accepted_after_review');self.engine.assert_settled();self.assertEqual(self.adapter.calls,[]);self.assertEqual(original,self.store.path(p['plan_id'],'.result').read_bytes())
  with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
  self.assertTrue(self.engine.prepare_once([self.change()])['plan_id'])
