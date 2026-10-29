"""PNG insertion preserves scope, byte identity, approval and durable uncertainty."""
import base64,copy,hashlib,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from test_native_reminder_file_create import Reader,SOURCE,ORIGIN,DEST,ROOT,CHILD
from test_png_format import png
from icloud_for_agents import native_reminder_image_create as module
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_access import require_write
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.native_grants import NativeGrants
from icloud_for_agents.native_protocol import mutation_request
from icloud_for_agents.native_reminders import RemindersCreateAdapter
class Adapter(RemindersCreateAdapter):
 backend_id='synthetic-png-image'
 def __init__(self):self.reader=Reader();self.calls=[]
 def apply(self,item):
  require_write('reminders',item);self.calls.append(item['id']);request=mutation_request('reminders',item)['change'];assert all(request[k]==item[k]for k in('attachment_id','before','width','height','sha256'));raw,width,height=module.data(item['fields']);self.reader.raw[item['attachment_id']]=raw;self.reader.attachments[item['id']]=[{'id':item['attachment_id'],'kind':'image','uti':'public.png'}];self.reader.items[item['id']]['modified']=201.0;return {k:item[k]for k in('id','source_id','list_id','attachment_id')}|{'saved_locally':True}
class PNGImageCreateTests(unittest.TestCase):
 def setUp(self):
  t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.store=PlanStore(Path(t.name)/'runtime');self.adapter=Adapter();self.engine=NativeChanges('reminders','synthetic@example.com',self.adapter,self.store)
  env={'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'source_ids':[SOURCE],'access_mode':'read-only','advanced_backend':'reminderkit','helper_path':'/synthetic/helper'}})};p=patch.dict(os.environ,env,clear=True);p.start();self.addCleanup(p.stop)
  def export(identity,attachment,maximum,*,reader):
   raw=reader.raw[attachment];return {'id':identity,'source_id':SOURCE,'list_id':reader.items[identity]['list_id'],'attachment_id':attachment,'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'uti':'public.png','kind':'image','data_mutated':False,'data':base64.b64encode(raw).decode()}
  p=patch.object(module,'export',side_effect=export);p.start();self.addCleanup(p.stop)
 def change(self,identity=ROOT,raw=None):return {'operation':'add_image_attachment','source_id':SOURCE,'id':identity,'fields':{'data_base64':base64.b64encode(png()if raw is None else raw).decode()}}
 def scope(self):return {'scenario':'Synthetic PNG images','rules':[{'operation':'add_image_attachment','source_id':SOURCE,'container_id':ORIGIN,'fields':['data_base64'],'destinations':[]}],'max_batch':2,'max_24h':2,'expires':None}
 def test_once_preassigned_exact_png_bytes_dimensions_and_bystander_preservation(self):
  before=copy.deepcopy(self.adapter.reader.items[CHILD]);p=self.engine.prepare_once([self.change()]);item=p['review']['payload']['changes'][0];self.assertEqual((item['width'],item['height']),(2,2));self.assertEqual(item['size'],len(png()));self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');self.assertEqual(self.adapter.reader.items[CHILD],before);self.engine.assert_settled()
  with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
  with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
 def test_grants_charge_parent_and_image_without_effects_trust_and_revoke(self):
  m=NativeGrants(self.engine);scope=self.scope();scope['max_batch']=1;g=m.prepare(scope)['plan_id'];m.activate(g)
  with self.assertRaisesRegex(SafetyError,'native-object bound'):self.engine.prepare_with_grant(g,[self.change()])
  m.revoke(g);g=m.prepare(self.scope())['plan_id'];m.activate(g)
  with self.assertRaises(SafetyError):self.engine.prepare_with_grant(g,[dict(self.change(),source_id=DEST)])
  p=self.engine.prepare_with_grant(g,[self.change()]);item=copy.deepcopy(p['review']['payload']['changes'][0]);item['effects']['quota_objects']=0;self.assertEqual(m.batch_count([item]),2);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');self.assertEqual(self.store.read(self.store.path(p['plan_id'],'.native-grant-use'))['count'],2)
  pending=self.engine.prepare_with_grant(g,[self.change(CHILD)])
  with self.assertRaises(SafetyError):self.engine.execute(pending['plan_id'])
  m.revoke(g)
  with self.assertRaises(SafetyError):self.engine.execute(pending['plan_id'])
  self.assertEqual(self.adapter.calls,[ROOT])
 def test_strict_synthetic_receipt_and_no_implicit_authority(self):
  with self.assertRaises(SafetyError):self.engine.prepare([self.change()])
  os.environ['ICLOUD_APPROVAL_POLICY']='local-receipt';p=self.engine.prepare_once([self.change()])
  with self.assertRaises(FileNotFoundError):self.engine.execute(p['plan_id'])
  self.assertEqual(self.adapter.calls,[]);self.store.write_once(self.store.path(p['plan_id'],'.approved'),{'plan_id':p['plan_id'],'sha256':p['sha256']});self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied')
 def test_bad_image_unknown_fields_foreign_shared_and_sectioned_targets_refuse(self):
  for raw in(b'',b'not PNG',png()+b'\0',png(body=b'\0'*1000000),png(interlace=1)):
   with self.assertRaises(SafetyError):self.engine.prepare_once([self.change(raw=raw)])
  for fields in({'data_base64':True},{'data_base64':'A==='},{'data_base64':self.change()['fields']['data_base64'],'filename':'synthetic.png'},{'data_base64':self.change()['fields']['data_base64'],'uti':'public.png'}):
   with self.assertRaises(SafetyError):self.engine.prepare_once([dict(self.change(),fields=fields)])
  with self.assertRaises(SafetyError):self.engine.prepare_once([dict(self.change(),width=2)])
  for key in('grocery','shared'):
   setattr(self.adapter.reader,key,True)
   with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
   setattr(self.adapter.reader,key,False)
  self.adapter.reader.sections=[{'id':DEST,'title':'Synthetic'}]
  with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
 def test_drift_and_wrong_saved_bytes_dimensions_or_metadata_never_verify(self):
  p=self.engine.prepare_once([self.change()]);self.adapter.reader.items[CHILD]['notes']='Concurrent edit'
  with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
  self.assertFalse(self.store.path(p['plan_id'],'.started').exists());p=self.engine.prepare_once([self.change()]);item=p['review']['payload']['changes'][0];self.engine.execute(p['plan_id']);v=module.expected_observation(self.adapter,item);self.assertTrue(module.record_matches(item,v))
  for key,value in(('sha256','0'*64),('width',3),('kind','file'),('uti','image/jpeg')):
   bad=copy.deepcopy(v);bad['file'][key]=value;self.assertFalse(module.record_matches(item,bad))
  bad=copy.deepcopy(v);bad['snapshot']['items'][1]['item']['notes']='Lost content';self.assertFalse(module.record_matches(item,bad))
 def test_retained_terminal_expected_state_reconciliation_never_replays(self):
  from icloud_for_agents.native_reminder_exchange import begin,finish
  apply=self.adapter.apply
  def uncertain(item):apply(item);finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic retained error')
  with patch.object(self.adapter,'apply',side_effect=uncertain):p=self.engine.prepare_once([self.change()]);self.engine.execute(p['plan_id'])
  original=self.store.path(p['plan_id'],'.result').read_bytes();self.assertEqual(self.engine.reconcile_known_changes(p['plan_id'])['status'],'applied_after_readback_reconciliation');self.assertEqual(original,self.store.path(p['plan_id'],'.result').read_bytes());self.engine.assert_settled();self.assertEqual(self.adapter.calls,[ROOT])
 def test_unchanged_error_requires_exact_review_and_retains_temp_uncertainty(self):
  from icloud_for_agents.native_reminder_exchange import begin,finish
  from icloud_for_agents.native_reminder_recovery import ReminderObservationRecovery
  def unchanged(item):finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic unchanged error')
  with patch.object(self.adapter,'apply',side_effect=unchanged):p=self.engine.prepare_once([self.change()]);self.engine.execute(p['plan_id'])
  original=self.store.path(p['plan_id'],'.result').read_bytes();m=ReminderObservationRecovery(self.engine.account,self.store,self.adapter)
  with self.assertRaises(SafetyError):m.prepare(p['plan_id'],legacy_missing_terminal=True)
  review=m.prepare(p['plan_id']);self.assertIn('temporary files',review['review']['payload']['effects']['image_insertion_coverage']);self.assertEqual(m.execute(review['plan_id'])['recovery_state'],'observed_unchanged_reminder_accepted_after_review');self.engine.assert_settled();self.assertEqual(self.adapter.calls,[]);self.assertEqual(original,self.store.path(p['plan_id'],'.result').read_bytes())
