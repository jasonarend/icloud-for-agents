"""Exact preassigned native groups, bounded source inventory and scoped authority."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from icloud_for_agents.native_reminders import RemindersCreateAdapter
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.native_grants import NativeGrants
from icloud_for_agents.native_protocol import mutation_request
from icloud_for_agents.native_access import require_write
from icloud_for_agents.native_reminder_groups import validate_inventory,expected_inventory
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError
SOURCE='11111111-1111-4111-8111-111111111111';LIST='22222222-2222-4222-8222-222222222222'
class Reader:
    def __init__(self):self.inventory={'protocol_version':1,'source_id':SOURCE,'groups':[],'account_order':[LIST],'data_mutated':False,'backend':'reminderkit-root-group-inventory-v1'};self.children={}
    def read(self,action,**fields):
        if action=='group_inventory':return copy.deepcopy(self.inventory)
        if action=='group_lists':return {'protocol_version':1,'source_id':SOURCE,'group_id':fields['group_id'],'lists':copy.deepcopy(self.children.get(fields['group_id'],[])),'done':True,'next_offset':len(self.children.get(fields['group_id'],[])),'data_mutated':False,'backend':'reminderkit-group-lists-v2','membership_complete':False,'ordering_verified':False,'pagination_consistency':'live_offset_may_shift'}
        raise AssertionError(action)
class Adapter(RemindersCreateAdapter):
    backend_id='synthetic-root-groups'
    def __init__(self):self.reader=Reader();self.calls=[]
    def apply(self,item):
        require_write('reminders',item);self.calls.append(item['id']);wire=mutation_request('reminders',item)['change'];assert wire['before']==item['before'];self.reader.inventory=expected_inventory(item);return {'id':item['id'],'source_id':item['source_id']}
class GroupTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.adapter=Adapter();self.store=PlanStore(Path(temp.name)/'runtime');context=patch.dict(os.environ,{'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'access_mode':'read-only','source_ids':[SOURCE],'advanced_backend':'reminderkit'}})},clear=True);context.start();self.addCleanup(context.stop);self.engine=NativeChanges('reminders','synthetic@example.com',self.adapter,self.store)
    def change(self,title='Synthetic root group'):return {'operation':'create_group','source_id':SOURCE,'fields':{'title':title}}
    def test_once_new_empty_group_preserves_prior_order_and_cannot_replay(self):
        p=self.engine.prepare_once([self.change()]);identity=p['review']['payload']['changes'][0]['id'];self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');self.assertEqual(self.adapter.reader.inventory['account_order'],[LIST,identity]);self.assertEqual(self.adapter.reader.inventory['groups'],[{'id':identity,'title':'Synthetic root group'}]);self.assertEqual(os.environ['ICLOUD_MAIL_ACCESS'],'read-only')
        with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
        with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
    def test_scope_empty_or_extra_fields_and_backend_refused(self):
        for change in (self.change(''),self.change(' '),self.change('bad\nname'),dict(self.change(),source_id='other'),dict(self.change(),fields={'title':'Name','id':LIST}),dict(self.change(),id=LIST)):
            with self.assertRaises(SafetyError):self.engine.prepare_once([change])
        os.environ['ICLOUD_NATIVE_CONFIG']=json.dumps({'reminders':{'access_mode':'read-only','source_ids':[SOURCE]}})
        with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
        self.assertEqual(self.adapter.calls,[])
    def test_inventory_bounds_foreign_group_and_duplicate_account_ids_refuse(self):
        base=self.adapter.reader.inventory
        for invalid in (dict(base,source_id='other'),dict(base,account_order=[LIST,LIST]),dict(base,groups=[{'id':SOURCE,'title':'Unbound'}]),dict(base,groups=[{'id':LIST,'title':''}]),dict(base,account_order=['bad-id']),dict(base,extra=True)):
            with self.assertRaises(SafetyError):validate_inventory(invalid,SOURCE)
    def test_inventory_drift_prevents_admission(self):
        p=self.engine.prepare_once([self.change()]);self.adapter.reader.inventory['account_order']=[]
        with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
        self.assertFalse(self.store.path(p['plan_id'],'.started').exists())
    def test_grant_survives_own_creations_and_revocation_stops_pending(self):
        m=NativeGrants(self.engine);g=m.prepare({'scenario':'Synthetic groups','rules':[{'operation':'create_group','source_id':SOURCE,'container_id':SOURCE,'fields':['title'],'destinations':[]}],'max_batch':1,'max_24h':2,'expires':None})['plan_id'];m.activate(g)
        for title in ('Synthetic first','Synthetic second'):
            p=self.engine.prepare_with_grant(g,[self.change(title)]);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');self.assertEqual(m.status(g)['status'],'active')
        p=self.engine.prepare_with_grant(g,[self.change('Synthetic third')]);m.revoke(g)
        with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
    def test_known_returned_id_readback_reconciles_without_another_creation(self):
        with patch.object(self.adapter,'verify',return_value=False):
            p=self.engine.prepare_once([self.change()]);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'failed_or_uncertain')
        original=self.store.path(p['plan_id'],'.result').read_bytes();self.assertEqual(self.engine.reconcile_known_changes(p['plan_id'])['status'],'applied_after_readback_reconciliation');self.assertEqual(original,self.store.path(p['plan_id'],'.result').read_bytes());self.assertEqual(len(self.adapter.calls),1)
    def test_retained_terminal_error_expected_creation_can_reconcile_without_replay(self):
        from icloud_for_agents.native_reminder_exchange import begin,finish
        apply=self.adapter.apply
        def uncertain(item):
            apply(item);finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic post-save terminal error')
        with patch.object(self.adapter,'apply',side_effect=uncertain):
            p=self.engine.prepare_once([self.change()]);self.engine.execute(p['plan_id'])
        original=self.store.path(p['plan_id'],'.result').read_bytes();self.assertEqual(self.engine.reconcile_known_changes(p['plan_id'])['status'],'applied_after_readback_reconciliation');self.engine.assert_settled();self.assertEqual(original,self.store.path(p['plan_id'],'.result').read_bytes());self.assertEqual(len(self.adapter.calls),1)
    def test_unchanged_terminal_error_requires_separate_review_and_rejects_legacy(self):
        from icloud_for_agents.native_reminder_exchange import begin,finish
        from icloud_for_agents.native_reminder_recovery import ReminderObservationRecovery
        def unchanged(item):
            finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic unchanged terminal error')
        with patch.object(self.adapter,'apply',side_effect=unchanged):
            p=self.engine.prepare_once([self.change()]);self.engine.execute(p['plan_id'])
        with self.assertRaises(SafetyError):self.engine.reconcile_known_changes(p['plan_id'])
        recovery=ReminderObservationRecovery(self.engine.account,self.store,self.adapter)
        with self.assertRaises(SafetyError):recovery.prepare(p['plan_id'],legacy_missing_terminal=True)
        review=recovery.prepare(p['plan_id']);self.assertEqual(recovery.execute(review['plan_id'])['recovery_state'],'observed_unchanged_reminder_accepted_after_review');self.engine.assert_settled();self.assertEqual(self.adapter.calls,[])
    def test_native_inventory_reader_refuses_wrong_scope_schema_and_unselected_requests(self):
        from icloud_for_agents.native_read import NativeReader
        config=json.loads(os.environ['ICLOUD_NATIVE_CONFIG']);config['reminders']['helper_path']='/synthetic/helper';os.environ['ICLOUD_NATIVE_CONFIG']=json.dumps(config)
        base=copy.deepcopy(self.adapter.reader.inventory)
        with patch.object(NativeReader,'_dispatch',return_value=base) as dispatch:
            self.assertEqual(NativeReader('reminders').read('group_inventory',source_id=SOURCE),base)
            for value in (dict(base,source_id=LIST),dict(base,account_order=[LIST,LIST]),dict(base,groups=[{'id':SOURCE,'title':'Not in order'}])):
                dispatch.return_value=value
                with self.assertRaises(SafetyError):NativeReader('reminders').read('group_inventory',source_id=SOURCE)
            dispatch.reset_mock()
            for fields in ({'source_id':LIST},{'source_id':SOURCE,'limit':1},{'source_id':SOURCE,'id':LIST}):
                with self.assertRaises(SafetyError):NativeReader('reminders').read('group_inventory',**fields)
            dispatch.assert_not_called()

    def renamed_fixture(self):
        p=self.engine.prepare_once([self.change()]);self.engine.execute(p['plan_id']);return p['review']['payload']['changes'][0]['id']
    def rename(self,identity,title='Synthetic renamed group'):return {'operation':'rename_group','source_id':SOURCE,'group_id':identity,'fields':{'title':title}}
    def test_once_rename_roundtrip_preserves_account_order_and_same_group_id(self):
        identity=self.renamed_fixture();before=copy.deepcopy(self.adapter.reader.inventory)
        for title in ('Synthetic renamed group','Synthetic root group'):
            p=self.engine.prepare_once([self.rename(identity,title)]);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied')
        self.assertEqual(before,self.adapter.reader.inventory)
    def test_rename_refuses_noop_foreign_id_and_child_view_drift(self):
        identity=self.renamed_fixture()
        for change in (self.rename(identity,'Synthetic root group'),self.rename(SOURCE),dict(self.rename(identity),source_id=LIST)):
            with self.assertRaises(SafetyError):self.engine.prepare_once([change])
        p=self.engine.prepare_once([self.rename(identity)]);self.adapter.reader.children[identity]=[{'id':LIST,'title':'Concurrent list','is_group':False}]
        with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
        self.assertFalse(self.store.path(p['plan_id'],'.started').exists())
    def test_rename_grant_stable_across_own_changes_and_revocation(self):
        identity=self.renamed_fixture();m=NativeGrants(self.engine);g=m.prepare({'scenario':'Synthetic group titles','rules':[{'operation':'rename_group','source_id':SOURCE,'container_id':identity,'fields':['title'],'destinations':[]}],'max_batch':1,'max_24h':2,'expires':None})['plan_id'];m.activate(g)
        for title in ('Synthetic renamed group','Synthetic root group'):
            p=self.engine.prepare_with_grant(g,[self.rename(identity,title)]);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');self.assertEqual(m.status(g)['status'],'active')
        p=self.engine.prepare_with_grant(g,[self.rename(identity)]);m.revoke(g)
        with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
    def test_terminal_error_after_rename_preserves_original_failure_and_observes_expected_group(self):
        from icloud_for_agents.native_reminder_exchange import begin,finish
        identity=self.renamed_fixture();apply=self.adapter.apply
        def uncertain(item):
            apply(item);finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic post-save error')
        with patch.object(self.adapter,'apply',side_effect=uncertain):
            p=self.engine.prepare_once([self.rename(identity)]);self.engine.execute(p['plan_id'])
        original=self.store.path(p['plan_id'],'.result').read_bytes();self.assertEqual(self.engine.reconcile_known_changes(p['plan_id'])['status'],'applied_after_readback_reconciliation');self.engine.assert_settled();self.assertEqual(original,self.store.path(p['plan_id'],'.result').read_bytes())
    def test_unchanged_rename_terminal_error_compares_bounded_child_view_before_acceptance(self):
        from icloud_for_agents.native_reminder_exchange import begin,finish
        from icloud_for_agents.native_reminder_recovery import ReminderObservationRecovery
        identity=self.renamed_fixture()
        def unchanged(item):
            finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic unchanged group rename')
        with patch.object(self.adapter,'apply',side_effect=unchanged):
            p=self.engine.prepare_once([self.rename(identity)]);self.engine.execute(p['plan_id'])
        recovery=ReminderObservationRecovery(self.engine.account,self.store,self.adapter);review=recovery.prepare(p['plan_id']);self.adapter.reader.children[identity]=[{'id':LIST,'title':'Concurrent member','is_group':False}]
        with self.assertRaises(SafetyError):recovery.execute(review['plan_id'])
        self.adapter.reader.children[identity]=[];self.assertEqual(recovery.execute(review['plan_id'])['recovery_state'],'observed_unchanged_reminder_accepted_after_review');self.engine.assert_settled()
