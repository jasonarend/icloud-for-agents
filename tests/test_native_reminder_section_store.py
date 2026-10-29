"""Fixed section-store scope, schema, opt-in and SQLite authorization boundaries."""
import json,os,sqlite3,tempfile,unittest,uuid
from pathlib import Path
from unittest.mock import patch
from icloud_for_agents import native_reminder_section_store as store
from icloud_for_agents.native_reminder_template_store import _readonly_store
from icloud_for_agents.core import SafetyError
SOURCE='11111111-1111-4111-8111-111111111111';LIST='22222222-2222-4222-8222-222222222222';OTHER='33333333-3333-4333-8333-333333333333';ITEM='44444444-4444-4444-8444-444444444444';SECTION='55555555-5555-4555-8555-555555555555';STORE='66666666-6666-4666-8666-666666666666'
class SectionStoreTests(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name);self.path=self.root/('Data-'+STORE+'.sqlite');db=sqlite3.connect(self.path)
  for table,columns in store.REQUIRED.items():
   defs=[name+(' BLOB' if name in {'ZIDENTIFIER','ZMEMBERSHIPSOFREMINDERSINSECTIONSASDATA'} else ' TEXT' if name=='Z_NAME' else ' INTEGER') for name in sorted(columns)]
   if table=='ZREMCDBASELIST':defs.append('ZPRIVATECONTENT BLOB')
   db.execute('CREATE TABLE '+table+' ('+','.join(defs)+')')
  db.executemany('INSERT INTO Z_PRIMARYKEY (Z_NAME,Z_ENT) VALUES (?,?)',[('REMCDAccount',10),('REMCDList',11)])
  db.execute('INSERT INTO ZREMCDOBJECT (Z_PK,Z_ENT,ZIDENTIFIER,ZINACTIVE,ZMARKEDFORDELETION) VALUES (1,10,?,0,0)',(uuid.UUID(SOURCE).bytes,))
  self.value={'minimumSupportedVersion':20230430,'memberships':[{'memberID':ITEM,'groupID':SECTION,'modifiedOn':100}]}
  db.execute('INSERT INTO ZREMCDBASELIST (Z_ENT,Z_OPT,ZIDENTIFIER,ZACCOUNT,ZMARKEDFORDELETION,ZMEMBERSHIPSOFREMINDERSINSECTIONSASDATA,ZPRIVATECONTENT) VALUES (11,1,?,1,0,?,?)',(uuid.UUID(LIST).bytes,json.dumps(self.value).encode(),b'Never read arbitrary content'));db.commit();db.close()
  env={'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'access_mode':'read-only','source_ids':[SOURCE],'helper_path':'/synthetic/helper','advanced_backend':'reminderkit','metadata_backend':'reminders-store-readonly'}})}
  p=patch.dict(os.environ,env,clear=True);p.start();self.addCleanup(p.stop)
  for target,value in [('paths',[self.path]),('platform.system','Darwin'),('platform.mac_ver',('27.0',('','',''),''))]:
   p=patch('icloud_for_agents.native_reminder_section_store.'+target,return_value=value);p.start();self.addCleanup(p.stop)
 def sql(self,query,values=()):
  db=sqlite3.connect(self.path);db.execute(query,values);db.commit();db.close()
 def test_exact_scoped_versioned_membership_metadata_without_writes_or_private_columns(self):
  original=self.path.read_bytes();result=store.read(SOURCE,LIST);self.assertEqual(result['value'],self.value);self.assertEqual(result['list_revision'],1);self.assertEqual(len(result['schema_sha256']),64);self.assertEqual(original,self.path.read_bytes())
  self.assertNotIn('Never read',json.dumps(result))
 def test_default_disabled_foreign_source_helper_and_os_refuse_before_discovery(self):
  original=json.loads(os.environ['ICLOUD_NATIVE_CONFIG'])
  for fields in ({'metadata_backend':'native'},{'advanced_backend':'disabled'},{'helper_path':None},{'source_ids':[OTHER]}):
   config=json.loads(json.dumps(original));config['reminders'].update(fields);os.environ['ICLOUD_NATIVE_CONFIG']=json.dumps(config)
   with patch.object(store,'paths',side_effect=AssertionError('No discovery before access')):
    with self.assertRaises(SafetyError):store.read(SOURCE,LIST)
  os.environ['ICLOUD_NATIVE_CONFIG']=json.dumps(original)
  with patch.object(store.platform,'mac_ver',return_value=('28.0',('','',''),'')):
   with self.assertRaises(SafetyError):store.read(SOURCE,LIST)
 def test_foreign_account_deleted_list_wrong_entity_and_missing_schema_refuse(self):
  for field,value,old in [('ZACCOUNT',2,1),('ZMARKEDFORDELETION',1,0),('Z_ENT',12,11),('Z_OPT',-1,1)]:
   self.sql('UPDATE ZREMCDBASELIST SET '+field+'=?',(value,))
   with self.assertRaises(SafetyError):store.read(SOURCE,LIST)
   self.sql('UPDATE ZREMCDBASELIST SET '+field+'=?',(old,))
  with self.assertRaises(SafetyError):store.read(SOURCE,OTHER)
  self.sql('ALTER TABLE ZREMCDBASELIST RENAME COLUMN ZACCOUNT TO UNKNOWN')
  with self.assertRaises(SafetyError):store.read(SOURCE,LIST)
 def test_authorizer_rejects_content_writes_attach_functions_and_unapproved_pragmas(self):
  with _readonly_store(self.path,store.REQUIRED) as (db,_):
   for sql in ['SELECT ZPRIVATECONTENT FROM ZREMCDBASELIST','UPDATE ZREMCDBASELIST SET Z_OPT=2',"ATTACH DATABASE ':memory:' AS other",'PRAGMA query_only=OFF','SELECT randomblob(100)']:
    with self.assertRaises(sqlite3.DatabaseError):db.execute(sql).fetchall()
 def test_duplicate_selected_account_store_symlink_and_writable_file_refuse(self):
  with patch.object(store,'paths',return_value=[self.path,self.path]):
   with self.assertRaises(SafetyError):store.read(SOURCE,LIST)
  link=self.root/'alias.sqlite';link.symlink_to(self.path)
  with patch.object(store,'paths',return_value=[link]):
   with self.assertRaises(SafetyError):store.read(SOURCE,LIST)
  self.path.chmod(0o666)
  with self.assertRaises(SafetyError):store.read(SOURCE,LIST)
