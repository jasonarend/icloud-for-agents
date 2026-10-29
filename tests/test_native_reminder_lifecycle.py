"""Owned exact-leaf removal/restoration, scoped grants and immutable evidence."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import test_native_reminder_moves as f
from icloud_for_agents.core import SafetyError,NativeObjectNotFound
from icloud_for_agents.native_reminders import RemindersCreateAdapter
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.native_grants import NativeGrants
from icloud_for_agents.native_access import require_write
from icloud_for_agents.native_protocol import mutation_request
from icloud_for_agents.approvals import PlanStore

class Reader(f.Reader):
    def __init__(self):super().__init__();self.items.pop(f.CHILD);self.parents.pop(f.CHILD);self.deleted={};self.recovery_extra=[]
    def read(self,action,**fields):
        if action=='get' and fields['id'] not in self.items:raise NativeObjectNotFound('Synthetic exact selected ID missing')
        if action=='recovery_lookup':
            identity=fields['id'];found=identity in self.deleted
            return {'protocol_version':1,'source_id':f.SOURCE,'id':identity,'found_in_view':found,'found_as_root':found,'parent_id':None,'recovery_root_id':identity if found else None,'reported_child_ids':None,'children_status':'unavailable','data_mutated':False}
        if action=='recovery_tree':
            identity=fields['id']
            if identity not in self.deleted:raise SafetyError('No synthetic deleted root')
            return {'protocol_version':1,'source_id':f.SOURCE,'requested_id':identity,'root_id':identity,'nodes':[{'id':identity,'title':self.deleted[identity]['title'],'parent_id':None,'depth':0,'reported_child_ids':None,'children_status':'unavailable'},*copy.deepcopy(self.recovery_extra)],'hierarchy_complete':False,'data_mutated':False}
        return super().read(action,**fields)

class Adapter(RemindersCreateAdapter):
    backend_id='synthetic-root-leaf-lifecycle'
    def __init__(self):self.reader=Reader();self.calls=[]
    def apply(self,item):
        require_write('reminders',item);self.calls.append(item['operation']);wire=mutation_request('reminders',item)['change'];assert wire['before']
        if item['operation']=='delete_reminder':self.reader.deleted[item['id']]=self.reader.items.pop(item['id'])
        else:
            value=self.reader.deleted.pop(item['id']);value['list_id']=item['fields']['list_id'];value['modified']+=1;self.reader.items[item['id']]=value
        return {'id':item['id'],'source_id':item['source_id'],'list_id':item['list_id'],'saved_locally':True}

class ReminderLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.adapter=Adapter();self.store=PlanStore(Path(temporary.name)/'runtime');env={'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'access_mode':'read-only','source_ids':[f.SOURCE],'advanced_backend':'reminderkit'}})};context=patch.dict(os.environ,env,clear=True);context.start();self.addCleanup(context.stop);self.engine=NativeChanges('reminders','synthetic@example.com',self.adapter,self.store)
    def delete(self):return {'operation':'delete_reminder','source_id':f.SOURCE,'id':f.ROOT,'fields':{'recoverable':True}}
    def restore(self,plan,destination=f.DEST,**extra):return {'operation':'restore_reminder','source_id':f.SOURCE,'id':f.ROOT,'fields':dict(deletion_plan_id=plan,list_id=destination,**extra)}
    def deleted(self):
        p=self.engine.prepare_once([self.delete()]);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');return p['plan_id']
    def test_once_delete_and_owned_restore_preserve_native_identity_fields_and_no_replay(self):
        before=copy.deepcopy(self.adapter.reader.items[f.ROOT]);identity=self.deleted();p=self.engine.prepare_once([self.restore(identity)]);wire=mutation_request('reminders',p['review']['payload']['changes'][0])['change'];self.assertEqual(wire['fields'],{'list_id':f.DEST});self.assertEqual(wire['before']['original_item']['id'],f.ROOT);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');after=self.adapter.reader.items[f.ROOT];self.assertEqual({k:v for k,v in after.items() if k not in {'list_id','modified'}},{k:v for k,v in before.items() if k not in {'list_id','modified'}})
        for old in (identity,p['plan_id']):
            with self.assertRaises(SafetyError):self.engine.execute(old)
        self.assertEqual(os.environ['ICLOUD_MAIL_ACCESS'],'read-only')
    def test_nonboolean_permanent_extra_source_scope_and_descendant_targets_refuse(self):
        for change in (dict(self.delete(),fields={'recoverable':1}),dict(self.delete(),fields={'recoverable':False}),dict(self.delete(),fields={'permanent':True}),dict(self.delete(),fields={'recoverable':True,'title':'Sneak'}),dict(self.delete(),source_id='other')):
            with self.assertRaises(SafetyError):self.engine.prepare_once([change])
        self.adapter.reader.items[f.CHILD]=dict(self.adapter.reader.items[f.ROOT],id=f.CHILD);self.adapter.reader.parents[f.CHILD]=f.ROOT;self.adapter.reader.flags[f.CHILD]=False
        with self.assertRaises(SafetyError):self.engine.prepare_once([self.delete()])
        with self.assertRaises(SafetyError):self.engine.prepare_once([dict(self.delete(),id=f.CHILD)])
        self.assertEqual(self.adapter.calls,[])
    def test_recurring_shared_or_unavailable_recovery_support_refuses(self):
        self.adapter.reader.items[f.ROOT]['has_recurrence']=True
        with self.assertRaises(SafetyError):self.engine.prepare_once([self.delete()])
        self.adapter.reader.items[f.ROOT]['has_recurrence']=False;self.adapter.reader.shared=True
        with self.assertRaises(SafetyError):self.engine.prepare_once([self.delete()])
        self.adapter.reader.shared=False;read=self.adapter.reader.read
        with patch.object(self.adapter.reader,'read',side_effect=lambda action,**kw:(_ for _ in ()).throw(SafetyError('Unavailable recovery')) if action=='recovery_lookup' else read(action,**kw)),self.assertRaises(SafetyError):self.engine.prepare_once([self.delete()])
    def test_wrong_original_owner_index_operation_or_native_id_cannot_restore(self):
        identity=self.deleted()
        for change in (self.restore(identity,deletion_index=True),self.restore(identity,deletion_index=1),dict(self.restore(identity),id=f.CHILD),self.restore('f'*32)):
            with self.assertRaises((SafetyError,FileNotFoundError)):self.engine.prepare_once([change])
        plan=self.store.read(self.store.path(identity,'.json'));plan['payload']['account']='other';self.store.path(identity,'.json').write_text(json.dumps(plan))
        with self.assertRaises(SafetyError):self.engine.prepare_once([self.restore(identity)])
    def test_recovery_extra_nodes_or_title_changes_refuse(self):
        identity=self.deleted();self.adapter.reader.recovery_extra=[{'id':f.CHILD,'parent_id':f.ROOT,'title':'Unexpected child'}]
        with self.assertRaises(SafetyError):self.engine.prepare_once([self.restore(identity)])
        self.adapter.reader.recovery_extra=[];self.adapter.reader.deleted[f.ROOT]['title']='Changed in recovery'
        with self.assertRaises(SafetyError):self.engine.prepare_once([self.restore(identity)])
    def test_strict_original_receipt_metadata_is_supported_and_required(self):
        os.environ['ICLOUD_APPROVAL_POLICY']='local-receipt';p=self.engine.prepare_once([self.delete()])
        with self.assertRaises(FileNotFoundError):self.engine.execute(p['plan_id'])
        # Isolated synthetic independent-owner fixture; never a live approval.
        self.store.write_once(self.store.path(p['plan_id'],'.approved'),{'plan_id':p['plan_id'],'sha256':p['sha256'],'approved_at':self.store.clock()});self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');r=self.engine.prepare_once([self.restore(p['plan_id'])]);self.store.write_once(self.store.path(r['plan_id'],'.approved'),{'plan_id':r['plan_id'],'sha256':r['sha256']});self.assertEqual(self.engine.execute(r['plan_id'])['status'],'applied')
    def test_delete_and_restore_grants_are_separate_with_exact_source_destinations_and_revocation(self):
        manager=NativeGrants(self.engine);scope={'scenario':'Synthetic leaf removal','rules':[{'operation':'delete_reminder','source_id':f.SOURCE,'container_id':f.ORIGIN,'fields':['recoverable'],'destinations':[]}],'max_batch':1,'max_24h':2,'expires':None};g=manager.prepare(scope)['plan_id'];manager.activate(g);p=self.engine.prepare_with_grant(g,[self.delete()]);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied')
        with self.assertRaises(SafetyError):self.engine.prepare_with_grant(g,[self.restore(p['plan_id'])])
        manager.revoke(g);scope['rules']=[{'operation':'restore_reminder','source_id':f.SOURCE,'container_id':f.ORIGIN,'fields':['deletion_plan_id','list_id'],'destinations':[f.DEST]}];g=manager.prepare(scope)['plan_id'];manager.activate(g);r=self.engine.prepare_with_grant(g,[self.restore(p['plan_id'])]);manager.revoke(g)
        with self.assertRaises(SafetyError):self.engine.execute(r['plan_id'])
    def test_ledger_or_membership_drift_refuses_pending_restore_before_admission(self):
        identity=self.deleted();r=self.engine.prepare_once([self.restore(identity)]);self.adapter.reader.items[f.CHILD]=dict(self.adapter.reader.deleted[f.ROOT],id=f.CHILD,list_id=f.DEST);self.adapter.reader.parents[f.CHILD]=None;self.adapter.reader.flags[f.CHILD]=False
        with self.assertRaises(SafetyError):self.engine.execute(r['plan_id'])
        self.assertFalse(self.store.path(r['plan_id'],'.started').exists())
    def uncertain_apply(self):
        from icloud_for_agents.native_reminder_exchange import begin,finish
        apply=self.adapter.apply
        def uncertain(item):
            apply(item);finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic save error after effect')
        return uncertain
    def test_expected_delete_after_terminal_error_reconciles_without_replay_and_can_restore(self):
        from icloud_for_agents.native_reminder_lifecycle import reconcile
        with patch.object(self.adapter,'apply',side_effect=self.uncertain_apply()):
            p=self.engine.prepare_once([self.delete()]);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'failed_or_uncertain')
        original=self.store.path(p['plan_id'],'.result').read_bytes()
        with self.assertRaises(SafetyError):self.engine.assert_settled()
        self.assertEqual(reconcile(self.engine,p['plan_id'])['status'],'applied_after_readback_reconciliation');self.engine.assert_settled();r=self.engine.prepare_once([self.restore(p['plan_id'])]);self.assertEqual(r['review']['payload']['changes'][0]['before']['deletion_proof']['completion_evidence'],'known_target_terminal_error_and_independent_expected_state');self.assertEqual(self.engine.execute(r['plan_id'])['status'],'applied');self.assertEqual(original,self.store.path(p['plan_id'],'.result').read_bytes());self.assertEqual(self.adapter.calls,['delete_reminder','restore_reminder'])
    def test_expected_restore_after_terminal_error_reconciles_full_original_fields(self):
        from icloud_for_agents.native_reminder_lifecycle import reconcile
        identity=self.deleted()
        with patch.object(self.adapter,'apply',side_effect=self.uncertain_apply()):
            p=self.engine.prepare_once([self.restore(identity)]);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'failed_or_uncertain')
        self.assertEqual(reconcile(self.engine,p['plan_id'])['status'],'applied_after_readback_reconciliation');self.engine.assert_settled();self.assertEqual(self.adapter.calls,['delete_reminder','restore_reminder'])
    def test_missing_terminal_or_wrong_current_state_cannot_fabricate_lifecycle_completion(self):
        from icloud_for_agents.native_reminder_lifecycle import reconcile
        with patch.object(self.adapter,'apply',side_effect=SafetyError('No terminal evidence')):
            p=self.engine.prepare_once([self.delete()]);self.engine.execute(p['plan_id'])
        with self.assertRaises((SafetyError,FileNotFoundError)):reconcile(self.engine,p['plan_id'])
        self.assertFalse(self.store._native_lifecycle_readback_completed(p['plan_id']))
    def test_tampered_saved_readback_scope_is_rejected_even_with_recomputed_observation_hash(self):
        from icloud_for_agents.native_reminder_lifecycle import reconcile
        from icloud_for_agents.core import digest
        with patch.object(self.adapter,'apply',side_effect=self.uncertain_apply()):
            p=self.engine.prepare_once([self.delete()]);self.engine.execute(p['plan_id'])
        reconcile(self.engine,p['plan_id']);path=self.store.path(p['plan_id'],'.native-lifecycle-readback');record=self.store.read(path);record['observed']['graph']['source_id']='other';record['observed_sha256']=digest(record['observed']);path.write_text(json.dumps(record));self.assertFalse(self.store._native_lifecycle_readback_completed(p['plan_id']))

    def test_unchanged_terminal_errors_require_review_and_never_replay(self):
        from icloud_for_agents.native_reminder_exchange import begin,finish
        from icloud_for_agents.native_reminder_recovery import ReminderObservationRecovery
        from icloud_for_agents.native_reminder_lifecycle import reconcile
        def unchanged(item):
            finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True})
            raise SafetyError('Synthetic terminal error; no observed state change')
        for operation in ('delete','restore'):
            if operation=='restore':deletion=self.deleted()
            change=self.delete() if operation=='delete' else self.restore(deletion)
            with patch.object(self.adapter,'apply',side_effect=unchanged):
                plan=self.engine.prepare_once([change]);self.engine.execute(plan['plan_id'])
            with self.assertRaises((SafetyError,NativeObjectNotFound)):reconcile(self.engine,plan['plan_id'])
            recovery=ReminderObservationRecovery(self.engine.account,self.store,self.adapter)
            with self.assertRaises(SafetyError):recovery.prepare(plan['plan_id'],legacy_missing_terminal=True)
            reviewed=recovery.prepare(plan['plan_id']);self.assertTrue(reviewed['review']['payload']['observed']['observed_snapshot_matches_before'])
            self.assertEqual(recovery.execute(reviewed['plan_id'])['recovery_state'],'observed_unchanged_reminder_accepted_after_review');self.engine.assert_settled()
            with self.assertRaises(SafetyError):self.engine.execute(plan['plan_id'])
    def test_unchanged_recovery_refuses_changed_membership(self):
        from icloud_for_agents.native_reminder_exchange import begin,finish
        from icloud_for_agents.native_reminder_recovery import ReminderObservationRecovery
        def unchanged(item):
            finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic')
        with patch.object(self.adapter,'apply',side_effect=unchanged):
            plan=self.engine.prepare_once([self.delete()]);self.engine.execute(plan['plan_id'])
        recovery=ReminderObservationRecovery(self.engine.account,self.store,self.adapter);review=recovery.prepare(plan['plan_id']);self.adapter.reader.items[f.ROOT]['title']='Concurrent change'
        with self.assertRaises(SafetyError):recovery.execute(review['plan_id'])
        with self.assertRaises(SafetyError):self.engine.assert_settled()
