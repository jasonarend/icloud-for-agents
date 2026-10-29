"""Existing leaf reparenting, root promotion, family fidelity and quota authority."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import test_native_reminder_moves as f
from test_native_reminder_order import Reader as OrderReader,ANCHOR
from icloud_for_agents.native_reminders import RemindersCreateAdapter
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.native_grants import NativeGrants
from icloud_for_agents.native_protocol import mutation_request
from icloud_for_agents.native_access import require_write
from icloud_for_agents.native_reminder_parents import reordered,charge,record_matches
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError
SIBLING='77777777-7777-4777-8777-777777777777'
class Reader(OrderReader):
 def __init__(self):
  super().__init__();self.items[SIBLING]=dict(self.items[f.CHILD],id=SIBLING,title='Synthetic sibling');self.parents[SIBLING]=f.ROOT;self.flags[SIBLING]=False;self.order=[f.ROOT,f.CHILD,SIBLING,ANCHOR]
 def read(self,action,**fields):
  value=super().read(action,**fields)
  if action=='get' and fields.get('id')==f.CHILD and 'native_metadata' in value['item'] and fields.get('include_native_metadata'):
   value['item']['native_metadata'].update(has_subtasks=None,children_status='unavailable')
  return value
class Adapter(RemindersCreateAdapter):
 backend_id='synthetic-reminder-reparent'
 def __init__(self):self.reader=Reader();self.calls=[]
 def apply(self,item):
  require_write('reminders',item);wire=mutation_request('reminders',item)['change'];assert set(wire['before'])=={'source_scope','source_graph','items'};self.calls.append(item['id']);self.reader.parents[item['id']]=item['fields']['parent_id'];self.reader.order=reordered(self.reader.order,item['id'],item['fields']);return {'id':item['id'],'source_id':item['source_id'],'list_id':item['list_id']}
class ParentTests(unittest.TestCase):
 def setUp(self):
  t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.adapter=Adapter();self.store=PlanStore(Path(t.name)/'runtime');p=patch.dict(os.environ,{'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'access_mode':'read-only','source_ids':[f.SOURCE],'advanced_backend':'reminderkit'}})},clear=True);p.start();self.addCleanup(p.stop);self.engine=NativeChanges('reminders','synthetic@example.com',self.adapter,self.store)
 def change(self,restore=False):return {'operation':'reparent_reminder','source_id':f.SOURCE,'id':f.CHILD,'fields':{'parent_id':f.ROOT if restore else ANCHOR,'anchor_id':SIBLING if restore else None,'placement':'before' if restore else 'end'}}
 def test_once_existing_leaf_roundtrip_restores_graph_order_and_all_contents(self):
  before=copy.deepcopy(self.adapter.reader.items);parents=copy.deepcopy(self.adapter.reader.parents);order=list(self.adapter.reader.order)
  for restore in (False,True):
   p=self.engine.prepare_once([self.change(restore)]);self.assertEqual(p['review']['payload']['changes'][0]['effects']['quota_objects'],3);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied')
   with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
  self.assertEqual(self.adapter.reader.items,before);self.assertEqual(self.adapter.reader.parents,parents);self.assertEqual(self.adapter.reader.order,order)
 def test_same_root_parent_sibling_order_once_and_grant_charge_unique_parent(self):
  before=copy.deepcopy(self.adapter.reader.items);parents=copy.deepcopy(self.adapter.reader.parents);order=list(self.adapter.reader.order)
  def change(after):return dict(self.change(True),fields={'parent_id':f.ROOT,'anchor_id':SIBLING,'placement':'after' if after else 'before'})
  for after in (True,False):
   p=self.engine.prepare_once([change(after)]);self.assertEqual(p['review']['payload']['changes'][0]['effects']['quota_objects'],2);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied')
  manager=NativeGrants(self.engine);scope=self.scope(max_batch=2,max_24h=4);g=manager.prepare(scope)['plan_id'];manager.activate(g)
  for after in (True,False):
   p=self.engine.prepare_with_grant(g,[change(after)]);self.assertEqual(manager.batch_count(p['review']['payload']['changes']),2);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied')
  manager.revoke(g);self.assertEqual(self.adapter.reader.items,before);self.assertEqual(self.adapter.reader.parents,parents);self.assertEqual(self.adapter.reader.order,order)
 def test_promotion_and_root_reattachment_preserve_unknown_child_presence(self):
  change=dict(self.change(),fields={'parent_id':None,'anchor_id':ANCHOR,'placement':'before'});p=self.engine.prepare_once([change]);self.assertEqual(p['review']['payload']['changes'][0]['effects']['quota_objects'],2);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');self.assertIsNone(self.adapter.reader.parents[f.CHILD]);p=self.engine.prepare_once([self.change(True)]);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied')
 def test_noop_cycle_descendants_nested_parent_and_wrong_anchor_refuse(self):
  changes=(self.change(True),dict(self.change(),id=f.ROOT),dict(self.change(),fields={'parent_id':f.CHILD,'anchor_id':None,'placement':'end'}),dict(self.change(),fields={'parent_id':SIBLING,'anchor_id':None,'placement':'end'}),dict(self.change(),fields={'parent_id':ANCHOR,'anchor_id':SIBLING,'placement':'after'}),dict(self.change(),fields={'parent_id':ANCHOR,'anchor_id':None,'placement':'before'}),dict(self.change(),source_id='foreign'))
  for value in changes:
   with self.assertRaises(SafetyError):self.engine.prepare_once([value])
  self.assertEqual(self.adapter.calls,[])
 def test_completed_recurring_shared_and_sectioned_family_refuse(self):
  for identity,key,value in ((f.CHILD,'completed',True),(f.ROOT,'has_recurrence',True),(ANCHOR,'has_recurrence',True)):
   before=self.adapter.reader.items[identity][key];self.adapter.reader.items[identity][key]=value
   with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
   self.adapter.reader.items[identity][key]=before
  self.adapter.reader.shared=True
  with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
 def test_expected_recovery_rejects_changed_content_family_and_graph(self):
  from icloud_for_agents.native_reminder_parents import expected_observation
  plan=self.engine.prepare_once([self.change()]);item=plan['review']['payload']['changes'][0];self.assertEqual(self.engine.execute(plan['plan_id'])['status'],'applied');observed=expected_observation(self.adapter,item);self.assertTrue(record_matches(item,observed))
  for key,value in (('title','Different content'),('due',{'year':2030,'month':1,'day':1}),('alarms',[{'unreviewed':True}])):
   forged=copy.deepcopy(observed);forged['snapshot']['items'][0]['item'][key]=value;self.assertFalse(record_matches(item,forged))
  forged=copy.deepcopy(observed);forged['snapshot']['items'][0]['native_metadata']['flagged']=not forged['snapshot']['items'][0]['native_metadata']['flagged'];self.assertFalse(record_matches(item,forged))
  forged=copy.deepcopy(observed);forged['snapshot']['items'][0]['native_metadata']['child_ids']=[];self.assertFalse(record_matches(item,forged))
  forged=copy.deepcopy(observed);forged['snapshot']['items'].reverse();self.assertFalse(record_matches(item,forged))
  forged=copy.deepcopy(observed);forged['snapshot']['source_graph']['ordered_ids'].reverse();self.assertFalse(record_matches(item,forged))
 def test_sectioned_family_and_stale_order_refuse(self):
  read=self.adapter.reader.read
  def sectioned(action,**fields):
   value=read(action,**fields)
   if action=='get' and fields.get('id')==f.ROOT and fields.get('include_native_metadata'):value['item']['native_metadata'].update(section={'id':'synthetic-section'},section_status='provider_reported')
   return value
  with patch.object(self.adapter.reader,'read',side_effect=sectioned):
   with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
  p=self.engine.prepare_once([self.change()]);self.adapter.reader.order.reverse()
  with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
  self.assertEqual(self.adapter.calls,[])
 def test_stale_parent_content_and_manual_sequence_refuse_before_dispatch(self):
  p=self.engine.prepare_once([self.change()]);self.adapter.reader.items[f.ROOT]['notes']='Concurrent parent edit'
  with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
  self.assertFalse(self.store.path(p['plan_id'],'.started').exists());self.assertEqual(self.adapter.calls,[])
 def scope(self,**extra):return {'scenario':'Synthetic parent families','rules':[{'operation':'reparent_reminder','source_id':f.SOURCE,'container_id':f.ORIGIN,'fields':['parent_id','anchor_id','placement'],'destinations':[f.ROOT,ANCHOR,f.ORIGIN]}],'max_batch':3,'max_24h':6,'expires':None,**extra}
 def test_grants_bind_parent_destinations_count_both_parents_and_revoke(self):
  m=NativeGrants(self.engine);small=m.prepare(self.scope(max_batch=2))['plan_id'];m.activate(small)
  with self.assertRaises(SafetyError):self.engine.prepare_with_grant(small,[self.change()])
  m.revoke(small);g=m.prepare(self.scope())['plan_id'];m.activate(g)
  for restore in (False,True):
   p=self.engine.prepare_with_grant(g,[self.change(restore)]);self.assertEqual(m.batch_count(p['review']['payload']['changes']),3);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');self.assertEqual(m.status(g)['status'],'active')
  p=self.engine.prepare_with_grant(g,[self.change()]);m.revoke(g)
  with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
  item=p['review']['payload']['changes'][0];item['family_ids'].remove(f.ROOT)
  with self.assertRaises(SafetyError):charge(item)
 def test_grant_without_destination_or_with_nonroot_destination_refuses(self):
  scope=self.scope();scope['rules'][0]['destinations']=[SIBLING]
  with self.assertRaises(SafetyError):NativeGrants(self.engine).prepare(scope)
  scope['rules'][0]['destinations']=[]
  with self.assertRaises(SafetyError):NativeGrants(self.engine).prepare(scope)
 def test_returned_id_and_retained_terminal_error_reconcile_without_replay(self):
  from icloud_for_agents.native_reminder_exchange import begin,finish
  with patch.object(self.adapter,'verify',return_value=False):
   p=self.engine.prepare_once([self.change()]);self.engine.execute(p['plan_id'])
  self.assertEqual(self.engine.reconcile_known_changes(p['plan_id'])['status'],'applied_after_readback_reconciliation');apply=self.adapter.apply
  def uncertain(item):apply(item);finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic post-save error')
  with patch.object(self.adapter,'apply',side_effect=uncertain):
   p=self.engine.prepare_once([self.change(True)]);self.engine.execute(p['plan_id'])
  original=self.store.path(p['plan_id'],'.result').read_bytes();self.assertEqual(self.engine.reconcile_known_changes(p['plan_id'])['status'],'applied_after_readback_reconciliation');self.engine.assert_settled();self.assertEqual(original,self.store.path(p['plan_id'],'.result').read_bytes());self.assertEqual(len(self.adapter.calls),2)
 def test_unchanged_terminal_error_requires_review_and_retained_exchange(self):
  from icloud_for_agents.native_reminder_exchange import begin,finish
  from icloud_for_agents.native_reminder_recovery import ReminderObservationRecovery
  def unchanged(item):finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic unchanged error')
  with patch.object(self.adapter,'apply',side_effect=unchanged):
   p=self.engine.prepare_once([self.change()]);self.engine.execute(p['plan_id'])
  m=ReminderObservationRecovery(self.engine.account,self.store,self.adapter)
  with self.assertRaises(SafetyError):m.prepare(p['plan_id'],legacy_missing_terminal=True)
  review=m.prepare(p['plan_id']);self.assertEqual(m.execute(review['plan_id'])['recovery_state'],'observed_unchanged_reminder_accepted_after_review');self.engine.assert_settled();self.assertEqual(self.adapter.calls,[])
