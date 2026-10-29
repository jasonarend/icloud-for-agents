"""Synthetic exact-attachment OCR orchestration and result validation tests."""
import base64,copy,hashlib,json,os,subprocess,unittest
from types import SimpleNamespace
from unittest.mock import patch
from icloud_for_agents.core import SafetyError
from icloud_for_agents import native_note_ocr as module
RAW=b'\x89PNG\r\n\x1a\nSynthetic test image bytes'
class Reader:
 def __init__(self):
  self.note={'item':{'id':'note','folder_id':'folder','source_id':'selected','locked':False,'modified':'2026-10-29T00:00:00Z','text':'Synthetic'}}
  self.export={'protocol_version':1,'source_id':'selected','folder_id':'folder','note_id':'note','attachment_id':'image','name':'synthetic.png','content_identifier':'synthetic-id','modified':None,'encoding':'base64','data':base64.b64encode(RAW).decode(),'size':len(RAW),'sha256':hashlib.sha256(RAW).hexdigest(),'data_mutated':False,'representation':'Notes_scripting_native_file','complete_backup':False};self.dispatched=[];self.after_dispatch=None
  self.result={'protocol_version':1,'source_id':'selected','image_sha256':self.export['sha256'],'size':len(RAW),'image_type':'public.png','width':900,'height':240,'orientation':1,'engine':'Apple_Vision','request_revision':3,'language':'en-US','language_correction':False,'lines':[{'text':'SYNTHETIC 123','confidence':0.95,'bounding_box':{'x':0.1,'y':0.2,'width':0.6,'height':0.1}}],'truncated':False,'derived':True,'native_notes_ocr':False,'data_mutated':False,'order':'Vision_observation_order_not_document_layout'}
 def read(self,action,**fields):
  if action=='get':return copy.deepcopy(self.note)
  if action=='attachment_export':return copy.deepcopy(self.export)
  raise AssertionError(action)
 def _dispatch(self,request,config):
  assert set(request)=={'protocol_version','service','action','source_ids','source_id','image_data','image_sha256'}and request['action']=='ocr_image'and request['source_ids']==['selected'];assert request['image_data']==self.export['data'];self.dispatched.append(request)
  if self.after_dispatch:self.after_dispatch()
  return copy.deepcopy(self.result)
class NoteOCRTests(unittest.TestCase):
 def setUp(self):
  self.reader=Reader();env={'ICLOUD_ENABLE_NOTES':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'access_mode':'read-only','source_ids':['selected'],'helper_path':'/synthetic/helper'}})}
  for context in (patch.dict(os.environ,env,clear=True),patch.object(module,'bundle_fingerprint',return_value='synthetic-helper'),patch.object(module,'image_processor',return_value='/synthetic/processor')):context.start();self.addCleanup(context.stop)
 def read(self,**kwargs):return module.recognize('folder','note','image',reader=self.reader,**kwargs)
 def test_fixed_request_exact_bytes_and_derived_non_native_provenance(self):
  before=copy.deepcopy(self.reader.note);value=self.read();self.assertEqual(value['lines'][0]['text'],'SYNTHETIC 123');self.assertEqual(len(self.reader.dispatched),1);self.assertTrue(value['derived']);self.assertFalse(value['native_notes_ocr']);self.assertFalse(value['data_mutated']);self.assertFalse(value['handwriting_verified']);self.assertFalse(value['complete_backup']);self.assertEqual(value['attachment_id'],'image');self.assertEqual(self.reader.note,before);self.assertNotIn('data',value)
 def test_maximum_input_bounds_refuse_before_provider_or_processor(self):
  for value in (True,0,131073,-1,1.5):
   with patch.object(self.reader,'read',side_effect=AssertionError('No provider')),self.assertRaises(SafetyError):self.read(max_bytes=value)
 def test_disabled_unselected_missing_helper_and_locked_note_refuse(self):
  for env in ({'ICLOUD_ENABLE_NOTES':'0'},{'ICLOUD_MAIL_ACCESS':'disabled'},{'ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'source_ids':['selected']}})}):
   with patch.dict(os.environ,env),self.assertRaises(SafetyError):self.read()
  for key,value in (('locked',True),('source_id','foreign'),('folder_id','foreign'),('id','foreign')):
   before=copy.deepcopy(self.reader.note);self.reader.note['item'][key]=value
   with self.subTest(key=key),self.assertRaises(SafetyError):self.read()
   self.reader.note=before
  self.assertEqual(self.reader.dispatched,[])
 def test_export_foreign_identity_malformed_hash_size_and_unsupported_format_refuse(self):
  for field,value in (('source_id','foreign'),('note_id','foreign'),('folder_id','foreign'),('attachment_id','foreign'),('sha256','0'*64),('size',1),('data','invalid base64')):
   before=copy.deepcopy(self.reader.export);self.reader.export[field]=value
   with self.subTest(field=field),self.assertRaises(SafetyError):self.read()
   self.reader.export=before
  self.reader.export.update(data=base64.b64encode(b'%PDF-1.7').decode(),size=8,sha256=hashlib.sha256(b'%PDF-1.7').hexdigest())
  with self.assertRaises(SafetyError):self.read()
  self.assertEqual(self.reader.dispatched,[])
 def test_response_source_hash_geometry_confidence_format_and_size_are_verified(self):
  baseline=copy.deepcopy(self.reader.result)
  for key,value in (('source_id','foreign'),('image_sha256','0'*64),('size',True),('image_type','public.jpeg'),('width',True),('width',8193),('height',9000),('orientation',9),('request_revision',4),('language','other'),('data_mutated',True),('native_notes_ocr',True),('derived',False),('truncated',0),('path','/never/read')):
   self.reader.result=copy.deepcopy(baseline);self.reader.result[key]=value
   with self.subTest(key=key),self.assertRaises(SafetyError):self.read()
  self.reader.result=baseline
  for confidence in (True,float('nan'),float('inf'),-1,1.1):
   self.reader.result['lines'][0]['confidence']=confidence
   with self.subTest(confidence=confidence),self.assertRaises(SafetyError):self.read()
 def test_text_and_box_bounds_reject_bool_nonfinite_and_oversized_outputs(self):
  baseline=copy.deepcopy(self.reader.result)
  for key,value in (('text','a'*2001),('text','\ud800'),('bounding_box',{'x':0.9,'y':0.2,'width':0.6,'height':0.1}),('bounding_box',{'x':True,'y':0.2,'width':0.6,'height':0.1}),('bounding_box',{'x':float('inf'),'y':0.2,'width':0.6,'height':0.1})):
   self.reader.result=copy.deepcopy(baseline);self.reader.result['lines'][0][key]=value
   with self.subTest(key=key),self.assertRaises(SafetyError):self.read()
  self.reader.result=copy.deepcopy(baseline);self.reader.result['lines']*=51
  with self.assertRaises(SafetyError):self.read()
 def test_native_empty_and_explicit_truncated_results_do_not_claim_note_absence_or_layout(self):
  self.reader.result['lines']=[];self.assertEqual(self.read()['lines'],[]);self.reader.result['truncated']=True;self.assertTrue(self.read()['truncated'])
 def test_note_bytes_permission_and_helper_drift_refuse_returned_text(self):
  for change in (lambda:self.reader.note['item'].__setitem__('modified','changed'),lambda:self.reader.export.__setitem__('name','changed'),lambda:os.environ.__setitem__('ICLOUD_MAIL_ACCESS','disabled')):
   saved_note=copy.deepcopy(self.reader.note);saved_export=copy.deepcopy(self.reader.export);self.reader.after_dispatch=change
   with self.assertRaises(SafetyError):self.read()
   self.reader.note=saved_note;self.reader.export=saved_export;os.environ['ICLOUD_MAIL_ACCESS']='read-only'
  self.reader.after_dispatch=None
  with patch.object(module,'bundle_fingerprint',side_effect=['before','before','after']),self.assertRaises(SafetyError):self.read()
 def test_unknown_helper_error_is_not_retried(self):
  with patch.object(self.reader,'_dispatch',side_effect=SafetyError('Read failed'))as dispatch,self.assertRaises(SafetyError):self.read()
  self.assertEqual(dispatch.call_count,1)

 def test_private_processor_transport_fixed_path_filtered_environment_and_bounded_output(self):
  config=module.native_config('notes');request={'source_id':'selected'}
  def launch(command,**kwargs):
   self.assertEqual(command,['/synthetic/processor']);self.assertEqual(json.loads(kwargs['input']),request)
   self.assertEqual(set(kwargs['env']),{'HOME','PATH','TMPDIR','LANG'});self.assertTrue(kwargs['close_fds']);self.assertEqual(kwargs['timeout'],65)
   kwargs['stdout'].write(json.dumps(self.reader.result).encode());return SimpleNamespace(returncode=0)
  with patch.dict(os.environ,{'HOME':'/synthetic','PATH':'/usr/bin','TMPDIR':'/tmp','LANG':'C','SECRET':'never-forwarded'}),patch.object(module.subprocess,'run',side_effect=launch):
   self.assertEqual(module.process_request(request,config),self.reader.result)
 def test_processor_failure_timeout_malformed_or_oversize_is_not_retried(self):
  config=module.native_config('notes');request={'source_id':'selected'}
  for raw,code in ((b'not json',0),(b'x'*131073,0),(b'{"protocol_version":1,"error":"read_timeout"}',0),(b'{"protocol_version":2}',0),(b'{}',1)):
   def launch(command,**kwargs):kwargs['stdout'].write(raw);return SimpleNamespace(returncode=code)
   with patch.object(module.subprocess,'run',side_effect=launch)as process,self.assertRaises(SafetyError):module.process_request(request,config)
   self.assertEqual(process.call_count,1)
  with patch.object(module.subprocess,'run',side_effect=subprocess.TimeoutExpired('fixed processor',65))as process,self.assertRaises(SafetyError):module.process_request(request,config)
  self.assertEqual(process.call_count,1)
 def test_processor_foreign_source_config_or_oversized_request_refuses_before_launch(self):
  config=module.native_config('notes')
  for request,selected in (({'source_id':'foreign'},config),({'source_id':'selected'},{}),({'source_id':'selected','image_data':'x'*240000},config)):
   with patch.object(module.subprocess,'run',side_effect=AssertionError('Must not launch')),self.assertRaises(SafetyError):module.process_request(request,selected)
 def test_native_reader_routes_fixed_processor_without_launching_notes_app(self):
  config=module.native_config('notes');request={'action':'ocr_image','source_id':'selected'}
  from icloud_for_agents.native_read import NativeReader
  with patch.object(module,'process_request',return_value={'synthetic':True})as process,patch('icloud_for_agents.native_app.application_request',side_effect=AssertionError('No app')):
   self.assertEqual(NativeReader('notes')._dispatch(request,config),{'synthetic':True});process.assert_called_once_with(request,config)
