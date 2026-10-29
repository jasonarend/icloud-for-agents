"""Exact scoped export validation rejects paths, oversize data and forged hashes."""
import base64,copy,hashlib,json,os,unittest
from unittest.mock import patch
from icloud_for_agents.native_note_attachments import validate_export,validate_request
from icloud_for_agents.native_read import NativeReader
from icloud_for_agents.core import SafetyError
FIELDS={'folder_id':'folder','id':'note','attachment_id':'asset','max_bytes':64}
def response(data=b'Synthetic attachment\x00bytes'):
 return {'protocol_version':1,'source_id':'selected','folder_id':'folder','note_id':'note','attachment_id':'asset','name':'Synthetic.bin','content_identifier':'cid:synthetic','modified':None,'encoding':'base64','data':base64.b64encode(data).decode(),'size':len(data),'sha256':hashlib.sha256(data).hexdigest(),'data_mutated':False,'representation':'Notes_scripting_native_file','complete_backup':False}
class AttachmentExportTests(unittest.TestCase):
 def setUp(self):
  p=patch.dict(os.environ,{'ICLOUD_ENABLE_NOTES':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'access_mode':'read-only','source_ids':['selected'],'helper_path':'/synthetic/helper'}})},clear=True);p.start();self.addCleanup(p.stop)
 def test_exact_read_returns_verified_bytes_without_write_elevation(self):
  value=response()
  with patch.object(NativeReader,'_dispatch',return_value=dict(value,request_id='synthetic',nonprompting_event_count=2)) as d:
   self.assertEqual(NativeReader('notes').read('attachment_export',**FIELDS),value);self.assertEqual(d.call_args.args[0]['max_bytes'],64)
  self.assertEqual(os.environ['ICLOUD_MAIL_ACCESS'],'read-only')
 def test_invalid_ids_sizes_and_caller_paths_refuse_before_dispatch(self):
  with patch.object(NativeReader,'_dispatch') as d:
   for fields in (dict(FIELDS,path='/tmp/file'),dict(FIELDS,id=''),dict(FIELDS,max_bytes=True),dict(FIELDS,max_bytes=0),dict(FIELDS,max_bytes=1048577),dict(FIELDS,attachment_id=[])):
    with self.assertRaises(SafetyError):NativeReader('notes').read('attachment_export',**fields)
   with self.assertRaises(SafetyError):NativeReader('notes').read('get',folder_id='folder',id='note',max_bytes=64)
   d.assert_not_called()
 def test_foreign_incomplete_oversize_bad_encoding_and_hash_refuse(self):
  for value in (dict(response(),source_id='other'),dict(response(),note_id='other'),dict(response(),data_mutated=True),dict(response(),complete_backup=True),dict(response(),sha256='f'*64),dict(response(),data='%%%'),dict(response(),size=3),dict(response(),size=True),dict(response(),encoding='utf8'),dict(response(),extra='unreviewed'),response(b'x'*65)):
   with self.assertRaises(SafetyError):validate_export(value,FIELDS,['selected'])
 def test_disabled_service_missing_helper_or_changed_scope_refuse(self):
  for config in ({'access_mode':'disabled','source_ids':['selected'],'helper_path':'/synthetic/helper'},{'access_mode':'read-only','source_ids':['selected']}):
   os.environ['ICLOUD_NATIVE_CONFIG']=json.dumps({'notes':config})
   with patch.object(NativeReader,'_dispatch') as d:
    with self.assertRaises(SafetyError):NativeReader('notes').read('attachment_export',**FIELDS)
    d.assert_not_called()
 def test_scope_change_while_exporting_refuses(self):
  def dispatch(*args):os.environ['ICLOUD_MAIL_ACCESS']='disabled';return response()
  with patch.object(NativeReader,'_dispatch',side_effect=dispatch):
   with self.assertRaises(SafetyError):NativeReader('notes').read('attachment_export',**FIELDS)
