"""Exact saved-template scope, explicit link disclosure and drift contracts."""
import copy,json,os,unittest
from unittest.mock import patch
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_read import NativeReader
from icloud_for_agents import native_template_metadata as module
SOURCE='AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA';TEMPLATE='BBBBBBBB-BBBB-4BBB-8BBB-BBBBBBBBBBBB';LIST='CCCCCCCC-CCCC-4CCC-8CCC-CCCCCCCCCCCC'
def result(included=False):
 return {'protocol_version':1,'source_id':SOURCE,'id':TEMPLATE,'title':'Synthetic template','minimum_supported_version':20220430,'configuration':{'source_list_id':LIST,'save_completed':False},'public_link_present':False,'public_link_included':included,'public_link':None,'data_mutated':False,'backend':'reminderkit-template-metadata-v1'}
def link():return {'url':'https://www.icloud.com/reminders/synthetic-test-only','can_be_updated':False,'creationDate':1.0,'lastModifiedDate':None,'expirationDate':None,'configuration':dict.fromkeys(module.FLAGS,False)}
class Reader:
 def __init__(self):self.calls=[];self.value=result();self.after=None
 def read(self,action,**fields):
  assert action=='template_metadata';self.calls.append(fields);value=copy.deepcopy(self.value)
  if self.after:self.after()
  return value
class TemplateMetadataTests(unittest.TestCase):
 def setUp(self):
  self.config={'reminders':{'source_ids':[SOURCE],'access_mode':'read-only','advanced_backend':'reminderkit','helper_path':'/synthetic/helper'}};self.reader=Reader()
  for p in (patch.dict(os.environ,{'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_NATIVE_CONFIG':json.dumps(self.config)},clear=True),patch('platform.system',return_value='Darwin')):p.start();self.addCleanup(p.stop)
 def read(self,**kw):return module.inspect(SOURCE,TEMPLATE,reader=self.reader,**kw)
 def test_metadata_without_public_link_body_or_authority(self):
  value=self.read();self.assertEqual(len(self.reader.calls),2);self.assertTrue(all(v['include_public_link']is False for v in self.reader.calls));self.assertFalse(value['sharing_link_fetched']);self.assertFalse(value['source_list_reference_resolved']);self.assertFalse(value['template_contents_complete']);self.assertFalse(value['data_mutated']);self.assertNotIn('items',value)
 def test_present_link_hidden_by_default_and_returned_only_when_explicitly_selected(self):
  self.reader.value['public_link_present']=True;self.assertIsNone(self.read()['public_link']);self.reader.value.update(public_link_included=True,public_link=link());value=self.read(include_public_link=True);self.assertEqual(value['public_link']['url'],link()['url']);self.assertFalse(value['sharing_link_fetched'])
 def test_invalid_scope_disabled_backend_or_flag_refuse_before_read(self):
  for change in ({'ICLOUD_ENABLE_REMINDERS':'0'},{'ICLOUD_MAIL_ACCESS':'disabled'},{'ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'source_ids':[SOURCE]}})}):
   with patch.dict(os.environ,change),self.assertRaises(SafetyError):self.read()
  for flag in (None,1,'true'):
   with self.assertRaises(SafetyError):self.read(include_public_link=flag)
  with self.assertRaises(SafetyError):module.inspect(LIST,TEMPLATE,reader=self.reader)
  self.assertEqual(self.reader.calls,[])
 def test_strict_response_identity_types_configuration_and_unrequested_link_refuse(self):
  for key,value in (('source_id',LIST),('id',LIST),('protocol_version',True),('minimum_supported_version',True),('title','\ud800'),('title','a'*1001),('public_link_present',1),('public_link_included',0),('data_mutated',True),('backend','foreign'),('public_link',link()),('configuration',{'source_list_id':'foreign','save_completed':False}),('extra','never')):
   self.reader.value=result();self.reader.value[key]=value
   with self.subTest(key=key),self.assertRaises(SafetyError):self.read()
 def test_link_format_dates_and_booleans_refuse_without_fetching(self):
  for key,value in (('url','file:///synthetic'),('url','https://user:pass@example.invalid/a'),('url','https://example.invalid/\nvalue'),('url','https://[bad'),('url','https://example.invalid:invalid/a'),('creationDate',True),('creationDate',float('nan')),('expirationDate',float('inf')),('can_be_updated',1),('configuration',{'unknown':False})):
   self.reader.value=result(True);self.reader.value.update(public_link_present=True,public_link=link());self.reader.value['public_link'][key]=value
   with self.subTest(key=key),self.assertRaises(SafetyError):self.read(include_public_link=True)
 def test_metadata_and_access_drift_refuse_no_retry(self):
  self.reader.after=lambda:self.reader.value.__setitem__('title','changed')
  with self.assertRaises(SafetyError):self.read()
  self.assertEqual(len(self.reader.calls),2);self.reader=Reader();self.reader.after=lambda:os.environ.__setitem__('ICLOUD_MAIL_ACCESS','disabled')
  with self.assertRaises(SafetyError):self.read()
 def test_native_reader_enforces_fixed_action_scope_and_result(self):
  with patch.object(NativeReader,'_dispatch',return_value=result())as dispatched:
   value=NativeReader('reminders').read('template_metadata',source_id=SOURCE,id=TEMPLATE,include_public_link=False);self.assertEqual(value,result())
   for fields in ({'source_id':LIST,'id':TEMPLATE,'include_public_link':False},{'source_id':SOURCE,'id':TEMPLATE,'include_public_link':1},{'source_id':SOURCE,'id':TEMPLATE,'include_public_link':False,'url':'never'}):
    with self.assertRaises(SafetyError):NativeReader('reminders').read('template_metadata',**fields)
   self.assertEqual(dispatched.call_count,1)
