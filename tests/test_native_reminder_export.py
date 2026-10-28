"""Bounded public export never claims restore or full native coverage."""
import json
import os
import unittest
from unittest.mock import patch

from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_read import NativeReader
from icloud_for_agents.native_reminder_export import export_page


class ReminderExportTests(unittest.TestCase):
    def setUp(self):
        self.row={'id':'task','source_id':'selected','list_id':'list','title':'Synthetic title','notes':'Synthetic body',
            'completed':False,'priority':0,'due':None,'start':None,'created':None,'modified':None,'completion_date':None,
            'url':None,'location':None,'has_recurrence':False,'has_alarms':False,'recurrence_rules':[],'alarms':[],
            'public_metadata_complete':True,'content_complete':False,
            'omitted_features':['native_tags','hierarchy','attachments','advanced_metadata']}
        self.page={'protocol_version':1,'items':[self.row],'done':False,'next_offset':1,'pagination_consistency':'live_offset_may_shift'}
        self.env=patch.dict(os.environ,{'ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'source_ids':['selected']}}),'ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_ENABLE_REMINDERS':'1'},clear=True)
        self.env.start();self.addCleanup(self.env.stop)
        self.system=patch('platform.system',return_value='Darwin');self.system.start();self.addCleanup(self.system.stop)
        self.dispatch=patch.object(NativeReader,'_dispatch',return_value=self.page);self.backend=self.dispatch.start();self.addCleanup(self.dispatch.stop)

    def test_selected_list_projection_and_limits_are_explicit(self):
        result=export_page(NativeReader('reminders'),'list',limit=1)
        self.assertEqual(result['items'],[self.row]);self.assertFalse(result['coverage']['complete_backup'])
        self.assertFalse(result['coverage']['restorable']);self.assertFalse(result['coverage']['atomic_snapshot'])
        self.assertTrue(result['coverage']['list_observed_in_page'])
        self.assertEqual(result['page']['next_offset'],1);self.assertFalse(result['page']['done'])
        self.assertEqual(self.backend.call_args.args[0]['list_id'],'list')
        self.assertNotIn('native_metadata',result['items'][0])

    def test_outside_scope_old_helper_and_oversized_pages_are_refused(self):
        for row in (dict(self.row,source_id='other'),dict(self.row,list_id='other'),
                    {k:v for k,v in self.row.items() if k!='content_complete'},dict(self.row,content_complete=True),
                    dict(self.row,notes='x'*1_000_001)):
            self.backend.return_value=dict(self.page,items=[row])
            with self.subTest(row_keys=list(row)),self.assertRaises(SafetyError):export_page(NativeReader('reminders'),'list',1)

    def test_bounds_rejected_before_dispatch(self):
        for args in ((None,1,0),('list',True,0),('list',21,0),('list',1,-1),('list',1,100001)):
            with self.subTest(args=args),self.assertRaises(SafetyError):export_page(NativeReader('reminders'),*args)
        self.backend.assert_not_called()

    def test_empty_page_does_not_assert_the_list_exists(self):
        self.backend.return_value=dict(self.page,items=[],done=True,next_offset=0)
        result=export_page(NativeReader('reminders'),'list')
        self.assertFalse(result['coverage']['list_observed_in_page'])
        self.assertIn('does not establish',result['coverage']['limitations'][0])

if __name__=='__main__':unittest.main()
