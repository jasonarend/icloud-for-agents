"""Source-bound EventKit creation contracts and explicit date/time semantics."""
import json
import os
import unittest
from unittest.mock import patch, PropertyMock
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_reminders import RemindersCreateAdapter,validate_date


class Reader:
    def __init__(self):
        self.membership={'list_id':'list','source_id':'selected','count':2,'sha256':'a'*64}
        self.sections=[]
        self.features={'protocol_version':1,'source_id':'selected','list_id':'list','grocery_categorization':False,'grocery_shopping_session':False,'is_group':False,'shared':False,'shared_to_me':False,'can_be_shared':True,'sharing_metadata_complete':False,'list_type_verified':False,'data_mutated':False,'backend':'reminderkit-list-features-v2'}
        self.list={'id':'list','title':'Synthetic','source_id':'selected','writable':True}
        self.item={'id':'created','title':'Task','notes':'','priority':0,'source_id':'selected',
                   'list_id':'list','completed':False,'due':None,'start':None}
    def read(self,action,**fields):
        if action=='list_membership':return dict(self.membership)
        if action=='list_features':return dict(self.features)
        if action=='list_sections':return {'source_id':'selected','list_id':'list','sections':[dict(s) for s in self.sections]}
        if action=='lists':return {'lists':[dict(self.list)],'done':True}
        if action=='get':return {'item':dict(self.item)}
        raise AssertionError(action)


class NativeRemindersTests(unittest.TestCase):
    def setUp(self):
        env={'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCESS':'write-enabled',
             'ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'access_mode':'write-enabled','source_ids':['selected']}})}
        context=patch.dict(os.environ,env,clear=True);context.start();self.addCleanup(context.stop)
        self.adapter=RemindersCreateAdapter();self.reader=Reader();self.adapter.reader=self.reader
    def prepare(self,**extra):
        change={'operation':'create_reminder','source_id':'selected','list_id':'list','fields':{'title':'Task'}}
        change.update(extra);return self.adapter.prepare(change)
    def test_valid_date_only_and_explicit_timezone_preserved(self):
        day={'year':2026,'month':10,'day':26}
        self.assertEqual(validate_date(day),day)
        timed=dict(day,hour=9,minute=30,timezone='America/Chicago')
        self.assertEqual(validate_date(timed),timed)
    def test_section_creation_requires_opt_in_and_preserves_inventory_and_membership(self):
        change={'operation':'create_section','source_id':'selected','list_id':'list','fields':{'title':'Synthetic section'}}
        with self.assertRaises(SafetyError):self.adapter.prepare(change)
        self.native_reader();self.reader.features['grocery_categorization']=True
        with self.assertRaises(SafetyError):self.adapter.prepare(change)
        self.reader.features['grocery_categorization']=False;item=self.adapter.prepare(change);outcome={'id':item['section_id'],'source_id':'selected','list_id':'list'}
        self.assertIn('do not establish',item['effects']['list_type']);self.assertEqual(item['effects']['observed_sharing'],{'shared':False,'shared_to_me':False,'sharing_metadata_complete':False})
        self.reader.sections.append({'id':item['section_id'],'title':'Synthetic section'})
        self.assertTrue(self.adapter.verify(item,outcome))
        self.reader.membership['count']=3;self.assertFalse(self.adapter.verify(item,outcome));self.reader.membership['count']=2
        self.reader.sections.append({'id':'extra','title':'Other'});self.assertFalse(self.adapter.verify(item,outcome))
    def test_section_rename_binds_exact_inventory_and_readback(self):
        section='AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA';change={'operation':'rename_section','source_id':'selected','list_id':'list','section_id':section,'fields':{'title':'After'}}
        with self.assertRaises(SafetyError):self.adapter.prepare(change)
        self.native_reader();self.reader.sections=[{'id':section,'title':'Before'}]
        item=self.adapter.prepare({**change,'section_id':section.lower()});self.assertEqual(item['section_id'],section)
        self.reader.sections[0]['title']='After'
        self.assertTrue(self.adapter.verify(item,{'id':section,'source_id':'selected','list_id':'list'}))
        self.reader.membership['count']+=1;self.assertFalse(self.adapter.verify(item,{'id':section,'source_id':'selected','list_id':'list'}));self.reader.membership['count']-=1
        self.reader.sections[0]['title']='Before';self.assertFalse(self.adapter.verify(item,{'id':section,'source_id':'selected','list_id':'list'}))
        with self.assertRaises(SafetyError):self.adapter.prepare({**change,'section_id':'22222222-2222-4222-8222-222222222222'})

    def test_section_rename_reconciliation_never_replays_and_checks_target(self):
        import tempfile
        from pathlib import Path
        from icloud_for_agents.approvals import PlanStore
        from icloud_for_agents.native_changes import NativeChanges
        self.native_reader();section='AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA';self.reader.sections=[{'id':section,'title':'Before'}];calls=[]
        def apply(item):
            calls.append(1);self.reader.sections[0]['title']=item['fields']['title']
            return {'id':section,'source_id':'selected','list_id':'list'}
        verify=self.adapter.verify;first=[True]
        def verify_once(item,outcome):
            if first[0]:first[0]=False;return False
            return verify(item,outcome)
        self.adapter.apply=apply;self.adapter.verify=verify_once
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_APPROVAL_POLICY':'trusted-client'}),patch.object(RemindersCreateAdapter,'backend_id',new_callable=PropertyMock,return_value='synthetic-section'):
            engine=NativeChanges('reminders','synthetic@example.com',self.adapter,PlanStore(Path(directory)/'runtime'))
            plan=engine.prepare_once([{'operation':'rename_section','source_id':'selected','list_id':'list','section_id':section,'fields':{'title':'After'}}])
            self.assertEqual(engine.execute(plan['plan_id'])['status'],'failed_or_uncertain')
            self.reader.sections[0]['title']='Wrong'
            with self.assertRaises(SafetyError):engine.reconcile_known_changes(plan['plan_id'])
            self.reader.sections[0]['title']='After'
            self.assertEqual(engine.reconcile_known_changes(plan['plan_id'])['status'],'applied_after_readback_reconciliation')
            self.assertEqual(len(calls),1)

    def test_bad_dates_boolean_components_and_dst_ambiguity_refused(self):
        for value in ({'year':True,'month':1,'day':1},{'year':2026,'month':2,'day':30},
                      {'year':2026,'month':1,'day':1,'hour':12},
                      {'year':2026,'month':1,'day':1,'timezone':'UTC'},
                      {'year':2026,'month':3,'day':8,'hour':2,'minute':30,'timezone':'America/Chicago'},
                      {'year':2026,'month':11,'day':1,'hour':1,'minute':30,'timezone':'America/Chicago'}):
            with self.subTest(value=value),self.assertRaises(SafetyError):validate_date(value)
    def test_exact_list_snapshot_and_explicit_unknown_sharing_effect(self):
        item=self.prepare();self.assertEqual(item['before'],self.reader.list)
        self.assertIn('does not expose',item['effects']['sharing'])
        self.assertEqual(item['fields']['priority'],0)
    def test_unknown_source_read_only_list_and_duplicate_list_refused(self):
        with self.assertRaises(SafetyError):self.prepare(source_id='other')
        self.reader.list['writable']=False
        with self.assertRaises(SafetyError):self.prepare()
        with self.assertRaises(SafetyError):self.adapter.prepare({'operation':'create_list','source_id':'selected','fields':{'title':'Synthetic'}})
    def test_fields_and_priority_cannot_implicitly_request_other_features(self):
        for fields in ({'title':'Task','priority':True},{'title':'Task','priority':3},
                       {'title':'Task','tags':['native?']},{'title':'Task','notes':'x'*20001}):
            with self.subTest(fields=fields),self.assertRaises(SafetyError):self.prepare(fields=fields)
    def test_final_readback_requires_all_requested_fields(self):
        item=self.prepare();outcome={'id':'created','source_id':'selected'}
        self.assertTrue(self.adapter.verify(item,outcome))
        for key,value in [('title','Wrong'),('list_id','other'),('completed',True),('priority',9),('due',{'year':2026})]:
            prior=self.reader.item[key];self.reader.item[key]=value
            self.assertFalse(self.adapter.verify(item,outcome));self.reader.item[key]=prior
    def test_start_after_due_and_mixed_precision_refused(self):
        day={'year':2026,'month':10,'day':26}
        with self.assertRaises(SafetyError):self.prepare(fields={'title':'Task','due':day,'start':dict(day,day=27)})
        with self.assertRaises(SafetyError):self.prepare(fields={'title':'Task','due':day,'start':dict(day,hour=9,minute=0,timezone='UTC')})

    def existing(self,operation='update_reminder',fields=None):
        return self.adapter.prepare({'operation':operation,'source_id':'selected','id':'created',
                                     'fields':fields or {'notes':'Edited body'}})
    def native_reader(self):
        import copy
        os.environ['ICLOUD_NATIVE_CONFIG']=json.dumps({'reminders':{'access_mode':'write-enabled','source_ids':['selected'],'advanced_backend':'reminderkit','helper_path':'/synthetic/helper'}})
        metadata={'flagged':False,'flag_state':0,'tags':[],'tags_complete':True,'coverage':['flagged','tags','hierarchy','children','section','early'],'early_alerts':[],'early_payload_fingerprint':None,'section':None,'section_status':'provider_not_reported','native_metadata_complete':False,'is_subtask':False,'parent_id':None,'has_subtasks':False,'hierarchy_complete':False,'children_status':'provider_reported','child_ids':[],'children_complete':False,'children_fetch_status':'provider_reported'}
        read=self.reader.read
        def enriched(action,**fields):
            result=read(action,**fields)
            if action=='get' and fields.get('include_native_urgent'):
                result['item']['native_metadata']={**{k:result['item'][k] for k in ('id','source_id','list_id')},'coverage':['urgent'],'urgent':False}
            if action=='get' and fields.get('include_native_metadata'):
                result['item']['native_metadata']={**copy.deepcopy(metadata),**{k:result['item'][k] for k in ('id','source_id','list_id')}}
            return result
        self.reader.read=enriched
        return metadata
    def test_selected_native_backend_binds_and_verifies_preserved_flags_and_tags(self):
        metadata=self.native_reader();item=self.existing();self.assertIs(item['before']['native_metadata']['flagged'],False)
        from icloud_for_agents.native_protocol import mutation_request
        self.assertEqual(mutation_request('reminders',item)['change']['before']['native_metadata'],item['before']['native_metadata'])
        self.reader.item['notes']='Edited body';outcome={'id':'created','source_id':'selected'}
        self.assertTrue(self.adapter.verify(item,outcome))
        metadata['flagged']=True;metadata['flag_state']=1
        self.assertFalse(self.adapter.verify(item,outcome));metadata.update(flagged=False,flag_state=0)
        metadata['tags']=[{'id':'tag','name':'Project','native_type':0}]
        self.assertFalse(self.adapter.verify(item,outcome));metadata['tags']=[]
        metadata.update(section={'id':'11111111-1111-4111-8111-111111111111','title':'Changed'},section_status='provider_reported')
        self.assertFalse(self.adapter.verify(item,outcome));metadata.update(section=None,section_status='provider_not_reported')
        metadata['has_subtasks']=True;metadata['child_ids']=['unexpected-child'];self.assertFalse(self.adapter.verify(item,outcome))

    def test_add_early_requires_timed_due_empty_native_and_exact_readback(self):
        with self.assertRaises(SafetyError):self.existing('add_early',{'unit':'minutes','count':5})
        metadata=self.native_reader()
        with self.assertRaises(SafetyError):self.existing('add_early',{'unit':'minutes','count':5})
        self.reader.item['due']={'year':2027,'month':1,'day':1,'hour':10,'minute':0,'timezone':'UTC'}
        for fields in ({'unit':'seconds','count':5},{'unit':[],'count':5},{'unit':'minutes','count':True},{'unit':'minutes','count':0}):
            with self.subTest(fields=fields),self.assertRaises(SafetyError):self.existing('add_early',fields)
        item=self.existing('add_early',{'unit':'minutes','count':5})
        metadata.update(early_alerts=[{'native_unit':0,'native_count':-5}],early_payload_fingerprint='a'*64)
        self.assertTrue(self.adapter.verify(item,{'id':'created','source_id':'selected'}))
        metadata['early_alerts'][0]['native_count']=-6
        self.assertFalse(self.adapter.verify(item,{'id':'created','source_id':'selected'}))
        with self.assertRaises(SafetyError):self.existing('add_early',{'unit':'minutes','count':5})
        metadata.update(early_alerts=[],early_payload_fingerprint=None)
        month=self.existing('add_early',{'unit':'months','count':2});metadata.update(early_alerts=[{'native_unit':4,'native_count':-2}],early_payload_fingerprint='b'*64)
        self.assertTrue(self.adapter.verify(month,{'id':'created','source_id':'selected'}))

    def test_clear_early_requires_one_exact_reviewed_alert_and_preserves_other_fields(self):
        with self.assertRaises(SafetyError):self.existing('clear_early',{'clear':True})
        metadata=self.native_reader()
        for fields in ({'clear':False},{'clear':1},{'clear':True,'extra':True},{}):
            with self.subTest(fields=fields),self.assertRaises(SafetyError):self.existing('clear_early',fields)
        with self.assertRaises(SafetyError):self.existing('clear_early',{'clear':True})
        metadata.update(early_alerts=[{'native_unit':0,'native_count':-5}],early_payload_fingerprint='a'*64)
        item=self.existing('clear_early',{'clear':True})
        from icloud_for_agents.native_protocol import mutation_request
        self.assertEqual(mutation_request('reminders',item)['change']['before']['native_metadata']['early_payload_fingerprint'],'a'*64)
        outcome={'id':'created','source_id':'selected'}
        self.assertFalse(self.adapter.verify(item,outcome))
        metadata.update(early_alerts=[],early_payload_fingerprint=None)
        self.assertTrue(self.adapter.verify(item,outcome))
        metadata['early_payload_fingerprint']='b'*64
        self.assertTrue(self.adapter.verify(item,outcome)) # A valid known-empty provider envelope is not a new alarm.
        self.reader.item['notes']='unrelated change';self.assertFalse(self.adapter.verify(item,outcome));self.reader.item['notes']=''
        metadata.update(early_alerts=[{'native_unit':0,'native_count':-5}]*2,early_payload_fingerprint='c'*64)
        with self.assertRaises(SafetyError):self.existing('clear_early',{'clear':True})

    def test_due_change_reviews_early_notification_and_refuses_unsafe_clear(self):
        metadata=self.native_reader();metadata.update(early_alerts=[{'native_unit':0,'native_count':-5}],early_payload_fingerprint='a'*64)
        due={'year':2027,'month':1,'day':1,'hour':10,'minute':0,'timezone':'UTC'};self.reader.item['due']=due
        item=self.existing('update_reminder',{'due':dict(due,day=2)})
        self.assertIn('reschedules',item['effects']['early_reminder'])
        for replacement in (None,{'year':2027,'month':1,'day':2}):
            with self.subTest(replacement=replacement),self.assertRaises(SafetyError):self.existing('update_reminder',{'due':replacement})

    def test_early_readback_reconciliation_requires_exact_observed_state(self):
        import tempfile
        from pathlib import Path
        from icloud_for_agents.approvals import PlanStore
        from icloud_for_agents.native_changes import NativeChanges
        metadata=self.native_reader();self.reader.item['due']={'year':2027,'month':1,'day':1,'hour':10,'minute':0,'timezone':'UTC'}
        calls=[]
        def apply(item):
            calls.append(1);metadata.update(early_alerts=[{'native_unit':0,'native_count':-5}],early_payload_fingerprint='a'*64)
            return {'id':'created','source_id':'selected','list_id':'list'}
        verify=self.adapter.verify;first=[True]
        def verify_once(item,outcome):
            if first[0]:first[0]=False;return False
            return verify(item,outcome)
        self.adapter.apply=apply;self.adapter.verify=verify_once
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_APPROVAL_POLICY':'trusted-client'}),patch.object(RemindersCreateAdapter,'backend_id',new_callable=PropertyMock,return_value='synthetic-early'):
            engine=NativeChanges('reminders','synthetic@example.com',self.adapter,PlanStore(Path(directory)/'runtime'))
            plan=engine.prepare_once([{'operation':'add_early','source_id':'selected','id':'created','fields':{'unit':'minutes','count':5}}])
            self.assertEqual(engine.execute(plan['plan_id'])['status'],'failed_or_uncertain')
            metadata['early_alerts'][0]['native_count']=-6
            with self.assertRaises(SafetyError):engine.reconcile_known_changes(plan['plan_id'])
            metadata['early_alerts'][0]['native_count']=-5
            self.assertEqual(engine.reconcile_known_changes(plan['plan_id'])['status'],'applied_after_readback_reconciliation')
            self.assertEqual(len(calls),1)

    def test_urgent_drift_blocks_nonurgent_write_readback_and_snapshot(self):
        self.native_reader();state={'urgent':False};read=self.reader.read
        def enriched(action,**fields):
            result=read(action,**fields)
            if action=='get' and fields.get('include_native_urgent'):result['item']['native_metadata']['urgent']=state['urgent']
            return result
        self.reader.read=enriched;item=self.existing();self.reader.item['notes']='Edited body'
        self.assertTrue(self.adapter.verify(item,{'id':'created','source_id':'selected'}))
        state['urgent']=True;self.assertFalse(self.adapter.verify(item,{'id':'created','source_id':'selected'}))
        self.reader.item['notes']=item['before']['item']['notes'];self.assertNotEqual(self.adapter.snapshot(item),item['before'])

    def test_native_flag_change_requires_opt_in_and_preserves_other_metadata(self):
        change={'operation':'set_flagged','source_id':'selected','id':'created','fields':{'flagged':True}}
        with self.assertRaises(SafetyError):self.adapter.prepare(change)
        metadata=self.native_reader();item=self.adapter.prepare(change)
        self.assertFalse(item['before']['native_metadata']['flagged'])
        from icloud_for_agents.native_protocol import mutation_request
        envelope=mutation_request('reminders',item)['change']
        self.assertEqual(envelope['before']['native_metadata'],item['before']['native_metadata'])
        metadata.update(flagged=True,flag_state=1)
        self.assertTrue(self.adapter.verify(item,{'id':'created','source_id':'selected'}))
        metadata['tags']=[{'id':'tag','name':'Synthetic','native_type':0}]
        self.assertFalse(self.adapter.verify(item,{'id':'created','source_id':'selected'}))
        with self.assertRaises(SafetyError):self.adapter.prepare({'operation':'set_flagged','source_id':'selected','id':'created','fields':{'flagged':1}})

    def test_subtask_creation_binds_parent_and_preassigned_child_identity(self):
        change={'operation':'create_subtask','source_id':'selected','id':'created','fields':{'title':'Synthetic subtask'}}
        with self.assertRaises(SafetyError):self.adapter.prepare(change)
        metadata=self.native_reader();metadata['has_subtasks']=True;metadata['child_ids']=['11111111-1111-4111-8111-111111111111']
        item=self.adapter.prepare(change);self.assertNotEqual(item['child_id'],item['id']);self.assertEqual(item['before']['native_metadata']['child_ids'],metadata['child_ids'])
        from icloud_for_agents.native_protocol import mutation_request
        self.assertEqual(mutation_request('reminders',item)['change']['child_id'],item['child_id'])
        read=self.reader.read
        def enriched(action,**fields):
            if action=='get' and fields.get('id')==item['child_id']:
                return {'item':{'id':item['child_id'],'source_id':'selected','list_id':'list','title':'Synthetic subtask','completed':False,'priority':0,'notes':'','due':None,'start':None,'has_alarms':False,'has_recurrence':False,'alarms':[],'recurrence_rules':[],'url':None,'location':None,'public_metadata_complete':True,'native_metadata':{'is_subtask':True,'parent_id':'created','flagged':False,'urgent':False,'tags_complete':True,'tags':[]}}}
            return read(action,**fields)
        self.reader.read=enriched;metadata['child_ids'].append(item['child_id']);metadata['child_ids'].sort()
        outcome={'id':item['child_id'],'source_id':'selected','parent_id':'created'};self.assertTrue(self.adapter.verify(item,outcome))
        metadata['child_ids'].remove('11111111-1111-4111-8111-111111111111');self.assertFalse(self.adapter.verify(item,outcome))
        metadata['child_ids']=None
        with self.assertRaises(SafetyError):self.adapter.prepare(change)
        metadata['child_ids']=[];self.reader.item['completed']=True
        with self.assertRaises(SafetyError):self.adapter.prepare(change)

    def test_urgent_write_requires_separate_exact_state_and_preserves_metadata(self):
        change={'operation':'set_urgent','source_id':'selected','id':'created','fields':{'urgent':True}}
        with self.assertRaises(SafetyError):self.adapter.prepare(change)
        metadata=self.native_reader();state={'urgent':False};read=self.reader.read
        def enriched(action,**fields):
            result=read(action,**fields)
            if action=='get' and fields.get('include_native_urgent'):
                result['item']['native_metadata']={**{key:result['item'][key] for key in ('id','source_id','list_id')},'coverage':['urgent'],'urgent':state['urgent']}
            return result
        self.reader.read=enriched;item=self.adapter.prepare(change);self.assertIs(item['before']['urgent'],False)
        from icloud_for_agents.native_protocol import mutation_request
        self.assertIs(mutation_request('reminders',item)['change']['before']['urgent'],False)
        state['urgent']=True;self.assertTrue(self.adapter.verify(item,{'id':'created','source_id':'selected'}))
        metadata['flagged']=True;metadata['flag_state']=1;self.assertFalse(self.adapter.verify(item,{'id':'created','source_id':'selected'}))
        metadata.update(flagged=False,flag_state=0)
        with self.assertRaises(SafetyError):self.adapter.prepare(change)
        with self.assertRaises(SafetyError):self.adapter.prepare({**change,'fields':{'urgent':1}})

    def test_exact_native_tag_removal_preserves_remaining_tags_and_flag(self):
        first={'id':'11111111-1111-4111-8111-111111111111','name':'SyntheticOne','native_type':0}
        second={'id':'22222222-2222-4222-8222-222222222222','name':'SyntheticTwo','native_type':0}
        change={'operation':'remove_tag','source_id':'selected','id':'created','fields':{'tag_id':first['id']}}
        with self.assertRaises(SafetyError):self.adapter.prepare(change)
        metadata=self.native_reader();metadata['tags']=[first,second];item=self.adapter.prepare(change)
        from icloud_for_agents.native_protocol import mutation_request
        self.assertEqual(mutation_request('reminders',item)['change']['before']['native_metadata']['tags'],[first,second])
        metadata['tags']=[second];self.assertTrue(self.adapter.verify(item,{'id':'created','source_id':'selected'}))
        metadata['flagged']=True;metadata['flag_state']=1;self.assertFalse(self.adapter.verify(item,{'id':'created','source_id':'selected'}))
        metadata['flagged']=False;metadata['flag_state']=0
        with self.assertRaises(SafetyError):self.adapter.prepare(change)
    def test_exact_native_tag_addition_requires_opt_in_and_preserves_observed_metadata(self):
        change={'operation':'add_tag','source_id':'selected','id':'created','fields':{'tag_name':'SyntheticNew'}}
        with self.assertRaises(SafetyError):self.adapter.prepare(change)
        metadata=self.native_reader();original={'id':'11111111-1111-4111-8111-111111111111','name':'Existing','native_type':0};metadata['tags']=[original]
        item=self.adapter.prepare(change);added={'id':'22222222-2222-4222-8222-222222222222','name':'SyntheticNew','native_type':0}
        metadata['tags']=[original,added]
        self.assertTrue(self.adapter.verify(item,{'id':'created','source_id':'selected','tag_id':added['id']}))
        self.assertFalse(self.adapter.verify(item,{'id':'created','source_id':'selected'}))
        metadata['flagged']=True;self.assertFalse(self.adapter.verify(item,{'id':'created','source_id':'selected','tag_id':added['id']}))
        metadata['flagged']=False
        with self.assertRaises(SafetyError):self.adapter.prepare(change)
        with self.assertRaises(SafetyError):self.adapter.prepare({**change,'fields':{'tag_name':'bad tag'}})
        with self.assertRaises(SafetyError):self.adapter.prepare({**change,'fields':{'tag_name':'\u0301bad'}})
        self.assertEqual(self.adapter.prepare({**change,'fields':{'tag_name':'café_東京'}})['fields']['tag_name'],'café_東京')
    def test_exact_tag_replacement_preserves_other_tag_identity(self):
        old={'id':'11111111-1111-4111-8111-111111111111','name':'Old','native_type':0}
        other={'id':'22222222-2222-4222-8222-222222222222','name':'Other','native_type':0}
        new={'id':'33333333-3333-4333-8333-333333333333','name':'New','native_type':0}
        change={'operation':'replace_tag','source_id':'selected','id':'created','fields':{'tag_id':old['id'],'tag_name':'New'}}
        with self.assertRaises(SafetyError):self.adapter.prepare(change)
        metadata=self.native_reader();metadata['tags']=[old,other];item=self.adapter.prepare(change)
        metadata['tags']=[other,new];self.assertTrue(self.adapter.verify(item,{'id':'created','source_id':'selected','tag_id':new['id']}))
        metadata['tags']=[new];self.assertFalse(self.adapter.verify(item,{'id':'created','source_id':'selected','tag_id':new['id']}))
        metadata['tags']=[old,other]
        with self.assertRaises(SafetyError):self.adapter.prepare({**change,'fields':{'tag_id':old['id'],'tag_name':'other'}})
        with self.assertRaises(SafetyError):self.adapter.prepare({**change,'fields':{'tag_id':'invalid','tag_name':'New'}})

    def test_selected_native_backend_refuses_missing_or_incomplete_native_snapshot(self):
        metadata=self.native_reader();metadata['tags_complete']=False
        with self.assertRaises(SafetyError):self.existing()
        metadata['tags_complete']=True;metadata['coverage']=['flagged']
        with self.assertRaises(SafetyError):self.existing()
        metadata['coverage']=['flagged','tags','hierarchy','children','section','early'];metadata['child_ids']=['contradictory-child']
        with self.assertRaises(SafetyError):self.existing()
        metadata['child_ids']=[];metadata['is_subtask']=True;metadata['parent_id']=None
        with self.assertRaises(SafetyError):self.existing()
    def test_move_readback_changes_only_native_list_binding(self):
        self.native_reader();item=self.existing();item.update(operation='move_reminder',fields={'list_id':'destination'})
        self.reader.item['list_id']='destination'
        self.assertTrue(self.adapter.verify(item,{'id':'created','source_id':'selected'}))

    def test_partial_update_preserves_unrequested_values(self):
        item=self.existing();self.assertEqual(item['fields'],{'notes':'Edited body'})
        self.assertEqual(item['before']['item'],self.reader.item)
        self.reader.item['notes']='Edited body'
        self.assertTrue(self.adapter.verify(item,{'id':'created','source_id':'selected'}))
        self.reader.item['priority']=9
        self.assertFalse(self.adapter.verify(item,{'id':'created','source_id':'selected'}))
    def test_existing_scope_recurrence_and_completion_are_explicit(self):
        for value in (True,False):
            item=self.existing('set_completed',{'completed':value})
            self.assertIn('descendant completion states are not verified',item['effects']['hierarchy'])
            self.reader.item['completed']=value
            self.assertTrue(self.adapter.verify(item,{'id':'created','source_id':'selected'}))
        with self.assertRaises(SafetyError):self.existing('set_completed',{'completed':1})
        self.reader.item['has_recurrence']=True
        with self.assertRaises(SafetyError):self.existing('set_completed',{'completed':True})
        self.reader.item['has_recurrence']=False;self.reader.item['source_id']='other'
        with self.assertRaises(SafetyError):self.existing()
    def test_moves_and_previously_approved_move_snapshots_are_refused(self):
        for advanced in (False,True):
            if advanced:self.native_reader()
            with self.subTest(advanced=advanced),self.assertRaisesRegex(SafetyError,'complete hierarchy'):
                self.existing('move_reminder',{'list_id':'list'})
            with self.subTest(advanced=advanced),self.assertRaisesRegex(SafetyError,'complete hierarchy'):
                self.adapter.snapshot({'operation':'move_reminder','source_id':'selected','id':'created','fields':{'list_id':'list'}})
    def test_partial_date_edit_is_checked_against_unchanged_other_date(self):
        self.reader.item['start']={'year':2026,'month':10,'day':27,'date_only':True,'timezone':None}
        with self.assertRaises(SafetyError):self.existing(fields={'due':{'year':2026,'month':10,'day':26}})
        item=self.existing(fields={'due':None})
        self.assertEqual(item['fields'],{'due':None})

    def test_unrequested_public_alarms_and_recurrence_metadata_must_survive_update(self):
        self.reader.item['alarms']=[{'relative_offset_seconds':-900}]
        self.reader.item['recurrence_rules']=[]
        item=self.existing();self.reader.item['notes']='Edited body'
        self.assertTrue(self.adapter.verify(item,{'id':'created','source_id':'selected'}))
        self.reader.item['alarms']=[]
        self.assertFalse(self.adapter.verify(item,{'id':'created','source_id':'selected'}))

    def rename(self,**changes):
        value={'operation':'rename_list','source_id':'selected','list_id':'list','fields':{'title':'Renamed synthetic'}};value.update(changes);return self.adapter.prepare(value)
    def test_ongoing_list_rename_permission_survives_its_own_changes_and_refuses_lost_writability(self):
        import tempfile
        from pathlib import Path
        from icloud_for_agents.approvals import PlanStore
        from icloud_for_agents.native_changes import NativeChanges
        from icloud_for_agents.native_grants import NativeGrants
        from icloud_for_agents.native_access import require_write
        adapter=type('SyntheticListAdapter',(RemindersCreateAdapter,),{'backend_id':'synthetic-list-rename'})();adapter.reader=self.reader
        calls=[]
        def apply(item):
            require_write('reminders',item);calls.append(item['id']);self.reader.list['title']=item['fields']['title']
            return {'id':item['id'],'source_id':'selected'}
        adapter.apply=apply
        config={'reminders':{'access_mode':'read-only','source_ids':['selected']}}
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_NATIVE_CONFIG':json.dumps(config)}):
            engine=NativeChanges('reminders','synthetic@example.com',adapter,PlanStore(Path(directory)/'runtime'));manager=NativeGrants(engine)
            scope={'scenario':'Synthetic list naming','rules':[{'operation':'rename_list','source_id':'selected','container_id':'list','fields':['title'],'destinations':[]}],'max_batch':1,'max_24h':3,'expires':None}
            grant=manager.prepare(scope);manager.activate(grant['plan_id'])
            for title in ('Renamed synthetic','Synthetic'):
                plan=engine.prepare_with_grant(grant['plan_id'],[{'operation':'rename_list','source_id':'selected','list_id':'list','fields':{'title':title}}])
                self.assertEqual(engine.execute(plan['plan_id'])['status'],'applied');self.assertEqual(manager.status(grant['plan_id'])['status'],'active')
            pending=engine.prepare_with_grant(grant['plan_id'],[{'operation':'rename_list','source_id':'selected','list_id':'list','fields':{'title':'Pending'}}])
            self.reader.list['writable']=False
            with self.assertRaises(SafetyError):engine.execute(pending['plan_id'])
            self.assertFalse(engine.store.path(pending['plan_id'],'.started').exists());self.assertEqual(calls,['list','list'])
            manager.revoke(grant['plan_id']);self.assertEqual(manager.status(grant['plan_id'])['status'],'revoked')
            self.assertEqual(os.environ['ICLOUD_MAIL_ACCESS'],'read-only')

    def test_list_rename_binds_membership_and_preserves_list_identity(self):
        item=self.rename();self.assertEqual(item['before']['membership'],self.reader.membership);self.assertEqual(item['id'],'list')
        self.reader.list['title']='Renamed synthetic'
        self.assertTrue(self.adapter.verify(item,{'id':'list','source_id':'selected'}))
        self.reader.membership['count']=1
        self.assertFalse(self.adapter.verify(item,{'id':'list','source_id':'selected'}))
    def test_list_rename_rejects_wrong_source_list_noop_and_unknown_effects(self):
        for change in [{'source_id':'other'},{'list_id':'other'},{'fields':{'title':'Synthetic'}},{'fields':{'title':'T','delete':True}},{'fields':{'title':'T\nX'}}]:
            with self.subTest(change=change),self.assertRaises(SafetyError):self.rename(**change)
        self.reader.list['writable']=False
        with self.assertRaises(SafetyError):self.rename()
    def test_rename_refuses_duplicate_names_and_incomplete_membership_proofs(self):
        original=self.reader.read
        def lists(action,**fields):
            if action=='lists':return {'lists':[self.reader.list,dict(self.reader.list,id='another',title='Renamed synthetic')],'done':True}
            return original(action,**fields)
        self.reader.read=lists
        with self.assertRaises(SafetyError):self.rename()
        self.reader.read=original;self.reader.membership['sha256']='invalid'
        with self.assertRaises(SafetyError):self.rename()

    def test_passive_links_are_exact_and_clearable(self):
        for link in ('https://example.com/spec?revision=2#decision','applenotes:note/SYNTHETIC','x-apple-reminderkit://SYNTHETIC'):
            item=self.prepare(fields={'title':'Task','url':link})
            self.reader.item.update(url=link,location=None)
            self.assertTrue(self.adapter.verify(item,{'id':'created','source_id':'selected'}))
        item=self.existing(fields={'url':None});self.reader.item.update(url=None,location=None)
        self.assertTrue(self.adapter.verify(item,{'id':'created','source_id':'selected'}))
    def test_links_cannot_include_credentials_active_schemes_or_silent_repairs(self):
        for url in ('https://user:password@example.com/','file:///tmp/example','javascript:alert(1)',
                    'data:text/plain,example','https://example.com/contains space','https://example.com/é',
                    'https://example.com/%broken','https://example.com:0/','https://example.com:65536/',
                    'https://example.com/\\path','https://example.com/<html>','applenotes:','HTTPS://example.com/'):
            with self.subTest(url=url),self.assertRaises(SafetyError):self.prepare(fields={'title':'Task','url':url})
        for location in ('', 'x'*1001,'line\nbreak',True):
            with self.subTest(location=location),self.assertRaises(SafetyError):self.prepare(fields={'title':'Task','location':location})
    def test_unrequested_link_and_location_survive_other_updates(self):
        self.reader.item.update(url='https://example.com/context',location='Room one')
        item=self.existing(fields={'notes':'Edited'});self.reader.item['notes']='Edited'
        self.assertTrue(self.adapter.verify(item,{'id':'created','source_id':'selected'}))
        self.reader.item['location']='Room two'
        self.assertFalse(self.adapter.verify(item,{'id':'created','source_id':'selected'}))
    def test_link_creation_readback_does_not_ignore_loss(self):
        item=self.prepare(fields={'title':'Task','url':'https://example.com/'})
        self.assertFalse(self.adapter.verify(item,{'id':'created','source_id':'selected'}))

    def time_alarm(self,**values):
        result={'relative_offset_seconds':0,'absolute_date':None,'type':0,'proximity':0,'structured_location':None,'email_address':None,'sound_name':None};result.update(values);return result
    def test_absolute_and_relative_time_alarm_sets_have_exact_readback(self):
        due={'year':2027,'month':1,'day':4,'hour':12,'minute':0,'timezone':'UTC'}
        fields={'title':'Task','due':due,'alarms':[{'relative_offset_seconds':-900},{'absolute_date':1800000000}]}
        item=self.prepare(fields=fields);self.reader.item.update(due=due,has_alarms=True,alarms=[self.time_alarm(absolute_date=1800000000),self.time_alarm(relative_offset_seconds=-900)])
        self.assertTrue(self.adapter.verify(item,{'id':'created','source_id':'selected'}))
        self.reader.item['alarms'].pop();self.assertFalse(self.adapter.verify(item,{'id':'created','source_id':'selected'}))
    def test_alarm_input_refuses_implicit_timezone_precision_duplicates_and_other_triggers(self):
        cases=[None,[{'absolute_date':True}],[{'absolute_date':float('nan')}],[{'absolute_date':1800000000.5}],
               [{'absolute_date':0}],[{'relative_offset_seconds':-900}],[{'structured_location':{'title':'Place'}}],
               [{'absolute_date':1800000000}]*2,[{'absolute_date':1800000000+i} for i in range(11)]]
        for alarms in cases:
            with self.subTest(alarms=alarms),self.assertRaises(SafetyError):self.prepare(fields={'title':'Task','alarms':alarms})
    def test_empty_alarm_array_explicitly_clears_only_supported_time_alarms(self):
        self.reader.item.update(alarms=[self.time_alarm(absolute_date=1800000000)],has_alarms=True,public_metadata_complete=True)
        item=self.existing(fields={'alarms':[]});self.reader.item.update(alarms=[],has_alarms=False)
        self.assertTrue(self.adapter.verify(item,{'id':'created','source_id':'selected'}))
    def test_location_email_custom_sound_or_incomplete_alarm_sets_cannot_be_replaced(self):
        for extra in ({'proximity':1},{'type':3,'email_address':'synthetic@example.com'},{'sound_name':'Synthetic sound'},{'structured_location':{'title':'Place'}}):
            self.reader.item.update(alarms=[self.time_alarm(**extra)],has_alarms=True,public_metadata_complete=True)
            with self.subTest(extra=extra),self.assertRaises(SafetyError):self.existing(fields={'alarms':[]})
        self.reader.item.update(alarms=[self.time_alarm()],has_alarms=True,public_metadata_complete=False)
        with self.assertRaises(SafetyError):self.existing(fields={'alarms':[]})

    def test_location_alarm_creation_requires_exact_review_and_readback(self):
        place={'title':'Synthetic place','latitude':12.25,'longitude':-34.5,'radius_meters':250,'proximity':'enter'}
        item=self.prepare(fields={'title':'Task','alarms':[{'location':place}]})
        self.assertEqual(item['effects']['time_alarms']['location_triggers'][0]['proximity'],'enter')
        location={k:v for k,v in place.items() if k!='proximity'}
        observed=self.time_alarm(proximity=1,structured_location=location)
        self.reader.item.update(has_alarms=True,alarms=[observed])
        self.assertTrue(self.adapter.verify(item,{'id':'created','source_id':'selected'}))
        observed['structured_location']={**location,'radius_meters':251}
        self.assertFalse(self.adapter.verify(item,{'id':'created','source_id':'selected'}))
        self.reader.item.update(has_alarms=True,alarms=[observed],public_metadata_complete=True)
        clear=self.existing(fields={'alarms':[]});self.reader.item.update(has_alarms=False,alarms=[])
        self.assertTrue(self.adapter.verify(clear,{'id':'created','source_id':'selected'}))
        for invalid in ({**place,'latitude':True},{**place,'longitude':181},{**place,'radius_meters':-1},{**place,'proximity':'other'}):
            with self.subTest(invalid=invalid),self.assertRaises(SafetyError):self.prepare(fields={'title':'Task','alarms':[{'location':invalid}]})

    def test_unknown_or_ambiguous_location_alarm_metadata_refuses_replacement(self):
        from icloud_for_agents.native_reminders import public_supported_alarms
        place={'title':'Synthetic','latitude':12.25,'longitude':-34.5,'radius_meters':250}
        alarm=self.time_alarm(proximity=1,structured_location=place)
        invalid=[{**alarm,'type':True},{**alarm,'proximity':True},{**alarm,'absolute_date':1800000000},
                 {**alarm,'relative_offset_seconds':2},{**alarm,'sound_name':'custom'},
                 {**alarm,'structured_location':{**place,'latitude':None}},{**alarm,'structured_location':{**place,'extra':'unknown'}}]
        for value in invalid:
            with self.subTest(value=value),self.assertRaises(SafetyError):public_supported_alarms([value])
        for key in ('structured_location','proximity','email_address','sound_name','absolute_date','relative_offset_seconds'):
            with self.subTest(missing=key),self.assertRaises(SafetyError):public_supported_alarms([{k:v for k,v in alarm.items() if k!=key}])
        with self.assertRaises(SafetyError):public_supported_alarms([alarm,alarm])
        with self.assertRaises(SafetyError):self.prepare(fields={'title':'Task','alarms':[{'location':{**place,'latitude':10**1000,'proximity':'enter'}}]})

    def test_fixed_zero_timezone_aliases_preserve_exact_wall_time_without_other_zone_substitution(self):
        from icloud_for_agents.native_reminders import date_parts_equal
        due={'year':2027,'month':1,'day':4,'hour':12,'minute':0,'timezone':'UTC'}
        self.assertTrue(date_parts_equal(dict(due,timezone='GMT'),due))
        self.assertFalse(date_parts_equal(dict(due,timezone='Europe/London'),due))
        self.assertFalse(date_parts_equal(dict(due,hour=11,timezone='GMT'),due))
