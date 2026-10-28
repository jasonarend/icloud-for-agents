"""Notes exports retain observed limits and never unlock or fetch assets."""
import json,os,unittest
from unittest.mock import patch
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_note_export import export_note
from icloud_for_agents.native_read import NativeReader

class NoteExportTests(unittest.TestCase):
    def setUp(self):
        self.item={'id':'note','source_id':'selected','folder_id':'folder','title':'Synthetic','html':'<div>Synthetic</div>',
            'text':'Synthetic','truncated':False,'created':None,'modified':None,'locked':False,'shared':False,
            'content_complete':False,'omitted_features':['native_objects','attachments','collaboration_metadata']}
        env={'ICLOUD_ENABLE_NOTES':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'source_ids':['selected']}})}
        for manager in (patch.dict(os.environ,env,clear=True),patch('platform.system',return_value='Darwin')):
            manager.start();self.addCleanup(manager.stop)
        manager=patch.object(NativeReader,'_dispatch',return_value={'protocol_version':1,'item':self.item})
        self.backend=manager.start();self.addCleanup(manager.stop)

    def test_scoped_export_discloses_loss_and_has_no_asset_call(self):
        value=export_note(NativeReader('notes'),'folder','note')
        self.assertEqual(value['item'],self.item);self.assertFalse(value['coverage']['restorable'])
        self.assertFalse(value['coverage']['attachment_content_included']);self.assertFalse(value['coverage']['body_truncated'])
        self.assertEqual(self.backend.call_count,1);self.assertEqual(self.backend.call_args.args[0]['action'],'get')

    def test_locked_note_never_returns_content(self):
        self.backend.return_value={'protocol_version':1,'item':{'id':'note','folder_id':'folder','source_id':'selected','locked':True,'content_available':False,'html':'do not return'}}
        value=export_note(NativeReader('notes'),'folder','note')
        self.assertNotIn('html',value['item']);self.assertIsNone(value['coverage']['body_truncated'])
        self.assertFalse(value['item']['content_available'])

    def test_truncation_and_incomplete_or_oversized_response(self):
        self.backend.return_value={'protocol_version':1,'item':dict(self.item,truncated=True)}
        self.assertTrue(export_note(NativeReader('notes'),'folder','note')['coverage']['body_truncated'])
        for bad in ({k:v for k,v in self.item.items() if k!='truncated'},dict(self.item,source_id='other'),
                    dict(self.item,content_complete=True),dict(self.item,html='x'*1_000_001)):
            self.backend.return_value={'protocol_version':1,'item':bad}
            with self.subTest(keys=list(bad)),self.assertRaises(SafetyError):export_note(NativeReader('notes'),'folder','note')

    def test_empty_selector_refuses_before_dispatch(self):
        with self.assertRaises(SafetyError):export_note(NativeReader('notes'),'','note')
        self.backend.assert_not_called()

if __name__=='__main__':unittest.main()
