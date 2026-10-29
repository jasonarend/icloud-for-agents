"""Scoped changes to the verified Lists-only native Smart List filter shape."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import test_native_reminder_moves as f
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError,digest
from icloud_for_agents.native_access import require_write
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.native_grants import NativeGrants
from icloud_for_agents.native_protocol import mutation_request
from icloud_for_agents.native_reminders import RemindersCreateAdapter
from icloud_for_agents.native_reminder_smart_sources import ids,encoded,source_ids

SMART='66666666-6666-4666-8666-666666666666'

class Reader(f.Reader):
    def __init__(self):
        super().__init__();self.view={'protocol_version':1,'source_id':f.SOURCE,'id':SMART,'title':'Synthetic source view',
                                     'definition_json':encoded([f.ORIGIN]),'data_mutated':False,'backend':'reminderkit-smart-definition-v1'}
    def read(self,action,**fields):
        if action=='smart_list_definition':
            if fields!={'source_id':f.SOURCE,'id':SMART}:raise SafetyError('Synthetic exact custom view required')
            return copy.deepcopy(self.view)
        return super().read(action,**fields)

class Adapter(RemindersCreateAdapter):
    backend_id='synthetic-smart-sources'
    def __init__(self):self.reader=Reader();self.calls=[]
    def apply(self,item):
        require_write('reminders',item);self.calls.append(item['operation']);self.reader.view['definition_json']=encoded(item['fields']['list_ids'])
        return {'id':item['id'],'source_id':item['source_id'],'saved_locally':True}

class SmartSourcesTests(unittest.TestCase):
    def setUp(self):
        env={'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_APPROVAL_POLICY':'trusted-client',
             'ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'source_ids':[f.SOURCE],'access_mode':'read-only','advanced_backend':'reminderkit','helper_path':'/synthetic/helper'}})}
        p=patch.dict(os.environ,env,clear=True);p.start();self.addCleanup(p.stop);tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.store=PlanStore(Path(tmp.name)/'runtime');self.adapter=Adapter();self.engine=NativeChanges('reminders','synthetic@example.com',self.adapter,self.store)
    def change(self,values=None):return {'operation':'set_smart_list_sources','source_id':f.SOURCE,'id':SMART,'fields':{'list_ids':[f.DEST]if values is None else values}}
    def scope(self):return {'scenario':'Synthetic view sources','rules':[{'operation':'set_smart_list_sources','source_id':f.SOURCE,'container_id':SMART,'fields':['list_ids'],'destinations':[f.ORIGIN,f.DEST]}],'max_batch':1,'max_24h':2,'expires':None}

    def test_exact_once_roundtrip_preserves_title_references_and_underlying_items(self):
        before=copy.deepcopy(self.adapter.reader.items);view=copy.deepcopy(self.adapter.reader.view)
        with self.assertRaises(SafetyError):self.engine.prepare([self.change()])
        for values in ([f.DEST],[f.ORIGIN],[f.ORIGIN,f.DEST],[f.ORIGIN]):
            p=self.engine.prepare_once([self.change(values)]);item=p['review']['payload']['changes'][0]
            self.assertEqual(item['reference_ids'],sorted([f.ORIGIN,f.DEST]));self.assertEqual(mutation_request('reminders',item)['change']['before'],item['before'])
            self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied')
        self.assertEqual(self.adapter.reader.view,view);self.assertEqual(self.adapter.reader.items,before);self.assertEqual(os.environ['ICLOUD_MAIL_ACCESS'],'read-only');self.engine.assert_settled()

    def test_invalid_ids_duplicates_bounds_and_unknown_criteria_refuse(self):
        for values in ([],[f.DEST]*2,['foreign'],[True],list(range(11))):
            with self.assertRaises(SafetyError):self.engine.prepare_once([self.change(values)])
        original=self.adapter.reader.view['definition_json']
        for value in ({'operation':'or','lists':{'include':[f.ORIGIN],'exclude':[]}},
                      {'operation':'and','lists':{'include':[f.ORIGIN],'exclude':[f.DEST]}},
                      {'operation':'and','lists':{'include':[f.ORIGIN],'exclude':[]},'flagged':True}):
            self.adapter.reader.view['definition_json']=json.dumps(value)
            with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
        self.adapter.reader.view['definition_json']=original
        with self.assertRaises(SafetyError):self.engine.prepare_once([self.change([f.ORIGIN])])
        self.assertEqual(self.adapter.calls,[])

    def test_foreign_shared_or_grocery_reference_and_current_drift_refuse(self):
        self.adapter.reader.shared=True
        with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
        self.adapter.reader.shared=False;p=self.engine.prepare_once([self.change()]);self.adapter.reader.view['title']='Changed'
        with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
        self.assertFalse(self.store.path(p['plan_id'],'.started').exists());self.assertEqual(self.adapter.calls,[])

    def test_strict_receipt_and_scenario_destination_quota_and_revocation(self):
        os.environ['ICLOUD_APPROVAL_POLICY']='local-receipt';p=self.engine.prepare_once([self.change()])
        with self.assertRaises(FileNotFoundError):self.engine.execute(p['plan_id'])
        # Isolated synthetic owner fixture only.
        self.store.write_once(self.store.path(p['plan_id'],'.approved'),{'plan_id':p['plan_id'],'sha256':p['sha256']});self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied')
        os.environ['ICLOUD_APPROVAL_POLICY']='trusted-client';m=NativeGrants(self.engine);scope=self.scope();scope['rules'][0]['destinations']=[f.DEST];g=m.prepare(scope)['plan_id'];m.activate(g)
        with self.assertRaises(SafetyError):self.engine.prepare_with_grant(g,[self.change([f.ORIGIN])])
        m.revoke(g);g=m.prepare(self.scope())['plan_id'];m.activate(g)
        for values in ([f.ORIGIN],[f.DEST]):
            p=self.engine.prepare_with_grant(g,[self.change(values)]);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');self.assertEqual(self.store.read(self.store.path(p['plan_id'],'.native-grant-use'))['count'],1)
        p=self.engine.prepare_with_grant(g,[self.change([f.ORIGIN])])
        with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
        m.revoke(g)
        with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])

    def test_readback_detects_title_or_unexpected_criterion_change(self):
        apply=self.adapter.apply
        def altered(item):
            result=apply(item);self.adapter.reader.view['title']='Unexpected rename';return result
        p=self.engine.prepare_once([self.change()])
        with patch.object(self.adapter,'apply',side_effect=altered):self.assertEqual(self.engine.execute(p['plan_id'])['status'],'failed_or_uncertain')
        with self.assertRaises(SafetyError):self.engine.assert_settled()

    def test_terminal_error_expected_state_and_forged_scope_evidence_do_not_replay(self):
        from icloud_for_agents.native_reminder_exchange import begin,finish
        apply=self.adapter.apply
        def uncertain(item):
            apply(item);finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic terminal error')
        p=self.engine.prepare_once([self.change()])
        with patch.object(self.adapter,'apply',side_effect=uncertain):self.engine.execute(p['plan_id'])
        original=self.store.path(p['plan_id'],'.result').read_bytes();self.assertEqual(self.engine.reconcile_known_changes(p['plan_id'])['status'],'applied_after_readback_reconciliation');self.engine.assert_settled()
        path=self.store.path(p['plan_id'],'.native-lifecycle-readback');value=self.store.read(path);value['observed']['snapshot']['scopes']=[];value['observed_sha256']=digest(value['observed']);path.write_text(json.dumps(value));self.assertFalse(self.store._native_lifecycle_readback_completed(p['plan_id']));self.assertEqual(self.store.path(p['plan_id'],'.result').read_bytes(),original)

    def test_unchanged_terminal_error_requires_review_and_forbids_replay(self):
        from icloud_for_agents.native_reminder_exchange import begin,finish
        from icloud_for_agents.native_reminder_recovery import ReminderObservationRecovery
        def unchanged(item):
            finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic no observed effect')
        p=self.engine.prepare_once([self.change()])
        with patch.object(self.adapter,'apply',side_effect=unchanged):self.engine.execute(p['plan_id'])
        r=ReminderObservationRecovery(self.engine.account,self.store,self.adapter);review=r.prepare(p['plan_id']);r.execute(review['plan_id']);self.engine.assert_settled()
        with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
