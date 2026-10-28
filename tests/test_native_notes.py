"""Notes content validation and exact source/folder-bound creation behavior."""
import json
import os
import unittest
from unittest.mock import patch, PropertyMock

from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_notes import NotesCreateAdapter, prepare_content


class Reader:
    def __init__(self):
        self.folder={'id':'folder','title':'Synthetic','source_id':'selected','parent_id':'selected','shared':False}
        self.destination={'id':'destination','title':'Destination','source_id':'selected','parent_id':'selected','shared':False}
        self.parents={};self.attachments=[]
        self.note={'id':'created','title':'Spec','text':'Spec Body','html':'<div>Spec</div><div>Body</div>','locked':False,'truncated':False,'shared':False,'source_id':'selected','folder_id':'folder','modified':'2026-10-26T12:00:00Z','created':'2026-10-25T12:00:00Z'}
    def read(self,action,**fields):
        if action=='default_folder':return {'folder':{'id':self.folder['id'],'source_id':'selected','parent_id':'selected','title':'Synthetic','shared':False,'role':'default_folder'}}
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
    def test_larger_plain_note_is_bounded_and_escaped(self):
        body='Transcript line.\n'*3000
        item=self.prepare(fields={'title':'Transcript','body':body})
        self.assertEqual(item['fields']['expected_text'],' '.join(body.split()))
        self.assertLessEqual(len(item['fields']['html'].encode()),90000)
        with self.assertRaises(SafetyError):prepare_content({'title':'Transcript','body':'x'*80001})
        with self.assertRaises(SafetyError):prepare_content({'title':'Transcript','body':'&'*20000})
        import tempfile
        from pathlib import Path
        from icloud_for_agents.native_changes import NativeChanges
        from icloud_for_agents.approvals import PlanStore
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_APPROVAL_POLICY':'trusted-client'}),patch.object(NotesCreateAdapter,'backend_id',new_callable=PropertyMock,return_value='synthetic-notes'):
            engine=NativeChanges('notes','synthetic@example.com',self.adapter,PlanStore(Path(directory)/'runtime'))
            plan=engine.prepare_once([{'operation':'create_note','source_id':'selected','folder_id':'folder','fields':{'title':'Transcript','body':body}}])
            self.assertEqual(plan['review']['payload']['changes'][0]['fields']['expected_text'],' '.join(body.split()))
            maximum=engine.prepare_once([{'operation':'create_note','source_id':'selected','folder_id':'folder','fields':{'title':'Transcript','body':'x'*80000}}]);self.assertEqual(len(maximum['review']['payload']['changes'][0]['fields']['expected_text']),80000)
            def apply(item):
                self.reader.note.update(title=item['fields']['title'],html='<div>'+item['fields']['title']+'</div>'+item['fields']['html'],text=item['fields']['expected_text'])
                return {'id':'created','source_id':'selected','folder_id':'folder'}
            self.adapter.apply=apply
            self.assertEqual(engine.execute(maximum['plan_id'])['status'],'applied')



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
        self.assertEqual(item['before'],dict(self.reader.folder,verified_default_folder={'id':'folder','source_id':'selected'}))
        self.assertEqual(item['source_id'],'selected')
        self.assertEqual(item['effects']['existing_content'],'unchanged')
        self.assertIn('default creation folder',item['effects']['folder_role'])
        self.assertNotIn('approved',item)
    def test_unverified_folder_and_default_folder_drift_refuse_writes(self):
        with self.assertRaisesRegex(SafetyError,'verified default folder'):
            self.prepare(folder_id='destination')
        item=self.prepare()
        self.reader.folder['id']='changed-default'
        with self.assertRaises(SafetyError):self.adapter.snapshot(item)
        self.reader.folder['id']='folder'
        with self.assertRaisesRegex(SafetyError,'verified default folder'):
            self.adapter.prepare({'operation':'create_folder','source_id':'selected','parent_folder_id':'destination','fields':{'title':'Synthetic child'}})

    def test_stale_default_folder_stops_consumed_plan_before_native_dispatch(self):
        import tempfile
        from pathlib import Path
        from icloud_for_agents.native_changes import NativeChanges
        from icloud_for_agents.approvals import PlanStore
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,{'ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_APPROVAL_POLICY':'trusted-client'}), patch.object(NotesCreateAdapter,'backend_id',new_callable=PropertyMock,return_value='synthetic-notes'):
            engine=NativeChanges('notes','synthetic@example.com',self.adapter,PlanStore(Path(directory)/'runtime'))
            plan=engine.prepare_once([{'operation':'create_note','source_id':'selected','folder_id':'folder','fields':{'title':'Synthetic','body':'Body'}}])
            original=self.reader.read;default_reads=0
            def changed(action,**fields):
                nonlocal default_reads
                if action=='default_folder':
                    default_reads+=1
                    if default_reads>1:return {'folder':{'id':'destination','source_id':'selected','parent_id':'selected','title':'Destination','shared':False,'role':'default_folder'}}
                return original(action,**fields)
            self.reader.read=changed
            with patch.object(self.adapter,'apply',side_effect=AssertionError('No native dispatch')):
                result=engine.execute(plan['plan_id'])
            self.assertEqual(result['status'],'stopped_before_dispatch')
            self.assertEqual(result['completed'],[])

    def test_legacy_plan_without_folder_evidence_is_not_admitted(self):
        import tempfile
        from pathlib import Path
        from icloud_for_agents.native_changes import NativeChanges
        from icloud_for_agents.approvals import PlanStore
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_APPROVAL_POLICY':'trusted-client'}),patch.object(NotesCreateAdapter,'backend_id',new_callable=PropertyMock,return_value='synthetic-notes'):
            engine=NativeChanges('notes','synthetic@example.com',self.adapter,PlanStore(Path(directory)/'runtime'))
            proposal=engine.prepare_once([{'operation':'create_note','source_id':'selected','folder_id':'folder','fields':{'title':'Synthetic','body':'Body'}}])
            path=engine.store.path(proposal['plan_id'],'.json');plan=engine.store.read(path)
            del plan['payload']['changes'][0]['before']['verified_default_folder']
            path.write_text(json.dumps(plan)) # Synthetic pre-upgrade plan fixture.
            with patch.object(self.adapter,'apply',side_effect=AssertionError('No native dispatch')),self.assertRaises(SafetyError):engine.execute(proposal['plan_id'])
            self.assertFalse(engine.store.path(proposal['plan_id'],'.started').exists())
    def test_opted_in_model_checks_content_folder_role_at_each_snapshot(self):
        config={'notes':{'access_mode':'write-enabled','source_ids':['selected'],'metadata_backend':'notes-store-readonly','folder_write_backend':'notes-shared'}}
        os.environ['ICLOUD_NATIVE_CONFIG']=json.dumps(config)
        original=self.reader.read
        role={'folder_id':'folder','source_id':'selected','role':'ordinary_non_system','backend':'notes-shared-folder-role-v1','data_mutated':False,'ancestor_count':0}
        def read(action,**fields):
            return dict(role) if action=='model_folder_role' else original(action,**fields)
        self.reader.read=read
        item=self.prepare();self.assertEqual(item['before']['verified_model_role']['role'],'ordinary_non_system')
        edited=self.edit();self.assertEqual(edited['before']['folder']['verified_model_role']['role'],'ordinary_non_system')
        role['role']='system_trash'
        with self.assertRaises(SafetyError):self.prepare()
        with self.assertRaises(SafetyError):self.adapter.snapshot(edited)
        role['role']='ordinary_non_system';role['source_id']='other'
        with self.assertRaises(SafetyError):self.prepare()

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
                       {'title':'Spec','body':'😀'*20001}):
            with self.subTest(fields=fields),self.assertRaises(SafetyError):prepare_content(fields)

    def edit(self,operation='append_note',fields=None):
        return self.adapter.prepare({'operation':operation,'source_id':'selected','folder_id':'folder','id':'created',
                                     'fields':fields or {'body':'Decision & details'}})
    def test_append_preserves_existing_html_and_escapes_new_text(self):
        before=self.reader.note['html'];item=self.edit()
        self.assertEqual(item['before']['note'],self.reader.note)
        self.assertTrue(item['fields']['html'].startswith(before))
        self.assertIn('Decision &amp; details',item['fields']['html'])
        self.assertIn('default creation folder',item['effects']['folder_role'])
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
        self.reader.read=original;self.reader.note['html']='<div>'+('a'*89000)+'</div>'
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
    def test_move_and_stale_move_snapshot_refuse_unverified_folder_roles(self):
        with self.assertRaisesRegex(SafetyError,'special folder roles'):self.move()
        old={'operation':'move_note','source_id':'selected','folder_id':'folder','id':'created','fields':{'destination_folder_id':'destination'}}
        with self.assertRaisesRegex(SafetyError,'special folder roles'):self.adapter.snapshot(old)

    def metadata_move_scope(self):
        os.environ['ICLOUD_NATIVE_CONFIG']=json.dumps({'notes':{'access_mode':'write-enabled','source_ids':['selected'],'metadata_backend':'notes-store-readonly'}})
        def role(identity,sources):
            self.assertEqual(sources,['selected'])
            folder=self.reader.destination if identity=='destination' else self.reader.parents.get(identity,self.reader.folder)
            return {'folder_id':identity,'source_id':'selected','title':folder['title'],'storage_kind':'regular',
                    'native_folder_type':0,'marked_for_deletion':False,'has_smart_query':False,
                    'revision':1,'parent_pk':None,'schema_sha256':'a'*64}
        context=patch('icloud_for_agents.native_note_store.note_location',side_effect=lambda identity,sources:{'id':identity,'source_id':'selected','folder_id':self.reader.note['folder_id'],'content_read':False,'database_writes':False})
        context.start();self.addCleanup(context.stop)
        return patch('icloud_for_agents.native_note_store.folder_role',side_effect=role)

    def test_metadata_move_binds_roles_and_preserves_exact_native_content(self):
        from icloud_for_agents.native_protocol import mutation_request
        self.reader.note['html']='<div>Spec</div><object><table><tr><td>Rich</td></tr></table></object>'
        with self.metadata_move_scope():
            item=self.move();request=mutation_request('notes',item)
            self.assertEqual(request['change']['folder_guard'],'verified_store_roles')
            self.assertEqual({r['folder_id'] for r in item['before']['roles']},{'folder','destination'})
            self.reader.note['folder_id']='destination'
            self.assertTrue(self.adapter.verify(item,{'id':'created','source_id':'selected','folder_id':'destination'}))
            self.reader.note['html']='changed'
            self.assertFalse(self.adapter.verify(item,{'id':'created','source_id':'selected','folder_id':'destination'}))

    def test_metadata_move_checks_ancestors_and_refuses_special_or_deleted_roles(self):
        self.reader.folder['parent_id']='ancestor'
        self.reader.parents['ancestor']={'id':'ancestor','title':'Ancestor','source_id':'selected','parent_id':'selected','shared':False}
        with self.metadata_move_scope() as roles:
            item=self.move();self.assertEqual({r['folder_id'] for r in item['before']['roles']},{'folder','destination','ancestor'})
            original=roles.side_effect
            for change in ({'storage_kind':'special_smart_or_deleted'},{'marked_for_deletion':True},{'has_smart_query':True},{'source_id':'foreign'}):
                roles.side_effect=lambda identity,sources,change=change:dict(original(identity,sources),**(change if identity=='ancestor' else {}))
                with self.subTest(change=change),self.assertRaises(SafetyError):self.move()

    def test_metadata_move_refuses_special_endpoint_before_scripting_reads(self):
        with self.metadata_move_scope() as roles,patch.object(self.reader,'read',wraps=self.reader.read) as reads:
            ordinary=roles.side_effect
            roles.side_effect=lambda identity,sources:dict(ordinary(identity,sources),storage_kind='special_smart_or_deleted' if identity=='destination' else 'regular')
            with self.assertRaises(SafetyError):self.move()
            reads.assert_not_called()

    def test_metadata_move_refuses_scripting_only_success_and_preexisting_location_drift(self):
        with self.metadata_move_scope():
            item=self.move();self.reader.note['folder_id']='destination'
            with patch('icloud_for_agents.native_note_store.note_location',return_value={'id':'created','source_id':'selected','folder_id':'folder'}):
                self.assertFalse(self.adapter.verify(item,{'id':'created','source_id':'selected','folder_id':'destination'}))
            with patch('icloud_for_agents.native_note_store.note_location',return_value={'id':'created','source_id':'selected','folder_id':'unrelated'}):
                with self.assertRaisesRegex(SafetyError,'persisted physical folder'):self.move()

    def test_historical_move_readback_retains_content_checks(self):
        before=self.adapter.note_snapshot({'operation':'move_note','source_id':'selected','folder_id':'folder','id':'created'},rewrite=False)
        before['destination']=self.adapter.private_folder('destination','selected')
        item={'operation':'move_note','source_id':'selected','folder_id':'folder','id':'created','fields':{'destination_folder_id':'destination'},'before':before}
        self.reader.note['folder_id']='destination';outcome={'id':'created','source_id':'selected','folder_id':'destination'}
        self.assertTrue(self.adapter.verify(item,outcome));self.reader.note['html']='<div>changed</div>'
        self.assertFalse(self.adapter.verify(item,outcome))

    def test_nested_folder_creation_binds_private_parent_and_direct_siblings(self):
        item=self.adapter.prepare({'operation':'create_folder','source_id':'selected','parent_folder_id':'folder','fields':{'title':'Synthetic'}})
        self.assertEqual(item['before']['parent_id'],'folder')
        self.assertEqual(item['before']['parent']['id'],'folder')
        self.assertEqual(item['before']['matching_titles'],0)
        self.reader.folder['shared']=True
        with self.assertRaises(SafetyError):self.adapter.prepare({'operation':'create_folder','source_id':'selected','parent_folder_id':'folder','fields':{'title':'Child'}})
    def test_parent_scope_is_not_an_ignored_field_or_source_alias(self):
        with self.assertRaises(SafetyError):self.prepare(parent_folder_id='folder')
        with self.assertRaises(SafetyError):self.adapter.prepare({'operation':'create_folder','source_id':'selected','parent_folder_id':'selected','fields':{'title':'Child'}})

    def test_nested_folder_verification_requires_exact_source_and_physical_parent(self):
        item=self.adapter.prepare({'operation':'create_folder','source_id':'selected','parent_folder_id':'folder','fields':{'title':'Child'}})
        created={'id':'child','title':'Child','source_id':'selected','parent_id':'folder','shared':False}
        self.adapter.folders=lambda:[dict(created)]
        self.assertTrue(self.adapter.verify(item,{'id':'child','source_id':'selected'}))
        created['parent_id']='selected'
        self.assertFalse(self.adapter.verify(item,{'id':'child','source_id':'selected'}))
        created.update(parent_id='folder',source_id='other')
        self.assertFalse(self.adapter.verify(item,{'id':'child','source_id':'selected'}))

    def test_batched_private_ancestors_avoid_additional_helper_launches(self):
        parent={'id':'parent','title':'Parent','source_id':'selected','parent_id':'selected','shared':False}
        self.reader.folder['parent_id']='parent';calls=[]
        def read(action,**fields):
            calls.append((action,fields));return {'folder':dict(self.reader.folder),'ancestors':[dict(parent)]}
        self.reader.read=read;result=self.adapter.private_folder('folder','selected')
        self.assertEqual(result['private_ancestors'],[parent]);self.assertEqual(len(calls),1)
    def test_incomplete_shared_cyclic_and_extraneous_batched_ancestors_refuse(self):
        self.reader.folder['parent_id']='parent';parent={'id':'parent','title':'Parent','source_id':'selected','parent_id':'selected','shared':False}
        for values in ([],[dict(parent,shared=True)],[dict(parent,parent_id='parent')],[parent,parent],[parent,dict(parent,id='unrelated')],[dict(parent,source_id='other')]):
            self.reader.read=lambda action,**fields:{'folder':dict(self.reader.folder),'ancestors':values}
            with self.subTest(values=values),self.assertRaises(SafetyError):self.adapter.private_folder('folder','selected')

    def test_prepend_retains_title_and_every_existing_body_byte(self):
        before=self.reader.note['html'];item=self.edit('prepend_note',{'body':'First & second'})
        self.assertEqual(item['fields']['html'],'<div>Spec</div><div>First &amp; second</div><div>Body</div>')
        self.reader.note['html']=item['fields']['html']
        self.assertTrue(self.adapter.verify(item,{'id':'created','source_id':'selected'}))
        self.reader.note['html']=self.reader.note['html'].replace('<div>Body</div>','<p>Body</p>')
        self.assertFalse(self.adapter.verify(item,{'id':'created','source_id':'selected'}))
    def test_literal_replacement_preserves_markup_title_and_exact_review(self):
        self.reader.note['html']='<div>Spec</div>\n<div><b>Body</b> &amp; Body</div>'
        fields={'find':'Body','replacement':'Revised <literal>','expected_matches':2}
        item=self.edit('replace_text',fields)
        self.assertEqual(item['requested_edit'],fields)
        self.assertEqual(item['fields']['html'],'<div>Spec</div>\n<div><b>Revised &lt;literal&gt;</b> &amp; Revised &lt;literal&gt;</div>')
        self.reader.note['html']=item['fields']['html']
        self.assertTrue(self.adapter.verify(item,{'id':'created','source_id':'selected'}))
    def test_literal_replacement_requires_exact_count_and_preserves_title(self):
        for fields in ({'find':'Body','replacement':'New','expected_matches':2},
                       {'find':'Spec','replacement':'New','expected_matches':1},
                       {'find':'Body','replacement':'New','expected_matches':True},
                       {'find':'Body','replacement':'Body','expected_matches':1},
                       {'find':'','replacement':'New','expected_matches':1}):
            with self.subTest(fields=fields),self.assertRaises(SafetyError):self.edit('replace_text',fields)
    def test_entity_or_cross_format_matches_refuse_instead_of_rewriting_markup(self):
        for html,find in [('<div>Spec</div><div>A&amp;B</div>','A&B'),
                          ('<div>Spec</div><div>Bo<b>dy</b></div>','Body')]:
            self.reader.note['html']=html
            with self.subTest(html=html),self.assertRaises(SafetyError):
                self.edit('replace_text',{'find':find,'replacement':'New','expected_matches':1})
    def test_missing_separate_title_boundary_cannot_prepend_or_replace(self):
        self.reader.note['html']='<div>Spec Body</div>'
        for op,fields in [('prepend_note',{'body':'New'}),('replace_text',{'find':'Body','replacement':'New','expected_matches':1})]:
            with self.subTest(op=op),self.assertRaises(SafetyError):self.edit(op,fields)
    def test_literal_unicode_offsets_multiline_and_replacement_escaping(self):
        self.reader.note['html']='<div>Spec</div>\n<div>😀 token</div>\n<div>token</div>'
        item=self.edit('replace_text',{'find':'token','replacement':'one\ntwo & three','expected_matches':2})
        self.assertEqual(item['fields']['html'],'<div>Spec</div>\n<div>😀 one<br>two &amp; three</div>\n<div>one<br>two &amp; three</div>')

    def test_export_serialization_tolerates_only_block_separator_newlines(self):
        from icloud_for_agents.notes_edit import equivalent_export
        self.assertTrue(equivalent_export('<div>Title</div><div>Body</div>','<div>Title</div>\n<div>Body</div>\n'))
        for a,b in [('<div>A B</div>','<div>A  B</div>'),('<div><b>B</b></div>','<div><i>B</i></div>'),
                    ('<pre><div>A</div><div>B</div></pre>','<pre><div>A</div>\n<div>B</div></pre>'),
                    ('<div>A</div><span>B</span>','<div>A</div>\n<span>B</span>'),
                    ('<div>A</div><div>B</div>','<div>A</div> <div>B</div>')]:
            with self.subTest(a=a,b=b):self.assertFalse(equivalent_export(a,b))

    def test_empty_text_deletion_accepts_only_plain_empty_paragraph_placeholder(self):
        from icloud_for_agents.notes_edit import equivalent_export
        expected='<div>Title</div><div></div>';actual='<div>Title</div>\n<div><br></div>'
        self.assertFalse(equivalent_export(expected,actual))
        self.assertTrue(equivalent_export(expected,actual,empty_paragraph_placeholder=True))
        self.assertFalse(equivalent_export(actual,expected,empty_paragraph_placeholder=True))
        self.assertFalse(equivalent_export('<div><b></b></div>','<div><br></div>',empty_paragraph_placeholder=True))
