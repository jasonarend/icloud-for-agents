"""Bounded subtree preservation, exact scope and affected-object grant accounting."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,PropertyMock
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_reminders import RemindersCreateAdapter
from icloud_for_agents.native_protocol import mutation_request
from icloud_for_agents.native_grants import NativeGrants
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.approvals import PlanStore
SOURCE='11111111-1111-4111-8111-111111111111';ORIGIN='22222222-2222-4222-8222-222222222222';DEST='33333333-3333-4333-8333-333333333333';ROOT='44444444-4444-4444-8444-444444444444';CHILD='55555555-5555-4555-8555-555555555555'

class Reader:
    def __init__(self):
        self.lists=[{'id':ORIGIN,'source_id':SOURCE,'title':'Synthetic origin','writable':True},{'id':DEST,'source_id':SOURCE,'title':'Synthetic destination','writable':True}];self.shared=False;self.calls=[]
        base={'source_id':SOURCE,'list_id':ORIGIN,'title':'Synthetic root','notes':'Keep this body','completed':False,'priority':0,'due':None,'start':None,'url':None,'location':None,'has_recurrence':False,'has_alarms':False,'recurrence_rules':[],'alarms':[],'created':100,'modified':200,'completion_date':None,'public_metadata_complete':True,'content_complete':False,'omitted_features':['native_metadata']}
        self.items={ROOT:dict(base,id=ROOT),CHILD:dict(base,id=CHILD,title='Synthetic child')};self.parents={ROOT:None,CHILD:ROOT};self.flags={ROOT:False,CHILD:True}
    def read(self,action,**fields):
        self.calls.append(action)
        if action=='lists':return {'lists':copy.deepcopy(self.lists),'done':True}
        if action=='list_features':return {'source_id':SOURCE,'list_id':fields['list_id'],'data_mutated':False,'is_group':False,'shared':self.shared,'shared_to_me':False,'grocery_categorization':False,'grocery_shopping_session':False}
        if action=='list_hierarchy':
            nodes=[{'id':key,'parent_id':self.parents[key],'title':v['title'],'completed':v['completed']} for key,v in self.items.items() if v['list_id']==fields['list_id']]
            return {'protocol_version':1,'source_id':SOURCE,'list_id':fields['list_id'],'backend':'reminderkit-list-hierarchy-v1','data_mutated':False,'membership_verified':True,'hierarchy_complete':True,'observation_consistency':'separate_provider_reads','coverage':'native_list_and_subtasks_matching_eventkit','nodes':sorted(nodes,key=lambda v:v['id'])}
        if action=='get':
            item=copy.deepcopy(self.items[fields['id']]);identity=item['id'];children=sorted(k for k,v in self.parents.items() if v==identity)
            if fields.get('include_native_urgent'):item['native_metadata']={**{key:item[key] for key in ('id','source_id','list_id')},'coverage':['urgent'],'urgent':False}
            if fields.get('include_native_metadata'):
                item['native_metadata']={**{key:item[key] for key in ('id','source_id','list_id')},'flagged':self.flags[identity],'flag_state':int(self.flags[identity]),'tags':[],'tags_complete':True,'coverage':['flagged','tags','hierarchy','children','section','early'],'early_alerts':[],'early_payload_fingerprint':None,'section':None,'section_status':'provider_not_reported','native_metadata_complete':False,'is_subtask':self.parents[identity] is not None,'parent_id':self.parents[identity],'has_subtasks':bool(children),'hierarchy_complete':False,'children_status':'provider_reported','child_ids':children,'children_complete':False,'children_fetch_status':'provider_reported'}
            return {'item':item}
        raise AssertionError(action)

class ReminderMoveTests(unittest.TestCase):
    def setUp(self):
        env={'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCESS':'write-enabled','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'access_mode':'write-enabled','source_ids':[SOURCE],'advanced_backend':'reminderkit','helper_path':'/synthetic/helper'}})}
        context=patch.dict(os.environ,env,clear=True);context.start();self.addCleanup(context.stop);self.adapter=RemindersCreateAdapter();self.reader=Reader();self.adapter.reader=self.reader
        backend=patch.object(RemindersCreateAdapter,'backend_id',new_callable=PropertyMock,return_value='synthetic-moves');backend.start();self.addCleanup(backend.stop)
    def change(self,**extra):return dict({'operation':'move_reminder','source_id':SOURCE,'id':ROOT,'fields':{'list_id':DEST}},**extra)
    def apply(self,item,*,children=True):
        ids=[ROOT,CHILD] if children else [ROOT]
        for identity in ids:self.reader.items[identity]['list_id']=DEST;self.reader.items[identity]['modified']+=1
        return {'id':ROOT,'source_id':SOURCE,'list_id':DEST,'moved_ids':sorted([ROOT,CHILD]),'saved_locally':True}
    def test_prepare_binds_every_descendant_and_exact_native_payload(self):
        item=self.adapter.prepare(self.change());self.assertEqual(len(item['before']['descendants']),1);self.assertEqual(item['effects']['quota_objects'],2)
        request=mutation_request('reminders',item)['change'];self.assertEqual(request['hierarchy_guard'],'verified_list_graphs');self.assertEqual(request['before']['source_nodes'],[{'id':ROOT,'parent_id':None},{'id':CHILD,'parent_id':ROOT}]);self.assertEqual(request['before']['descendants'][0]['item']['id'],CHILD)
        self.assertEqual(self.adapter.snapshot(item),item['before']);outcome=self.apply(item);self.assertTrue(self.adapter.verify(item,outcome))
    def test_partial_subtree_move_and_content_or_flag_drift_never_verify(self):
        item=self.adapter.prepare(self.change());outcome=self.apply(item,children=False);self.assertFalse(self.adapter.verify(item,outcome))
        self.reader.items[CHILD]['list_id']=DEST;self.reader.items[CHILD]['notes']='changed';self.assertFalse(self.adapter.verify(item,outcome));self.reader.items[CHILD]['notes']='Keep this body'
        self.reader.flags[CHILD]=False;self.assertFalse(self.adapter.verify(item,outcome))
    def test_shared_foreign_same_list_child_and_recurring_targets_refuse(self):
        self.reader.shared=True
        with self.assertRaises(SafetyError):self.adapter.prepare(self.change())
        self.reader.shared=False
        for change in (self.change(source_id='foreign'),self.change(id=CHILD),self.change(fields={'list_id':ORIGIN}),self.change(fields={'list_id':DEST,'extra':True})):
            with self.assertRaises(SafetyError):self.adapter.prepare(change)
        self.reader.items[CHILD]['has_recurrence']=True
        with self.assertRaises(SafetyError):self.adapter.prepare(self.change())
    def test_grants_charge_descendants_to_batch_rolling_cap_and_legacy_records(self):
        with tempfile.TemporaryDirectory() as directory:
            store=PlanStore(Path(directory)/'runtime');engine=NativeChanges('reminders','synthetic@example.com',self.adapter,store);manager=NativeGrants(engine)
            scope={'scenario':'Move synthetic subtree','rules':[{'operation':'move_reminder','source_id':SOURCE,'container_id':ORIGIN,'fields':['list_id'],'destinations':[DEST]}],'max_batch':1,'max_24h':2,'expires':None}
            proposal=manager.prepare(scope);manager.activate(proposal['plan_id'])
            with self.assertRaisesRegex(SafetyError,'native-object bound'):engine.prepare_with_grant(proposal['plan_id'],[self.change()])
            manager.revoke(proposal['plan_id']);scope['max_batch']=2;proposal=manager.prepare(scope);manager.activate(proposal['plan_id']);batch=engine.prepare_with_grant(proposal['plan_id'],[self.change()]);self.adapter.apply=self.apply
            self.assertEqual(engine.execute(batch['plan_id'])['status'],'applied');self.assertEqual(store.read(store.path(batch['plan_id'],'.native-grant-use'))['count'],2)
            self.reader.items[ROOT]['list_id']=ORIGIN;self.reader.items[CHILD]['list_id']=ORIGIN
            next_batch=engine.prepare_with_grant(proposal['plan_id'],[self.change()])
            with self.assertRaisesRegex(SafetyError,'24-hour'):engine.execute(next_batch['plan_id'])
            manager.revoke(proposal['plan_id']);legacy={'operation':'move_reminder','before':{}}
            self.assertEqual(manager.batch_count([legacy],legacy=True),1)
            with self.assertRaises(SafetyError):manager.batch_count([legacy])

    def test_uncertain_move_observation_reads_children_and_both_lists_without_settling(self):
        from icloud_for_agents.native_reminder_observation import inspect_uncertain_reminder
        with tempfile.TemporaryDirectory() as directory:
            store=PlanStore(Path(directory)/'runtime');engine=NativeChanges('reminders','synthetic@example.com',self.adapter,store)
            item=self.adapter.prepare(self.change());proposal=store.prepare('reminders_change',{'account':engine.account,'context':engine.context(True),'changes':[item]});identity=proposal['plan_id'];store.consume(identity,engine.account);store.finish(identity,{'status':'failed_or_uncertain','phase':'mutation','attempted':[],'completed':[],'do_not_retry':True})
            with patch('icloud_for_agents.native_reminder_exchange.inspect',return_value={'terminal_response':'not_recorded'}):
                observed=inspect_uncertain_reminder(engine,identity);self.assertTrue(observed['observed_snapshot_matches_before']);self.assertIn('both_bounded_list_graphs',observed['snapshot_coverage'])
                self.reader.items[CHILD]['notes']='Changed child';self.assertFalse(inspect_uncertain_reminder(engine,identity)['observed_snapshot_matches_before']);self.reader.items[CHILD]['notes']='Keep this body'
                self.apply(item);self.assertFalse(inspect_uncertain_reminder(engine,identity)['observed_snapshot_matches_before'])
            self.assertFalse(store.operation_settled(identity));self.assertTrue(observed['original_uncertainty_retained'])
