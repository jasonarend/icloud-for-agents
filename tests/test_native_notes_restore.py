"""Exact saved system-recovery origin, ordinary destination and preserved native note."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from test_native_notes import Reader
from icloud_for_agents.native_notes import NotesCreateAdapter
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.native_grants import NativeGrants
from icloud_for_agents.native_protocol import mutation_request
from icloud_for_agents.native_access import require_write
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError

class Adapter(NotesCreateAdapter):
    backend_id='synthetic-native-notes-restore'
    def __init__(self):
        self.reader=Reader();self.reader.folder['title']='System recovery';self.reader.note['html']='<div>Spec</div><table><tr><td>Cell</td></tr></table>';self.reader.attachments=[{'id':'asset','note_id':'created','content_identifier':'cid:asset','name':None}];self.revision=1;self.calls=[];self.hide_empty=False
        read=self.reader.read
        def guarded(action,**fields):
            if self.hide_empty and action=='folder_get' and fields['folder_id']=='folder' and self.reader.note['folder_id']!='folder':raise SafetyError('Empty system recovery folder is hidden')
            return read(action,**fields)
        self.reader.read=guarded
    def role(self,identity,sources):
        if identity not in {'folder','destination'} or sources!=['selected']:raise SafetyError('Unknown synthetic role')
        node=self.reader.folder if identity=='folder' else self.reader.destination;recovery=identity=='folder'
        return {'folder_id':identity,'source_id':'selected','title':node['title'],'parent_pk':None,'storage_kind':'special_smart_or_deleted' if recovery else 'regular','native_folder_type':1 if recovery else 0,'marked_for_deletion':False,'has_smart_query':False,'revision':self.revision,'schema_sha256':'synthetic-schema'}
    def recovery(self,source):
        if source!='selected':raise SafetyError('Unknown source')
        return dict(self.role('folder',[source]),role='system_trash',data_mutated=False)
    def location(self,identity,sources):return {'id':identity,'folder_id':self.reader.note['folder_id'],'source_id':'selected','content_read':False,'database_writes':False}
    def apply(self,item):
        require_write('notes',item);wire=mutation_request('notes',item)['change'];assert wire['folder_guard']=='verified_recovery_roles';self.calls.append(item['id']);self.reader.note['folder_id']=item['fields']['destination_folder_id'];self.revision+=1
        return {'id':item['id'],'source_id':item['source_id'],'folder_id':self.reader.note['folder_id']}

class NotesRestoreTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.adapter=Adapter();self.store=PlanStore(Path(temp.name)/'runtime');env={'ICLOUD_ENABLE_NOTES':'1','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'access_mode':'read-only','source_ids':['selected'],'metadata_backend':'notes-store-readonly'}})}
        for context in (patch.dict(os.environ,env,clear=True),patch('icloud_for_agents.native_note_store.folder_role',side_effect=self.adapter.role),patch('icloud_for_agents.native_note_store.recovery_folder',side_effect=self.adapter.recovery),patch('icloud_for_agents.native_note_store.note_location',side_effect=self.adapter.location)):
            context.start();self.addCleanup(context.stop)
        self.engine=NativeChanges('notes','synthetic@example.com',self.adapter,self.store)
    def change(self):return {'operation':'restore_note','source_id':'selected','folder_id':'folder','id':'created','fields':{'destination_folder_id':'destination'}}
    def test_restores_same_rich_note_and_attachments_when_empty_recovery_becomes_hidden(self):
        self.adapter.hide_empty=True;before=copy.deepcopy(self.adapter.reader.note);assets=copy.deepcopy(self.adapter.reader.attachments);p=self.engine.prepare_once([self.change()]);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');self.assertEqual(self.adapter.reader.attachments,assets);self.assertEqual({k:v for k,v in self.adapter.reader.note.items() if k!='folder_id'},{k:v for k,v in before.items() if k!='folder_id'})
        with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
        self.assertEqual(os.environ['ICLOUD_MAIL_ACCESS'],'read-only')
    def test_wrong_source_ordinary_origin_extra_fields_and_same_target_refused(self):
        for change in (dict(self.change(),source_id='other'),dict(self.change(),folder_id='destination'),dict(self.change(),fields={'destination_folder_id':'folder'}),dict(self.change(),fields={'destination_folder_id':'destination','permanent':True}),dict(self.change(),id='other')):
            with self.assertRaises(SafetyError):self.engine.prepare_once([change])
        self.assertEqual(self.adapter.calls,[])
    def test_source_role_and_destination_special_deleted_smart_shared_drift_refused(self):
        role=self.adapter.role
        for key,value in (('native_folder_type',0),('marked_for_deletion',True),('has_smart_query',True),('source_id','other'),('parent_pk',10)):
            with patch('icloud_for_agents.native_note_store.recovery_folder',return_value=dict(self.adapter.recovery('selected'),**{key:value})),self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
        for key,value in (('storage_kind','special_smart_or_deleted'),('native_folder_type',1),('marked_for_deletion',True),('has_smart_query',True),('title','Changed'),('parent_pk',10)):
            with patch('icloud_for_agents.native_note_store.folder_role',side_effect=lambda identity,sources:dict(role(identity,sources),**({key:value} if identity=='destination' else {}))),self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
        self.adapter.reader.destination['shared']=True
        with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
    def test_persisted_location_disagreement_refuses_before_dispatch(self):
        with patch('icloud_for_agents.native_note_store.note_location',return_value=dict(self.adapter.location('created',['selected']),folder_id='destination')),self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
    def test_locked_shared_truncated_note_or_incomplete_attachment_inventory_refused(self):
        for key in ('locked','shared','truncated'):
            self.adapter.reader.note[key]=True
            with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
            self.adapter.reader.note[key]=False
        original=self.adapter.reader.read
        with patch.object(self.adapter.reader,'read',side_effect=lambda action,**kw:{'attachments':[],'done':False} if action=='attachments' else original(action,**kw)),self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
    def test_scoped_grant_survives_empty_origin_and_own_revisions_then_revokes_pending(self):
        self.adapter.hide_empty=True;m=NativeGrants(self.engine);scope={'scenario':'Synthetic recovery','rules':[{'operation':'restore_note','source_id':'selected','container_id':'folder','fields':['destination_folder_id'],'destinations':['destination']}],'max_batch':1,'max_24h':3,'expires':None};g=m.prepare(scope)['plan_id'];m.activate(g);p=self.engine.prepare_with_grant(g,[self.change()]);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');self.assertEqual(m.status(g)['status'],'active');self.adapter.reader.note['folder_id']='folder';p=self.engine.prepare_with_grant(g,[self.change()]);m.revoke(g)
        with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
    def test_known_id_verification_failure_reconciles_without_native_replay(self):
        with patch.object(self.adapter,'verify',return_value=False):
            p=self.engine.prepare_once([self.change()]);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'failed_or_uncertain')
        old=self.store.path(p['plan_id'],'.result').read_bytes();self.assertEqual(self.engine.reconcile_known_changes(p['plan_id'])['status'],'applied_after_readback_reconciliation');self.assertEqual(self.adapter.calls,['created']);self.assertEqual(old,self.store.path(p['plan_id'],'.result').read_bytes())
    def test_terminal_error_observation_recovers_saved_restore_when_empty_origin_is_hidden(self):
        from icloud_for_agents.native_move_recovery import NotesMoveRecovery
        from icloud_for_agents.core import digest
        self.adapter.hide_empty=True;apply=self.adapter.apply
        def uncertain(item):apply(item);raise SafetyError('Synthetic terminal error after native move')
        with patch.object(self.adapter,'apply',side_effect=uncertain):
            p=self.engine.prepare_once([self.change()]);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'failed_or_uncertain')
        original=self.store.path(p['plan_id'],'.result').read_bytes();manager=NotesMoveRecovery('synthetic@example.com',self.store,self.adapter)
        with patch('icloud_for_agents.native_move_recovery.completed_exchange_evidence',side_effect=lambda req,**kw:{'request_id':'synthetic-restore','request_sha256':digest(req),'helper_response_sha256':'a'*64,'helper_outcome':'error_response','mutation_dispatched':True}):
            review=manager.prepare(p['plan_id']);self.assertEqual(review['review']['payload']['observed']['location'],'destination');self.assertFalse(review['review']['payload']['effects']['native_data_changed']);self.assertEqual(manager.execute(review['plan_id'])['status'],'completed');self.engine.assert_settled()
        self.assertEqual(original,self.store.path(p['plan_id'],'.result').read_bytes());self.assertEqual(self.adapter.calls,['created'])
    def test_forged_restore_observation_requires_saved_location_and_unchanged_recovery_roles(self):
        from icloud_for_agents.native_move_recovery import NotesMoveRecovery
        from icloud_for_agents.core import digest
        with patch.object(self.adapter,'apply',side_effect=SafetyError('Synthetic terminal error')):
            p=self.engine.prepare_once([self.change()]);self.engine.execute(p['plan_id'])
        manager=NotesMoveRecovery('synthetic@example.com',self.store,self.adapter)
        with patch('icloud_for_agents.native_move_recovery.completed_exchange_evidence',side_effect=lambda req,**kw:{'request_id':'synthetic-restore','request_sha256':digest(req),'helper_response_sha256':'a'*64,'helper_outcome':'error_response','mutation_dispatched':True}):
            r=manager.prepare(p['plan_id']);plan=self.store.read(self.store.path(r['plan_id'],'.json'));self.assertTrue(self.store._native_move_recovery_for(p['plan_id'],plan))
            for mutate in (lambda observed:observed.pop('persisted_location'),lambda observed:observed['persisted_location'].update(folder_id='other'),lambda observed:observed['recovery_roles'][0].update(native_folder_type=9)):
                forged=copy.deepcopy(plan);mutate(forged['payload']['observed']);self.assertFalse(self.store._native_move_recovery_for(p['plan_id'],forged))
