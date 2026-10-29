"""Exact template renames, once/scoped authority and immutable error recovery."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from test_native_template_metadata import SOURCE,TEMPLATE,LIST,result
from icloud_for_agents import native_template_rename as module
from icloud_for_agents.core import SafetyError,digest
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.native_reminders import RemindersCreateAdapter
from icloud_for_agents.native_access import require_write
from icloud_for_agents.native_grants import NativeGrants
from icloud_for_agents.native_protocol import mutation_request
class Reader:
 def __init__(self):self.value=result();self.revision=1;self.items=[{'item_id':LIST,'parent_item_id':None,'title':'Synthetic saved task','priority':1}];self.sections={'protocol_version':1,'source_id':SOURCE,'id':TEMPLATE,'sections':[],'sections_complete':False,'ordering_verified':False,'membership_verified':False,'data_mutated':False,'backend':'reminderkit-template-sections-v1'}
 def read(self,action,**fields):
  if fields.get('source_id')!=SOURCE or fields.get('id')!=TEMPLATE:raise SafetyError('Wrong template')
  if action=='template_metadata':return copy.deepcopy(self.value)
  if action=='template_sections':return copy.deepcopy(self.sections)
  raise AssertionError(action)
 def outline(self,*args,**kw):return {'title':self.value['title'],'template_revision':self.revision,'items':copy.deepcopy(self.items),'total_items':len(self.items),'done':len(self.items)<=20,'schema_sha256':'synthetic-fixed-schema'}
class Adapter(RemindersCreateAdapter):
 backend_id='synthetic-template-rename'
 def __init__(self):self.reader=Reader();self.calls=[]
 def apply(self,item):
  require_write('reminders',item);self.calls.append(item['operation']);self.reader.value['title']=item['fields']['title'];self.reader.revision+=1
  return {'source_id':item['source_id'],'id':item['id'],'saved_locally':True}
class TemplateRenameTests(unittest.TestCase):
 def setUp(self):
  env={'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'source_ids':[SOURCE],'access_mode':'read-only','advanced_backend':'reminderkit','metadata_backend':'reminders-store-readonly','helper_path':'/synthetic/helper'}})}
  p=patch.dict(os.environ,env,clear=True);p.start();self.addCleanup(p.stop);t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.store=PlanStore(Path(t.name)/'runtime');self.adapter=Adapter();self.engine=NativeChanges('reminders','synthetic@example.com',self.adapter,self.store)
  p=patch.object(module,'outline',side_effect=self.adapter.reader.outline);p.start();self.addCleanup(p.stop)
 def change(self,title='Synthetic renamed template'):return {'operation':'rename_template','source_id':SOURCE,'id':TEMPLATE,'fields':{'title':title}}
 def scope(self):return {'scenario':'Synthetic exact template rename','rules':[{'operation':'rename_template','source_id':SOURCE,'container_id':TEMPLATE,'fields':['title'],'destinations':[]}],'max_batch':1,'max_24h':2,'expires':None}
 def test_once_exact_preservation_protocol_and_no_saved_permission_change(self):
  old=copy.deepcopy(self.adapter.reader.value);items=copy.deepcopy(self.adapter.reader.items)
  with self.assertRaises(SafetyError):self.engine.prepare([self.change()])
  p=self.engine.prepare_once([self.change()]);item=p['review']['payload']['changes'][0];self.assertEqual(item['before']['metadata'],old);self.assertEqual(mutation_request('reminders',item)['change']['before'],item['before']);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');self.assertEqual(self.adapter.reader.value,dict(old,title='Synthetic renamed template'));self.assertEqual(self.adapter.reader.items,items);self.assertEqual(os.environ['ICLOUD_MAIL_ACCESS'],'read-only');self.engine.assert_settled()
  with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
 def test_strict_synthetic_receipt_required(self):
  os.environ['ICLOUD_APPROVAL_POLICY']='local-receipt';p=self.engine.prepare_once([self.change()])
  with self.assertRaises(FileNotFoundError):self.engine.execute(p['plan_id'])
  self.store.write_once(self.store.path(p['plan_id'],'.approved'),{'plan_id':p['plan_id'],'sha256':p['sha256']});self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied')
 def test_title_scope_unknown_fields_shared_and_oversized_templates_refuse(self):
  for title in('', ' ', '\n','\ud800','a'*1001,True):
   with self.assertRaises(SafetyError):self.engine.prepare_once([self.change(title)])
  for c in(dict(self.change(),id=LIST),dict(self.change(),source_id=LIST),dict(self.change(),extra=True),dict(self.change(),fields={'title':'new','body':'never'})):
   with self.assertRaises(SafetyError):self.engine.prepare_once([c])
  self.adapter.reader.value['public_link_present']=True
  with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
  self.adapter.reader.value['public_link_present']=False;self.adapter.reader.items*=21
  with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
  self.assertEqual(self.adapter.calls,[])
 def test_native_configuration_sections_and_outline_drift_refuse_before_dispatch(self):
  for mutate in(lambda:self.adapter.reader.value.__setitem__('configuration',{'source_list_id':LIST,'save_completed':True}),lambda:self.adapter.reader.items[0].__setitem__('title','changed'),lambda:self.adapter.reader.__setattr__('revision',42)):
   p=self.engine.prepare_once([self.change()]);mutate()
   with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
   self.assertFalse(self.store.path(p['plan_id'],'.started').exists())
  self.assertEqual(self.adapter.calls,[])
 def test_once_scope_quota_and_revocation(self):
  m=NativeGrants(self.engine);g=m.prepare(self.scope())['plan_id'];m.activate(g)
  for title in('Synthetic renamed template','Synthetic template'):
   p=self.engine.prepare_with_grant(g,[self.change(title)]);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');self.assertEqual(self.store.read(self.store.path(p['plan_id'],'.native-grant-use'))['count'],1)
  p=self.engine.prepare_with_grant(g,[self.change()])
  with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
  m.revoke(g)
  with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
 def test_revoked_grant_cannot_execute_prepared_plan(self):
  m=NativeGrants(self.engine);g=m.prepare(self.scope())['plan_id'];m.activate(g);p=self.engine.prepare_with_grant(g,[self.change()]);m.revoke(g)
  with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
  self.assertEqual(self.adapter.calls,[])
 def test_failed_readback_and_terminal_error_reconcile_without_replay_or_forged_evidence(self):
  from icloud_for_agents.native_reminder_exchange import begin,finish
  apply=self.adapter.apply
  def uncertain(item):apply(item);finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic terminal error')
  p=self.engine.prepare_once([self.change()])
  with patch.object(self.adapter,'apply',side_effect=uncertain):self.engine.execute(p['plan_id'])
  original=self.store.path(p['plan_id'],'.result').read_bytes();self.assertEqual(self.engine.reconcile_known_changes(p['plan_id'])['status'],'applied_after_readback_reconciliation');self.engine.assert_settled();self.assertEqual(len(self.adapter.calls),1)
  path=self.store.path(p['plan_id'],'.native-lifecycle-readback');v=self.store.read(path);v['observed']['snapshot']['outline']['items'][0]['title']='forged';v['observed_sha256']=digest(v['observed']);path.write_text(json.dumps(v));self.assertFalse(self.store._native_lifecycle_readback_completed(p['plan_id']));self.assertEqual(self.store.path(p['plan_id'],'.result').read_bytes(),original)
 def test_unchanged_error_observation_preserves_failure_and_no_legacy_acceptance(self):
  from icloud_for_agents.native_reminder_exchange import begin,finish
  from icloud_for_agents.native_reminder_recovery import ReminderObservationRecovery
  def uncertain(item):finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic unchanged terminal error')
  p=self.engine.prepare_once([self.change()])
  with patch.object(self.adapter,'apply',side_effect=uncertain):self.engine.execute(p['plan_id'])
  original=self.store.path(p['plan_id'],'.result').read_bytes();r=ReminderObservationRecovery(self.engine.account,self.store,self.adapter)
  with self.assertRaises(SafetyError):r.prepare(p['plan_id'],legacy_missing_terminal=True)
  q=r.prepare(p['plan_id']);r.execute(q['plan_id']);self.engine.assert_settled();self.assertEqual(self.store.path(p['plan_id'],'.result').read_bytes(),original)
  with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
 def test_readback_rejects_saved_item_change_or_decreasing_revision(self):
  apply=self.adapter.apply
  def changed(item):value=apply(item);self.adapter.reader.items[0]['priority']=9;return value
  p=self.engine.prepare_once([self.change()])
  with patch.object(self.adapter,'apply',side_effect=changed):self.assertEqual(self.engine.execute(p['plan_id'])['status'],'failed_or_uncertain')
  with self.assertRaises(SafetyError):self.engine.assert_settled()
