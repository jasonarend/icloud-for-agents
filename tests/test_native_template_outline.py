"""Fixed-query saved-template outlines over isolated synthetic SQLite stores."""
import copy,json,os,sqlite3,tempfile,unittest,uuid
from pathlib import Path
from unittest.mock import patch
from icloud_for_agents.core import SafetyError
from icloud_for_agents import native_reminder_template_store as store

SOURCE='11111111-1111-4111-8111-111111111111';TEMPLATE='22222222-2222-4222-8222-222222222222'
ROOT='33333333-3333-4333-8333-333333333333';CHILD='44444444-4444-4444-8444-444444444444';OTHER='55555555-5555-4555-8555-555555555555'
STORE='66666666-6666-4666-8666-666666666666'


class Reader:
    def __init__(self):self.calls=[];self.after=None
    def read(self,action,**fields):
        assert action=='template_sections' and fields=={'source_id':SOURCE,'id':TEMPLATE}
        self.calls.append(action)
        value={'protocol_version':1,'source_id':SOURCE,'id':TEMPLATE,'sections':[],'sections_complete':False,
               'ordering_verified':False,'membership_verified':False,'data_mutated':False,'backend':'reminderkit-template-sections-v1'}
        if self.after:self.after(len(self.calls),value)
        return value


def create(path):
    connection=sqlite3.connect(path)
    for table,columns in store.REQUIRED.items():
        fields=[]
        for name in sorted(columns):
            kind='BLOB' if name in {'ZIDENTIFIER','ZPARENTSAVEDREMINDERIDENTIFIER'} else 'TEXT' if name in {'Z_NAME','Z_UUID','ZNAME','ZTITLE'} else 'INTEGER'
            fields.append(name+' '+kind)
        if table=='ZREMCDSAVEDREMINDER':fields.append('ZMETADATA BLOB')
        connection.execute('CREATE TABLE '+table+' ('+','.join(fields)+')')
    connection.execute('INSERT INTO Z_METADATA VALUES (?)',(STORE,))
    connection.executemany('INSERT INTO Z_PRIMARYKEY (Z_NAME,Z_ENT) VALUES (?,?)',[('REMCDAccount',10),('REMCDTemplate',11),('REMCDSavedReminder',12)])
    connection.execute('INSERT INTO ZREMCDOBJECT (Z_PK,Z_ENT,ZIDENTIFIER,ZINACTIVE,ZMARKEDFORDELETION) VALUES (1,10,?,0,0)',(uuid.UUID(SOURCE).bytes,))
    connection.execute('INSERT INTO ZREMCDTEMPLATE (Z_PK,Z_ENT,Z_OPT,ZIDENTIFIER,ZACCOUNT,ZPARENTACCOUNT,ZMARKEDFORDELETION,ZNAME) VALUES (2,11,1,?,1,1,0,?)',(uuid.UUID(TEMPLATE).bytes,'Synthetic template'))
    for identity,parent,title in ((ROOT,None,'Synthetic root'),(CHILD,ROOT,'Synthetic child')):
        connection.execute('INSERT INTO ZREMCDSAVEDREMINDER (Z_ENT,Z_OPT,ZACCOUNT,ZTEMPLATE,ZIDENTIFIER,ZPARENTSAVEDREMINDERIDENTIFIER,ZTITLE,ZPRIORITY,ZHASHASHTAGS,ZHASLOCATIONTRIGGERSORVEHICLEEVENTTRIGGERS,ZMARKEDFORDELETION,ZMETADATA) VALUES (12,1,1,2,?,?,?,0,0,0,0,?)',
                           (uuid.UUID(identity).bytes,None if parent is None else uuid.UUID(parent).bytes,title,b'Private body blob must never be read or returned'))
    connection.commit();connection.close()


class TemplateOutlineTests(unittest.TestCase):
    def setUp(self):
        env={'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_APPROVAL_POLICY':'trusted-client',
             'ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'source_ids':[SOURCE],'helper_path':'/synthetic/helper','advanced_backend':'reminderkit','metadata_backend':'reminders-store-readonly','access_mode':'read-only'}})}
        context=patch.dict(os.environ,env,clear=True);context.start();self.addCleanup(context.stop)
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup);self.root=Path(directory.name)/'group/Container_v1/Stores';self.root.mkdir(parents=True,mode=0o700)
        self.path=self.root/('Data-'+STORE+'.sqlite');create(self.path);self.reader=Reader()
        for target,value in [('stores_root',self.root),('platform.system','Darwin'),('platform.mac_ver',('27.0',('','',''),''))]:
            p=patch('icloud_for_agents.native_reminder_template_store.'+target,return_value=value);p.start();self.addCleanup(p.stop)
    def read(self,**kwargs):return store.outline(SOURCE,TEMPLATE,reader=self.reader,**kwargs)
    def sql(self,query,values=()):
        connection=sqlite3.connect(self.path);connection.execute(query,values);connection.commit();connection.close()

    def test_scoped_outline_page_parent_links_and_explicit_omissions_without_blobs(self):
        value=self.read(limit=1);self.assertEqual(value['total_items'],2);self.assertFalse(value['done']);self.assertEqual(value['next_offset'],1)
        self.assertEqual(value['items'][0]['item_id'],ROOT);self.assertIsNone(value['items'][0]['parent_item_id'])
        child=self.read(limit=1,offset=1);self.assertTrue(child['done']);self.assertEqual(child['items'][0]['parent_item_id'],ROOT)
        self.assertFalse(value['content_complete']);self.assertFalse(value['restorable']);self.assertFalse(value['ordering_verified']);self.assertFalse(value['data_mutated']);self.assertFalse(value['database_writes']);self.assertIn('notes',value['omitted_features'])
        self.assertNotIn('Private body blob',json.dumps(value));self.assertEqual(len(self.reader.calls),4)
        self.assertFalse(value['item_inventory_complete']);self.assertFalse(value['sync_verified']);self.assertEqual(value['total_items_scope'],'visible_local_saved_rows')
        beyond=self.read(offset=10);self.assertTrue(beyond['done']);self.assertEqual(beyond['items'],[]);self.assertEqual(beyond['next_offset'],10)

    def test_disabled_unselected_unopted_backend_and_bounds_refuse_before_store_access(self):
        original=json.loads(os.environ['ICLOUD_NATIVE_CONFIG'])
        for profile in ({'metadata_backend':'native'},{'advanced_backend':'disabled'},{'source_ids':[OTHER]}):
            config=copy.deepcopy(original);config['reminders'].update(profile);os.environ['ICLOUD_NATIVE_CONFIG']=json.dumps(config)
            with patch.object(store,'paths',side_effect=AssertionError('No store discovery')):
                with self.assertRaises(SafetyError):self.read()
        os.environ['ICLOUD_NATIVE_CONFIG']=json.dumps(original)
        for args in ({'limit':True},{'limit':21},{'limit':0},{'offset':-1},{'offset':1.0},{'offset':100001}):
            with self.assertRaises(SafetyError):self.read(**args)
        os.environ['ICLOUD_MAIL_ACCESS']='disabled'
        with self.assertRaises(SafetyError):self.read()

    def test_native_scope_denial_or_mismatch_cannot_fall_back_to_sql(self):
        with patch.object(store,'paths',side_effect=AssertionError('No SQL after denial')):
            with patch.object(self.reader,'read',side_effect=SafetyError('Synthetic OS denial')):
                with self.assertRaises(SafetyError):self.read()
            self.reader.after=lambda call,value:value.update(source_id=OTHER)
            with self.assertRaises(SafetyError):self.read()

    def test_foreign_account_template_item_scope_and_unknown_uuid_storage_refuse(self):
        for query,parameters,restore in [
            ('UPDATE ZREMCDTEMPLATE SET ZACCOUNT=9',(), 'UPDATE ZREMCDTEMPLATE SET ZACCOUNT=1'),
            ('UPDATE ZREMCDSAVEDREMINDER SET ZACCOUNT=9',(), 'UPDATE ZREMCDSAVEDREMINDER SET ZACCOUNT=1'),
            ('UPDATE ZREMCDOBJECT SET ZINACTIVE=1',(), 'UPDATE ZREMCDOBJECT SET ZINACTIVE=0'),
            ('UPDATE ZREMCDTEMPLATE SET ZMARKEDFORDELETION=1',(), 'UPDATE ZREMCDTEMPLATE SET ZMARKEDFORDELETION=0')]:
            self.sql(query,parameters)
            with self.assertRaises(SafetyError):self.read()
            self.sql(restore)
        self.sql('UPDATE ZREMCDSAVEDREMINDER SET ZIDENTIFIER=? WHERE ZIDENTIFIER=?',('not a native UUID blob',uuid.UUID(CHILD).bytes))
        with self.assertRaises(SafetyError):self.read()

    def test_incomplete_or_cyclic_hierarchy_duplicate_identity_and_oversized_collection_refuse(self):
        self.sql('UPDATE ZREMCDSAVEDREMINDER SET ZPARENTSAVEDREMINDERIDENTIFIER=? WHERE ZIDENTIFIER=?',(uuid.UUID(OTHER).bytes,uuid.UUID(CHILD).bytes))
        with self.assertRaises(SafetyError):self.read()
        self.sql('UPDATE ZREMCDSAVEDREMINDER SET ZPARENTSAVEDREMINDERIDENTIFIER=? WHERE ZIDENTIFIER=?',(uuid.UUID(ROOT).bytes,uuid.UUID(CHILD).bytes))
        self.sql('UPDATE ZREMCDSAVEDREMINDER SET ZPARENTSAVEDREMINDERIDENTIFIER=? WHERE ZIDENTIFIER=?',(uuid.UUID(CHILD).bytes,uuid.UUID(ROOT).bytes))
        with self.assertRaises(SafetyError):self.read()
        self.sql('UPDATE ZREMCDSAVEDREMINDER SET ZPARENTSAVEDREMINDERIDENTIFIER=NULL WHERE ZIDENTIFIER=?',(uuid.UUID(ROOT).bytes,))
        self.sql('INSERT INTO ZREMCDSAVEDREMINDER SELECT * FROM ZREMCDSAVEDREMINDER LIMIT 1')
        with self.assertRaises(SafetyError):self.read()
        self.sql('DELETE FROM ZREMCDSAVEDREMINDER')
        connection=sqlite3.connect(self.path)
        connection.executemany('INSERT INTO ZREMCDSAVEDREMINDER (Z_ENT,Z_OPT,ZACCOUNT,ZTEMPLATE,ZIDENTIFIER,ZTITLE,ZPRIORITY,ZHASHASHTAGS,ZHASLOCATIONTRIGGERSORVEHICLEEVENTTRIGGERS,ZMARKEDFORDELETION) VALUES (12,1,1,2,?,?,0,0,0,0)',[(uuid.uuid4().bytes,'Synthetic bounded row')for _ in range(201)])
        connection.commit();connection.close()
        with self.assertRaises(SafetyError):self.read()

    def test_duplicate_account_stores_unknown_schema_and_symlink_or_writable_paths_refuse(self):
        other=self.root/('Data-'+OTHER+'.sqlite');create(other)
        with self.assertRaises(SafetyError):self.read()
        other.unlink();other.symlink_to(self.path)
        with self.assertRaises(SafetyError):self.read()
        other.unlink();self.path.chmod(0o666)
        with self.assertRaises(SafetyError):self.read()
        self.path.chmod(0o600);self.sql('ALTER TABLE ZREMCDTEMPLATE RENAME COLUMN ZACCOUNT TO UNSUPPORTED_ACCOUNT')
        with self.assertRaises(SafetyError):self.read()

    def test_malformed_metadata_access_drift_and_unsupported_platform_refuse(self):
        for query in ('UPDATE ZREMCDSAVEDREMINDER SET ZPRIORITY=10','UPDATE ZREMCDSAVEDREMINDER SET ZTITLE=NULL','UPDATE ZREMCDSAVEDREMINDER SET ZHASHASHTAGS=2'):
            self.sql(query)
            with self.assertRaises(SafetyError):self.read()
            self.sql("UPDATE ZREMCDSAVEDREMINDER SET ZPRIORITY=0,ZTITLE='Synthetic row',ZHASHASHTAGS=0")
        def change(call,value):
            if call%2==0:
                config=json.loads(os.environ['ICLOUD_NATIVE_CONFIG']);config['reminders']['metadata_backend']='native';os.environ['ICLOUD_NATIVE_CONFIG']=json.dumps(config)
        self.reader.calls=[];self.reader.after=change
        with self.assertRaises(SafetyError):self.read()
        self.reader.after=None
        with patch.object(store.platform,'mac_ver',return_value=('26.0',('','',''),'')):
            with self.assertRaises(SafetyError):self.read()

    def test_sqlite_readonly_authorizer_blocks_writes_attach_functions_and_large_values(self):
        with store.readonly_store(self.path) as (connection,_):
            for query in ('DELETE FROM ZREMCDTEMPLATE',"ATTACH DATABASE ':memory:' AS extra",'PRAGMA query_only=OFF',"SELECT lower(ZNAME) FROM ZREMCDTEMPLATE"):
                with self.assertRaises(sqlite3.DatabaseError):connection.execute(query)
            with self.assertRaises(sqlite3.DatabaseError):connection.execute('SELECT ZMETADATA FROM ZREMCDSAVEDREMINDER')
        self.assertEqual(self.read()['total_items'],2)
        self.sql('UPDATE ZREMCDSAVEDREMINDER SET ZTITLE=?',('x'*1048577,))
        with self.assertRaises(SafetyError):self.read()
