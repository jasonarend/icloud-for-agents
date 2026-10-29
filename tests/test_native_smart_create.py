"""Preassigned custom-view creation, preservation, permission and recovery contracts."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from test_native_smart_sources import Reader as BaseReader, f, SMART
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError,digest
from icloud_for_agents.native_access import require_write
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.native_grants import NativeGrants
from icloud_for_agents.native_protocol import mutation_request
from icloud_for_agents.native_reminders import RemindersCreateAdapter
from icloud_for_agents.native_reminder_smart_create import expected_snapshot
from icloud_for_agents.native_reminder_smart_sources import encoded


class Reader(BaseReader):
    def __init__(self):
        super().__init__();self.views={SMART:copy.deepcopy(self.view)};self.incomplete=False
        self.roots={'protocol_version':1,'source_id':f.SOURCE,'groups':[],'account_order':[f.ORIGIN,f.DEST,SMART],
                    'data_mutated':False,'backend':'reminderkit-root-group-inventory-v1'}
    def read(self,action,**fields):
        if action=='group_inventory':return copy.deepcopy(self.roots)
        if action=='smart_lists':
            rows=sorted([{'id':v['id'],'title':v['title']} for v in self.views.values()],key=lambda v:v['id'])
            return {'protocol_version':1,'source_id':f.SOURCE,'smart_lists':rows,'next_offset':len(rows),'done':not self.incomplete,
                    'pagination_consistency':'live_offset_may_shift','filter_rules_complete':False,'data_mutated':False,'backend':'reminderkit-smart-lists-v1'}
        if action=='smart_list_definition':return copy.deepcopy(self.views[fields['id']])
        return super().read(action,**fields)


class Adapter(RemindersCreateAdapter):
    backend_id='synthetic-smart-create'
    def __init__(self):self.reader=Reader();self.calls=[]
    def apply(self,item):
        require_write('reminders',item);self.calls.append(item['operation'])
        self.reader.views[item['id']]={'protocol_version':1,'source_id':item['source_id'],'id':item['id'],'title':item['fields']['title'],
                                      'minimum_supported_version':20220430,'definition_json':encoded(item['fields']['list_ids']),'data_mutated':False,'backend':'reminderkit-smart-definition-v2'}
        self.reader.roots['account_order'].append(item['id'])
        return {'id':item['id'],'source_id':item['source_id'],'saved_locally':True}


class SmartCreateTests(unittest.TestCase):
    def setUp(self):
        env={'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_APPROVAL_POLICY':'trusted-client',
             'ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'source_ids':[f.SOURCE],'access_mode':'read-only','advanced_backend':'reminderkit','helper_path':'/synthetic/helper'}})}
        p=patch.dict(os.environ,env,clear=True);p.start();self.addCleanup(p.stop);tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.store=PlanStore(Path(tmp.name)/'runtime');self.adapter=Adapter();self.engine=NativeChanges('reminders','synthetic@example.com',self.adapter,self.store)
    def change(self,title='Synthetic created view',values=None):
        return {'operation':'create_smart_list','source_id':f.SOURCE,'fields':{'title':title,'list_ids':[f.ORIGIN] if values is None else values}}
    def scope(self):
        return {'scenario':'Synthetic custom-view creation','rules':[{'operation':'create_smart_list','source_id':f.SOURCE,'container_id':f.SOURCE,
                'fields':['title','list_ids'],'destinations':[f.ORIGIN]}],'max_batch':1,'max_24h':2,'expires':None}

    def test_once_preassigned_id_exact_readback_and_underlying_items_unchanged(self):
        original=copy.deepcopy(self.adapter.reader.items);old=copy.deepcopy(self.adapter.reader.views)
        with self.assertRaises(SafetyError):self.engine.prepare([self.change()])
        p=self.engine.prepare_once([self.change(values=[f.DEST,f.ORIGIN])]);item=p['review']['payload']['changes'][0]
        self.assertEqual(item['fields']['list_ids'],sorted([f.ORIGIN,f.DEST]));self.assertNotIn(item['id'],old)
        self.assertEqual(mutation_request('reminders',item)['change']['before'],item['before'])
        self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');self.assertEqual(self.adapter.snapshot(item),expected_snapshot(item))
        self.assertEqual({k:self.adapter.reader.views[k] for k in old},old);self.assertEqual(self.adapter.reader.items,original);self.engine.assert_settled()
        with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])

    def test_invalid_titles_raw_filters_caller_ids_duplicates_and_incomplete_inventory_refuse(self):
        for title in ('','  ','x\n','x'*1001,'\ud800'):
            with self.assertRaises(SafetyError):self.engine.prepare_once([self.change(title)])
        for extra in ({'id':f.ROOT},{'filter_json':'{}'}):
            with self.assertRaises(SafetyError):self.engine.prepare_once([dict(self.change(),**extra)])
        for values in ([],[f.ORIGIN,f.ORIGIN],['foreign'],[True],list(range(11))):
            with self.assertRaises(SafetyError):self.engine.prepare_once([self.change(values=values)])
        with self.assertRaises(SafetyError):self.engine.prepare_once([self.change(self.adapter.reader.view['title'])])
        self.adapter.reader.incomplete=True
        with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
        self.assertEqual(self.adapter.calls,[])

    def test_shared_reference_root_order_and_custom_inventory_drift_refuse_before_dispatch(self):
        self.adapter.reader.shared=True
        with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
        self.adapter.reader.shared=False
        p=self.engine.prepare_once([self.change()]);self.adapter.reader.roots['account_order'].reverse()
        with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
        self.assertFalse(self.store.path(p['plan_id'],'.started').exists());self.assertEqual(self.adapter.calls,[])

    def test_strict_receipt_and_scoped_source_destinations_quota_and_revocation(self):
        os.environ['ICLOUD_APPROVAL_POLICY']='local-receipt';p=self.engine.prepare_once([self.change()])
        with self.assertRaises(FileNotFoundError):self.engine.execute(p['plan_id'])
        # Isolated synthetic owner fixture only; production approval is independent.
        self.store.write_once(self.store.path(p['plan_id'],'.approved'),{'plan_id':p['plan_id'],'sha256':p['sha256']})
        self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied')
        os.environ['ICLOUD_APPROVAL_POLICY']='trusted-client';m=NativeGrants(self.engine);scope=self.scope();g=m.prepare(scope)['plan_id'];m.activate(g)
        with self.assertRaises(SafetyError):self.engine.prepare_with_grant(g,[self.change('Foreign reference',[f.DEST])])
        for title in ('Synthetic scoped one','Synthetic scoped two'):
            p=self.engine.prepare_with_grant(g,[self.change(title)]);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied')
            self.assertEqual(self.store.read(self.store.path(p['plan_id'],'.native-grant-use'))['count'],1)
        pending=self.engine.prepare_with_grant(g,[self.change('Synthetic over quota')])
        with self.assertRaises(SafetyError):self.engine.execute(pending['plan_id'])
        m.revoke(g)
        with self.assertRaises(SafetyError):self.engine.execute(pending['plan_id'])
        self.assertEqual(os.environ['ICLOUD_MAIL_ACCESS'],'read-only')

    def test_terminal_error_expected_state_preserves_failure_and_forged_view_refuses(self):
        from icloud_for_agents.native_reminder_exchange import begin,finish
        apply=self.adapter.apply
        def uncertain(item):
            apply(item);finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic terminal error')
        p=self.engine.prepare_once([self.change()])
        with patch.object(self.adapter,'apply',side_effect=uncertain):self.engine.execute(p['plan_id'])
        original=self.store.path(p['plan_id'],'.result').read_bytes()
        self.assertEqual(self.engine.reconcile_known_changes(p['plan_id'])['status'],'applied_after_readback_reconciliation');self.engine.assert_settled()
        path=self.store.path(p['plan_id'],'.native-lifecycle-readback');value=self.store.read(path);value['observed']['view']['definition_json']='{}';value['observed_sha256']=digest(value['observed']);path.write_text(json.dumps(value))
        self.assertFalse(self.store._native_lifecycle_readback_completed(p['plan_id']));self.assertEqual(self.store.path(p['plan_id'],'.result').read_bytes(),original)

    def test_unchanged_terminal_error_needs_review_retains_absence_limits_and_forbids_replay(self):
        from icloud_for_agents.native_reminder_exchange import begin,finish
        from icloud_for_agents.native_reminder_recovery import ReminderObservationRecovery
        def unchanged(item):
            finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic no observed effect')
        p=self.engine.prepare_once([self.change()])
        with patch.object(self.adapter,'apply',side_effect=unchanged):self.engine.execute(p['plan_id'])
        r=ReminderObservationRecovery(self.engine.account,self.store,self.adapter)
        with self.assertRaises(SafetyError):r.prepare(p['plan_id'],legacy_missing_terminal=True)
        review=r.prepare(p['plan_id']);self.assertIn('not proven absent globally',review['review']['payload']['effects']['smart_creation_coverage'])
        r.execute(review['plan_id']);self.engine.assert_settled()
        with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])

    def test_readback_refuses_zero_version_even_with_matching_title_and_filter(self):
        apply=self.adapter.apply
        def wrong_version(item):
            result=apply(item);self.adapter.reader.views[item['id']]['minimum_supported_version']=0;return result
        p=self.engine.prepare_once([self.change()])
        with patch.object(self.adapter,'apply',side_effect=wrong_version):self.assertEqual(self.engine.execute(p['plan_id'])['status'],'failed_or_uncertain')
        with self.assertRaises(SafetyError):self.engine.reconcile_known_changes(p['plan_id'])
        with self.assertRaises(SafetyError):self.engine.assert_settled()

    def test_verification_failure_reconciles_returned_identity_without_new_write(self):
        p=self.engine.prepare_once([self.change()]);verify=self.adapter.verify
        with patch.object(self.adapter,'verify',return_value=False):self.assertEqual(self.engine.execute(p['plan_id'])['status'],'failed_or_uncertain')
        self.assertEqual(len(self.adapter.calls),1);self.assertTrue(verify(p['review']['payload']['changes'][0],{'id':p['review']['payload']['changes'][0]['id'],'source_id':f.SOURCE}))
        self.engine.reconcile_known_changes(p['plan_id']);self.engine.assert_settled();self.assertEqual(len(self.adapter.calls),1)
