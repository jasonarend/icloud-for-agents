"""Exact reviewed family lifecycle, owned recovery and per-object grant bounds."""
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
from icloud_for_agents.native_reminder_family_lifecycle import members,record_matches,charge
from icloud_for_agents.approvals import PlanStore


class Reader(f.Reader):
    def __init__(self):super().__init__();self.deleted={};self.extra=[]
    def read(self,action,**fields):
        if action=='get' and fields['id'] not in self.items:raise NativeObjectNotFound('Synthetic selected object missing')
        if action=='recovery_lookup':
            identity=fields['id'];found=identity in self.deleted;parent=self.parents.get(identity) if found else None
            return {'protocol_version':1,'source_id':f.SOURCE,'id':identity,'found_in_view':found,'found_as_root':found and parent is None,'parent_id':parent,'recovery_root_id':(parent or identity) if found else None,'reported_child_ids':None,'children_status':'unavailable','data_mutated':False}
        if action=='recovery_tree':
            identity=fields['id']
            if identity not in self.deleted:raise SafetyError('Missing synthetic recovery root')
            rows=[{'id':key,'title':value['title'],'parent_id':self.parents[key],'depth':0 if key==identity else 1,'reported_child_ids':None,'children_status':'unavailable'} for key,value in self.deleted.items() if key==identity or self.parents[key]==identity]
            return {'protocol_version':1,'source_id':f.SOURCE,'requested_id':identity,'root_id':identity,'nodes':rows+copy.deepcopy(self.extra),'hierarchy_complete':False,'data_mutated':False}
        return super().read(action,**fields)


class Adapter(RemindersCreateAdapter):
    backend_id='synthetic-family-lifecycle'
    def __init__(self):self.reader=Reader();self.calls=[]
    def apply(self,item):
        require_write('reminders',item);self.calls.append(item['operation']);wire=mutation_request('reminders',item)['change'];assert wire['fields']['include_descendants'] is True
        baseline=item['before']['original'] if item['operation']=='restore_reminder' else item['before'];ids=members(baseline,item['id'])
        for identity in ids:
            if item['operation']=='delete_reminder':self.reader.deleted[identity]=self.reader.items.pop(identity)
            else:
                value=self.reader.deleted.pop(identity);value['modified']+=1;self.reader.items[identity]=value
        return {'id':item['id'],'source_id':item['source_id'],'list_id':item['list_id'],'saved_locally':True}


class FamilyLifecycleTests(unittest.TestCase):
    def setUp(self):
        env={'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'access_mode':'read-only','source_ids':[f.SOURCE],'advanced_backend':'reminderkit','helper_path':'/synthetic/helper'}})}
        p=patch.dict(os.environ,env,clear=True);p.start();self.addCleanup(p.stop)
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.store=PlanStore(Path(tmp.name)/'runtime');self.adapter=Adapter();self.engine=NativeChanges('reminders','synthetic@example.com',self.adapter,self.store)
    def delete(self,**extra):return dict({'operation':'delete_reminder','source_id':f.SOURCE,'id':f.ROOT,'fields':{'recoverable':True,'include_descendants':True}},**extra)
    def restore(self,identity,**fields):return {'operation':'restore_reminder','source_id':f.SOURCE,'id':f.ROOT,'fields':{'deletion_plan_id':identity,'list_id':f.ORIGIN,'include_descendants':True,**fields}}
    def deleted(self):
        p=self.engine.prepare_once([self.delete()]);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');return p['plan_id']

    def test_exact_once_roundtrip_retains_all_ids_content_parent_and_read_only_settings(self):
        original=copy.deepcopy(self.adapter.reader.items);identity=self.deleted();r=self.engine.prepare_once([self.restore(identity)])
        self.assertEqual(self.engine.execute(r['plan_id'])['status'],'applied');self.assertEqual(set(self.adapter.reader.items),set(original));self.assertEqual(self.adapter.reader.parents[f.CHILD],f.ROOT)
        for key,value in original.items():self.assertEqual({k:v for k,v in value.items() if k!='modified'},{k:v for k,v in self.adapter.reader.items[key].items() if k!='modified'})
        self.assertEqual(os.environ['ICLOUD_MAIL_ACCESS'],'read-only');self.engine.assert_settled()

    def test_prepare_wire_approval_effects_and_charge_bind_every_direct_child(self):
        p=self.engine.prepare_once([self.delete()]);item=p['review']['payload']['changes'][0];self.assertEqual(charge(item),2);self.assertEqual(item['effects']['affected_objects'],[f.ROOT,f.CHILD])
        wire=mutation_request('reminders',item)['change'];self.assertTrue(wire['fields']['include_descendants']);self.assertEqual(wire['before']['descendants'][0]['item']['id'],f.CHILD)
        damaged=copy.deepcopy(item);damaged['before']['descendants']=[]
        with self.assertRaises(SafetyError):charge(damaged)
        self.assertFalse(record_matches(damaged,{'id':f.ROOT,'source_id':f.SOURCE,'state':'in_recovery'}))

    def test_old_leaf_authority_and_nonboolean_or_extra_fields_do_not_admit_family(self):
        for fields in ({'recoverable':True},{'recoverable':1,'include_descendants':True},{'recoverable':True,'include_descendants':1},{'recoverable':False,'include_descendants':True},{'recoverable':True,'include_descendants':True,'extra':True}):
            with self.assertRaises(SafetyError):self.engine.prepare_once([dict(self.delete(),fields=fields)])
        with self.assertRaises(SafetyError):self.engine.prepare_once([self.delete(id=f.CHILD)])
        self.assertEqual(self.adapter.calls,[])

    def test_nested_recurring_shared_and_oversized_families_refuse(self):
        self.adapter.reader.items[f.CHILD]['has_recurrence']=True
        with self.assertRaises(SafetyError):self.engine.prepare_once([self.delete()])
        self.adapter.reader.items[f.CHILD]['has_recurrence']=False;self.adapter.reader.shared=True
        with self.assertRaises(SafetyError):self.engine.prepare_once([self.delete()])
        self.adapter.reader.shared=False
        for index in range(10):
            identity=str(index)+'-synthetic-child';self.adapter.reader.items[identity]=dict(self.adapter.reader.items[f.CHILD],id=identity);self.adapter.reader.parents[identity]=f.ROOT;self.adapter.reader.flags[identity]=False
        with self.assertRaises(SafetyError):self.engine.prepare_once([self.delete()])
        for identity in list(self.adapter.reader.items):
            if identity not in (f.ROOT,f.CHILD):self.adapter.reader.items.pop(identity);self.adapter.reader.parents.pop(identity);self.adapter.reader.flags.pop(identity)
        grandchild='66666666-6666-4666-8666-666666666666';self.adapter.reader.items[grandchild]=dict(self.adapter.reader.items[f.CHILD],id=grandchild);self.adapter.reader.parents[grandchild]=f.CHILD;self.adapter.reader.flags[grandchild]=False
        with self.assertRaises(SafetyError):self.engine.prepare_once([self.delete()])

    def test_child_content_drift_refuses_before_started_marker(self):
        p=self.engine.prepare_once([self.delete()]);self.adapter.reader.items[f.CHILD]['notes']='Changed independently'
        with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
        self.assertFalse(self.store.path(p['plan_id'],'.started').exists());self.assertEqual(self.adapter.calls,[])

    def test_partial_cascade_is_uncertain_and_does_not_verify_family(self):
        apply=self.adapter.apply
        def partial(item):
            value=apply(item);self.adapter.reader.items[f.CHILD]=self.adapter.reader.deleted.pop(f.CHILD);return value
        p=self.engine.prepare_once([self.delete()])
        with patch.object(self.adapter,'apply',side_effect=partial):self.assertEqual(self.engine.execute(p['plan_id'])['status'],'failed_or_uncertain')
        with self.assertRaises(SafetyError):self.engine.assert_settled()

    def test_restore_requires_explicit_family_owned_ledger_original_destination_and_exact_recovery(self):
        identity=self.deleted()
        with self.assertRaises(SafetyError):self.engine.prepare_once([self.restore(identity,list_id=f.DEST)])
        leaf=self.restore(identity);leaf['fields'].pop('include_descendants')
        with self.assertRaises(SafetyError):self.engine.prepare_once([leaf])
        self.adapter.reader.deleted[f.CHILD]['title']='Changed in recovery'
        with self.assertRaises(SafetyError):self.engine.prepare_once([self.restore(identity)])
        self.adapter.reader.deleted[f.CHILD]['title']='Synthetic child';self.adapter.reader.extra=[{'id':'unexpected','parent_id':f.ROOT,'title':'Unreviewed'}]
        with self.assertRaises(SafetyError):self.engine.prepare_once([self.restore(identity)])

    def test_restore_detects_altered_child_and_keeps_uncertainty(self):
        identity=self.deleted();apply=self.adapter.apply
        def altered(item):
            value=apply(item);self.adapter.reader.items[f.CHILD]['notes']='Incorrect content';return value
        r=self.engine.prepare_once([self.restore(identity)])
        with patch.object(self.adapter,'apply',side_effect=altered):self.assertEqual(self.engine.execute(r['plan_id'])['status'],'failed_or_uncertain')
        with self.assertRaises(SafetyError):self.engine.assert_settled()

    def test_strict_receipts_are_needed_for_both_whole_family_operations(self):
        os.environ['ICLOUD_APPROVAL_POLICY']='local-receipt';p=self.engine.prepare_once([self.delete()])
        with self.assertRaises(FileNotFoundError):self.engine.execute(p['plan_id'])
        # Synthetic isolated owner fixture only; never a receipt for live data.
        self.store.write_once(self.store.path(p['plan_id'],'.approved'),{'plan_id':p['plan_id'],'sha256':p['sha256']});self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied')
        r=self.engine.prepare_once([self.restore(p['plan_id'])]);self.store.write_once(self.store.path(r['plan_id'],'.approved'),{'plan_id':r['plan_id'],'sha256':r['sha256']});self.assertEqual(self.engine.execute(r['plan_id'])['status'],'applied')

    def test_family_grants_require_new_field_and_charge_all_objects_and_rolling_usage(self):
        manager=NativeGrants(self.engine);scope={'scenario':'Synthetic family lifecycle','rules':[{'operation':'delete_reminder','source_id':f.SOURCE,'container_id':f.ORIGIN,'fields':['recoverable'],'destinations':[]}],'max_batch':2,'max_24h':2,'expires':None}
        g=manager.prepare(scope)['plan_id'];manager.activate(g)
        with self.assertRaises(SafetyError):self.engine.prepare_with_grant(g,[self.delete()])
        manager.revoke(g);scope['rules'][0]['fields'].append('include_descendants');scope['max_batch']=1;g=manager.prepare(scope)['plan_id'];manager.activate(g)
        with self.assertRaisesRegex(SafetyError,'native-object bound'):self.engine.prepare_with_grant(g,[self.delete()])
        manager.revoke(g);scope['max_batch']=2;g=manager.prepare(scope)['plan_id'];manager.activate(g);p=self.engine.prepare_with_grant(g,[self.delete()]);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');self.assertEqual(self.store.read(self.store.path(p['plan_id'],'.native-grant-use'))['count'],2)
        with self.assertRaises(SafetyError):self.engine.prepare_with_grant(g,[self.restore(p['plan_id'])])
        manager.revoke(g)

    def test_retained_terminal_error_family_readback_never_replays_and_allows_owned_restore(self):
        from icloud_for_agents.native_reminder_exchange import begin,finish
        from icloud_for_agents.native_reminder_lifecycle import reconcile
        apply=self.adapter.apply
        def uncertain(item):
            apply(item);finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic error after family effect')
        p=self.engine.prepare_once([self.delete()])
        with patch.object(self.adapter,'apply',side_effect=uncertain):self.assertEqual(self.engine.execute(p['plan_id'])['status'],'failed_or_uncertain')
        original=self.store.path(p['plan_id'],'.result').read_bytes();self.assertEqual(reconcile(self.engine,p['plan_id'])['status'],'applied_after_readback_reconciliation');self.engine.assert_settled();r=self.engine.prepare_once([self.restore(p['plan_id'])]);self.assertEqual(self.engine.execute(r['plan_id'])['status'],'applied');self.assertEqual(self.store.path(p['plan_id'],'.result').read_bytes(),original)

    def test_family_restore_error_readback_binds_child_content_and_preserves_original_failure(self):
        from icloud_for_agents.native_reminder_exchange import begin,finish
        from icloud_for_agents.native_reminder_lifecycle import reconcile
        from icloud_for_agents.core import digest
        deletion=self.deleted();apply=self.adapter.apply
        def uncertain(item):
            apply(item);finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic restore error')
        p=self.engine.prepare_once([self.restore(deletion)])
        with patch.object(self.adapter,'apply',side_effect=uncertain):self.engine.execute(p['plan_id'])
        original=self.store.path(p['plan_id'],'.result').read_bytes();self.assertEqual(reconcile(self.engine,p['plan_id'])['status'],'applied_after_readback_reconciliation');self.engine.assert_settled()
        path=self.store.path(p['plan_id'],'.native-lifecycle-readback');value=self.store.read(path);value['observed']['snapshots'][1]['item']['notes']='Forged child';value['observed_sha256']=digest(value['observed']);path.write_text(json.dumps(value))
        self.assertFalse(self.store._native_lifecycle_readback_completed(p['plan_id']));self.assertEqual(self.store.path(p['plan_id'],'.result').read_bytes(),original)

    def test_unchanged_family_terminal_errors_need_separate_review_and_never_replay(self):
        from icloud_for_agents.native_reminder_exchange import begin,finish
        from icloud_for_agents.native_reminder_recovery import ReminderObservationRecovery
        def unchanged(item):
            finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic no observed change')
        for restoring in (False,True):
            deletion=self.deleted() if restoring else None
            p=self.engine.prepare_once([self.restore(deletion) if restoring else self.delete()])
            with patch.object(self.adapter,'apply',side_effect=unchanged):self.engine.execute(p['plan_id'])
            recovery=ReminderObservationRecovery(self.engine.account,self.store,self.adapter);review=recovery.prepare(p['plan_id'])
            self.assertTrue(review['review']['payload']['observed']['observed_snapshot_matches_before']);recovery.execute(review['plan_id']);self.engine.assert_settled()
            with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])

    def test_family_restore_grants_count_every_object_and_rolling_delete_limit_stops_fresh_dispatch(self):
        manager=NativeGrants(self.engine);scope={'scenario':'Bounded synthetic family grant','rules':[{'operation':'delete_reminder','source_id':f.SOURCE,'container_id':f.ORIGIN,'fields':['recoverable','include_descendants'],'destinations':[]}],'max_batch':2,'max_24h':2,'expires':None}
        grant=manager.prepare(scope)['plan_id'];manager.activate(grant);p=self.engine.prepare_with_grant(grant,[self.delete()]);self.engine.execute(p['plan_id'])
        r=self.engine.prepare_once([self.restore(p['plan_id'])]);self.assertEqual(self.engine.execute(r['plan_id'])['status'],'applied')
        next_plan=self.engine.prepare_with_grant(grant,[self.delete()])
        with self.assertRaisesRegex(SafetyError,'24-hour'):self.engine.execute(next_plan['plan_id'])
        self.assertFalse(self.store.path(next_plan['plan_id'],'.started').exists());manager.revoke(grant)
        deletion=self.deleted();scope['rules']=[{'operation':'restore_reminder','source_id':f.SOURCE,'container_id':f.ORIGIN,'fields':['deletion_plan_id','list_id','include_descendants'],'destinations':[f.ORIGIN]}]
        grant=manager.prepare(scope)['plan_id'];manager.activate(grant);r=self.engine.prepare_with_grant(grant,[self.restore(deletion)]);self.assertEqual(self.engine.execute(r['plan_id'])['status'],'applied');self.assertEqual(self.store.read(self.store.path(r['plan_id'],'.native-grant-use'))['count'],2);manager.revoke(grant)
