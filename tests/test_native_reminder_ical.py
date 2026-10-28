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
            'completed':False,'priority':1,'due':None,'start':None,'public_metadata_complete':True,'content_complete':False,
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
    def test_refuses_unrepresentable_and_wrong_scope(self):
        invalid=[dict(self.item,source_id='foreign'),dict(self.item,has_recurrence=True),dict(self.item,has_alarms=True),
                 dict(self.item,public_metadata_complete=False),dict(self.item,content_complete=True),dict(self.item,priority=True),
                 dict(self.item,title='unsafe\x00title'),dict(self.item,due={'year':2026,'month':10,'day':28,'hour':8,'minute':0,'second':0,'timezone':'America/Chicago','date_only':False}),
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
