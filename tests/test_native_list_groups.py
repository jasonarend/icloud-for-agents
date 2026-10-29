"""Scoped physical-list grouping preserves list identity and bounded membership."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from icloud_for_agents.native_reminders import RemindersCreateAdapter
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.native_grants import NativeGrants
from icloud_for_agents.native_access import require_write
from icloud_for_agents.native_protocol import mutation_request
from icloud_for_agents.native_list_groups import expected_snapshot
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError
SOURCE='11111111-1111-4111-8111-111111111111';LIST='22222222-2222-4222-8222-222222222222';GROUP='33333333-3333-4333-8333-333333333333';ANCHOR='44444444-4444-4444-8444-444444444444'
class Reader:
    def __init__(self):
        self.lists=[{'id':LIST,'source_id':SOURCE,'title':'Synthetic list','writable':True}]
        self.features={'list_id':LIST,'source_id':SOURCE,'data_mutated':False,**{k:False for k in ('grocery_categorization','grocery_shopping_session','is_group','shared','shared_to_me')}}
        self.inventory={'protocol_version':1,'source_id':SOURCE,'groups':[{'id':GROUP,'title':'Synthetic group'},{'id':ANCHOR,'title':'Synthetic root anchor'}],'account_order':[GROUP,LIST,ANCHOR],'data_mutated':False,'backend':'reminderkit-root-group-inventory-v1'}
        self.parent={'protocol_version':1,'source_id':SOURCE,'list_id':LIST,'parent':None,'parent_status':'provider_not_reported','hierarchy_complete':False,'data_mutated':False,'backend':'reminderkit-list-parent-v1'}
        self.membership={'protocol_version':1,'source_id':SOURCE,'list_id':LIST,'count':2,'sha256':'a'*64}
        self.children={'protocol_version':1,'source_id':SOURCE,'group_id':GROUP,'lists':[],'next_offset':0,'done':True,'data_mutated':False,'backend':'reminderkit-group-lists-v3','custom_smart_lists_included':True,'membership_complete':False,'ordering_verified':False,'pagination_consistency':'live_offset_may_shift'}
    def read(self,action,**fields):return copy.deepcopy({'lists':{'lists':self.lists,'done':True},'list_features':self.features,'group_inventory':self.inventory,'list_parent':self.parent,'list_membership':self.membership,'group_lists':self.children}[action])
class Adapter(RemindersCreateAdapter):
    backend_id='synthetic-list-groups'
    def __init__(self):self.reader=Reader();self.calls=[]
    def apply(self,item):
        require_write('reminders',item);wire=mutation_request('reminders',item)['change'];assert set(wire['before'])=={'source_scope','membership','inventory','parent','group_members'};assert set(wire['before']['membership'])=={'source_id','list_id','count','sha256'}
        self.calls.append(item['operation']);value=expected_snapshot(item);self.reader.inventory=value['inventory'];self.reader.parent=value['parent'];self.reader.children=value['group_members'];return {'id':item['id'],'source_id':item['source_id'],'list_id':item['list_id']}
class ListGroupTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.adapter=Adapter();self.store=PlanStore(Path(temp.name)/'runtime');p=patch.dict(os.environ,{'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'access_mode':'read-only','source_ids':[SOURCE],'advanced_backend':'reminderkit'}})},clear=True);p.start();self.addCleanup(p.stop);self.engine=NativeChanges('reminders','synthetic@example.com',self.adapter,self.store)
    def change(self,group=True):return {'operation':'group_list' if group else 'ungroup_list','source_id':SOURCE,'list_id':LIST,'fields':{'group_id':GROUP} if group else {'anchor_id':ANCHOR,'placement':'before'}}
    def apply(self,group=True):
        p=self.engine.prepare_once([self.change(group)]);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');return p
    def test_once_roundtrip_preserves_exact_account_sequence_and_task_membership(self):
        initial=copy.deepcopy(self.adapter.reader.__dict__);p=self.apply()
        self.assertEqual(self.adapter.reader.parent['parent']['id'],GROUP)
        with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
        self.apply(False);self.assertEqual(initial,self.adapter.reader.__dict__);self.assertEqual(os.environ['ICLOUD_MAIL_ACCESS'],'read-only')
    def test_schema_private_roles_nonempty_smart_group_and_missing_anchor_refuse(self):
        for change in (self.change(False),dict(self.change(),source_id=GROUP),dict(self.change(),fields={'group_id':GROUP,'title':'extra'}),dict(self.change(),list_id=GROUP)):
            with self.assertRaises(SafetyError):self.engine.prepare_once([change])
        self.adapter.reader.children['lists']=[{'id':ANCHOR,'kind':'smart_list','is_group':False,'title':'Synthetic smart list'}]
        with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
        self.adapter.reader.children['lists']=[]
        for key in ('shared','shared_to_me','is_group','grocery_categorization','grocery_shopping_session'):
            self.adapter.reader.features[key]=True
            with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
            self.adapter.reader.features[key]=False
        self.apply();self.adapter.reader.inventory['account_order'].remove(ANCHOR)
        with self.assertRaises(SafetyError):self.engine.prepare_once([self.change(False)])
    def test_all_bound_observations_drift_before_dispatch(self):
        for name,key,value in (('membership','count',3),('children','done',False),('parent','parent',{'id':GROUP,'title':'Synthetic group'}),('inventory','account_order',[LIST,GROUP,ANCHOR])):
            old=copy.deepcopy(getattr(self.adapter.reader,name));p=self.engine.prepare_once([self.change()]);getattr(self.adapter.reader,name)[key]=value
            with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
            self.assertFalse(self.store.path(p['plan_id'],'.started').exists());setattr(self.adapter.reader,name,old)
        self.assertEqual(self.adapter.calls,[])
    def test_ongoing_exact_group_anchor_scope_and_revocation(self):
        m=NativeGrants(self.engine);g=m.prepare({'scenario':'Synthetic physical grouping','rules':[{'operation':'group_list','source_id':SOURCE,'container_id':LIST,'fields':['group_id'],'destinations':[GROUP]},{'operation':'ungroup_list','source_id':SOURCE,'container_id':LIST,'fields':['anchor_id','placement'],'destinations':[ANCHOR]}],'max_batch':1,'max_24h':4,'expires':None})['plan_id'];m.activate(g)
        with self.assertRaises(SafetyError):self.engine.prepare_with_grant(g,[dict(self.change(),fields={'group_id':ANCHOR})])
        for group in (True,False):
            p=self.engine.prepare_with_grant(g,[self.change(group)]);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');self.assertEqual(m.status(g)['status'],'active')
        p=self.engine.prepare_with_grant(g,[self.change()]);m.revoke(g)
        with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
    def test_returned_id_and_retained_terminal_error_observe_saved_state_without_replay(self):
        from icloud_for_agents.native_reminder_exchange import begin,finish
        with patch.object(self.adapter,'verify',return_value=False):
            p=self.engine.prepare_once([self.change()]);self.engine.execute(p['plan_id'])
        self.assertEqual(self.engine.reconcile_known_changes(p['plan_id'])['status'],'applied_after_readback_reconciliation')
        apply=self.adapter.apply
        def uncertain(item):
            apply(item);finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic post-save error')
        with patch.object(self.adapter,'apply',side_effect=uncertain):
            p=self.engine.prepare_once([self.change(False)]);self.engine.execute(p['plan_id'])
        original=self.store.path(p['plan_id'],'.result').read_bytes();self.assertEqual(self.engine.reconcile_known_changes(p['plan_id'])['status'],'applied_after_readback_reconciliation');self.engine.assert_settled();self.assertEqual(original,self.store.path(p['plan_id'],'.result').read_bytes());self.assertEqual(self.adapter.calls,['group_list','ungroup_list'])
    def test_unchanged_terminal_error_requires_separate_review_and_rechecks_membership(self):
        from icloud_for_agents.native_reminder_exchange import begin,finish
        from icloud_for_agents.native_reminder_recovery import ReminderObservationRecovery
        def unchanged(item):
            finish(begin(mutation_request('reminders',item)),{'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True});raise SafetyError('Synthetic unchanged terminal error')
        with patch.object(self.adapter,'apply',side_effect=unchanged):
            p=self.engine.prepare_once([self.change()]);self.engine.execute(p['plan_id'])
        recovery=ReminderObservationRecovery(self.engine.account,self.store,self.adapter)
        with self.assertRaises(SafetyError):recovery.prepare(p['plan_id'],legacy_missing_terminal=True)
        review=recovery.prepare(p['plan_id']);self.adapter.reader.membership['count']=3
        with self.assertRaises(SafetyError):recovery.execute(review['plan_id'])
        self.adapter.reader.membership['count']=2;self.assertEqual(recovery.execute(review['plan_id'])['recovery_state'],'observed_unchanged_reminder_accepted_after_review');self.engine.assert_settled();self.assertEqual(self.adapter.calls,[])
    def populated(self):
        existing='55555555-5555-4555-8555-555555555555';smart='66666666-6666-4666-8666-666666666666';self.adapter.reader.inventory['account_order'].extend([existing,smart]);self.adapter.reader.children['lists']=[{'id':existing,'title':'Synthetic existing list','kind':'list','is_group':False},{'id':smart,'title':'Synthetic existing custom view','kind':'smart_list','is_group':False}];self.adapter.reader.children['next_offset']=2
        return existing,smart
    def test_explicit_populated_group_roundtrip_preserves_existing_physical_and_smart_members(self):
        self.populated();initial=copy.deepcopy(self.adapter.reader.__dict__);change=self.change();change['fields']['allow_nonempty']=True
        with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
        p=self.engine.prepare_once([change]);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');self.assertEqual(len(self.adapter.reader.children['lists']),3);self.apply(False);self.assertEqual(self.adapter.reader.__dict__,initial)
    def test_existing_grant_cannot_silently_acquire_populated_destination_authority(self):
        self.populated();m=NativeGrants(self.engine);scope={'scenario':'Synthetic existing group','rules':[{'operation':'group_list','source_id':SOURCE,'container_id':LIST,'fields':['group_id'],'destinations':[GROUP]}],'max_batch':1,'max_24h':2,'expires':None};g=m.prepare(scope)['plan_id'];m.activate(g);change=self.change();change['fields']['allow_nonempty']=True
        with self.assertRaises(SafetyError):self.engine.prepare_with_grant(g,[change])
        m.revoke(g);scope['rules'][0]['fields'].append('allow_nonempty');g=m.prepare(scope)['plan_id'];m.activate(g);p=self.engine.prepare_with_grant(g,[change]);self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');m.revoke(g)
    def test_populated_false_flag_nested_group_missing_order_duplicate_and_bound_refuse(self):
        self.populated();change=self.change();change['fields']['allow_nonempty']=True;before=copy.deepcopy(self.adapter.reader.__dict__)
        for flag in (False,1,'yes',None):
            with self.assertRaises(SafetyError):self.engine.prepare_once([dict(change,fields={'group_id':GROUP,'allow_nonempty':flag})])
        self.adapter.reader.children['lists'][0]['is_group']=True
        with self.assertRaises(SafetyError):self.engine.prepare_once([change])
        self.adapter.reader.__dict__.update(copy.deepcopy(before));self.adapter.reader.inventory['account_order'].remove(self.adapter.reader.children['lists'][0]['id'])
        with self.assertRaises(SafetyError):self.engine.prepare_once([change])
        self.adapter.reader.__dict__.update(copy.deepcopy(before));self.adapter.reader.children['lists'][0]['id']=LIST
        with self.assertRaises(SafetyError):self.engine.prepare_once([change])
        self.adapter.reader.__dict__.update(copy.deepcopy(before));self.adapter.reader.children['lists']*=50
        with self.assertRaises(SafetyError):self.engine.prepare_once([change])
    def test_populated_bystander_change_refuses_preflight_and_readback(self):
        self.populated();change=self.change();change['fields']['allow_nonempty']=True;p=self.engine.prepare_once([change]);self.adapter.reader.children['lists'][0]['title']='Concurrent member rename'
        with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
        self.assertFalse(self.store.path(p['plan_id'],'.started').exists());self.assertEqual(self.adapter.calls,[])
        self.adapter.reader.children['lists'][0]['title']='Synthetic existing list';p=self.engine.prepare_once([change]);item=p['review']['payload']['changes'][0];self.assertEqual(self.engine.execute(p['plan_id'])['status'],'applied');outcome={'id':LIST,'source_id':SOURCE,'list_id':LIST};self.adapter.reader.children['lists']=[v for v in self.adapter.reader.children['lists']if v['id']==LIST]
        with self.assertRaises(SafetyError):self.adapter.verify(item,outcome)
