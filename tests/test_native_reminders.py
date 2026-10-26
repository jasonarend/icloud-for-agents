"""Source-bound EventKit creation contracts and explicit date/time semantics."""
import json
import os
import unittest
from unittest.mock import patch
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_reminders import RemindersCreateAdapter,validate_date


class Reader:
    def __init__(self):
        self.list={'id':'list','title':'Synthetic','source_id':'selected','writable':True}
        self.item={'id':'created','title':'Task','notes':'','priority':0,'source_id':'selected',
                   'list_id':'list','completed':False,'due':None,'start':None}
    def read(self,action,**fields):
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
            self.reader.item['completed']=value
            self.assertTrue(self.adapter.verify(item,{'id':'created','source_id':'selected'}))
        with self.assertRaises(SafetyError):self.existing('set_completed',{'completed':1})
        self.reader.item['has_recurrence']=True
        with self.assertRaises(SafetyError):self.existing('set_completed',{'completed':True})
        self.reader.item['has_recurrence']=False;self.reader.item['source_id']='other'
        with self.assertRaises(SafetyError):self.existing()
    def test_move_requires_writable_same_source_destination(self):
        with self.assertRaises(SafetyError):self.existing('move_reminder',{'list_id':'missing'})
        item=self.existing('move_reminder',{'list_id':'list'})
        self.assertEqual(item['before']['destination'],self.reader.list)
        self.assertTrue(self.adapter.verify(item,{'id':'created','source_id':'selected'}))
    def test_partial_date_edit_is_checked_against_unchanged_other_date(self):
        self.reader.item['start']={'year':2026,'month':10,'day':27,'date_only':True,'timezone':None}
        with self.assertRaises(SafetyError):self.existing(fields={'due':{'year':2026,'month':10,'day':26}})
        item=self.existing(fields={'due':None})
        self.assertEqual(item['fields'],{'due':None})
