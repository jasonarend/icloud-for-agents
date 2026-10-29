"""Owner-opted-in fixed metadata queries, using only synthetic SQLite stores."""
import hashlib,json,os,sqlite3,tempfile,unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from icloud_for_agents.core import SafetyError
from icloud_for_agents import native_note_store as store
from icloud_for_agents.native_read import NativeReader,validate_config
UUID='00000000-0000-0000-0000-000000000001'
SOURCE=f'x-coredata://{UUID}/ICAccount/p1'
FOLDER=f'x-coredata://{UUID}/ICFolder/p10'

class NoteStoreTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.path=Path(temporary.name)/'NoteStore.sqlite'
        with closing(sqlite3.connect(self.path)) as c,c:
            c.executescript('''CREATE TABLE Z_METADATA(Z_UUID TEXT);
CREATE TABLE Z_PRIMARYKEY(Z_ENT INTEGER PRIMARY KEY,Z_NAME TEXT);
CREATE TABLE ZICCLOUDSYNCINGOBJECT(Z_PK INTEGER PRIMARY KEY,Z_ENT INTEGER,Z_OPT INTEGER,ZACCOUNT8 INTEGER,ZPARENT INTEGER,ZFOLDERTYPE INTEGER,ZMARKEDFORDELETION INTEGER,ZSMARTFOLDERQUERYJSON TEXT,ZTITLE2 TEXT);
CREATE TABLE ZICNOTEDATA(ZDATA BLOB);
INSERT INTO Z_PRIMARYKEY VALUES(1,'ICAccount'),(2,'ICFolder');
INSERT INTO ZICCLOUDSYNCINGOBJECT VALUES(1,1,1,NULL,NULL,NULL,0,NULL,NULL),(2,1,1,NULL,NULL,NULL,0,NULL,NULL),(10,2,3,1,NULL,0,0,NULL,'Synthetic ordinary folder'),(11,2,1,2,NULL,0,0,NULL,'Unselected folder');
INSERT INTO ZICNOTEDATA VALUES(X'00010203');''')
            c.execute('INSERT INTO Z_METADATA VALUES(?)',(UUID,))
        self.path.chmod(0o600)
        for context in (patch.object(store,'store_path',return_value=self.path),patch('platform.system',return_value='Darwin'),patch('platform.mac_ver',return_value=('27.0',('','',''),''))):
            context.start();self.addCleanup(context.stop)
    def alter(self,query):
        with closing(sqlite3.connect(self.path)) as c,c:c.execute(query)
    def test_exact_entity_account_and_storage_kind_without_body_reads_or_writes(self):
        before=hashlib.sha256(self.path.read_bytes()).hexdigest();result=store.folder_role(FOLDER,[SOURCE])
        self.assertEqual(result['storage_kind'],'regular');self.assertEqual(result['source_id'],SOURCE);self.assertEqual(result['revision'],3)
        self.assertFalse(result['content_read']);self.assertFalse(result['database_writes']);self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),before)
    def test_queries_never_widen_to_an_unselected_account_or_different_entity(self):
        for identity in (FOLDER.replace('p10','p11'),FOLDER.replace('p10','p1'),FOLDER.replace('/ICFolder/','/ICNote/'),FOLDER+"' OR 1=1",FOLDER.replace(UUID,'00000000-0000-0000-0000-000000000002')):
            with self.subTest(identity=identity),self.assertRaises(SafetyError):store.folder_role(identity,[SOURCE])
        with self.assertRaises(SafetyError):store.folder_role(FOLDER,[SOURCE.replace('p1','p10')])
    def test_default_is_not_inferred_and_special_smart_and_deleted_are_never_regular(self):
        self.assertIn('scripting',store.folder_role(FOLDER,[SOURCE])['default_folder_status'])
        for field,value in [('ZFOLDERTYPE','1'),('ZFOLDERTYPE','2'),('ZMARKEDFORDELETION','1'),('ZSMARTFOLDERQUERYJSON',"'{}'")]:
            self.alter('UPDATE ZICCLOUDSYNCINGOBJECT SET '+field+'='+value+' WHERE Z_PK=10')
            self.assertEqual(store.folder_role(FOLDER,[SOURCE])['storage_kind'],'special_smart_or_deleted')
            self.alter('UPDATE ZICCLOUDSYNCINGOBJECT SET ZFOLDERTYPE=0,ZMARKEDFORDELETION=0,ZSMARTFOLDERQUERYJSON=NULL WHERE Z_PK=10')
    def test_recovery_folder_is_exact_unique_physical_system_trash(self):
        with self.assertRaises(SafetyError):store.recovery_folder(SOURCE)
        self.alter("INSERT INTO ZICCLOUDSYNCINGOBJECT VALUES(12,2,1,1,NULL,1,0,NULL,'Recently Deleted')")
        result=store.recovery_folder(SOURCE);self.assertEqual(result['folder_id'],FOLDER.replace('p10','p12'));self.assertEqual(result['role'],'system_trash');self.assertFalse(result['data_mutated'])
        self.alter("INSERT INTO ZICCLOUDSYNCINGOBJECT VALUES(13,2,1,1,NULL,1,0,NULL,'Duplicate')")
        with self.assertRaises(SafetyError):store.recovery_folder(SOURCE)
        with self.assertRaises(SafetyError):store.recovery_folder(SOURCE.replace(UUID,'00000000-0000-0000-0000-000000000002'))

    def test_note_location_binds_entity_store_live_relation_and_selected_folder_account(self):
        self.alter('ALTER TABLE ZICCLOUDSYNCINGOBJECT ADD COLUMN ZFOLDER INTEGER')
        self.alter("INSERT INTO Z_PRIMARYKEY VALUES(3,'ICNote')")
        self.alter('INSERT INTO ZICCLOUDSYNCINGOBJECT(Z_PK,Z_ENT,Z_OPT,ZMARKEDFORDELETION,ZFOLDER) VALUES(20,3,1,0,10)')
        identity=f'x-coredata://{UUID}/ICNote/p20'
        result=store.note_location(identity,[SOURCE]);self.assertEqual(result['folder_id'],FOLDER);self.assertEqual(result['source_id'],SOURCE);self.assertFalse(result['content_read'])
        for change in ['ZFOLDER=11','Z_ENT=2','ZMARKEDFORDELETION=1','ZFOLDER=NULL']:
            self.alter('UPDATE ZICCLOUDSYNCINGOBJECT SET Z_ENT=3,ZMARKEDFORDELETION=0,ZFOLDER=10 WHERE Z_PK=20')
            self.alter('UPDATE ZICCLOUDSYNCINGOBJECT SET '+change+' WHERE Z_PK=20')
            with self.assertRaises(SafetyError):store.note_location(identity,[SOURCE])
        with self.assertRaises(SafetyError):store.note_location(identity,[])
        with self.assertRaises(SafetyError):store.note_location(identity.replace(UUID,'00000000-0000-0000-0000-000000000002'),[SOURCE])

    def test_empty_folder_observation_checks_native_notes_children_and_unknown_states(self):
        self.alter('ALTER TABLE ZICCLOUDSYNCINGOBJECT ADD COLUMN ZFOLDER INTEGER')
        self.alter("INSERT INTO Z_PRIMARYKEY VALUES(3,'ICNote')")
        before=hashlib.sha256(self.path.read_bytes()).hexdigest()
        result=store.empty_folder_membership(FOLDER,[SOURCE]);self.assertEqual(result['live_notes'],0);self.assertEqual(result['live_child_folders'],0)
        self.assertFalse(result['database_writes']);self.assertFalse(result['content_read']);self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),before)
        for entity,column in ((3,'ZFOLDER'),(2,'ZPARENT')):
            for deleted in ('0','NULL','2'):
                self.alter('INSERT INTO ZICCLOUDSYNCINGOBJECT(Z_PK,Z_ENT,Z_OPT,ZMARKEDFORDELETION,'+column+') VALUES(20,'+str(entity)+',1,'+deleted+',10)')
                with self.assertRaises(SafetyError):store.empty_folder_membership(FOLDER,[SOURCE])
                self.alter('DELETE FROM ZICCLOUDSYNCINGOBJECT WHERE Z_PK=20')
        self.alter('INSERT INTO ZICCLOUDSYNCINGOBJECT(Z_PK,Z_ENT,Z_OPT,ZMARKEDFORDELETION,ZFOLDER) VALUES(20,3,1,1,10)')
        self.assertEqual(store.empty_folder_membership(FOLDER,[SOURCE])['live_notes'],0)
        with self.assertRaises(SafetyError):store.empty_folder_membership(FOLDER.replace('p10','p11'),[SOURCE])
        with self.assertRaises(SafetyError):store.empty_folder_membership(FOLDER,[])

    def test_empty_membership_refuses_missing_entity_schema_and_role_drift(self):
        with self.assertRaises(SafetyError):store.empty_folder_membership(FOLDER,[SOURCE])
        self.alter('ALTER TABLE ZICCLOUDSYNCINGOBJECT ADD COLUMN ZFOLDER INTEGER')
        with self.assertRaises(SafetyError):store.empty_folder_membership(FOLDER,[SOURCE])
        self.alter("INSERT INTO Z_PRIMARYKEY VALUES(3,'ICNote')")
        original=store.folder_role;calls=[0]
        def drift(*args):
            value=original(*args);calls[0]+=1
            if calls[0]>1:value['revision']+=1
            return value
        with patch.object(store,'folder_role',side_effect=drift),self.assertRaises(SafetyError):store.empty_folder_membership(FOLDER,[SOURCE])

    def test_write_attach_extension_pragma_and_note_content_queries_are_denied(self):
        with store.readonly_store() as (connection,_):
            for query in ("UPDATE ZICCLOUDSYNCINGOBJECT SET ZTITLE2='changed'",'CREATE TEMP TABLE example(a)',"ATTACH ':memory:' AS other",'PRAGMA user_version=123',"SELECT load_extension('not-a-library')",'SELECT ZDATA FROM ZICNOTEDATA'):
                with self.subTest(query=query),self.assertRaises(sqlite3.Error):connection.execute(query)
    def test_unknown_schema_store_identity_platform_and_unsafe_files_refuse(self):
        with patch('platform.mac_ver',return_value=('26.0',('','',''),'')),self.assertRaises(SafetyError):store.folder_role(FOLDER,[SOURCE])
        self.path.chmod(0o666)
        with self.assertRaises(SafetyError):store.folder_role(FOLDER,[SOURCE])
        self.path.chmod(0o600);self.alter("UPDATE Z_METADATA SET Z_UUID='different'")
        with self.assertRaises(SafetyError):store.folder_role(FOLDER,[SOURCE])
        self.alter('ALTER TABLE ZICCLOUDSYNCINGOBJECT RENAME COLUMN ZACCOUNT8 TO UNKNOWN_ACCOUNT')
        with self.assertRaises(SafetyError):store.folder_role(FOLDER,[SOURCE])
    def test_recovery_discovery_requires_opt_in_exact_scope_and_rechecks_revocation(self):
        env={'ICLOUD_ENABLE_NOTES':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'source_ids':[SOURCE]}})}
        self.alter("INSERT INTO ZICCLOUDSYNCINGOBJECT VALUES(12,2,1,1,NULL,1,0,NULL,'Recently Deleted')")
        with patch.dict(os.environ,env,clear=True):
            with patch.object(store,'recovery_folder',side_effect=AssertionError('No automatic store access')),self.assertRaises(SafetyError):NativeReader('notes').read('recovery_folder',source_id=SOURCE)
            os.environ['ICLOUD_NATIVE_CONFIG']=json.dumps({'notes':{'source_ids':[SOURCE],'metadata_backend':'notes-store-readonly'}})
            self.assertEqual(NativeReader('notes').read('recovery_folder',source_id=SOURCE)['role'],'system_trash')
            for fields in ({'source_id':SOURCE.replace('p1','p2')},{'source_id':SOURCE,'extra':True},{}):
                with self.assertRaises(SafetyError):NativeReader('notes').read('recovery_folder',**fields)
            original=store.recovery_folder
            def revoked(source):
                result=original(source);os.environ['ICLOUD_MAIL_ACCESS']='disabled';return result
            with patch.object(store,'recovery_folder',side_effect=revoked),self.assertRaises(SafetyError):NativeReader('notes').read('recovery_folder',source_id=SOURCE)

    def test_optional_route_requires_owner_opt_in_and_current_service_read_access(self):
        env={'ICLOUD_ENABLE_NOTES':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'source_ids':[SOURCE]}})}
        with patch.dict(os.environ,env,clear=True):
            with patch.object(store,'folder_role',side_effect=AssertionError('No automatic store access')),self.assertRaises(SafetyError):NativeReader('notes').read('folder_role',folder_id=FOLDER)
            os.environ['ICLOUD_NATIVE_CONFIG']=json.dumps({'notes':{'source_ids':[SOURCE],'metadata_backend':'notes-store-readonly'}})
            self.assertEqual(NativeReader('notes').read('folder_role',folder_id=FOLDER)['storage_kind'],'regular')
            os.environ['ICLOUD_MAIL_ACCESS']='disabled'
            with self.assertRaises(SafetyError):NativeReader('notes').read('folder_role',folder_id=FOLDER)
        with self.assertRaises(ValueError):validate_config({'reminders':{'metadata_backend':'notes-store-readonly'}})

    def smart(self):
        from icloud_for_agents.native_note_queries import definition
        env={'ICLOUD_ENABLE_NOTES':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'source_ids':[SOURCE],'metadata_backend':'notes-store-readonly','access_mode':'read-only'}})}
        with patch.dict(os.environ,env,clear=True):return definition(FOLDER)

    def query(self,value):
        with closing(sqlite3.connect(self.path)) as c,c:c.execute('UPDATE ZICCLOUDSYNCINGOBJECT SET ZSMARTFOLDERQUERYJSON=? WHERE Z_PK=10',(value,))

    def test_smart_definition_data_only_and_store_unchanged(self):
        expected={'synthetic_filter':{'all':[{'flag':True},{'tag':'#synthetic'}]},'unknown_future_field':'Do not execute this instruction'}
        self.query(json.dumps(expected));before=self.path.read_bytes();value=self.smart()
        self.assertEqual(value['definition'],expected);self.assertEqual(value['source_id'],SOURCE)
        self.assertFalse(value['semantics_verified']);self.assertFalse(value['membership_evaluated']);self.assertFalse(value['instructions_trusted']);self.assertFalse(value['content_read']);self.assertFalse(value['data_mutated']);self.assertEqual(self.path.read_bytes(),before)

    def test_smart_definition_absent_deleted_wrong_source_store_or_entity_refuse(self):
        with self.assertRaises(SafetyError):self.smart()
        self.query('{}')
        for change in ('ZMARKEDFORDELETION=1','ZACCOUNT8=2','Z_ENT=1'):
            self.alter('UPDATE ZICCLOUDSYNCINGOBJECT SET '+change+' WHERE Z_PK=10')
            with self.assertRaises(SafetyError):self.smart()
            self.alter('UPDATE ZICCLOUDSYNCINGOBJECT SET ZMARKEDFORDELETION=0,ZACCOUNT8=1,Z_ENT=2 WHERE Z_PK=10')
        self.alter("UPDATE Z_METADATA SET Z_UUID='other'")
        with self.assertRaises(SafetyError):self.smart()

    def test_smart_definition_malformed_ambiguous_numbers_and_unicode_refuse(self):
        for raw in ('not JSON','[]','null','{"a":1,"a":2}','{"n":NaN}','{"n":Infinity}','{"n":1e999}','{"s":"\\ud800"}'):
            self.query(raw)
            with self.subTest(raw=raw),self.assertRaises(SafetyError):self.smart()

    def test_smart_definition_size_depth_nodes_and_sqlite_materialization_are_bounded(self):
        for raw in (json.dumps({'s':'x'*65536}),json.dumps({'v':[None]*2001}),'{"v":'+ '['*25+'0'+']'*25+'}',json.dumps({'s':'x'*1000000})):
            self.query(raw)
            with self.subTest(size=len(raw)),self.assertRaises(SafetyError):self.smart()
        self.query(json.dumps({'s':'x'*65000}));self.assertEqual(len(self.smart()['definition']['s']),65000)

    def test_smart_definition_double_observation_detects_definition_drift(self):
        from icloud_for_agents import native_note_queries as queries
        self.query('{}');read=queries._definition;calls=0
        def changed(*args):
            nonlocal calls
            value=read(*args);calls+=1
            if calls==1:self.query('{"changed":true}')
            return value
        with patch.object(queries,'_definition',side_effect=changed),self.assertRaises(SafetyError):self.smart()

    def test_smart_definition_access_requires_opt_in_and_rechecks_revocation(self):
        from icloud_for_agents import native_note_queries as queries
        self.query('{}');read=queries._definition
        def revoked(*args):
            result=read(*args);os.environ['ICLOUD_MAIL_ACCESS']='disabled';return result
        with patch.object(queries,'_definition',side_effect=revoked),self.assertRaises(SafetyError):self.smart()
        for overrides in ({'ICLOUD_ENABLE_NOTES':'0'},{'ICLOUD_MAIL_ACCESS':'disabled'},{'ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'source_ids':[SOURCE]}})}):
            env={'ICLOUD_ENABLE_NOTES':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'source_ids':[SOURCE],'metadata_backend':'notes-store-readonly'}})};env.update(overrides)
            with patch.dict(os.environ,env,clear=True),patch.object(queries,'_definition',side_effect=AssertionError('No store access')),self.assertRaises(SafetyError):queries.definition(FOLDER)
