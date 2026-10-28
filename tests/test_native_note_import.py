"""Notes observation copies are lossy reviewed creations, never restores."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_access import require_write
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.native_note_import import prepare_copy
from icloud_for_agents.native_once import planning_access

class Adapter:
    backend_id='synthetic-note-copy';operations={'create_note'}
    def __init__(self):self.saved=[]
    def prepare(self,change):
        planning_access('notes');return dict(change,before={'folder':'synthetic'},effects={},fidelity_verified=True)
    def snapshot(self,item):return {'folder':'synthetic'}
    def apply(self,item):
        require_write('notes',item);self.saved.append(copy.deepcopy(item['fields']));return {'id':'new-note'}
    def verify(self,item,result):return result['id']=='new-note' and self.saved[-1]==item['fields']

class NoteImportTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.store=PlanStore(Path(temporary.name)/'runtime')
        env={'ICLOUD_ENABLE_NOTES':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com',
             'ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'access_mode':'read-only','source_ids':['selected']}})}
        manager=patch.dict(os.environ,env,clear=True);manager.start();self.addCleanup(manager.stop)
        self.adapter=Adapter();self.engine=NativeChanges('notes','synthetic@example.com',self.adapter,self.store)
        self.item={'id':'old-note','title':'Synthetic','html':'<div>Synthetic</div><div>Body</div>',
                   'locked':False,'truncated':False,'content_complete':False}
        self.snapshot={'format':'icloud-for-agents-note-observation','version':1,'item':self.item}
    def prepare(self):return prepare_copy(self.engine,self.snapshot,'selected','destination',accept_omissions=True)
    def test_review_binds_loss_and_requires_exact_one_time_authority(self):
        plan=self.prepare();payload=plan['review']['payload'];self.assertEqual(payload['context']['temporary_elevation'],'exact-batch-once')
        self.assertIn('duplicates',payload['copy_review']['effects'][0]);self.assertIn('attachments',payload['copy_review']['effects'][2])
        self.assertEqual(payload['changes'][0]['fields']['body'],'<div>Body</div>');self.assertEqual(self.adapter.saved,[])
        self.assertEqual(self.engine.execute(plan['plan_id'])['status'],'applied')
        with self.assertRaises(SafetyError):self.engine.execute(plan['plan_id'])
    def test_strict_receipt_and_existing_uncertainty_guard(self):
        os.environ['ICLOUD_APPROVAL_POLICY']='local-receipt';plan=self.prepare()
        with self.assertRaises(FileNotFoundError):self.engine.execute(plan['plan_id'])
        self.store.write_once(self.store.path(plan['plan_id'],'.approved'),{'plan_id':plan['plan_id'],'sha256':plan['sha256']})
        self.assertEqual(self.engine.execute(plan['plan_id'])['status'],'applied')
        prior=self.prepare();self.store.write_once(self.store.path(prior['plan_id'],'.approved'),{'plan_id':prior['plan_id'],'sha256':prior['sha256']})
        self.store.consume(prior['plan_id'],self.engine.account);self.store.finish(prior['plan_id'],{'status':'failed_or_uncertain','phase':'mutation','attempted':[],'completed':[]})
        copy_plan=self.prepare()
        with self.assertRaises(SafetyError):self.engine.execute(copy_plan['plan_id'])
        self.assertEqual(len(self.adapter.saved),1)
    def test_locked_truncated_and_ambiguous_title_refuse(self):
        with self.assertRaises(SafetyError):prepare_copy(self.engine,self.snapshot,'selected','destination',accept_omissions=False)
        for changes in ({'locked':True},{'truncated':True},{'content_complete':True},{'html':'<div>Other</div><div>Body</div>'}):
            self.snapshot['item']=dict(self.item,**changes)
            with self.subTest(changes=changes),self.assertRaises(SafetyError):self.prepare()
        self.assertEqual(self.adapter.saved,[])
    def test_real_adapter_checks_source_folder_and_html(self):
        from icloud_for_agents.native_notes import NotesCreateAdapter
        helper=self.store.root.parent/'helper';helper.write_text('synthetic')
        os.environ['ICLOUD_NATIVE_CONFIG']=json.dumps({'notes':{'access_mode':'read-only','source_ids':['selected'],'helper_path':str(helper)}})
        adapter=NotesCreateAdapter();engine=NativeChanges('notes',self.engine.account,adapter,self.store)
        folder={'shared':False,'verified_default_folder':{'id':'destination','source_id':'selected'}}
        with patch('icloud_for_agents.native_app.bundle_fingerprint',return_value='synthetic'),patch.object(adapter,'snapshot',return_value=folder):
            plan=prepare_copy(engine,self.snapshot,'selected','destination',accept_omissions=True)
            self.assertEqual(plan['review']['payload']['changes'][0]['fields']['html'],'<div>Body</div>')
            self.item['html']='<div>Synthetic</div><script>alert(1)</script>'
            with self.assertRaises(SafetyError):prepare_copy(engine,self.snapshot,'selected','destination',accept_omissions=True)
if __name__=='__main__':unittest.main()
