"""Synthetic saved-template content policy, decoder and exact membership tests."""
import base64,copy,json,os,sqlite3,tempfile,unittest,uuid
from pathlib import Path
from unittest.mock import patch
from icloud_for_agents import native_template_item_details as module,native_reminder_template_store as store
from icloud_for_agents.core import SafetyError
from test_native_template_outline import SOURCE,TEMPLATE,ROOT,CHILD,OTHER,STORE,Reader,create
from test_notes_checklist_format import blob,run


def metadata(title='Synthetic root',notes='Synthetic private notes 🚀'):
 return {'title':title,'priority':0,'titleDocumentData':base64.b64encode(blob(title,[run(title)])).decode(),
         'notesDocumentData':base64.b64encode(blob(notes,[run(notes)])if notes else blob('',[])).decode(),
         'creationDate':100.5,'flagged':0,'alarmTriggers':{'dateTriggers':[]},'recurrenceRules':[]}
def encoded(value):return b'\x01'+json.dumps(value,ensure_ascii=False).encode()
class TemplateItemDetailsTests(unittest.TestCase):
 def setUp(self):
  t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.path=Path(t.name)/('Data-'+STORE+'.sqlite');create(self.path);self.reader=Reader();self.write(encoded(metadata()))
  env={'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'source_ids':[SOURCE],'access_mode':'read-only','advanced_backend':'reminderkit','metadata_backend':'reminders-store-readonly','helper_path':'/synthetic/helper'}})}
  for p in(patch.dict(os.environ,env,clear=True),patch.object(store,'paths',return_value=[self.path]),patch.object(module.platform,'system',return_value='Darwin'),patch.object(module.platform,'mac_ver',return_value=('27.0',('','',''),''))):p.start();self.addCleanup(p.stop)
 def write(self,raw):
  c=sqlite3.connect(self.path);c.execute('UPDATE ZREMCDSAVEDREMINDER SET ZMETADATA=? WHERE ZIDENTIFIER=?',(raw,uuid.UUID(ROOT).bytes));c.commit();c.close()
 def read(self,**kw):return module.inspect(SOURCE,TEMPLATE,ROOT,reader=self.reader,**kw)
 def test_exact_selected_text_and_explicit_uninterpreted_native_values_without_raw_bytes(self):
  old=self.path.read_bytes();value=self.read();self.assertEqual(value['title'],'Synthetic root');self.assertEqual(value['notes'],'Synthetic private notes 🚀');self.assertEqual(value['native_metadata_uninterpreted']['creationDate'],100.5);self.assertFalse(value['data_mutated']);self.assertFalse(value['database_writes']);self.assertFalse(value['restorable']);self.assertFalse(value['representation_complete']);self.assertTrue(value['saved_local_state']);self.assertFalse(value['unsaved_ui_state_verified']);self.assertNotIn('notesDocumentData',json.dumps(value));self.assertEqual(self.path.read_bytes(),old);self.assertEqual(len(self.reader.calls),2)
 def test_ordinary_outline_connection_cannot_read_content_blob(self):
  with store.readonly_store(self.path)as(c,schema):
   with self.assertRaises(sqlite3.DatabaseError):c.execute('SELECT ZMETADATA FROM ZREMCDSAVEDREMINDER')
 def test_foreign_item_deleted_or_wrong_account_content_never_returns(self):
  with self.assertRaises(SafetyError):module.inspect(SOURCE,TEMPLATE,OTHER,reader=self.reader)
  for sql in("UPDATE ZREMCDSAVEDREMINDER SET ZMARKEDFORDELETION=1 WHERE ZPARENTSAVEDREMINDERIDENTIFIER IS NULL","UPDATE ZREMCDSAVEDREMINDER SET ZACCOUNT=99 WHERE ZPARENTSAVEDREMINDERIDENTIFIER IS NULL"):
   c=sqlite3.connect(self.path);c.execute('BEGIN');c.execute(sql);c.commit();c.close()
   with self.assertRaises(SafetyError):self.read()
 def test_opt_in_permission_source_and_os_refuse_before_body_projection(self):
  for env in({'ICLOUD_ENABLE_REMINDERS':'0'},{'ICLOUD_MAIL_ACCESS':'disabled'},{'ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'source_ids':[SOURCE],'advanced_backend':'reminderkit','helper_path':'/synthetic/helper'}})}):
   with patch.dict(os.environ,env),patch.object(module,'_observation',side_effect=AssertionError('No blob')),self.assertRaises(SafetyError):self.read()
  with patch.object(module.platform,'mac_ver',return_value=('26.0',('','',''),'')),self.assertRaises(SafetyError):self.read()
 def test_unknown_version_bad_json_duplicate_keys_and_encoding_refuse(self):
  for raw in(b'',b'\x02{}',b'\x01not json',b'\x01{"title":"a","title":"b"}',b'\x01{"v":NaN}',b'\x01\xff',b'x'*1048577):
   with self.subTest(length=len(raw)),self.assertRaises(SafetyError):module.decode(raw,'Synthetic root',0)
 def test_title_priority_base64_and_document_bounds_refuse(self):
  for key,value in(('title','foreign'),('priority',True),('priority',1),('titleDocumentData','not base64'),('notesDocumentData',base64.b64encode(b'bad gzip').decode()),('notesDocumentData',base64.b64encode(blob('x'*20001,[run('x'*20001)])).decode())):
   v=metadata();v[key]=value
   with self.subTest(key=key),self.assertRaises(SafetyError):module.decode(encoded(v),'Synthetic root',0)
  value=metadata();value['titleDocumentData']=base64.b64encode(blob('other',[run('other')])).decode()
  with self.assertRaises(SafetyError):module.decode(encoded(value),'Synthetic root',0)
 def test_unknown_fields_are_omitted_and_metadata_budgets_refuse(self):
  v=metadata();v['unsupportedSecret']='not returned';value=module.decode(encoded(v),'Synthetic root',0);self.assertIn('unsupportedSecret',value['omitted_metadata_keys']);self.assertNotIn('not returned',json.dumps(value))
  for key,value in(('flagged',10**400),('recurrenceRules',[None]*1001),('alarmTriggers',{'a':'x'*65537})):
   v=metadata();v[key]=value
   with self.subTest(key=key),self.assertRaises(SafetyError):module.decode(encoded(v),'Synthetic root',0)
 def test_repeated_blob_native_scope_and_access_drift_refuse(self):
  original=module._observation;calls=[]
  def drifting(*args):
   value=original(*args);calls.append(1)
   if len(calls)==1:self.write(encoded(metadata(notes='changed')))
   return value
  with patch.object(module,'_observation',side_effect=drifting),self.assertRaises(SafetyError):self.read()
  self.reader.after=lambda count,value:value.__setitem__('sections',[{'id':OTHER,'title':'Changed'}])if count%2==0 else None
  with self.assertRaises(SafetyError):self.read()
 def test_plaintext_projection_does_not_change_ordinary_checklist_output(self):
  from icloud_for_agents.notes_checklist_format import decode,text_projection
  raw=blob('Synthetic',[run('Synthetic')]);self.assertNotIn('text',decode(raw));self.assertEqual(text_projection(raw)['text'],'Synthetic')
