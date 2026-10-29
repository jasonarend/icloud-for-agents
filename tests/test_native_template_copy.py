"""Captured template text copies retain normal temporary/scoped write authority."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from test_native_reminder_import import Adapter as BaseAdapter
from test_native_reminder_moves import SOURCE,ORIGIN,DEST,ROOT,CHILD
from icloud_for_agents import native_template_copy as module
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.native_grants import NativeGrants
from icloud_for_agents.core import SafetyError
TEMPLATE='66666666-6666-4666-8666-666666666666'
class Adapter(BaseAdapter):
 def __init__(self):super().__init__();self.reader=object()
 def lists(self):return [{'id':ORIGIN,'source_id':SOURCE,'writable':True},{'id':DEST,'source_id':SOURCE,'writable':True}]
class TemplateCopyTests(unittest.TestCase):
 def setUp(self):
  t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.store=PlanStore(Path(t.name)/'runtime');self.adapter=Adapter();self.engine=NativeChanges('reminders','synthetic@example.com',self.adapter,self.store)
  env={'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'source_ids':[SOURCE],'access_mode':'read-only','advanced_backend':'reminderkit','metadata_backend':'reminders-store-readonly','helper_path':'/synthetic/helper'}})}
  self.outline={'title':'Synthetic template','done':True,'total_items':1,'items':[{'item_id':ROOT,'parent_item_id':None,'title':'Synthetic task','priority':1}]}
  self.details={'source_id':SOURCE,'template_id':TEMPLATE,'item_id':ROOT,'parent_item_id':None,'title':'Synthetic task','notes':'Saved text with 🚀','priority':1,'native_metadata_uninterpreted':{'creationDate':100.5,'alarmTriggers':{'unknown_native_shape':'never schedule this'}},'representation_complete':False}
  for p in(patch.dict(os.environ,env,clear=True),patch.object(module,'outline',side_effect=lambda *a,**k:copy.deepcopy(self.outline)),patch.object(module,'details',side_effect=lambda *a,**k:copy.deepcopy(self.details))):p.start();self.addCleanup(p.stop)
 def prepare(self,**kw):return module.prepare(self.engine,SOURCE,TEMPLATE,ORIGIN,accept_omissions=True,**kw)
 def scope(self):return {'scenario':'Synthetic template text copies','rules':[{'operation':'copy_observation','source_id':SOURCE,'container_id':ORIGIN,'fields':['title','notes','priority'],'destinations':[]}],'max_batch':1,'max_24h':2,'expires':None}
 def test_once_captures_only_text_priority_and_explicit_loss_without_writing_at_preparation(self):
  p=self.prepare();payload=p['review']['payload'];fields=payload['changes'][0]['fields'];self.assertEqual(fields,{'title':'Synthetic task','notes':'Saved text with 🚀','priority':1});self.assertEqual(self.adapter.saved,[]);review=payload['copy_review'];self.assertFalse(review['include_dates']);self.assertFalse(review['include_alarms']);self.assertEqual(review['source_template']['template_id'],TEMPLATE);self.assertEqual(review['source_format'],'observed_saved_template_text_projection');self.assertIn('not used',review['effects'][1]);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');self.assertEqual(self.adapter.saved,[fields]);self.engine.assert_settled()
  with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
 def test_native_scheduling_never_interpreted_and_later_source_edit_does_not_change_approved_text(self):
  p=self.prepare();self.details['notes']='Later source change';self.details['native_metadata_uninterpreted']['creationDate']=None
  self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');self.assertEqual(self.adapter.saved[0]['notes'],'Saved text with 🚀');self.assertNotIn('due',self.adapter.saved[0]);self.assertNotIn('alarms',self.adapter.saved[0])
 def test_acceptance_disabled_service_and_invalid_ids_refuse_before_body(self):
  with patch.object(module,'details',side_effect=AssertionError('No content read')):
   for accepted in(False,1,'yes'):
    with self.assertRaises(SafetyError):module.prepare(self.engine,SOURCE,TEMPLATE,ORIGIN,accept_omissions=accepted)
   with patch.dict(os.environ,{'ICLOUD_ENABLE_REMINDERS':'0'}),self.assertRaises(SafetyError):self.prepare()
   with self.assertRaises(SafetyError):module.prepare(self.engine,SOURCE,'foreign',ORIGIN,accept_omissions=True)
 def test_incomplete_oversized_nested_or_empty_template_refuses(self):
  for key,value in(('done',False),('total_items',0),('total_items',11)):
   old=copy.deepcopy(self.outline);self.outline[key]=value
   with self.assertRaises(SafetyError):self.prepare()
   self.outline=old
  self.outline['items'][0]['parent_item_id']=CHILD
  with self.assertRaises(SafetyError):self.prepare()
 def test_wrong_item_template_source_priority_or_text_refuses(self):
  for key,value in(('item_id',CHILD),('template_id',DEST),('source_id',DEST),('priority',5),('priority',True),('notes',None),('title','Unexpected')):
   old=copy.deepcopy(self.details);self.details[key]=value
   with self.assertRaises(SafetyError):self.prepare()
   self.details=old
 def test_outline_drift_refuses_without_preparing_changes(self):
  original=copy.deepcopy(self.outline);changed=copy.deepcopy(original);changed['title']='Changed while reading'
  with patch.object(module,'outline',side_effect=[original,changed]),self.assertRaises(SafetyError):self.prepare()
  self.assertEqual(list(self.store.root.glob('*.json')),[])
 def test_strict_synthetic_receipt_and_scope_quota_revocation(self):
  os.environ['ICLOUD_APPROVAL_POLICY']='local-receipt';p=self.prepare()
  with self.assertRaises(FileNotFoundError):self.engine.execute(p['plan_id'])
  self.store.write_once(self.store.path(p['plan_id'],'.approved'),{'plan_id':p['plan_id'],'sha256':p['sha256']});self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');os.environ['ICLOUD_APPROVAL_POLICY']='trusted-client';m=NativeGrants(self.engine);g=m.prepare(self.scope())['plan_id'];m.activate(g)
  with self.assertRaises(SafetyError):module.prepare(self.engine,SOURCE,TEMPLATE,DEST,accept_omissions=True,grant_id=g)
  for _ in range(2):self.assertEqual(self.engine.execute(self.prepare(grant_id=g)['plan_id'])['status'],'applied')
  pending=self.prepare(grant_id=g)
  with self.assertRaises(SafetyError):self.engine.execute(pending['plan_id'])
  m.revoke(g)
  with self.assertRaises(SafetyError):self.engine.execute(pending['plan_id'])
 def test_original_uncertainty_blocks_execution_without_replaying_or_altering_source(self):
  prior=self.prepare();self.store.consume(prior['plan_id'],self.engine.account);self.store.finish(prior['plan_id'],{'status':'failed_or_uncertain','phase':'mutation','attempted':[],'completed':[]});next_plan=self.prepare()
  with self.assertRaises(SafetyError):self.engine.execute(next_plan['plan_id'])
  self.assertEqual(self.adapter.saved,[])
