"""Empty-list categorization preserves exact scope, approvals and error evidence."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from test_native_reminder_moves import Reader as BaseReader,SOURCE,ORIGIN,DEST,ROOT
from icloud_for_agents import native_grocery_mode as module
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError,digest
from icloud_for_agents.native_access import require_write
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.native_grants import NativeGrants
from icloud_for_agents.native_protocol import mutation_request
from icloud_for_agents.native_reminders import RemindersCreateAdapter
class Reader(BaseReader):
 def __init__(self):super().__init__();self.items={};self.parents={};self.enabled=False;self.shopping=False;self.sections=[]
 def read(self,action,**fields):
  if action=='list_sections':return {'protocol_version':1,'source_id':SOURCE,'list_id':fields['list_id'],'sections':copy.deepcopy(self.sections),'data_mutated':False}
  value=super().read(action,**fields)
  if action=='list_features':value.update(grocery_categorization=self.enabled,grocery_shopping_session=self.shopping)
  return value
class Adapter(RemindersCreateAdapter):
 backend_id='synthetic-grocery-mode'
 def __init__(self):self.reader=Reader();self.calls=[]
 def apply(self,item):require_write('reminders',item);self.calls.append(item['operation']);self.reader.enabled=item['fields']['enabled'];return {'id':item['id'],'source_id':item['source_id'],'saved_locally':True}
class GroceryModeTests(unittest.TestCase):
 def setUp(self):
  t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.store=PlanStore(Path(t.name)/'runtime');self.adapter=Adapter();self.engine=NativeChanges('reminders','synthetic@example.com',self.adapter,self.store)
  p=patch.dict(os.environ,{'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'access_mode':'read-only','source_ids':[SOURCE],'advanced_backend':'reminderkit','helper_path':'/synthetic/helper'}})},clear=True);p.start();self.addCleanup(p.stop)
 def change(self,enabled=True):return {'operation':'set_grocery_categorization','source_id':SOURCE,'list_id':ORIGIN,'fields':{'enabled':enabled}}
 def scope(self):return {'scenario':'Synthetic empty grocery flag','rules':[{'operation':'set_grocery_categorization','source_id':SOURCE,'container_id':ORIGIN,'fields':['enabled'],'destinations':[]}],'max_batch':1,'max_24h':2,'expires':None}
 def test_once_roundtrip_only_flag_changes_and_protocol_binds_before(self):
  with self.assertRaises(SafetyError):self.engine.prepare([self.change()])
  for enabled in(True,False):
   p=self.engine.prepare_once([self.change(enabled)]);item=p['review']['payload']['changes'][0];self.assertEqual(mutation_request('reminders',item)['change']['before'],item['before']);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');self.assertEqual(self.adapter.reader.enabled,enabled);self.engine.assert_settled()
   with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
 def test_scope_quota_and_revocation(self):
  m=NativeGrants(self.engine);g=m.prepare(self.scope())['plan_id'];m.activate(g)
  with self.assertRaises(SafetyError):self.engine.prepare_with_grant(g,[dict(self.change(),list_id=DEST)])
  for enabled in(True,False):self.assertEqual(self.engine.execute(self.engine.prepare_with_grant(g,[self.change(enabled)])['plan_id'])['status'],'applied')
  p=self.engine.prepare_with_grant(g,[self.change()])
  with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
  m.revoke(g)
  with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
 def test_strict_synthetic_receipt_no_op_and_bad_fields(self):
  for value in(False,1,'true',None):
   with self.assertRaises(SafetyError):self.engine.prepare_once([self.change(value)])
  os.environ['ICLOUD_APPROVAL_POLICY']='local-receipt';p=self.engine.prepare_once([self.change()])
  with self.assertRaises(FileNotFoundError):self.engine.execute(p['plan_id'])
  self.store.write_once(self.store.path(p['plan_id'],'.approved'),{'plan_id':p['plan_id'],'sha256':p['sha256']});self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied')
 def test_populated_sectioned_shopping_shared_or_foreign_lists_refuse(self):
  for attr,value in(('sections',[{'id':DEST,'title':'Synthetic section'}]),('shared',True),('shopping',True)):
   original=copy.deepcopy(getattr(self.adapter.reader,attr));setattr(self.adapter.reader,attr,value)
   with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
   setattr(self.adapter.reader,attr,original)
  self.adapter.reader.items[ROOT]={'id':ROOT,'source_id':SOURCE,'list_id':ORIGIN,'title':'Synthetic task','completed':False};self.adapter.reader.parents[ROOT]=None
  with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
  self.assertEqual(self.adapter.calls,[])
 def test_drift_refuses_before_dispatch_and_wrong_readback_does_not_pass(self):
  p=self.engine.prepare_once([self.change()]);self.adapter.reader.lists[0]['title']='Concurrent change'
  with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
  self.assertFalse(self.store.path(p['plan_id'],'.started').exists());self.assertEqual(self.adapter.calls,[])
  p=self.engine.prepare_once([self.change()]);item=p['review']['payload']['changes'][0];self.engine.execute(p['plan_id']);v=module.expected_observation(self.adapter,item);v['snapshot']['list']['title']='forged';self.assertFalse(module.record_matches(item,v))
 def test_error_readback_preserves_original_terminal_failure_without_replay(self):
  from icloud_for_agents.native_reminder_exchange import begin,finish
  apply=self.adapter.apply
  def uncertain(item):apply(item);finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic terminal error')
  p=self.engine.prepare_once([self.change()])
  with patch.object(self.adapter,'apply',side_effect=uncertain):self.engine.execute(p['plan_id'])
  original=self.store.path(p['plan_id'],'.result').read_bytes();self.engine.reconcile_known_changes(p['plan_id']);self.engine.assert_settled();self.assertEqual(self.store.path(p['plan_id'],'.result').read_bytes(),original);self.assertEqual(len(self.adapter.calls),1)
 def test_unchanged_terminal_review_rejects_legacy_missing_evidence_and_replay(self):
  from icloud_for_agents.native_reminder_exchange import begin,finish
  from icloud_for_agents.native_reminder_recovery import ReminderObservationRecovery
  def uncertain(item):finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic unchanged error')
  p=self.engine.prepare_once([self.change()])
  with patch.object(self.adapter,'apply',side_effect=uncertain):self.engine.execute(p['plan_id'])
  manager=ReminderObservationRecovery(self.engine.account,self.store,self.adapter)
  with self.assertRaises(SafetyError):manager.prepare(p['plan_id'],legacy_missing_terminal=True)
  q=manager.prepare(p['plan_id']);manager.execute(q['plan_id']);self.engine.assert_settled()
  with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
