"""Exact HTML reference search is bounded, scoped and stops on read failure."""
import json,os,unittest
from unittest.mock import patch
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_note_links import find_references
from icloud_for_agents.native_read import NativeReader

class NoteReferencesTests(unittest.TestCase):
    def setUp(self):
        env={'ICLOUD_ENABLE_NOTES':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'source_ids':['selected','other']}})}
        for manager in (patch.dict(os.environ,env,clear=True),patch('platform.system',return_value='Darwin')):
            manager.start();self.addCleanup(manager.stop)
        self.rows=[{'id':name,'folder_id':'folder','source_id':'selected','title':name} for name in ('match','locked','truncated')]
        self.items={'match':{'id':'match','folder_id':'folder','source_id':'selected','title':'Current title','locked':False,'truncated':False,'html':'<a href="https://example.com/spec">spec</a>'},
                    'locked':{'id':'locked','folder_id':'folder','source_id':'selected','locked':True,'content_available':False},
                    'truncated':{'id':'truncated','folder_id':'folder','source_id':'selected','title':'Truncated','locked':False,'truncated':True,'html':'<a href="https://example.com/spec">spec</a>'}}
        def dispatch(request,config):
            if request['action']=='query':return {'protocol_version':1,'items':self.rows,'done':True,'next_offset':3,'pagination_consistency':'live_offset_may_shift'}
            return {'protocol_version':1,'item':self.items[request['id']]}
        manager=patch.object(NativeReader,'_dispatch',side_effect=dispatch);self.backend=manager.start();self.addCleanup(manager.stop)
    def find(self,**kwargs):return find_references(NativeReader('notes'),'folder','https://example.com/spec',**kwargs)
    def test_matches_locked_and_truncated_are_explicit(self):
        value=self.find();self.assertTrue(value['done']);self.assertEqual(value['next_offset'],3)
        self.assertEqual([row['id'] for row in value['matches']],['match','truncated']);self.assertEqual(value['matches'][0]['title'],'Current title')
        self.assertEqual(value['unavailable'],[{'id':'locked','reason':'locked'},{'id':'truncated','reason':'truncated_html'}])
        self.assertFalse(value['coverage']['native_backlinks_complete']);self.assertFalse(value['coverage']['targets_opened'])
        self.assertNotIn('https://example.com/spec',json.dumps(value));self.assertEqual(self.backend.call_count,4)
    def test_html_attribute_entities_are_decoded_before_matching(self):
        self.items['match']['html']='<a href="https://example.com/?a=1&amp;b=2">spec</a>'
        value=find_references(NativeReader('notes'),'folder','https://example.com/?a=1&b=2')
        self.assertEqual([row['id'] for row in value['matches']],['match'])
        self.assertEqual(value['coverage']['match'],'exact_decoded_href_in_bounded_exported_html')
    def test_read_failure_stops_without_retry_or_implicit_skip(self):
        original=self.backend.side_effect;calls=[]
        def fail(request,config):
            calls.append(request['action'])
            if request.get('id')=='locked':raise SafetyError('synthetic timeout')
            return original(request,config)
        self.backend.side_effect=fail;value=self.find()
        self.assertEqual(calls,['query','get','get']);self.assertEqual(value['stop_reason'],'read_failed_no_retry')
        self.assertIsNone(value['next_offset']);self.assertFalse(value['done']);self.assertEqual(value['unavailable'],[{'id':'locked','reason':'read_failed'}])
    def test_source_change_is_reported_and_invalid_options_refuse(self):
        self.items['match']['source_id']='other';value=self.find()
        self.assertEqual(value['unavailable'][0],{'id':'match','reason':'scope_changed'});self.assertEqual([row['id'] for row in value['matches']],['truncated'])
        self.backend.reset_mock()
        for kwargs in ({'limit':True},{'limit':4},{'offset':-1}):
            with self.subTest(kwargs=kwargs),self.assertRaises(SafetyError):self.find(**kwargs)
        with self.assertRaises(SafetyError):find_references(NativeReader('notes'),'folder','bad\nvalue')
        self.backend.assert_not_called()
if __name__=='__main__':unittest.main()
