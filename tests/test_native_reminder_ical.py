"""RFC 5545 subset export; unsupported semantics must fail closed."""
import json,os,unittest
from datetime import datetime,timezone
from unittest.mock import patch
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_read import NativeReader
from icloud_for_agents.native_reminder_ical import export_ical

class ReminderICalTests(unittest.TestCase):
    def setUp(self):
        self.item={'id':'task','source_id':'selected','list_id':'list','title':'Synthetic, title; \\','notes':'first\nsecond',
            'completed':False,'priority':1,'due':None,'start':None,'url':None,'location':None,'created':None,'modified':None,'completion_date':None,'public_metadata_complete':True,'content_complete':False,
            'has_recurrence':False,'has_alarms':False,'recurrence_rules':[],'alarms':[]}
        env={'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'source_ids':['selected']}})}
        for manager in (patch.dict(os.environ,env,clear=True),patch('platform.system',return_value='Darwin')):
            manager.start();self.addCleanup(manager.stop)
        manager=patch.object(NativeReader,'_dispatch',return_value={'protocol_version':1,'item':self.item})
        self.backend=manager.start();self.addCleanup(manager.stop)
        self.now=datetime(2026,1,2,3,4,5,tzinfo=timezone.utc)
    def export(self):return export_ical(NativeReader('reminders'),'task',self.now)
    def test_dates_escaping_folding_and_loss(self):
        self.item['title']='Synthetic,;\\'+('é'*100)+'\nATTENDEE:evil'
        self.item['start']={'year':2026,'month':10,'day':28,'date_only':True,'timezone':None}
        self.item['due']={'year':2026,'month':10,'day':29,'date_only':True,'timezone':None}
        result=self.export();text=result['content'];unfolded=text.replace('\r\n ','')
        self.assertIn('DTSTART;VALUE=DATE:20261028\r\n',unfolded);self.assertIn('DUE;VALUE=DATE:20261029\r\n',unfolded)
        self.assertIn('DTSTAMP:20260102T030405Z\r\n',unfolded);self.assertIn('\\nATTENDEE:evil',unfolded)
        self.assertNotIn('\r\nATTENDEE:',text);self.assertIn('Synthetic\\,\\;\\\\',unfolded)
        self.assertTrue(all(len(line.encode('utf-8'))<=75 for line in text.split('\r\n')))
        self.assertFalse(result['coverage']['restorable']);self.assertFalse(result['coverage']['complete_backup'])
        self.assertEqual(self.backend.call_count,1);self.assertEqual(self.backend.call_args.args[0]['action'],'get')
    def test_complete_utc_fields(self):
        self.item['due']={'year':2026,'month':10,'day':28,'hour':14,'minute':30,'second':0,'timezone':'UTC','date_only':False}
        self.assertIn('DUE:20261028T143000Z\r\n',self.export()['content'])
    def test_url_and_location_are_preserved_without_property_injection(self):
        self.item['url']='https://example.com/notes?a=1&b=2';self.item['location']='Office, West; floor 2\nATTENDEE:evil'
        content=self.export()['content'].replace('\r\n ','')
        self.assertIn('URL:https://example.com/notes?a=1&b=2\r\n',content)
        self.assertIn('LOCATION:Office\\, West\\; floor 2\\nATTENDEE:evil\r\n',content)
        self.assertNotIn('\r\nATTENDEE:',content)
        for value in ('https://example.com/\r\nATTENDEE:evil','javascript:alert(1)','https://user:pass@example.com'):
            self.item['url']=value
            with self.subTest(value=value),self.assertRaises(SafetyError):self.export()
    def test_iana_timezone_conversion_and_dst_refusals(self):
        self.item['due']={'year':2026,'month':10,'day':28,'hour':8,'minute':0,'second':0,'timezone':'America/Chicago','date_only':False}
        self.assertIn('DUE:20261028T130000Z\r\n',self.export()['content'])
        for day in ({'year':2026,'month':11,'day':1,'hour':1,'minute':30}, {'year':2026,'month':3,'day':8,'hour':2,'minute':30}):
            self.item['due']={**day,'second':0,'timezone':'America/Chicago','date_only':False}
            with self.subTest(day=day),self.assertRaises(SafetyError):self.export()
    def test_public_timestamps_and_precision_are_explicit(self):
        self.item.update(created=1767323045.75,modified=1767323046.1,completion_date=1767323047.5,completed=True)
        result=self.export();content=result['content']
        self.assertIn('CREATED:20260102T030405Z\r\n',content);self.assertIn('LAST-MODIFIED:20260102T030406Z\r\n',content)
        self.assertIn('COMPLETED:20260102T030407Z\r\n',content);self.assertEqual(result['coverage']['timestamp_precision'],'whole_seconds_subseconds_discarded')
        self.item['created']=-0.0000001
        self.assertIn('CREATED:19691231T235959Z\r\n',self.export()['content'])
        self.item['completed']=False
        with self.assertRaises(SafetyError):self.export()
        for invalid in (True,float('nan'),10**400):
            self.item.update(completed=True,created=invalid)
            with self.subTest(invalid=invalid),self.assertRaises(SafetyError):self.export()
    def test_absolute_display_alarms_and_refusal_of_other_kinds(self):
        alarm={'type':0,'proximity':0,'structured_location':None,'email_address':None,'sound_name':None,
               'absolute_date':1767323045,'relative_offset_seconds':0}
        self.item.update(has_alarms=True,alarms=[alarm]);result=self.export();content=result['content']
        self.assertEqual(result['coverage']['absolute_display_alarms'],1)
        self.assertIn('BEGIN:VALARM\r\nACTION:DISPLAY\r\nTRIGGER;VALUE=DATE-TIME:20260102T030405Z\r\n',content)
        self.assertIn('importing can schedule notifications',result['coverage']['limitations'][-2])
        for bad in (dict(alarm,absolute_date=None,relative_offset_seconds=-300),dict(alarm,type=1),
                    dict(alarm,structured_location={}),dict(alarm,absolute_date=1767323045.5),dict(alarm,absolute_date=10**400),
                    dict(alarm,relative_offset_seconds=-1),dict(alarm,unknown='unverified')):
            self.item['alarms']=[bad]
            with self.subTest(bad=bad),self.assertRaises(SafetyError):self.export()
    def test_refuses_unrepresentable_and_wrong_scope(self):
        invalid=[dict(self.item,source_id='foreign'),dict(self.item,has_recurrence=True),dict(self.item,has_alarms=True),
                 dict(self.item,public_metadata_complete=False),dict(self.item,content_complete=True),dict(self.item,priority=True),
                 dict(self.item,title='unsafe\x00title'),dict(self.item,title='unsafe\x7ftitle'),dict(self.item,location='unsafe\x7flocation'),dict(self.item,due={'year':2026,'month':10,'day':28,'hour':8,'minute':0,'second':0,'timezone':'Unknown/Zone','date_only':False}),
                 dict(self.item,due={'year':2026,'month':10,'day':28,'hour':8,'minute':0,'timezone':'UTC','date_only':False})]
        for item in invalid:
            self.backend.return_value={'protocol_version':1,'item':item}
            with self.subTest(item=item),self.assertRaises(SafetyError):self.export()
    def test_mixed_or_inverted_dates_are_refused(self):
        self.item['start']={'year':2026,'month':10,'day':29,'date_only':True,'timezone':None}
        self.item['due']={'year':2026,'month':10,'day':28,'date_only':True,'timezone':None}
        with self.assertRaises(SafetyError):self.export()
        self.item['due']=dict(self.item['start'])
        with self.assertRaises(SafetyError):self.export()
        self.item['due']={'year':2026,'month':10,'day':30,'hour':10,'minute':0,'second':0,'timezone':'UTC','date_only':False}
        with self.assertRaises(SafetyError):self.export()
if __name__=='__main__':unittest.main()
