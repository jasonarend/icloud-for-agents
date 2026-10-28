"""Strict iCalendar inputs produce reviewed, limited Reminder copies."""
import hashlib,json,os,tempfile,unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.native_reminder_ical import export_ical
from icloud_for_agents.native_reminder_ical_import import observation,prepare_ical_copy
from test_native_reminder_import import Adapter

class ICalImportTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.store=PlanStore(Path(tmp.name)/'runtime')
        env={'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com',
             'ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'access_mode':'read-only','source_ids':['selected']}})}
        manager=patch.dict(os.environ,env,clear=True);manager.start();self.addCleanup(manager.stop)
        self.adapter=Adapter();self.engine=NativeChanges('reminders','synthetic@example.com',self.adapter,self.store)
        self.row={'id':'old','source_id':'selected','title':'Synthetic, title','notes':'Body\nMore','completed':True,'priority':5,
             'start':None,'due':{'year':2026,'month':10,'day':28,'hour':13,'minute':0,'second':0,'timezone':'UTC','date_only':False},
             'url':'https://example.com/path','location':'Synthetic place','created':None,'modified':None,'completion_date':None,
             'public_metadata_complete':True,'content_complete':False,'has_recurrence':False,'has_alarms':False,'recurrence_rules':[],'alarms':[]}
        class Reader:
            def read(inner,*args,**kwargs):return {'item':self.row}
        self.calendar=export_ical(Reader(),'old',datetime(2026,1,1,tzinfo=timezone.utc))['content']
    def prepare(self,**kw):return prepare_ical_copy(self.engine,self.calendar,'selected','destination',accept_omissions=True,**kw)
    def test_generated_vtodo_roundtrips_through_reviewed_copy(self):
        plan=self.prepare(include_dates=True,include_urls=True);payload=plan['review']['payload'];review=payload['copy_review']
        self.assertEqual(review['source_ical_sha256'],hashlib.sha256(self.calendar.encode()).hexdigest())
        self.assertEqual(review['source_format'],'user_supplied_icalendar');self.assertEqual(review['source_completed_count'],1)
        fields=payload['changes'][0]['fields'];self.assertEqual(fields['title'],'Synthetic, title');self.assertEqual(fields['notes'],'Body\nMore')
        self.assertEqual(fields['due']['timezone'],'UTC');self.assertEqual(fields['url'],'https://example.com/path')
        self.assertNotIn('completed',fields);self.assertNotIn('location',fields);self.assertEqual(self.adapter.saved,[])
        self.assertEqual(self.engine.execute(plan['plan_id'])['status'],'applied')
        with self.assertRaises(SafetyError):self.engine.execute(plan['plan_id'])
    def test_strict_receipt_and_omission_acceptance(self):
        with self.assertRaises(SafetyError):prepare_ical_copy(self.engine,self.calendar,'selected','destination',accept_omissions=False)
        os.environ['ICLOUD_APPROVAL_POLICY']='local-receipt';plan=self.prepare()
        with self.assertRaises(FileNotFoundError):self.engine.execute(plan['plan_id'])
        self.store.write_once(self.store.path(plan['plan_id'],'.approved'),{'plan_id':plan['plan_id'],'sha256':plan['sha256']})
        self.assertEqual(self.engine.execute(plan['plan_id'])['status'],'applied')
    def test_folded_unicode_and_escaped_text_roundtrip(self):
        self.row['title']='Synthetic '+('é'*100)+',;\\';self.row['notes']='Line one\nLine two,;\\'
        class Reader:
            def read(inner,*args,**kwargs):return {'item':self.row}
        text=export_ical(Reader(),'old',datetime(2026,1,1,tzinfo=timezone.utc))['content']
        snapshot,_=observation(text)
        self.assertEqual(snapshot['items'][0]['title'],self.row['title']);self.assertEqual(snapshot['items'][0]['notes'],self.row['notes'])
    def test_absolute_display_alarm_requires_explicit_selection(self):
        alarm={'type':0,'proximity':0,'structured_location':None,'email_address':None,'sound_name':None,'absolute_date':1767323045,'relative_offset_seconds':0}
        self.row.update(has_alarms=True,alarms=[alarm])
        class Reader:
            def read(inner,*args,**kwargs):return {'item':self.row}
        self.calendar=export_ical(Reader(),'old',datetime(2026,1,1,tzinfo=timezone.utc))['content']
        omitted=self.prepare();review=omitted['review']['payload']['copy_review']
        self.assertEqual(review['source_absolute_alarm_count'],1);self.assertFalse(review['include_alarms'])
        self.assertNotIn('alarms',omitted['review']['payload']['changes'][0]['fields'])
        selected=self.prepare(include_alarms=True);review=selected['review']['payload']['copy_review']
        self.assertTrue(review['include_alarms']);self.assertIn('schedule notifications',review['effects'][-1])
        self.assertEqual(selected['review']['payload']['changes'][0]['fields']['alarms'],[{'absolute_date':1767323045}])
        for bad in (self.calendar.replace('ACTION:DISPLAY','ACTION:AUDIO'),self.calendar.replace('TRIGGER;VALUE=DATE-TIME:20260102T030405Z','TRIGGER:-PT15M'),
                    self.calendar.replace('TRIGGER;VALUE=DATE-TIME:20260102T030405Z','TRIGGER:20260102T030405Z'),
                    self.calendar.replace('END:VALARM','REPEAT:2\r\nEND:VALARM'),self.calendar.replace('TRIGGER;VALUE=DATE-TIME:20260102T030405Z','TRIGGER;VALUE=DATE-TIME:21000101T000000Z')):
            with self.subTest(bad=bad[-100:]),self.assertRaises(SafetyError):observation(bad)
    def test_unrepresentable_components_and_controls_refuse(self):
        base=self.calendar
        invalid=(base.replace('END:VTODO','RRULE:FREQ=DAILY\r\nEND:VTODO'),
                 base.replace('END:VTODO','BEGIN:VALARM\r\nACTION:DISPLAY\r\nEND:VALARM\r\nEND:VTODO'),
                 base.replace('END:VTODO','SUMMARY:Again\r\nEND:VTODO'),base.replace('BEGIN:VTODO','BEGIN:VEVENT'),
                 base.replace('\r\n','\n'),base.replace('SUMMARY:','SUMMARY:\x7f'),
                 base.replace('STATUS:COMPLETED','STATUS:IN-PROCESS'),base.replace('PRIORITY:5','PRIORITY:3'),
                 base.replace('DUE:20261028T130000Z','DUE;TZID=America/Chicago:20261028T080000'),
                 base.replace('DUE:20261028T130000Z','DUE:20261028T130001Z'),
                 base.replace('SUMMARY:Synthetic\\, title','SUMMARY:unescaped, title'))
        for text in invalid:
            with self.subTest(sample=text[:50]),self.assertRaises(SafetyError):observation(text)
        self.assertEqual(self.adapter.saved,[])
    def test_date_order_and_unsupported_unknown_property_refuse(self):
        for text in (self.calendar.replace('END:VTODO','X-HIDDEN:metadata\r\nEND:VTODO'),
                     self.calendar.replace('DUE:20261028T130000Z','DTSTART:20261028T130000Z\r\nDUE:20261028T130000Z'),
                     self.calendar.replace('DUE:20261028T130000Z','DTSTART;VALUE=DATE:20261028\r\nDUE:20261028T130000Z')):
            with self.subTest(text=text[:30]),self.assertRaises(SafetyError):observation(text)
    def test_excessive_fold_segments_are_bounded(self):
        text=self.calendar.replace('SUMMARY:Synthetic\\, title','SUMMARY:Synthetic'+('\r\n x'*10001))
        with self.assertRaises(SafetyError):observation(text)
    def test_real_adapter_rejects_active_url(self):
        from icloud_for_agents.native_reminders import RemindersCreateAdapter
        helper=self.store.root.parent/'helper';helper.write_text('synthetic')
        os.environ['ICLOUD_NATIVE_CONFIG']=json.dumps({'reminders':{'access_mode':'read-only','source_ids':['selected'],'helper_path':str(helper)}})
        adapter=RemindersCreateAdapter();engine=NativeChanges('reminders',self.engine.account,adapter,self.store)
        bad=self.calendar.replace('URL:https://example.com/path','URL:javascript:alert(1)')
        with patch.object(adapter,'snapshot',return_value={'id':'destination','source_id':'selected','writable':True}),self.assertRaises(SafetyError):
            prepare_ical_copy(engine,bad,'selected','destination',accept_omissions=True,include_urls=True)
    def test_real_adapter_reviews_notification_effects(self):
        from icloud_for_agents.native_reminders import RemindersCreateAdapter
        helper=self.store.root.parent/'helper';helper.write_text('synthetic')
        os.environ['ICLOUD_NATIVE_CONFIG']=json.dumps({'reminders':{'access_mode':'read-only','source_ids':['selected'],'helper_path':str(helper)}})
        adapter=RemindersCreateAdapter();engine=NativeChanges('reminders',self.engine.account,adapter,self.store)
        alarm='BEGIN:VALARM\r\nACTION:DISPLAY\r\nTRIGGER;VALUE=DATE-TIME:20260102T030405Z\r\nDESCRIPTION:Task\r\nEND:VALARM\r\n'
        text=self.calendar.replace('END:VTODO',alarm+'END:VTODO')
        with patch.object(adapter,'snapshot',return_value={'id':'destination','source_id':'selected','writable':True}):
            plan=prepare_ical_copy(engine,text,'selected','destination',accept_omissions=True,include_alarms=True)
        item=plan['review']['payload']['changes'][0]
        self.assertEqual(item['fields']['alarms'],[{'absolute_date':1767323045}]);self.assertIn('sync',item['effects']['time_alarms']['devices'])
        self.assertIn('schedule notifications',plan['review']['payload']['copy_review']['effects'][-1])
if __name__=='__main__':unittest.main()
