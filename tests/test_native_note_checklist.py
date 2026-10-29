"""Exact synthetic SQLite checklist content scope, access and drift tests."""
import copy,hashlib,json,os,sqlite3,tempfile,unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from icloud_for_agents.core import SafetyError
from icloud_for_agents import native_note_store as store,native_note_checklist as module
from test_notes_checklist_format import fixture,blob
UUID='00000000-0000-0000-0000-000000000001';SOURCE=f'x-coredata://{UUID}/ICAccount/p1';FOLDER=f'x-coredata://{UUID}/ICFolder/p10';NOTE=f'x-coredata://{UUID}/ICNote/p20'
class Reader:
 def __init__(self):
  text,_=fixture();self.item={'id':NOTE,'folder_id':FOLDER,'source_id':SOURCE,'text':text,'locked':False,'truncated':False,'modified':'2026-10-29T00:00:00Z'}
 def read(self,action,**fields):
  if action!='get':raise AssertionError(action)
  return {'item':copy.deepcopy(self.item)}
class ChecklistStoreTests(unittest.TestCase):
 def setUp(self):
  tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.path=Path(tmp.name)/'NoteStore.sqlite';self.reader=Reader();text,runs=fixture();self.data=blob(text,runs)
  with closing(sqlite3.connect(self.path))as c,c:
   c.executescript('''CREATE TABLE Z_METADATA(Z_UUID TEXT);CREATE TABLE Z_PRIMARYKEY(Z_ENT INTEGER PRIMARY KEY,Z_NAME TEXT);
CREATE TABLE ZICCLOUDSYNCINGOBJECT(Z_PK INTEGER PRIMARY KEY,Z_ENT INTEGER,Z_OPT INTEGER,ZACCOUNT8 INTEGER,ZPARENT INTEGER,ZFOLDERTYPE INTEGER,ZMARKEDFORDELETION INTEGER,ZSMARTFOLDERQUERYJSON TEXT,ZTITLE2 TEXT,ZFOLDER INTEGER,ZISPINNED INTEGER,ZNOTEDATA INTEGER,ZISPASSWORDPROTECTED INTEGER);
CREATE TABLE ZICNOTEDATA(Z_PK INTEGER PRIMARY KEY,Z_ENT INTEGER,Z_OPT INTEGER,ZNOTE INTEGER,ZDATA BLOB,ZCRYPTOINITIALIZATIONVECTOR BLOB,ZCRYPTOTAG BLOB);
INSERT INTO Z_PRIMARYKEY VALUES(1,'ICAccount'),(2,'ICFolder'),(3,'ICNote'),(4,'ICNoteData');
INSERT INTO ZICCLOUDSYNCINGOBJECT(Z_PK,Z_ENT,Z_OPT,ZMARKEDFORDELETION)VALUES(1,1,1,0),(2,1,1,0);
INSERT INTO ZICCLOUDSYNCINGOBJECT(Z_PK,Z_ENT,Z_OPT,ZACCOUNT8,ZFOLDERTYPE,ZMARKEDFORDELETION,ZTITLE2)VALUES(10,2,1,1,0,0,'Synthetic folder'),(11,2,1,2,0,0,'Foreign folder');
INSERT INTO ZICCLOUDSYNCINGOBJECT(Z_PK,Z_ENT,Z_OPT,ZFOLDER,ZISPINNED,ZMARKEDFORDELETION,ZNOTEDATA,ZISPASSWORDPROTECTED)VALUES(20,3,5,10,0,0,30,0),(21,3,1,11,0,0,31,0);''')
   c.execute('INSERT INTO Z_METADATA VALUES(?)',(UUID,));c.execute('INSERT INTO ZICNOTEDATA VALUES(30,4,2,20,?,NULL,NULL)',(self.data,))
  self.path.chmod(0o600)
  env={'ICLOUD_ENABLE_NOTES':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'access_mode':'read-only','source_ids':[SOURCE],'metadata_backend':'notes-store-readonly'}})}
  for context in (patch.dict(os.environ,env,clear=True),patch.object(store,'store_path',return_value=self.path),patch('platform.system',return_value='Darwin'),patch('platform.mac_ver',return_value=('27.0',('','',''),''))):context.start();self.addCleanup(context.stop)
 def alter(self,query,params=()):
  with closing(sqlite3.connect(self.path))as c,c:c.execute(query,params)
 def read(self,**kwargs):return module.inventory(FOLDER,NOTE,reader=self.reader,**kwargs)
 def test_pages_stable_native_state_ids_content_and_no_database_write(self):
  before=hashlib.sha256(self.path.read_bytes()).hexdigest();a=self.read(limit=2);b=self.read(limit=2,offset=2);self.assertEqual(a['total'],3);self.assertEqual(a['checked'],1);self.assertEqual(a['next_offset'],2);self.assertTrue(b['done']);self.assertEqual(a['snapshot_sha256'],b['snapshot_sha256']);self.assertTrue(a['native_body_blob_read']);self.assertTrue(a['saved_local_state']);self.assertFalse(a['unsaved_ui_state_verified']);self.assertFalse(a['data_mutated']);self.assertFalse(a['representation_complete']);self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),before);self.assertEqual(self.read(offset=3)['items'],[])
 def test_page_bounds_and_bool_refuse_before_provider(self):
  for kwargs in ({'limit':True},{'limit':0},{'limit':21},{'offset':True},{'offset':-1},{'offset':1001}):
   with patch.object(self.reader,'read',side_effect=AssertionError('No read')),self.assertRaises(SafetyError):self.read(**kwargs)
 def test_disabled_opt_out_locked_truncated_and_unselected_refuse_before_blob(self):
  for env in ({'ICLOUD_ENABLE_NOTES':'0'},{'ICLOUD_MAIL_ACCESS':'disabled'},{'ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'source_ids':[SOURCE]}})}):
   with patch.dict(os.environ,env),patch.object(module,'_blob',side_effect=AssertionError('No blob')),self.assertRaises(SafetyError):self.read()
  for field,value in (('locked',True),('truncated',True),('source_id','unselected'),('folder_id','unselected')):
   prior=copy.deepcopy(self.reader.item);self.reader.item[field]=value
   with patch.object(module,'_blob',side_effect=AssertionError('No blob')),self.assertRaises(SafetyError):self.read()
   self.reader.item=prior
 def test_exact_note_folder_account_and_entity_relations_refuse_foreign_inputs(self):
  with self.assertRaises(SafetyError):module.inventory(FOLDER.replace('p10','p11'),NOTE,reader=self.reader)
  for query in ('UPDATE ZICCLOUDSYNCINGOBJECT SET ZFOLDER=11 WHERE Z_PK=20','UPDATE ZICCLOUDSYNCINGOBJECT SET Z_ENT=2 WHERE Z_PK=20','UPDATE ZICCLOUDSYNCINGOBJECT SET ZNOTEDATA=31 WHERE Z_PK=20','UPDATE ZICNOTEDATA SET Z_ENT=3 WHERE Z_PK=30','UPDATE ZICNOTEDATA SET ZNOTE=21 WHERE Z_PK=30'):
   with closing(sqlite3.connect(self.path))as c:backup='\n'.join(c.iterdump())
   self.alter(query)
   with self.subTest(query=query),self.assertRaises(SafetyError):self.read()
   self.path.unlink()
   with closing(sqlite3.connect(self.path))as c,c:c.executescript(backup)
   self.path.chmod(0o600)
 def test_encrypted_password_protected_deleted_and_oversized_blobs_refuse(self):
  for query,params in [('UPDATE ZICCLOUDSYNCINGOBJECT SET ZISPASSWORDPROTECTED=1 WHERE Z_PK=20',()),('UPDATE ZICCLOUDSYNCINGOBJECT SET ZMARKEDFORDELETION=1 WHERE Z_PK=20',()),('UPDATE ZICNOTEDATA SET ZCRYPTOTAG=? WHERE Z_PK=30',(b'private',)),('UPDATE ZICNOTEDATA SET ZCRYPTOINITIALIZATIONVECTOR=? WHERE Z_PK=30',(b'private',)),('UPDATE ZICNOTEDATA SET ZDATA=? WHERE Z_PK=30',(b'a'*(1048576+1),))]:
   self.alter(query,params)
   with self.subTest(query=query),self.assertRaises(SafetyError):self.read()
   self.alter('UPDATE ZICCLOUDSYNCINGOBJECT SET ZISPASSWORDPROTECTED=0,ZMARKEDFORDELETION=0 WHERE Z_PK=20');self.alter('UPDATE ZICNOTEDATA SET ZCRYPTOTAG=NULL,ZCRYPTOINITIALIZATIONVECTOR=NULL,ZDATA=? WHERE Z_PK=30',(self.data,))
 def test_missing_duplicate_and_unknown_data_rows_and_schema_refuse(self):
  self.alter('INSERT INTO ZICNOTEDATA VALUES(31,4,1,20,?,NULL,NULL)',(self.data,))
  with self.assertRaises(SafetyError):self.read()
  self.alter('DELETE FROM ZICNOTEDATA WHERE Z_PK=31');self.alter('ALTER TABLE ZICNOTEDATA RENAME COLUMN ZNOTE TO UNKNOWN_NOTE')
  with self.assertRaises(SafetyError):self.read()
 def test_mismatched_app_text_and_concurrent_blob_note_or_access_drift_refuse(self):
  self.reader.item['text']='Changed'
  with self.assertRaises(SafetyError):self.read()
  self.reader.item['text']=fixture()[0];decode=module.decode
  for change in (lambda:self.alter('UPDATE ZICNOTEDATA SET Z_OPT=Z_OPT+1 WHERE Z_PK=30'),lambda:self.alter('UPDATE ZICCLOUDSYNCINGOBJECT SET Z_OPT=Z_OPT+1 WHERE Z_PK=20'),lambda:os.environ.__setitem__('ICLOUD_MAIL_ACCESS','disabled')):
   def drift(data):value=decode(data);change();return value
   with patch.object(module,'decode',side_effect=drift),self.assertRaises(SafetyError):self.read()
   os.environ['ICLOUD_MAIL_ACCESS']='read-only'
 def test_authorizer_denies_writes_functions_unrelated_columns_and_tables(self):
  with store.readonly_store()as(db,_):
   db.set_authorizer(module._authorize)
   for query in ("UPDATE ZICNOTEDATA SET ZDATA=X'00'",'SELECT ZTITLE2 FROM ZICCLOUDSYNCINGOBJECT',"SELECT load_extension('x')",'CREATE TEMP TABLE x(a)',"ATTACH ':memory:' AS other",'PRAGMA user_version=1'):
    with self.subTest(query=query),self.assertRaises(sqlite3.Error):db.execute(query)
