"""Rename boundaries, immutable membership and persistent exact grant scope."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_notes import NotesCreateAdapter
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.native_grants import NativeGrants
from icloud_for_agents.native_access import require_write
from icloud_for_agents.native_protocol import mutation_request

class Reader:
    def __init__(self):
        self.folders=[{'id':'child','title':'Child','source_id':'selected','parent_id':'folder','shared':False,'is_default':False},{'id':'folder','title':'Original','source_id':'selected','parent_id':'selected','shared':False,'is_default':False}]
        self.notes=[{'id':'locked-note','folder_id':'child'},{'id':'rich-note','folder_id':'folder'}];self.siblings=[{'id':'sibling','title':'Other'}];self.roles={};self.revision=1
    def read(self,action,**fields):
        if action=='folder_inventory':return copy.deepcopy({'folders':self.folders,'notes':self.notes,'siblings':self.siblings})
        folder=next((f for f in self.folders if f['id']==fields.get('folder_id')),None)
        if folder is None:raise SafetyError('Unknown synthetic folder')
        if action=='folder_get':return {'folder':{k:v for k,v in folder.items() if k!='is_default'}}
        if action=='folder_role':
            result={'folder_id':folder['id'],'source_id':folder['source_id'],'title':folder['title'],'storage_kind':'regular','native_folder_type':0,'marked_for_deletion':False,'has_smart_query':False,'revision':self.revision,'parent_pk':None,'schema_sha256':'a'*64}
            result.update(self.roles.get(folder['id'],{}));return result
        raise AssertionError('Rename must not read note bodies or attachments: '+action)

class Adapter(NotesCreateAdapter):
    backend_id='synthetic-folder-rename'
    def __init__(self):self.reader=Reader();self.calls=[]
    def apply(self,item):
        require_write('notes',item);self.calls.append(item['id'])
        next(f for f in self.reader.folders if f['id']==item['id'])['title']=item['fields']['title'];self.reader.revision+=1
        return {'id':item['id'],'source_id':item['source_id']}

class FolderRenameTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.store=PlanStore(Path(temporary.name)/'runtime');self.adapter=Adapter()
        env={'ICLOUD_ENABLE_NOTES':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'source_ids':['selected'],'access_mode':'read-only','metadata_backend':'notes-store-readonly','folder_write_backend':'notes-shared'}})}
        context=patch.dict(os.environ,env,clear=True);context.start();self.addCleanup(context.stop);self.engine=NativeChanges('notes','synthetic@example.com',self.adapter,self.store)
    def change(self,title='Renamed'):return {'operation':'rename_folder','source_id':'selected','folder_id':'folder','fields':{'title':title}}
    def test_scripting_default_refuses_ordinary_rename(self):
        config={'notes':{'source_ids':['selected'],'access_mode':'read-only','metadata_backend':'notes-store-readonly'}}
        with patch.dict(os.environ,{'ICLOUD_NATIVE_CONFIG':json.dumps(config)}):
            with self.assertRaisesRegex(SafetyError,'native model writer'):self.engine.prepare_once([self.change()])
        self.assertEqual(self.adapter.calls,[])

    def test_one_time_native_rename_preserves_rich_and_locked_membership(self):
        before=copy.deepcopy(self.adapter.reader.notes);p=self.engine.prepare_once([self.change()]);request=mutation_request('notes',p['review']['payload']['changes'][0])
        self.assertEqual(set(request['change']['before']),{'inventory','role'});self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');self.assertEqual(self.adapter.reader.notes,before)
        self.assertEqual(os.environ['ICLOUD_MAIL_ACCESS'],'read-only')
        with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
    def test_default_shared_special_deleted_unknown_and_stale_store_metadata_refuse(self):
        reader=self.adapter.reader
        for index,key,value in [(0,'is_default',True),(1,'is_default',True),(0,'shared',True)]:
            prior=reader.folders[index][key];reader.folders[index][key]=value
            with self.subTest(index=index,key=key),self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
            reader.folders[index][key]=prior
        for patch in ({'storage_kind':'special_smart_or_deleted'},{'marked_for_deletion':True},{'has_smart_query':True},{'title':'stale'},{'source_id':'unselected'}):
            reader.roles['child']=patch
            with self.subTest(patch=patch),self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
        self.assertEqual(self.adapter.calls,[])
    def test_disconnected_repeated_or_overflowing_membership_is_never_partial(self):
        reader=self.adapter.reader;original=copy.deepcopy(reader.notes)
        for notes in ([{'id':'a','folder_id':'missing'}],[{'id':'a','folder_id':'folder'}]*2,[{'id':str(i),'folder_id':'folder'} for i in range(201)]):
            reader.notes=notes
            with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
        reader.notes=original;reader.folders[0]['parent_id']='missing'
        with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
    def test_duplicate_sibling_title_and_noop_are_refused(self):
        for title in ('Other','Original'):
            with self.subTest(title=title),self.assertRaises(SafetyError):self.engine.prepare_once([self.change(title)])
    def test_changed_inventory_stops_before_consuming_or_dispatching(self):
        p=self.engine.prepare_once([self.change()]);self.adapter.reader.notes.append({'id':'added','folder_id':'folder'})
        with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
        self.assertFalse(self.store.path(p['plan_id'],'.started').exists());self.assertEqual(self.adapter.calls,[])
    def test_scenario_remains_active_after_its_own_authorized_title_change(self):
        manager=NativeGrants(self.engine);scope={'scenario':'Synthetic folder naming','rules':[{'operation':'rename_folder','source_id':'selected','container_id':'folder','fields':['title'],'destinations':[]}],'max_batch':1,'max_24h':2,'expires':None}
        grant=manager.prepare(scope);manager.activate(grant['plan_id'])
        for title in ('Renamed','Original'):
            plan=self.engine.prepare_with_grant(grant['plan_id'],[self.change(title)]);self.assertEqual(self.engine.execute(plan['plan_id'])['status'],'applied');self.assertEqual(manager.status(grant['plan_id'])['status'],'active')
        manager.revoke(grant['plan_id']);self.assertEqual(manager.status(grant['plan_id'])['status'],'revoked')

    def test_script_cache_only_rename_requires_reviewed_restore_and_persisted_readback(self):
        reader=self.adapter.reader;reader.roles['folder']={'title':'Original'}
        plan=self.engine.prepare_once([self.change()]);failure=self.engine.execute(plan['plan_id']);self.assertEqual(failure['status'],'failed_or_uncertain')
        original=self.store.path(plan['plan_id'],'.result').read_bytes()
        repair=self.engine.prepare_folder_rename_correction(plan['plan_id'],'before')
        self.assertEqual(self.engine.execute(repair['plan_id'])['status'],'applied')
        status=self.engine.reconcile_correction(repair['plan_id']);self.assertEqual(status['status'],'restored_before_after_explicit_correction');self.assertFalse(status['original_request_succeeded'])
        self.assertEqual(self.store.path(plan['plan_id'],'.result').read_bytes(),original)
        with self.assertRaises(SafetyError):self.engine.execute(plan['plan_id'])
    def test_title_repair_cannot_overwrite_external_titles_or_membership(self):
        reader=self.adapter.reader;reader.roles['folder']={'title':'Original'}
        plan=self.engine.prepare_once([self.change()]);self.engine.execute(plan['plan_id'])
        reader.folders[1]['title']='External title'
        with self.assertRaises(SafetyError):self.engine.prepare_folder_rename_correction(plan['plan_id'],'before')
        reader.folders[1]['title']='Renamed';reader.notes.append({'id':'external','folder_id':'folder'})
        with self.assertRaises(SafetyError):self.engine.prepare_folder_rename_correction(plan['plan_id'],'before')
        self.assertFalse(self.store.path(plan['plan_id'],'.native-correction').exists())
