"""Notes content validation and exact source/folder-bound creation behavior."""
import json
import os
import unittest
from unittest.mock import patch

from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_notes import NotesCreateAdapter, prepare_content


class Reader:
    def __init__(self):
        self.folder={'id':'folder','title':'Synthetic','source_id':'selected','parent_id':'selected','shared':False}
        self.note={'id':'created','title':'Spec','text':'Spec Body','html':'<div>Spec</div><div>Body</div>','locked':False,'truncated':False}
    def read(self,action,**fields):
        if action=='folders':return {'folders':[dict(self.folder)],'done':True}
        if action=='folder_get':return {'folder':dict(self.folder)}
        if action=='get':return {'item':dict(self.note)}
        raise AssertionError(action)


class NativeNotesTests(unittest.TestCase):
    def setUp(self):
        env={'ICLOUD_ENABLE_NOTES':'1','ICLOUD_MAIL_ACCESS':'write-enabled',
             'ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'access_mode':'write-enabled','source_ids':['selected']}})}
        context=patch.dict(os.environ,env,clear=True);context.start();self.addCleanup(context.stop)
        self.adapter=NotesCreateAdapter();self.reader=Reader();self.adapter.reader=self.reader
    def prepare(self,**extra):
        change={'operation':'create_note','source_id':'selected','folder_id':'folder','fields':{'title':'Spec','body':'Body'}}
        change.update(extra);return self.adapter.prepare(change)
    def test_plain_content_escaped_and_multiline_preserved(self):
        content=prepare_content({'title':'Spec','body':'<unsafe>&\nsecond'})
        self.assertIn('&lt;unsafe&gt;&amp;<br>',content['html'])
        self.assertEqual(content['expected_text'],'<unsafe>& second')
    def test_supported_rich_html_has_reviewable_text(self):
        content=prepare_content({'title':'Spec','format':'html','body':'<h2>Decision</h2><table><tr><td>A</td><td>B</td></tr></table>'})
        self.assertEqual(content['expected_text'],'Decision A B')
        self.assertIn('<table>',content['html'])
    def test_active_external_or_unknown_html_refused_without_silent_stripping(self):
        for body in ('<script>execute()</script>','<img src="https://example.com/private">',
                     '<a href="javascript:execute()">x</a>','<div onclick="execute()">x</div>',
                     '<!-- hidden -->','<!DOCTYPE html>','<?xml version="1.0"?>'):
            with self.subTest(body=body),self.assertRaises(SafetyError):
                prepare_content({'title':'Spec','format':'html','body':body})
    def test_scope_shared_and_unreviewed_input_refused(self):
        with self.assertRaises(SafetyError):self.prepare(source_id='other')
        with self.assertRaises(SafetyError):self.prepare(approval=True)
        self.reader.folder['source_id']='other'
        with self.assertRaises(SafetyError):self.prepare()
        self.reader.folder['source_id']='selected';self.reader.folder['shared']=True
        with self.assertRaises(SafetyError):self.prepare()
    def test_prepare_binds_exact_folder_snapshot_and_preserves_existing_content(self):
        item=self.prepare()
        self.assertEqual(item['before'],self.reader.folder)
        self.assertEqual(item['source_id'],'selected')
        self.assertEqual(item['effects']['existing_content'],'unchanged')
        self.assertNotIn('approved',item)
    def test_duplicate_root_folder_refused(self):
        with self.assertRaises(SafetyError):
            self.adapter.prepare({'operation':'create_folder','source_id':'selected','fields':{'title':'Synthetic'}})
    def test_readback_must_match_source_title_and_complete_body(self):
        item=self.prepare();outcome={'id':'created','source_id':'selected'}
        self.assertTrue(self.adapter.verify(item,outcome))
        self.assertFalse(self.adapter.verify(item,dict(outcome,source_id='other')))
        for key,value in [('title','Wrong'),('html','<div>Missing content</div>'),('truncated',True),('locked',True)]:
            prior=self.reader.note[key];self.reader.note[key]=value
            self.assertFalse(self.adapter.verify(item,outcome));self.reader.note[key]=prior
    def test_unknown_formats_overlong_unicode_and_blank_titles_refused(self):
        for fields in ({'title':'Spec','format':'markdown'}, {'title':' '}, {'title':'x\ny'},
                       {'title':'Spec','body':'😀'*6000}):
            with self.subTest(fields=fields),self.assertRaises(SafetyError):prepare_content(fields)
