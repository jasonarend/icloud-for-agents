"""Exact native Notes metadata/PDF reads using synthetic SQLite and owner files."""
import base64,copy,hashlib,json,os,sqlite3,tempfile,unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
import test_native_note_store as store_fixture
UUID,SOURCE,FOLDER=store_fixture.UUID,store_fixture.SOURCE,store_fixture.FOLDER
from icloud_for_agents import native_note_assets as assets
from icloud_for_agents.core import SafetyError
NOTE=f'x-coredata://{UUID}/ICNote/p20'
ASSET=f'x-coredata://{UUID}/ICAttachment/p30'
ACCOUNT_STORAGE='11111111-1111-4111-8111-111111111111'
ASSET_STORAGE='22222222-2222-4222-8222-222222222222'
GENERATION='1_33333333-3333-4333-8333-333333333333'
RAW=b'%PDF-1.4\nSynthetic generated representation only.\n%%EOF\n'

class Reader:
 def __init__(self):self.item={'id':NOTE,'folder_id':FOLDER,'source_id':SOURCE,'locked':False,'modified':'2026-01-01T00:00:00.000Z','text':'Synthetic note'};self.calls=0
 def read(self,action,**fields):
  assert action=='get' and fields=={'folder_id':FOLDER,'id':NOTE};self.calls+=1;return {'item':copy.deepcopy(self.item)}

class NativeAssetTests(unittest.TestCase):
 def setUp(self):
  temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.path=Path(temporary.name)/'NoteStore.sqlite'
  with closing(sqlite3.connect(self.path)) as c,c:
   c.executescript("""CREATE TABLE Z_METADATA(Z_UUID TEXT);
CREATE TABLE Z_PRIMARYKEY(Z_ENT INTEGER PRIMARY KEY,Z_NAME TEXT);
CREATE TABLE ZICCLOUDSYNCINGOBJECT(Z_PK INTEGER PRIMARY KEY,Z_ENT INTEGER,Z_OPT INTEGER,ZACCOUNT8 INTEGER,ZPARENT INTEGER,ZFOLDERTYPE INTEGER,ZMARKEDFORDELETION INTEGER,ZSMARTFOLDERQUERYJSON TEXT,ZTITLE2 TEXT);
INSERT INTO Z_PRIMARYKEY VALUES(1,'ICAccount'),(2,'ICFolder');
INSERT INTO ZICCLOUDSYNCINGOBJECT VALUES(1,1,1,NULL,NULL,NULL,0,NULL,NULL),(2,1,1,NULL,NULL,NULL,0,NULL,NULL),(10,2,3,1,NULL,0,0,NULL,'Synthetic folder'),(11,2,1,2,NULL,0,0,NULL,'Other account');""")
   c.execute('INSERT INTO Z_METADATA VALUES(?)',(UUID,))
   for name,kind in [('ZFOLDER','INTEGER'),('ZISPINNED','INTEGER'),('ZIDENTIFIER','TEXT'),('ZNOTE','INTEGER'),('ZTYPEUTI','TEXT'),('ZPARENTATTACHMENT','INTEGER'),('ZFALLBACKPDFGENERATION','TEXT'),('ZFALLBACKPDFCRYPTOINITIALIZATIONVECTOR','BLOB'),('ZFALLBACKPDFCRYPTOTAG','BLOB')]:c.execute('ALTER TABLE ZICCLOUDSYNCINGOBJECT ADD COLUMN '+name+' '+kind)
   c.execute("INSERT INTO Z_PRIMARYKEY VALUES(3,'ICNote'),(4,'ICAttachment')")
   c.execute('UPDATE ZICCLOUDSYNCINGOBJECT SET ZIDENTIFIER=? WHERE Z_PK=1',(ACCOUNT_STORAGE,))
   c.execute('INSERT INTO ZICCLOUDSYNCINGOBJECT(Z_PK,Z_ENT,Z_OPT,ZMARKEDFORDELETION,ZFOLDER,ZISPINNED) VALUES(20,3,1,0,10,0)')
   c.execute('INSERT INTO ZICCLOUDSYNCINGOBJECT(Z_PK,Z_ENT,Z_OPT,ZMARKEDFORDELETION,ZIDENTIFIER,ZNOTE,ZTYPEUTI,ZFALLBACKPDFGENERATION) VALUES(30,4,1,0,?,20,?,?)',(ASSET_STORAGE,'com.apple.paper.doc.pdf',GENERATION))
  self.path.chmod(0o600)
  for p in (patch.object(store_fixture.store,'store_path',return_value=self.path),patch('platform.system',return_value='Darwin'),patch('platform.mac_ver',return_value=('27.0',('','',''),''))):p.start();self.addCleanup(p.stop)
  self.group=self.path.parent/'group';self.file=self.group/'Accounts'/ACCOUNT_STORAGE/'FallbackPDFs'/ASSET_STORAGE/GENERATION/'FallbackPDF.pdf';self.file.parent.mkdir(parents=True);self.file.write_bytes(RAW);self.file.chmod(0o600);self.reader=Reader()
  for p in (patch.object(assets,'NativeReader',side_effect=lambda name:self.reader),patch.object(assets,'group_root',return_value=self.group),patch.dict(os.environ,{'HOME':str(self.path.parent),'ICLOUD_ENABLE_NOTES':'1','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_RUNTIME_ROOT':str(self.path.parent/'runtime'),'ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'source_ids':[SOURCE],'access_mode':'read-only','metadata_backend':'notes-store-readonly'}})},clear=True)):
   p.start();self.addCleanup(p.stop)
 def alter(self,query):
  with closing(sqlite3.connect(self.path)) as c,c:c.execute(query)
 def export(self,**kwargs):return assets.export_pdf(FOLDER,NOTE,ASSET,reader=self.reader,**kwargs)
 def test_pin_metadata_true_false_and_unavailable_are_distinct(self):
  before=hashlib.sha256(self.path.read_bytes()).hexdigest();value=assets.metadata(FOLDER,NOTE);self.assertIs(value['pinned'],False);self.assertFalse(value['content_read']);self.assertFalse(value['data_mutated']);self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),before)
  self.alter('UPDATE ZICCLOUDSYNCINGOBJECT SET ZISPINNED=1 WHERE Z_PK=20');self.assertIs(assets.metadata(FOLDER,NOTE)['pinned'],True)
  self.alter('UPDATE ZICCLOUDSYNCINGOBJECT SET ZISPINNED=NULL WHERE Z_PK=20');value=assets.metadata(FOLDER,NOTE);self.assertIsNone(value['pinned']);self.assertEqual(value['pinned_status'],'unavailable')
  self.alter('UPDATE ZICCLOUDSYNCINGOBJECT SET ZISPINNED=2 WHERE Z_PK=20')
  with self.assertRaises(SafetyError):assets.metadata(FOLDER,NOTE)
 def test_inventory_is_direct_live_partial_metadata_without_foreign_parent_disclosure(self):
  self.alter("INSERT INTO ZICCLOUDSYNCINGOBJECT(Z_PK,Z_ENT,Z_OPT,ZNOTE,ZMARKEDFORDELETION) VALUES(31,4,1,21,0),(32,4,1,20,1)")
  self.alter('UPDATE ZICCLOUDSYNCINGOBJECT SET ZPARENTATTACHMENT=999 WHERE Z_PK=30');value=assets.inventory(FOLDER,NOTE);self.assertEqual(len(value['assets']),1);row=value['assets'][0];self.assertEqual(row['id'],ASSET);self.assertTrue(row['generated_pdf_candidate']);self.assertIsNone(row['parent_asset_id']);self.assertEqual(row['parent_status'],'outside_observed_scope');self.assertFalse(value['native_asset_inventory_complete']);self.assertNotIn(ACCOUNT_STORAGE,json.dumps(value));self.assertNotIn(ASSET_STORAGE,json.dumps(value))
 def test_generated_pdf_bytes_hash_scope_and_unchanged_store(self):
  before=self.path.read_bytes();value=self.export();self.assertEqual(base64.b64decode(value['data']),RAW);self.assertEqual(value['size'],len(RAW));self.assertEqual(value['sha256'],hashlib.sha256(RAW).hexdigest());self.assertEqual(value['asset_id'],ASSET);self.assertFalse(value['original_file']);self.assertFalse(value['complete_backup']);self.assertFalse(value['download_attempted']);self.assertFalse(value['data_mutated']);self.assertEqual(value['representation'],'Notes_generated_fallback_pdf');self.assertEqual(self.path.read_bytes(),before);self.assertEqual(self.reader.calls,2)
 def test_disabled_or_unselected_backend_refuses_before_store(self):
  with patch.object(assets,'readonly_store',side_effect=AssertionError('No database access')):
   for mode,config,enabled in [('disabled',{'metadata_backend':'notes-store-readonly','source_ids':[SOURCE]},'1'),('read-only',{'source_ids':[SOURCE]},'1'),('read-only',{'metadata_backend':'notes-store-readonly','source_ids':[]},'1'),('read-only',{'metadata_backend':'notes-store-readonly','source_ids':[SOURCE]},'0')]:
    with patch.dict(os.environ,{'ICLOUD_MAIL_ACCESS':mode,'ICLOUD_NATIVE_CONFIG':json.dumps({'notes':config}),'ICLOUD_ENABLE_NOTES':enabled}):
     with self.assertRaises(SafetyError):assets.metadata(FOLDER,NOTE)
 def test_wrong_store_folder_asset_and_identity_refuse_before_file(self):
  with patch.object(assets,'_pdf_bytes',side_effect=AssertionError('No file read')):
   for folder,note,asset in [(FOLDER.replace('p10','p11'),NOTE,ASSET),(FOLDER,NOTE,ASSET.replace('p30','p31')),(FOLDER,NOTE,ASSET.replace(UUID,'00000000-0000-0000-0000-000000000002')),(FOLDER,NOTE,ASSET+'/../../secret'),(FOLDER,NOTE,ASSET.replace('ICAttachment','ICNote'))]:
    with self.assertRaises(SafetyError):assets.export_pdf(folder,note,asset,reader=self.reader)
 def test_locked_inventory_refuses_before_native_asset_query(self):
  self.reader.item['locked']=True
  with patch.object(assets,'_state',side_effect=AssertionError('No asset database query')),self.assertRaises(SafetyError):assets.inventory(FOLDER,NOTE)
 def test_locked_unverified_scope_and_crypto_never_open_file(self):
  with patch.object(assets,'_pdf_bytes',side_effect=AssertionError('No file read')):
   for patch_value in ({'locked':True},{'source_id':'foreign'},{'folder_id':'foreign'},{'id':'foreign'}):
    old=copy.deepcopy(self.reader.item);self.reader.item.update(patch_value)
    with self.assertRaises(SafetyError):self.export()
    self.reader.item=old
   self.alter("UPDATE ZICCLOUDSYNCINGOBJECT SET ZFALLBACKPDFCRYPTOTAG=X'01' WHERE Z_PK=30")
   with self.assertRaises(SafetyError):self.export()
 def test_unsupported_type_generation_and_over_limit_refuse(self):
  for change in ("ZTYPEUTI='public.png'","ZFALLBACKPDFGENERATION='../../outside'","ZFALLBACKPDFGENERATION='1_invalid'"):
   self.alter('UPDATE ZICCLOUDSYNCINGOBJECT SET '+change+' WHERE Z_PK=30')
   with self.assertRaises(SafetyError):self.export()
   self.alter("UPDATE ZICCLOUDSYNCINGOBJECT SET ZTYPEUTI='com.apple.paper.doc.pdf',ZFALLBACKPDFGENERATION='"+GENERATION+"' WHERE Z_PK=30")
  for maximum in (True,0,1048577,len(RAW)-1):
   with self.assertRaises(SafetyError):self.export(maximum=maximum)
 def test_symlink_hardlink_fifo_world_writable_and_nonpdf_refuse(self):
  outside=self.path.parent/'outside';outside.write_bytes(RAW)
  for kind in ('symlink','hardlink','fifo','writable','nonpdf'):
   self.file.unlink()
   if kind=='symlink':self.file.symlink_to(outside)
   elif kind=='hardlink':os.link(outside,self.file)
   elif kind=='fifo':os.mkfifo(self.file)
   else:self.file.write_bytes(b'not a PDF' if kind=='nonpdf' else RAW);self.file.chmod(0o666 if kind=='writable' else 0o600)
   with self.subTest(kind=kind),self.assertRaises(SafetyError):self.export()
 def test_symlink_directory_refused(self):
  original=self.group/'Accounts'/ACCOUNT_STORAGE;replacement=self.group/'other';original.rename(replacement);original.symlink_to(replacement,target_is_directory=True)
  with self.assertRaises(SafetyError):self.export()
 def test_metadata_and_native_note_drift_and_revocation_refuse(self):
  read=assets._pdf_bytes
  def changed(*args):
   value=read(*args);self.alter('UPDATE ZICCLOUDSYNCINGOBJECT SET Z_OPT=Z_OPT+1 WHERE Z_PK=30');return value
  with patch.object(assets,'_pdf_bytes',side_effect=changed),self.assertRaises(SafetyError):self.export()
  calls=0
  def native_drift(action,**fields):
   nonlocal calls
   calls+=1;value={'item':copy.deepcopy(self.reader.item)}
   if calls==2:value['item']['modified']='2026-01-02T00:00:00.000Z'
   return value
  with patch.object(self.reader,'read',side_effect=native_drift),self.assertRaises(SafetyError):self.export()
  def revoked(*args):
   value=read(*args);os.environ['ICLOUD_MAIL_ACCESS']='disabled';return value
  with patch.object(assets,'_pdf_bytes',side_effect=revoked),self.assertRaises(SafetyError):self.export()
 def test_same_descriptor_file_change_refuses(self):
  read=os.read;changed=False
  def drift(fd,count):
   nonlocal changed
   value=read(fd,count)
   if not changed:
    changed=True;info=self.file.stat();os.utime(self.file,ns=(info.st_atime_ns,info.st_mtime_ns+1000000))
   return value
  with patch.object(assets.os,'read',side_effect=drift),self.assertRaises(SafetyError):self.export()
 def test_deleted_or_foreign_note_and_oversized_inventory_refuse(self):
  for change in ('ZMARKEDFORDELETION=1','ZFOLDER=11','Z_ENT=2'):
   self.alter('UPDATE ZICCLOUDSYNCINGOBJECT SET '+change+' WHERE Z_PK=20')
   with self.assertRaises(SafetyError):assets.inventory(FOLDER,NOTE)
   self.alter('UPDATE ZICCLOUDSYNCINGOBJECT SET ZMARKEDFORDELETION=0,ZFOLDER=10,Z_ENT=3 WHERE Z_PK=20')
  with closing(sqlite3.connect(self.path)) as c,c:
   for n in range(100):c.execute('INSERT INTO ZICCLOUDSYNCINGOBJECT(Z_PK,Z_ENT,Z_OPT,ZNOTE,ZMARKEDFORDELETION) VALUES(?,4,1,20,0)',(100+n,))
  with self.assertRaises(SafetyError):assets.inventory(FOLDER,NOTE)

 def tag_fixture(self):
  with closing(sqlite3.connect(self.path)) as c,c:
   for column,kind in [('ZNOTE1','INTEGER'),('ZACCOUNT4','INTEGER'),('ZTYPEUTI1','TEXT'),('ZALTTEXT','TEXT'),('ZTOKENCONTENTIDENTIFIER','TEXT')]:c.execute('ALTER TABLE ZICCLOUDSYNCINGOBJECT ADD COLUMN '+column+' '+kind)
   c.execute("INSERT INTO Z_PRIMARYKEY VALUES(5,'ICInlineAttachment')")
   c.execute('INSERT INTO ZICCLOUDSYNCINGOBJECT(Z_PK,Z_ENT,Z_OPT,ZMARKEDFORDELETION,ZNOTE1,ZACCOUNT4,ZIDENTIFIER,ZTYPEUTI1,ZALTTEXT,ZTOKENCONTENTIDENTIFIER) VALUES(40,5,2,0,20,1,?,?,?,?)',(ASSET_STORAGE,'com.apple.notes.inlinetextattachment.hashtag','#Synthetic','SYNTHETIC'))

 def tags(self):
  from icloud_for_agents.native_note_tags import inventory
  return inventory(FOLDER,NOTE,reader=self.reader)

 def test_native_inline_tags_preserve_provider_case_without_plaintext_inference(self):
  self.tag_fixture();before=self.path.read_bytes();self.reader.item['text']='Ordinary #not-a-native-tag text';value=self.tags()
  self.assertEqual(value['tags'],[{'id':f'x-coredata://{UUID}/ICInlineAttachment/p40','revision':2,'display_text':'#Synthetic','native_token':'SYNTHETIC'}]);self.assertFalse(value['native_tag_inventory_complete']);self.assertFalse(value['data_mutated']);self.assertFalse(value['content_returned']);self.assertEqual(self.path.read_bytes(),before)
  self.alter('UPDATE ZICCLOUDSYNCINGOBJECT SET ZMARKEDFORDELETION=1 WHERE Z_PK=40');self.assertEqual(self.tags()['tags'],[])

 def test_native_tags_bind_account_entity_note_and_tag_type(self):
  self.tag_fixture()
  self.alter('UPDATE ZICCLOUDSYNCINGOBJECT SET ZACCOUNT4=2 WHERE Z_PK=40')
  with self.assertRaises(SafetyError):self.tags()
  self.alter('UPDATE ZICCLOUDSYNCINGOBJECT SET ZACCOUNT4=1 WHERE Z_PK=40')
  for change in ('ZNOTE1=21','Z_ENT=4',"ZTYPEUTI1='other.inline.type'"):
   self.alter('UPDATE ZICCLOUDSYNCINGOBJECT SET '+change+' WHERE Z_PK=40');self.assertEqual(self.tags()['tags'],[])
   self.alter("UPDATE ZICCLOUDSYNCINGOBJECT SET ZNOTE1=20,Z_ENT=5,ZTYPEUTI1='com.apple.notes.inlinetextattachment.hashtag' WHERE Z_PK=40")

 def test_native_tags_locked_disabled_and_missing_schema_refuse(self):
  from icloud_for_agents import native_note_tags as tags
  self.reader.item['locked']=True
  with patch.object(tags,'_tags',side_effect=AssertionError('No native metadata')),self.assertRaises(SafetyError):self.tags()
  self.reader.item['locked']=False
  with self.assertRaises(SafetyError):self.tags()
  self.tag_fixture()
  with patch.dict(os.environ,{'ICLOUD_MAIL_ACCESS':'disabled'}),patch.object(tags,'_tags',side_effect=AssertionError('No native metadata')),self.assertRaises(SafetyError):self.tags()

 def test_native_tags_malformed_display_token_identity_and_count_refuse(self):
  self.tag_fixture()
  for change in ("ZALTTEXT='plain'","ZALTTEXT='#'","ZTOKENCONTENTIDENTIFIER=NULL","ZIDENTIFIER='../../unsafe'","ZTOKENCONTENTIDENTIFIER='"+'x'*1001+"'"):
   self.alter('UPDATE ZICCLOUDSYNCINGOBJECT SET '+change+' WHERE Z_PK=40')
   with self.assertRaises(SafetyError):self.tags()
   with closing(sqlite3.connect(self.path)) as c,c:c.execute('UPDATE ZICCLOUDSYNCINGOBJECT SET ZALTTEXT=?,ZTOKENCONTENTIDENTIFIER=?,ZIDENTIFIER=? WHERE Z_PK=40',('#Synthetic','SYNTHETIC',ASSET_STORAGE))
  with closing(sqlite3.connect(self.path)) as c,c:
   for n in range(100):c.execute('INSERT INTO ZICCLOUDSYNCINGOBJECT(Z_PK,Z_ENT,Z_OPT,ZMARKEDFORDELETION,ZNOTE1,ZTYPEUTI1) VALUES(?,5,1,0,20,?)',(100+n,'com.apple.notes.inlinetextattachment.hashtag'))
  with self.assertRaises(SafetyError):self.tags()

 def test_native_tag_observation_drift_and_revocation_refuse(self):
  from icloud_for_agents import native_note_tags as tags
  self.tag_fixture();read=tags._tags;calls=0
  def changed(*args):
   nonlocal calls
   value=read(*args);calls+=1
   if calls==1:self.alter('UPDATE ZICCLOUDSYNCINGOBJECT SET Z_OPT=Z_OPT+1 WHERE Z_PK=40')
   return value
  with patch.object(tags,'_tags',side_effect=changed),self.assertRaises(SafetyError):self.tags()
  def revoked(*args):
   value=read(*args);os.environ['ICLOUD_MAIL_ACCESS']='disabled';return value
  with patch.object(tags,'_tags',side_effect=revoked),self.assertRaises(SafetyError):self.tags()
