"""Exact-anchor root ordering, preserved children and honest permission accounting."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import test_native_reminder_moves as f
from icloud_for_agents.native_reminders import RemindersCreateAdapter
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.native_grants import NativeGrants
from icloud_for_agents.native_protocol import mutation_request
from icloud_for_agents.native_access import require_write
from icloud_for_agents.native_reminder_order import reordered
from icloud_for_agents.native_reminder_list_graph import validate_list_graph
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError
ANCHOR='66666666-6666-4666-8666-666666666666'
class Reader(f.Reader):
    def __init__(self):
        super().__init__();self.items[ANCHOR]=dict(self.items[f.ROOT],id=ANCHOR,title='Synthetic anchor');self.parents[ANCHOR]=None;self.flags[ANCHOR]=False;self.order=[ANCHOR,f.ROOT,f.CHILD]
    def read(self,action,**fields):
        if action=='list_order':return dict(super().read('list_hierarchy',**fields),backend='reminderkit-list-order-v1',ordered_ids=list(self.order))
        return super().read(action,**fields)
class Adapter(RemindersCreateAdapter):
    backend_id='synthetic-root-order'
    def __init__(self):self.reader=Reader();self.calls=[]
    def apply(self,item):
        require_write('reminders',item);self.calls.append(item['id']);wire=mutation_request('reminders',item)['change'];assert wire['before']['items'];self.reader.order=reordered(self.reader.order,item['id'],item['fields']['anchor_id'],item['fields']['placement']);return {'id':item['id'],'source_id':item['source_id'],'list_id':item['list_id']}
class OrderTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.adapter=Adapter();self.store=PlanStore(Path(temp.name)/'runtime');context=patch.dict(os.environ,{'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'access_mode':'read-only','source_ids':[f.SOURCE],'advanced_backend':'reminderkit'}})},clear=True);context.start();self.addCleanup(context.stop);self.engine=NativeChanges('reminders','synthetic@example.com',self.adapter,self.store)
    def change(self,placement='before',anchor=ANCHOR):return {'operation':'reorder_reminder','source_id':f.SOURCE,'id':f.ROOT,'fields':{'anchor_id':anchor,'placement':placement}}
    def scope(self,**extra):return {'scenario':'Synthetic exact root order','rules':[{'operation':'reorder_reminder','source_id':f.SOURCE,'container_id':f.ORIGIN,'fields':['anchor_id','placement'],'destinations':[ANCHOR]}],'max_batch':2,'max_24h':6,'expires':None,**extra}
    def test_once_roundtrip_preserves_complete_fields_and_child_parent(self):
        before=copy.deepcopy(self.adapter.reader.items);parents=copy.deepcopy(self.adapter.reader.parents)
        for placement in ('before','after'):
            p=self.engine.prepare_once([self.change(placement)]);self.assertEqual(p['review']['payload']['changes'][0]['effects']['quota_objects'],2);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied')
            with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
        self.assertEqual(self.adapter.reader.order,[ANCHOR,f.ROOT,f.CHILD]);self.assertEqual(before,self.adapter.reader.items);self.assertEqual(parents,self.adapter.reader.parents)
    def test_invalid_scope_anchors_nested_self_and_noop_refuse(self):
        for change in (self.change('after'),self.change('before',f.CHILD),self.change('before',f.ROOT),self.change('invalid'),dict(self.change(),source_id='other'),dict(self.change(),fields={'anchor_id':ANCHOR,'placement':'before','title':'extra'})):
            with self.assertRaises(SafetyError):self.engine.prepare_once([change])
        self.assertEqual(self.adapter.calls,[])
    def test_graph_order_and_child_drift_refuse_before_start(self):
        for field in ('order','child'):
            p=self.engine.prepare_once([self.change()])
            if field=='order':self.adapter.reader.order.reverse()
            else:self.adapter.reader.items[f.CHILD]['notes']='Concurrent edit'
            with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
            self.assertFalse(self.store.path(p['plan_id'],'.started').exists());self.adapter.reader.order=[ANCHOR,f.ROOT,f.CHILD];self.adapter.reader.items[f.CHILD]['notes']='Keep this body'
    def test_grant_exact_anchor_charges_descendants_and_revokes(self):
        m=NativeGrants(self.engine);small=m.prepare(self.scope(max_batch=1))['plan_id'];m.activate(small)
        with self.assertRaises(SafetyError):self.engine.prepare_with_grant(small,[self.change()])
        m.revoke(small);g=m.prepare(self.scope())['plan_id'];m.activate(g)
        for placement in ('before','after'):
            p=self.engine.prepare_with_grant(g,[self.change(placement)]);self.assertEqual(m.batch_count(p['review']['payload']['changes']),2);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');self.assertEqual(m.status(g)['status'],'active')
        p=self.engine.prepare_with_grant(g,[self.change()]);m.revoke(g)
        with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
    def test_read_order_membership_bounds_and_duplicates_refuse(self):
        graph=self.adapter.reader.read('list_order',list_id=f.ORIGIN);self.assertEqual(validate_list_graph(graph,f.ORIGIN,[f.SOURCE],ordered=True)['ordered_ids'],graph['ordered_ids'])
        for ids in ([ANCHOR,ANCHOR,f.ROOT],[ANCHOR,f.ROOT],['foreign',f.ROOT,f.CHILD]):
            with self.assertRaises(SafetyError):validate_list_graph(dict(graph,ordered_ids=ids),f.ORIGIN,[f.SOURCE],ordered=True)
    def test_known_id_reconciliation_does_not_repeat_save(self):
        with patch.object(self.adapter,'verify',return_value=False):
            p=self.engine.prepare_once([self.change()]);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'failed_or_uncertain')
        original=self.store.path(p['plan_id'],'.result').read_bytes();self.assertEqual(self.engine.reconcile_known_changes(p['plan_id'])['status'],'applied_after_readback_reconciliation');self.assertEqual(original,self.store.path(p['plan_id'],'.result').read_bytes());self.assertEqual(len(self.adapter.calls),1)
    def test_terminal_error_expected_state_reconciles_without_returned_attempt(self):
        from icloud_for_agents.native_reminder_exchange import begin,finish
        apply=self.adapter.apply
        def uncertain(item):
            apply(item);finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic post-effect terminal error')
        with patch.object(self.adapter,'apply',side_effect=uncertain):
            p=self.engine.prepare_once([self.change()]);self.engine.execute(p['plan_id'])
        original=self.store.path(p['plan_id'],'.result').read_bytes();self.assertEqual(self.engine.reconcile_known_changes(p['plan_id'])['status'],'applied_after_readback_reconciliation');self.engine.assert_settled();self.assertEqual(original,self.store.path(p['plan_id'],'.result').read_bytes());self.assertEqual(len(self.adapter.calls),1)
        proof=self.store.path(p['plan_id'],'.native-lifecycle-readback');record=self.store.read(proof);record['observed']['snapshot']['descendants'][0]['item']['notes']='Forged';from icloud_for_agents.core import digest
        record['observed_sha256']=digest(record['observed']);proof.write_text(json.dumps(record));self.assertFalse(self.store._native_lifecycle_readback_completed(p['plan_id']))
    def test_unchanged_order_terminal_error_requires_separate_review_and_no_legacy(self):
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
