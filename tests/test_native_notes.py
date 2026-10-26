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
        self.destination={'id':'destination','title':'Destination','source_id':'selected','parent_id':'selected','shared':False}
        self.parents={};self.attachments=[]
        self.note={'id':'created','title':'Spec','text':'Spec Body','html':'<div>Spec</div><div>Body</div>','locked':False,'truncated':False,'shared':False,'source_id':'selected','folder_id':'folder','modified':'2026-10-26T12:00:00Z','created':'2026-10-25T12:00:00Z'}
    def read(self,action,**fields):
        if action=='folders':return {'folders':[dict(self.folder)],'done':True}
        if action=='folder_get':
            value=self.destination if fields['folder_id']=='destination' else self.parents.get(fields['folder_id'],self.folder)
            return {'folder':dict(value)}
        if action=='get':return {'item':dict(self.note)}
        if action=='attachments':return {'attachments':list(self.attachments),'done':True}
        if action=='contains':return {'exists':fields['folder_id']==self.note['folder_id'] and fields['id']==self.note['id']}
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

    def edit(self,operation='append_note',fields=None):
        return self.adapter.prepare({'operation':operation,'source_id':'selected','folder_id':'folder','id':'created',
                                     'fields':fields or {'body':'Decision & details'}})
    def test_append_preserves_existing_html_and_escapes_new_text(self):
        before=self.reader.note['html'];item=self.edit()
        self.assertEqual(item['before']['note'],self.reader.note)
        self.assertTrue(item['fields']['html'].startswith(before))
        self.assertIn('Decision &amp; details',item['fields']['html'])
        self.reader.note['html']=item['fields']['html']
        self.assertTrue(self.adapter.verify(item,{'id':'created','source_id':'selected'}))
    def test_full_replacement_requires_both_title_and_body(self):
        for fields in ({'title':'New'},{'body':'Body'}):
            with self.assertRaises(SafetyError):self.edit('update_note',fields)
        item=self.edit('update_note',{'title':'New','body':'Body'})
        self.assertIn('Replace this entire',item['effects']['existing_content'])
        self.reader.note.update(title='New',html=item['fields']['html'])
        self.assertTrue(self.adapter.verify(item,{'id':'created','source_id':'selected'}))
    def test_native_objects_and_unknown_formatting_are_not_silently_rewritten(self):
        for html in ('<object><table><tr><td>Native cell</td></tr></table></object>',
                     '<table><tr><td>Native cell</td></tr></table>', '<div style="font-size:24px">Styled</div>',
                     '<ul class="Apple-checkbox"><li>Native checklist</li></ul>', '<img src="private-asset">'):
            self.reader.note['html']=html
            with self.subTest(html=html),self.assertRaises(SafetyError):self.edit()
    def test_shared_locked_unsaved_and_wrong_scope_notes_refuse_before_write(self):
        for key,value in [('shared',True),('locked',True),('modified',None),('truncated',True),
                          ('source_id','other'),('folder_id','other')]:
            prior=self.reader.note[key];self.reader.note[key]=value
            with self.subTest(key=key),self.assertRaises(SafetyError):self.edit()
            self.reader.note[key]=prior
    def test_attachments_and_oversized_combined_body_refuse(self):
        original=self.reader.read
        def attached(action,**fields):
            return {'attachments':[{'id':'native'}],'done':True} if action=='attachments' else original(action,**fields)
        self.reader.read=attached
        with self.assertRaises(SafetyError):self.edit()
        self.reader.read=original;self.reader.note['html']='<div>'+('a'*29000)+'</div>'
        with self.assertRaises(SafetyError):self.edit(fields={'body':'b'*2000})
    def test_existing_note_identity_and_exact_complete_content_are_verified(self):
        item=self.edit();self.reader.note['html']=item['fields']['html']
        self.assertFalse(self.adapter.verify(item,{'id':'other','source_id':'selected'}))
        self.reader.note['html']='<div>Spec</div><div>Missing original body</div>'
        self.assertFalse(self.adapter.verify(item,{'id':'created','source_id':'selected'}))

    def test_append_cannot_claim_preservation_when_old_formatting_is_lost(self):
        self.reader.note['html']='<div>Spec</div><div><b>Body</b></div>'
        item=self.edit()
        self.reader.note['html']=item['fields']['html'].replace('<b>Body</b>','Body')
        self.assertFalse(self.adapter.verify(item,{'id':'created','source_id':'selected'}))

    def move(self):
        return self.adapter.prepare({'operation':'move_note','source_id':'selected','folder_id':'folder','id':'created',
                                     'fields':{'destination_folder_id':'destination'}})
    def test_native_move_preserves_rich_html_and_attachment_identities(self):
        self.reader.note['html']='<div>Spec</div><object><table><tr><td>Native cell</td></tr></table></object>'
        self.reader.attachments=[{'id':'asset','name':'synthetic.pdf','content_identifier':'cid:asset','note_id':'created'}]
        item=self.move();self.assertNotIn('html',item['fields'])
        self.assertEqual(item['before']['attachments'],self.reader.attachments)
        self.reader.note['folder_id']='destination'
        outcome={'id':'created','source_id':'selected','folder_id':'destination'}
        self.assertTrue(self.adapter.verify(item,outcome))
        self.reader.note['html']='<div>Missing table</div>'
        self.assertFalse(self.adapter.verify(item,outcome))
    def test_move_refuses_other_sources_shared_destinations_and_noop(self):
        for key,value in [('source_id','other'),('shared',True)]:
            previous=self.reader.destination[key];self.reader.destination[key]=value
            with self.subTest(key=key),self.assertRaises(SafetyError):self.move()
            self.reader.destination[key]=previous
        with self.assertRaises(SafetyError):
            self.adapter.prepare({'operation':'move_note','source_id':'selected','folder_id':'folder','id':'created','fields':{'destination_folder_id':'folder'}})
    def test_shared_or_unavailable_ancestors_refuse_before_content_access(self):
        parent={'id':'parent','title':'Parent','source_id':'selected','parent_id':'selected','shared':True}
        self.reader.parents['parent']=parent;self.reader.destination['parent_id']='parent'
        with self.assertRaises(SafetyError):self.move()
        parent['shared']=False;item=self.move()
        self.assertEqual(item['before']['destination']['private_ancestors'],[parent])
        parent['parent_id']='parent'
        with self.assertRaises(SafetyError):self.move()
    def test_move_readback_rejects_changed_assets_creation_or_destination(self):
        self.reader.attachments=[{'id':'asset','name':'synthetic.pdf','content_identifier':'cid:asset','note_id':'created'}]
        item=self.move();self.reader.note['folder_id']='destination';outcome={'id':'created','source_id':'selected','folder_id':'destination'}
        self.assertTrue(self.adapter.verify(item,outcome))
        self.reader.attachments=[];self.assertFalse(self.adapter.verify(item,outcome))
        self.reader.attachments=item['before']['attachments'];self.reader.note['created']='changed'
        self.assertFalse(self.adapter.verify(item,outcome))
        self.assertFalse(self.adapter.verify(item,dict(outcome,folder_id='folder')))
