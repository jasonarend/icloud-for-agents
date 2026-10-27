"""Repeat configuration, exact native identity, field scope and refusal contracts."""
import copy
import unittest
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_recurrence import validate_recurrence,observed_rule,recurrence_equal,validate_seed
import test_native_reminders as fixtures


def native(rule):
    result=copy.deepcopy(rule);end=result['end']
    if end is not None:result['end']={'count':end.get('count',0),'date':end.get('date')}
    result.update(fields_complete=True,calendar_identifier='gregorian',first_weekday=2 if rule['frequency']=='weekly' and rule['interval']>1 else 0)
    return result

class RuleTests(unittest.TestCase):
    def test_rules_and_explicit_end_conditions_roundtrip(self):
        for frequency in ('daily','weekly','monthly','yearly'):
            for end in (None,{'count':3},{'date':1800000000}):
                rule=validate_recurrence({'frequency':frequency,'interval':1,'end':end})
                self.assertEqual(observed_rule(native(rule)),rule);self.assertTrue(recurrence_equal([native(rule)],rule))
        self.assertTrue(recurrence_equal([],None));self.assertFalse(recurrence_equal([{}],None))
    def test_ignored_filters_bad_types_duplicates_and_ambiguous_end_refuse(self):
        base={'frequency':'daily','interval':1,'end':None}
        for patch in ({'frequency':[]},{'interval':True},{'interval':0},{'end':{'count':2,'date':1800000000}},
                      {'end':{'count':True}},{'end':{'date':float('nan')}},{'months_of_year':[1]},
                      {'days_of_week':[{'weekday':2,'week_number':0}]},{'set_positions':[-1]},
                      {'days_of_month':[1,1]},{'timezone':'UTC'}):
            with self.subTest(patch=patch),self.assertRaises(SafetyError):validate_recurrence(dict(base,**patch))
    def test_advanced_supported_patterns_are_canonical_and_custom_week_start_refuses(self):
        rule=validate_recurrence({'frequency':'monthly','interval':2,'end':{'count':12},'days_of_week':[{'weekday':6,'week_number':-1}],'set_positions':[-1]})
        self.assertEqual(observed_rule(native(rule)),rule)
        bad=native(rule);bad['first_weekday']=1
        with self.assertRaises(SafetyError):observed_rule(bad)
        bad=native(rule);bad['fields_complete']=False
        self.assertFalse(recurrence_equal([bad],rule))
    def test_absolute_end_requires_a_zoned_seed_and_cannot_precede_it(self):
        rule=validate_recurrence({'frequency':'daily','interval':1,'end':{'date':1800000000}})
        with self.assertRaises(SafetyError):validate_seed(rule,{'year':2027,'month':1,'day':1})
        with self.assertRaises(SafetyError):validate_seed(rule,{'year':2028,'month':1,'day':1,'hour':12,'minute':0,'timezone':'UTC'})

class RecurrenceAdapterTests(unittest.TestCase):
    def setUp(self):
        fixtures.NativeRemindersTests.setUp(self);self.reader.item.update(public_metadata_complete=True,recurrence_rules=[],has_recurrence=False,created=12345,due={'year':2027,'month':1,'day':4,'date_only':True})
    def existing(self,operation,fields):return fixtures.NativeRemindersTests.existing(self,operation,fields)
    def set_rule(self,rule=None,**extra):
        change={'operation':'set_recurrence','source_id':'selected','id':'created','fields':{'recurrence_scope':'exact_native_rule','recurrence':rule}};change.update(extra)
        return self.adapter.prepare(change)
    def test_set_change_and_clear_verify_rule_and_preserve_every_other_public_field(self):
        rule={'frequency':'weekly','interval':2,'end':{'count':3},'days_of_week':[{'weekday':2,'week_number':0}]}
        item=self.set_rule(rule);prepared=item['fields']['recurrence'];self.reader.item.update(recurrence_rules=[native(prepared)],has_recurrence=True)
        self.assertTrue(self.adapter.verify(item,{'id':'created','source_id':'selected'}))
        self.reader.item['notes']='External'
        self.assertFalse(self.adapter.verify(item,{'id':'created','source_id':'selected'}));self.reader.item['notes']=''
        removal=self.set_rule();self.reader.item.update(recurrence_rules=[],has_recurrence=False)
        self.assertTrue(self.adapter.verify(removal,{'id':'created','source_id':'selected'}))
    def test_ordinary_update_completion_and_move_remain_gated_for_repeating_tasks(self):
        rule=validate_recurrence({'frequency':'daily','interval':1,'end':None});self.reader.item.update(recurrence_rules=[native(rule)],has_recurrence=True)
        for op,fields in [('update_reminder',{'title':'New'}),('set_completed',{'completed':True}),('move_reminder',{'list_id':'list'})]:
            with self.subTest(op=op),self.assertRaises(SafetyError):self.existing(op,fields)
    def test_explicit_scope_uncompleted_state_and_complete_metadata_are_required(self):
        for fields in ({'recurrence':None},{'recurrence':None,'recurrence_scope':'series'},{'recurrence':None,'recurrence_scope':'occurrence'}):
            with self.assertRaises(SafetyError):self.set_rule(fields=fields)
        for key,value in [('completed',True),('public_metadata_complete',False),('source_id','other')]:
            before=self.reader.item[key];self.reader.item[key]=value
            with self.subTest(key=key),self.assertRaises(SafetyError):self.set_rule()
            self.reader.item[key]=before

    def test_refused_provider_rule_can_use_exact_reviewed_before_state_correction(self):
        import os,tempfile
        from pathlib import Path
        from unittest.mock import patch
        from icloud_for_agents.approvals import PlanStore
        from icloud_for_agents.native_changes import NativeChanges
        adapter=type('SyntheticRecurrenceAdapter',(type(self.adapter),),{'backend_id':'synthetic-rule-fixture'})();adapter.reader=self.reader
        failed=[True];calls=[]
        def apply(item):
            from icloud_for_agents.native_access import require_write
            require_write('reminders',item);calls.append(item['id']);rule=None if failed[0] else item['fields']['recurrence']
            self.reader.item.update(recurrence_rules=[native(rule)] if rule else [],has_recurrence=bool(rule))
            return {'id':item['id'],'source_id':'selected','list_id':'list'}
        adapter.apply=apply
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com'}):
            engine=NativeChanges('reminders','synthetic@example.com',adapter,PlanStore(Path(directory)/'runtime'))
            proposed=engine.prepare_once([{'operation':'set_recurrence','source_id':'selected','id':'created','fields':{'recurrence_scope':'exact_native_rule','recurrence':{'frequency':'daily','interval':1,'end':{'count':3}}}}])
            self.assertEqual(engine.execute(proposed['plan_id'])['status'],'failed_or_uncertain');failed[0]=False
            original=engine.store.path(proposed['plan_id'],'.result').read_bytes()
            repair=engine.prepare_update_correction(proposed['plan_id'],'before');self.assertEqual(engine.execute(repair['plan_id'])['status'],'applied')
            self.assertEqual(engine.reconcile_correction(repair['plan_id'])['status'],'restored_before_after_explicit_correction')
            self.assertEqual(engine.store.path(proposed['plan_id'],'.result').read_bytes(),original);self.assertEqual(len(calls),2)
            with self.assertRaises(SafetyError):engine.execute(proposed['plan_id'])
