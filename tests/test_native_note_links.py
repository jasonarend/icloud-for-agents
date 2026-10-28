"""Links are partial, scoped observations and never opened or resolved."""
import json,os,unittest
from unittest.mock import patch
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_note_links import inspect_links
from icloud_for_agents.native_read import NativeReader

class NoteLinksTests(unittest.TestCase):
    def setUp(self):
        env={'ICLOUD_ENABLE_NOTES':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'source_ids':['selected']}})}
        for manager in (patch.dict(os.environ,env,clear=True),patch('platform.system',return_value='Darwin')):
            manager.start();self.addCleanup(manager.stop)
        self.item={'id':'note','folder_id':'folder','source_id':'selected','locked':False,'truncated':False,
                   'modified':'2026-10-28T12:00:00Z','html':'<p><a href="https://example.com/spec">Spec</a> <a href="applenotes:note/synthetic">Note</a></p>'}
        manager=patch.object(NativeReader,'_dispatch',return_value={'protocol_version':1,'item':self.item})
        self.backend=manager.start();self.addCleanup(manager.stop)
    def read(self,**kwargs):return inspect_links(NativeReader('notes'),'folder','note',**kwargs)
    def test_scoped_links_and_versioned_pagination(self):
        first=self.read(limit=1);self.assertFalse(first['done']);self.assertEqual(first['next_offset'],1)
        self.assertEqual(first['links'][0]['target'],'https://example.com/spec');self.assertFalse(first['links'][0]['target_verified'])
        second=self.read(limit=1,offset=1,expected_modified=first['modified']);self.assertTrue(second['done'])
        self.assertEqual(second['links'][0]['target'],'applenotes:note/synthetic')
        self.assertFalse(second['coverage']['native_links_complete']);self.assertFalse(second['coverage']['backlinks_verified'])
        self.assertFalse(second['coverage']['targets_opened']);self.assertEqual(self.backend.call_count,2)
    def test_locked_and_changed_note_never_yields_links(self):
        self.backend.return_value={'protocol_version':1,'item':{'id':'note','folder_id':'folder','source_id':'selected','locked':True,'content_available':False,'html':'must-not-leak'}}
        value=self.read();self.assertIsNone(value['links']);self.assertFalse(value['content_available'])
        self.backend.return_value={'protocol_version':1,'item':self.item}
        with self.assertRaises(SafetyError):self.read(expected_modified='different')
        with self.assertRaises(SafetyError):self.read(offset=3)
    def test_untrusted_targets_are_never_resolved_and_large_values_are_omitted(self):
        self.item['html']='<a href="javascript:alert(1)">Active-looking</a><a href="'+('x'*4001)+'">Long</a><a href="bad\x01target">'+('L'*1001)+'</a>'
        value=self.read();self.assertEqual(value['links'][0]['target'],'javascript:alert(1)')
        self.assertEqual(value['links'][0]['target_status'],'observed_untrusted');self.assertFalse(value['coverage']['targets_opened'])
        self.assertIsNone(value['links'][1]['target']);self.assertEqual(value['links'][1]['target_status'],'omitted_invalid_or_oversized')
        self.assertIsNone(value['links'][2]['target']);self.assertTrue(value['links'][2]['label_truncated']);self.assertEqual(len(value['links'][2]['label']),1000)
    def test_source_scope_truncation_and_input_bounds(self):
        self.item['truncated']=True;self.assertTrue(self.read()['source_truncated'])
        self.item['source_id']='foreign'
        with self.assertRaises(SafetyError):self.read()
        self.backend.reset_mock()
        for args in ({'limit':True},{'limit':51},{'offset':-1},{'expected_modified':''}):
            with self.subTest(args=args),self.assertRaises(SafetyError):self.read(**args)
        self.backend.assert_not_called()
    def test_large_encoded_page_requires_smaller_page_size(self):
        self.item['html']=''.join('<a href="'+('🚀'*3500)+'">x</a>' for _ in range(25))
        with self.assertRaisesRegex(SafetyError,'reduce the page size'):self.read(limit=25)
        self.assertEqual(len(self.read(limit=1)['links']),1)
if __name__=='__main__':unittest.main()
