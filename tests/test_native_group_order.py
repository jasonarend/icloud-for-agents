"""Root-group ordering scope, exact anchors, preservation and recovery."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from test_native_list_groups import Reader,SOURCE,LIST,GROUP,ANCHOR
from icloud_for_agents.native_reminders import RemindersCreateAdapter
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.native_grants import NativeGrants
from icloud_for_agents.native_access import require_write
from icloud_for_agents.native_protocol import mutation_request
from icloud_for_agents.native_group_order import expected_snapshot
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError
class Adapter(RemindersCreateAdapter):
 backend_id='synthetic-root-group-order'
 def __init__(self):self.reader=Reader();self.calls=[]
 def apply(self,item):
  require_write('reminders',item);wire=mutation_request('reminders',item)['change'];assert set(wire['before'])=={'inventory','children'};self.calls.append(item['id']);self.reader.inventory=expected_snapshot(item)['inventory'];return {'id':item['id'],'source_id':item['source_id']}
class GroupOrderTests(unittest.TestCase):
 def setUp(self):
  t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.store=PlanStore(Path(t.name)/'runtime');self.adapter=Adapter();p=patch.dict(os.environ,{'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'access_mode':'read-only','source_ids':[SOURCE],'advanced_backend':'reminderkit'}})},clear=True);p.start();self.addCleanup(p.stop);self.engine=NativeChanges('reminders','synthetic@example.com',self.adapter,self.store)
 def change(self,restore=False):return {'operation':'reorder_group','source_id':SOURCE,'group_id':GROUP,'fields':{'anchor_id':LIST if restore else ANCHOR,'placement':'before' if restore else 'after'}}
 def test_once_roundtrip_restores_entire_account_sequence_and_keeps_contents(self):
  before=copy.deepcopy(self.adapter.reader.__dict__)
  for restore in (False,True):
   p=self.engine.prepare_once([self.change(restore)]);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied')
   with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
  self.assertEqual(before,self.adapter.reader.__dict__);self.assertEqual(os.environ['ICLOUD_MAIL_ACCESS'],'read-only')
 def test_noop_self_foreign_missing_anchor_and_unfinished_children_refuse(self):
  for change in (self.change(True),dict(self.change(),source_id=LIST),dict(self.change(),fields={'anchor_id':GROUP,'placement':'after'}),dict(self.change(),fields={'anchor_id':ANCHOR,'placement':'index'})):
   with self.assertRaises(SafetyError):self.engine.prepare_once([change])
  self.adapter.reader.children['done']=False
  with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
  self.assertEqual(self.adapter.calls,[])
 def test_account_order_and_child_view_drift_prevent_dispatch(self):
  for name,key,value in (('inventory','account_order',[LIST,GROUP,ANCHOR]),('children','lists',[{'id':LIST,'kind':'list','is_group':False,'title':'Concurrent child'}])):
   before=copy.deepcopy(getattr(self.adapter.reader,name));p=self.engine.prepare_once([self.change()]);getattr(self.adapter.reader,name)[key]=value
   with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
   self.assertFalse(self.store.path(p['plan_id'],'.started').exists());setattr(self.adapter.reader,name,before)
 def test_ongoing_exact_anchors_survive_own_order_changes_and_revoke(self):
  m=NativeGrants(self.engine);g=m.prepare({'scenario':'Synthetic group order','rules':[{'operation':'reorder_group','source_id':SOURCE,'container_id':GROUP,'fields':['anchor_id','placement'],'destinations':[ANCHOR,LIST]}],'max_batch':1,'max_24h':2,'expires':None})['plan_id'];m.activate(g)
  for restore in (False,True):
   p=self.engine.prepare_with_grant(g,[self.change(restore)]);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');self.assertEqual(m.status(g)['status'],'active')
  p=self.engine.prepare_with_grant(g,[self.change()]);m.revoke(g)
  with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
 def test_returned_id_and_terminal_error_reconcile_current_sequence_without_replay(self):
  from icloud_for_agents.native_reminder_exchange import begin,finish
  with patch.object(self.adapter,'verify',return_value=False):
   p=self.engine.prepare_once([self.change()]);self.engine.execute(p['plan_id'])
  self.assertEqual(self.engine.reconcile_known_changes(p['plan_id'])['status'],'applied_after_readback_reconciliation');apply=self.adapter.apply
  def uncertain(item):apply(item);finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic post-save terminal error')
  with patch.object(self.adapter,'apply',side_effect=uncertain):
   p=self.engine.prepare_once([self.change(True)]);self.engine.execute(p['plan_id'])
  original=self.store.path(p['plan_id'],'.result').read_bytes();self.assertEqual(self.engine.reconcile_known_changes(p['plan_id'])['status'],'applied_after_readback_reconciliation');self.engine.assert_settled();self.assertEqual(original,self.store.path(p['plan_id'],'.result').read_bytes());self.assertEqual(len(self.adapter.calls),2)
 def test_unchanged_terminal_error_requires_review_and_rechecks_children(self):
  from icloud_for_agents.native_reminder_exchange import begin,finish
  from icloud_for_agents.native_reminder_recovery import ReminderObservationRecovery
  def unchanged(item):finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic unchanged error')
  with patch.object(self.adapter,'apply',side_effect=unchanged):
   p=self.engine.prepare_once([self.change()]);self.engine.execute(p['plan_id'])
  m=ReminderObservationRecovery(self.engine.account,self.store,self.adapter)
  with self.assertRaises(SafetyError):m.prepare(p['plan_id'],legacy_missing_terminal=True)
  review=m.prepare(p['plan_id']);self.assertEqual(m.execute(review['plan_id'])['recovery_state'],'observed_unchanged_reminder_accepted_after_review');self.engine.assert_settled();self.assertEqual(self.adapter.calls,[])
