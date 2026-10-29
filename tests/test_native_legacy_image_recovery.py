"""Experimental image failures remain auditable without exposing an image writer."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from test_native_notes import Reader
from icloud_for_agents.native_notes import NotesCreateAdapter
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.native_edit_recovery import NotesEditRecovery
from icloud_for_agents.native_grants import NativeGrants
from icloud_for_agents.native_protocol import mutation_request
from icloud_for_agents.native_access import access_status
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError,digest
class Adapter(NotesCreateAdapter):
 backend_id='notes-creation-v1:'+('a'*64)
 def __init__(self):self.reader=Reader()
 def apply(self,*args):raise AssertionError('Historical observation never writes')
class LegacyImageRecoveryTests(unittest.TestCase):
 def setUp(self):
  t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.store=PlanStore(Path(t.name)/'runtime');self.adapter=Adapter();p=patch.dict(os.environ,{'HOME':t.name,'ICLOUD_ENABLE_NOTES':'1','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_RUNTIME_ROOT':str(self.store.root),'ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'access_mode':'read-only','source_ids':['selected'],'helper_path':'/synthetic/helper'}})},clear=True);p.start();self.addCleanup(p.stop);self.engine=NativeChanges('notes','synthetic@example.com',self.adapter,self.store)
 def original(self):
  item={'operation':'add_image','source_id':'selected','folder_id':'folder','id':'created','fields':{'title':'Spec','filename':'Synthetic.png','data_base64':'c3ludGhldGlj','size':9,'sha256':'b'*64,'media_type':'image/png'},'fidelity_verified':True,'effects':{}}
  item['before']=self.adapter.note_snapshot(item,rewrite=False);plan=self.store.prepare('notes_change',{'account':self.engine.account,'context':self.engine.context(True),'changes':[item]});identity=plan['plan_id'];self.store.consume(identity,self.engine.account);self.store.finish(identity,{'status':'failed_or_uncertain','phase':'mutation','attempted':[],'completed':[],'do_not_retry':True})
  request=mutation_request('notes',item);nonce='request-synthetic-'+identity;directory=self.store.root/'native-app'/nonce
  self.store.write_once(self.store.path(identity,'.native-exchange-0'),{'plan_sha256':plan['sha256'],'request_sha256':digest(request),'helper_fingerprint':'a'*64,'backend_id':self.adapter.backend_id,'exchange_directory':str(directory.resolve()),'request_id':nonce})
  self.store.write_once(self.store.path(identity,'.native-terminal-0'),{'plan_sha256':plan['sha256'],'request_sha256':digest(request),'request_id':nonce,'response':{'protocol_version':1,'request_id':nonce,'error':'synthetic_terminal_error','mutation_dispatched':True}})
  return plan
 def test_fresh_image_write_and_grant_are_unavailable(self):
  change={'operation':'add_image','source_id':'selected','folder_id':'folder','id':'created','fields':{'filename':'Synthetic.png','data_base64':'c3ludGhldGlj'}}
  with self.assertRaises(SafetyError):self.engine.prepare_once([change])
  with self.assertRaises(SafetyError):NativeGrants(self.engine).prepare({'scenario':'Synthetic','rules':[{'operation':'add_image','source_id':'selected','container_id':'folder','fields':['filename','data_base64'],'destinations':[]}],'max_batch':1,'max_24h':1,'expires':None})
  status=access_status('notes');self.assertNotIn('add_image',status['implemented_write_operations']);self.assertIn('add_image',status['unavailable_write_operations'])
 def test_retained_error_can_accept_restored_original_snapshot_and_preserves_failure(self):
  original=self.original();identity=original['plan_id'];failure=self.store.path(identity,'.result').read_bytes();m=NotesEditRecovery(self.engine.account,self.store,self.adapter);review=m.prepare(identity,'folder');self.assertEqual(m.execute(review['plan_id'])['recovery_state'],'observed_unchanged_note_accepted_after_review');self.engine.assert_settled();self.assertTrue(self.store.operation_settled(identity));self.assertEqual(self.store.path(identity,'.result').read_bytes(),failure)
  with self.assertRaises(SafetyError):self.engine.execute(identity)
  path=self.store.path(identity,'.native-exchange-0');binding=self.store.read(path);binding['request_sha256']='f'*64;path.write_text(json.dumps(binding));self.assertFalse(self.store.operation_settled(identity))
 def test_changed_content_missing_terminal_and_scope_drift_do_not_accept(self):
  original=self.original();identity=original['plan_id'];m=NotesEditRecovery(self.engine.account,self.store,self.adapter);self.adapter.reader.note['html']+='changed'
  with self.assertRaises(SafetyError):m.prepare(identity,'folder')
  self.adapter.reader.note['html']=original['review']['payload']['changes'][0]['before']['note']['html'];self.store.path(identity,'.native-terminal-0').unlink()
  with self.assertRaises(SafetyError):m.prepare(identity,'folder')
  self.assertFalse(self.store.operation_settled(identity))
