"""Recoverable existing-object deletion, native role boundaries and empty-Trash refusal."""
import copy,json,os,unittest
from unittest.mock import patch
import test_native_notes_restore as restore_fixture
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.native_grants import NativeGrants
from icloud_for_agents.native_protocol import mutation_request
from icloud_for_agents.native_access import require_write

class Adapter(restore_fixture.Adapter):
    backend_id='synthetic-native-notes-delete'
    def __init__(self):super().__init__();self.reader.note['folder_id']='destination'
    def apply(self,item):
        if item['operation']=='restore_note':return super().apply(item)
        require_write('notes',item);wire=mutation_request('notes',item)['change'];assert wire['folder_guard']=='verified_trash_destination' and wire['fields']['recoverable'] is True
        self.calls.append(item['id']);self.reader.note['folder_id']=item['fields']['destination_folder_id'];self.revision+=1
        return {'id':item['id'],'source_id':item['source_id'],'folder_id':self.reader.note['folder_id']}

# Reuse setup without inheriting unrelated restoration test cases.
class NotesDeleteTests(unittest.TestCase):
    def setUp(self):
        restore_fixture.NotesRestoreTests.setUp(self);self.adapter.__class__=Adapter;self.adapter.backend_id=Adapter.backend_id;self.adapter.reader.note['folder_id']='destination'
    def change(self):return {'operation':'delete_note','source_id':'selected','folder_id':'destination','id':'created','fields':{'recoverable':True}}
    def test_recoverable_delete_and_separately_approved_restore_preserve_rich_native_identity(self):
        before=copy.deepcopy(self.adapter.reader.note);assets=copy.deepcopy(self.adapter.reader.attachments);plan=self.engine.prepare_once([self.change()]);wire=mutation_request('notes',plan['review']['payload']['changes'][0]);self.assertTrue(wire['change']['fields']['recoverable']);self.assertEqual(self.engine.execute(plan['plan_id'])['status'],'applied');self.assertEqual(self.adapter.reader.note['folder_id'],'folder')
        with self.assertRaises(SafetyError):self.engine.execute(plan['plan_id'])
        restore={'operation':'restore_note','source_id':'selected','folder_id':'folder','id':'created','fields':{'destination_folder_id':'destination'}};p=self.engine.prepare_once([restore]);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');self.assertEqual(self.adapter.reader.note,before);self.assertEqual(self.adapter.reader.attachments,assets)
    def test_empty_hidden_trash_refuses_before_native_dispatch_without_placeholder(self):
        self.adapter.hide_empty=True
        with self.assertRaisesRegex(SafetyError,'empty or unavailable'):self.engine.prepare_once([self.change()])
        self.assertEqual(self.adapter.calls,[]);self.assertEqual(self.adapter.reader.note['folder_id'],'destination')
    def test_metadata_opt_in_and_selected_source_gate_before_storage_reads(self):
        from icloud_for_agents.native_notes_delete import endpoints
        from icloud_for_agents.native_notes_restore import endpoints as restore_endpoints
        with patch('icloud_for_agents.native_note_store.recovery_folder',side_effect=AssertionError('No metadata read')):
            with self.assertRaises(SafetyError):endpoints(self.adapter,'other','destination')
            with self.assertRaises(SafetyError):restore_endpoints(self.adapter,'other','folder','destination')
            os.environ['ICLOUD_NATIVE_CONFIG']=json.dumps({'notes':{'access_mode':'read-only','source_ids':['selected']}})
            with self.assertRaises(SafetyError):endpoints(self.adapter,'selected','destination')
    def test_exact_recoverable_boolean_only_and_no_system_source_or_custom_destination(self):
        for fields in ({},{'recoverable':False},{'recoverable':1},{'recoverable':True,'destination_folder_id':'other'},{'permanent':True},None):
            with self.assertRaises(SafetyError):self.engine.prepare_once([dict(self.change(),fields=fields)])
        with self.assertRaises(SafetyError):self.engine.prepare_once([dict(self.change(),folder_id='folder')])
        self.assertEqual(self.adapter.calls,[])
    def test_delete_grant_has_explicit_recovery_effects_survives_own_revisions_and_revokes(self):
        manager=NativeGrants(self.engine);scope={'scenario':'Synthetic recoverable removal','rules':[{'operation':'delete_note','source_id':'selected','container_id':'destination','fields':['recoverable'],'destinations':[]}],'max_batch':1,'max_24h':2,'expires':None};g=manager.prepare(scope);self.assertIn('No permanent deletion',g['review']['payload']['effects']['recovery_scope']);manager.activate(g['plan_id']);p=self.engine.prepare_with_grant(g['plan_id'],[self.change()]);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');self.assertEqual(manager.status(g['plan_id'])['status'],'active');self.adapter.reader.note['folder_id']='destination';p=self.engine.prepare_with_grant(g['plan_id'],[self.change()]);manager.revoke(g['plan_id'])
        with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
    def test_changed_physical_location_and_attachments_refuse_readback_success(self):
        p=self.engine.prepare_once([self.change()]);item=p['review']['payload']['changes'][0];outcome={'id':'created','source_id':'selected','folder_id':'folder'};self.assertFalse(self.adapter.verify(item,outcome));self.adapter.reader.note['folder_id']='folder';self.adapter.reader.attachments=[]
        with self.assertRaises(SafetyError):self.adapter.verify(item,outcome)
    def test_uncertain_terminal_delete_observation_accepts_only_preserved_saved_trash_object(self):
        from icloud_for_agents.native_move_recovery import NotesMoveRecovery
        from icloud_for_agents.core import digest
        apply=self.adapter.apply
        def uncertain(item):apply(item);raise SafetyError('Synthetic terminal error after move')
        with patch.object(self.adapter,'apply',side_effect=uncertain):
            p=self.engine.prepare_once([self.change()]);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'failed_or_uncertain')
        original=self.store.path(p['plan_id'],'.result').read_bytes();manager=NotesMoveRecovery('synthetic@example.com',self.store,self.adapter)
        with patch('icloud_for_agents.native_move_recovery.completed_exchange_evidence',side_effect=lambda req,**kw:{'request_id':'synthetic-delete','request_sha256':digest(req),'helper_response_sha256':'a'*64,'helper_outcome':'error_response','mutation_dispatched':True}):
            review=manager.prepare(p['plan_id']);self.assertEqual(review['review']['payload']['observed']['location'],'destination');self.assertEqual(manager.execute(review['plan_id'])['status'],'completed');self.engine.assert_settled()
        self.assertEqual(original,self.store.path(p['plan_id'],'.result').read_bytes());self.assertEqual(self.adapter.calls,['created'])
