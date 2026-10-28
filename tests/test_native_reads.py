"""Native reads: no live Apple access; enforce scope before subprocess dispatch."""
import asyncio
import importlib
import json
import os
import unittest
from unittest.mock import patch

from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_read import NativeReader, validate_config
from icloud_for_agents import server
from fastmcp.exceptions import ToolError

BODY_SEARCH_EVIDENCE={'plaintext_search_limit':5000000,'plaintext_search_unit':'utf16_code_units'}

class NativeReadTests(unittest.TestCase):
    def tearDown(self):
        importlib.reload(server)

    def env(self, **extra):
        env = {'ICLOUD_ENABLE_MAIL': '0', 'ICLOUD_ENABLE_CALENDAR': '0',
               'ICLOUD_ENABLE_CONTACTS': '0', 'ICLOUD_ENABLE_NOTES': '1',
               'ICLOUD_ENABLE_REMINDERS': '1', 'ICLOUD_MAIL_ACCESS': 'read-only'}
        env.update(extra)
        return patch.dict(os.environ, env, clear=True)

    def test_native_store_refusal_has_safe_separate_access_guidance(self):
        from icloud_for_agents.native_read import _confirmed_refusal
        request={'service':'notes','action':'apply'}
        failure=_confirmed_refusal('notes',request,{'error':'notes_model_refused','category':'notes_model_store_unavailable','mutation_dispatched':False,'diagnostic_stage':'resolve_exact_folder'})
        self.assertEqual(failure.evidence['reason'],'notes_model_store_unavailable')
        self.assertIn('Automation access is separate',str(failure));self.assertFalse(failure.evidence['mutation_dispatched'])
        injected=_confirmed_refusal('notes',request,{'error':'notes_model_refused','category':'untrusted arbitrary content','mutation_dispatched':False})
        self.assertEqual(injected.evidence['reason'],'helper_validation');self.assertNotIn('untrusted arbitrary content',str(injected))

    def test_notes_timeout_reports_specific_error_without_retry(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            helper=Path(directory)/'helper';helper.write_text('synthetic');helper.chmod(0o700)
            config={'notes':{'source_ids':['selected'],'helper_path':str(helper)}}
            with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch('icloud_for_agents.native_app.bundle_fingerprint',return_value='synthetic'),patch('icloud_for_agents.native_app.application_request',return_value={'error':'notes_read_timeout'}) as request:
                with self.assertRaisesRegex(SafetyError,'Notes read timed out.*no automatic retry'):
                    NativeReader('notes').read('search',text='',match_scope='title',locked=True)
                self.assertEqual(request.call_count,1)

    def test_default_reminder_list_is_scoped_and_older_helpers_fail_closed(self):
        config={'reminders':{'source_ids':['selected']}}
        valid={'protocol_version':1,'state':'available','list':{'id':'list','title':'List','source_id':'selected','writable':True,'color':None}}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=valid) as backend:
            self.assertEqual(NativeReader('reminders').read('default_list'),valid)
            self.assertEqual(backend.call_args.args[0]['source_ids'],['selected'])
            for invalid in ({'protocol_version':1},dict(valid,list=dict(valid['list'],source_id='other')),
                            dict(valid,list=dict(valid['list'],color={'red':10**400,'green':0,'blue':0,'alpha':1})),
                            {'protocol_version':1,'state':'outside_selected_scope','list':valid['list']}):
                backend.return_value=invalid
                with self.assertRaises(SafetyError):NativeReader('reminders').read('default_list')
            for state in ('unavailable','outside_selected_scope'):
                backend.return_value={'protocol_version':1,'state':state,'list':None}
                self.assertIsNone(NativeReader('reminders').read('default_list')['list'])

    def test_default_notes_folder_requires_exact_selected_source(self):
        config={'notes':{'source_ids':['selected']}}
        result={'protocol_version':1,'folder':{'id':'folder','source_id':'selected','parent_id':'selected','title':'Notes','shared':False,'role':'default_folder'}}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=result) as backend:
            self.assertEqual(NativeReader('notes').read('default_folder',source_id='selected'),result)
            backend.return_value=dict(result,request_id='local-request',nonprompting_event_count=2)
            self.assertEqual(NativeReader('notes').read('default_folder',source_id='selected'),result)
            with self.assertRaises(SafetyError):NativeReader('notes').read('default_folder',source_id='foreign')
            backend.return_value={'protocol_version':1,'folder':dict(result['folder'],source_id='foreign')}
            with self.assertRaises(SafetyError):NativeReader('notes').read('default_folder',source_id='selected')

    def test_section_discovery_requires_opt_in_and_exact_scoped_metadata(self):
        config={'reminders':{'source_ids':['selected'],'advanced_backend':'reminderkit','helper_path':'/synthetic/helper'}}
        section={'id':'11111111-1111-4111-8111-111111111111','title':'Synthetic'}
        result={'protocol_version':1,'source_id':'selected','list_id':'list','sections':[section], 'ordering_verified':False,'membership_verified':False,'data_mutated':False}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=result) as backend:
            self.assertEqual(NativeReader('reminders').read('list_sections',list_id='list'),result)
            for bad in (dict(result,source_id='other'),dict(result,list_id='other'),dict(result,ordering_verified=True),dict(result,sections=[section,section])):
                backend.return_value=bad
                with self.assertRaises(SafetyError):NativeReader('reminders').read('list_sections',list_id='list')
        config['reminders'].pop('advanced_backend')
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',side_effect=AssertionError('No helper')):
            with self.assertRaises(SafetyError):NativeReader('reminders').read('list_sections',list_id='list')

    def test_attachment_details_require_exact_scope_and_typed_fields(self):
        config={'notes':{'source_ids':['selected']}}
        metadata={'created':{'status':'available','value':None},'modified':{'status':'unavailable','value':None},'url':{'status':'available','value':'https://example.com'},'shared':{'status':'available','value':False}}
        row={'id':'attachment','name':None,'content_identifier':'cid:synthetic','note_id':'note','native_metadata':metadata}
        result={'protocol_version':1,'attachments':[row],'done':True,'next_offset':1,'content_downloaded':False,'metadata_complete':False,'metadata_requested':True,'source_id':'selected','folder_id':'folder','note_id':'note'}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=result) as backend:
            self.assertEqual(NativeReader('notes').read('attachments',folder_id='folder',id='note',limit=10,offset=0,include_attachment_metadata=True),result)
            with self.assertRaises(SafetyError):NativeReader('notes').read('attachments',folder_id='folder',include_attachment_metadata=True)
            for bad in (dict(result,source_id='foreign'),dict(result,note_id='other'),dict(result,metadata_requested=False),dict(result,next_offset=2),dict(result,attachments=[dict(row,native_metadata=dict(metadata,shared={'status':'available','value':1}))]),dict(result,attachments=[row,row])):
                backend.return_value=bad
                with self.assertRaises(SafetyError):NativeReader('notes').read('attachments',folder_id='folder',id='note',limit=10,offset=0,include_attachment_metadata=True)
            backend.return_value={'protocol_version':1,'attachments':[],'done':True,'locked':True,'content_available':False,'excluded_locked_note':True,'metadata_requested':True,'source_id':'selected','folder_id':'folder','note_id':'note'}
            self.assertTrue(NativeReader('notes').read('attachments',folder_id='folder',id='note',limit=10,offset=0,include_attachment_metadata=True)['locked'])

    def test_basic_attachment_inventory_requires_exact_scoped_evidence(self):
        config={'notes':{'source_ids':['selected']}}
        row={'id':'attachment','name':None,'content_identifier':None,'note_id':'note'}
        page={'protocol_version':1,'source_id':'selected','folder_id':'folder','note_id':'note','attachments':[row],
              'done':True,'next_offset':1,'content_downloaded':False,'metadata_complete':False}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=page) as backend:
            self.assertEqual(NativeReader('notes').read('attachments',folder_id='folder',id='note'),page)
            for invalid in ({**page,'source_id':'foreign'}, {**page,'folder_id':'other'}, {**page,'note_id':'other'},
                            {**page,'attachments':[{**row,'note_id':'other'}]}, {**page,'next_offset':2}, {**page,'attachments':[],'next_offset':0,'done':False},
                            {k:v for k,v in page.items() if k!='source_id'}):
                backend.return_value=invalid
                with self.subTest(invalid=invalid),self.assertRaises(SafetyError):NativeReader('notes').read('attachments',folder_id='folder',id='note')

    def test_list_feature_reads_require_scoped_typed_provider_flags(self):
        config={'reminders':{'source_ids':['selected'],'advanced_backend':'reminderkit','helper_path':'/synthetic/helper'}}
        result={'protocol_version':1,'source_id':'selected','list_id':'list','grocery_categorization':True,'grocery_shopping_session':False,'is_group':False,'shared':False,'shared_to_me':False,'can_be_shared':True,'sharing_metadata_complete':False,'list_type_verified':False,'data_mutated':False,'backend':'reminderkit-list-features-v2'}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=result) as backend:
            self.assertEqual(NativeReader('reminders').read('list_features',list_id='list'),result)
            for bad in (dict(result,source_id='foreign'),dict(result,list_id='other'),dict(result,grocery_categorization=1),dict(result,shared=1),dict(result,sharing_metadata_complete=True),dict(result,list_type_verified=True),dict(result,data_mutated=True),dict(result,extra='untrusted')):
                backend.return_value=bad
                with self.assertRaises(SafetyError):NativeReader('reminders').read('list_features',list_id='list')
        config['reminders'].pop('advanced_backend')
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',side_effect=AssertionError('No helper')):
            with self.assertRaises(SafetyError):NativeReader('reminders').read('list_features',list_id='list')

    def test_section_membership_distinguishes_reported_from_unknown(self):
        config={'reminders':{'source_ids':['selected'],'advanced_backend':'reminderkit','helper_path':'/synthetic/helper'}}
        metadata={'id':'task','source_id':'selected','list_id':'list','coverage':['section'],'section':None,'section_status':'provider_not_reported'}
        response={'protocol_version':1,'item':{'id':'task','source_id':'selected','list_id':'list','native_metadata':metadata}}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=response):
            self.assertIsNone(NativeReader('reminders').read('get',id='task',include_native_section=True)['item']['native_metadata']['section'])
            metadata.update(section={'id':'11111111-1111-4111-8111-111111111111','title':'Synthetic'},section_status='provider_reported')
            self.assertEqual(NativeReader('reminders').read('get',id='task',include_native_section=True)['item']['native_metadata']['section']['title'],'Synthetic')
            metadata['section']['id']='invalid'
            with self.assertRaises(SafetyError):NativeReader('reminders').read('get',id='task',include_native_section=True)

    def test_optional_native_store_status_is_owner_selected_and_read_only(self):
        config={'notes':{'source_ids':['selected'],'access_mode':'read-only','helper_path':'/synthetic/helper','metadata_backend':'notes-store-readonly','folder_write_backend':'notes-shared'}}
        calls=[]
        def dispatch(request,config):
            calls.append(request['action'])
            return {'protocol_version':1,'service':'notes','permission_granted':True} if request['action']=='status' else {'protocol_version':1,'store_read_access':'denied_or_unavailable','model_context_opened':False}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',side_effect=dispatch):
            result=NativeReader('notes').read('status')
        self.assertEqual(calls,['status','store_status']);self.assertEqual(result['native_store']['store_read_access'],'denied_or_unavailable')
        calls.clear();config['notes'].pop('folder_write_backend')
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',side_effect=dispatch):NativeReader('notes').read('status')
        self.assertEqual(calls,['status'])
    def test_optional_store_status_rechecks_disabled_access_between_steps(self):
        config={'notes':{'source_ids':['selected'],'helper_path':'/synthetic/helper','metadata_backend':'notes-store-readonly','folder_write_backend':'notes-shared'}}
        def dispatch(request,config):
            os.environ['ICLOUD_ENABLE_NOTES']='0'
            return {'protocol_version':1}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',side_effect=dispatch) as backend:
            with self.assertRaises(SafetyError):NativeReader('notes').read('status')
            self.assertEqual(backend.call_count,1)

    def test_markdown_is_local_read_projection_and_never_forwarded_to_helper(self):
        config={'notes':{'source_ids':['selected']}};calls=[]
        def dispatch(request,config):
            calls.append(request);return {'protocol_version':1,'item':{'id':'synthetic','source_id':'selected','folder_id':'folder','locked':False,'html':'<h1>Title</h1><p><b>Decision</b></p>','truncated':False}}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',side_effect=dispatch):
            result=NativeReader('notes').read('get',folder_id='folder',id='synthetic',representation='markdown')
        self.assertNotIn('representation',calls[0]);self.assertIn('**Decision**',result['item']['markdown']);self.assertFalse(result['item']['markdown_projection']['round_trip_safe']);self.assertIn('html',result['item'])
    def test_markdown_does_not_turn_missing_or_locked_html_into_an_empty_export(self):
        config={'notes':{'source_ids':['selected']}}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value={'protocol_version':1,'item':{'id':'synthetic','source_id':'selected','folder_id':'folder','locked':True,'content_available':False}}):
            result=NativeReader('notes').read('get',folder_id='folder',id='synthetic',representation='markdown')
        self.assertNotIn('markdown',result['item']);self.assertEqual(result['item']['markdown_projection']['status'],'unavailable')
    def test_exact_note_requires_selected_identity_even_when_locked(self):
        config={'notes':{'source_ids':['selected']}}
        item={'id':'synthetic','source_id':'selected','folder_id':'folder','locked':True,'content_available':False}
        invalid=[{**item,'source_id':'foreign'}, {**item,'folder_id':'other'}, {**item,'id':'other'},
                 {k:v for k,v in item.items() if k!='source_id'}, {**item,'content_available':True}]
        for value in invalid:
            with self.subTest(value=value),self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value={'item':value}),self.assertRaises(SafetyError):
                NativeReader('notes').read('get',folder_id='folder',id='synthetic')
    def test_representation_type_scope_and_value_refused_before_helper(self):
        config={name:{'source_ids':['selected']} for name in ('notes','reminders')}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',side_effect=AssertionError('No helper')):
            for name,action,value in [('notes','get','unknown'),('notes','get',True),('notes','query','native'),('reminders','get','markdown')]:
                with self.subTest(name=name,action=action,value=value),self.assertRaises(SafetyError):NativeReader(name).read(action,representation=value)

    def test_private_reminder_metadata_is_opt_in_and_preserves_exact_public_scope(self):
        config={'reminders':{'source_ids':['selected'],'helper_path':'/synthetic/helper','advanced_backend':'reminderkit'}};calls=[]
        def dispatch(request,config):
            calls.append(request);item={'id':'synthetic','source_id':'selected','list_id':'list'}
            coverage=[label for key,label in (('include_native_metadata','flagged'),('include_native_tags','tags'),('include_native_hierarchy','hierarchy'),('include_native_children','children')) if request.get(key)]
            if coverage:
                item['native_metadata']={'id':'synthetic','source_id':'selected','list_id':'list','coverage':coverage,'flagged':False,'flag_state':0,'tags':[],'tags_complete':True,'is_subtask':False,'parent_id':None,'has_subtasks':None,'hierarchy_complete':False,'children_status':'unavailable','child_ids':None,'children_complete':False,'children_fetch_status':'unavailable'}
            return {'protocol_version':1,'item':item}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',side_effect=dispatch):
            NativeReader('reminders').read('get',id='synthetic')
            NativeReader('reminders').read('get',id='synthetic',include_native_metadata=True)
            NativeReader('reminders').read('get',id='synthetic',include_native_tags=True)
            NativeReader('reminders').read('get',id='synthetic',include_native_hierarchy=True)
            NativeReader('reminders').read('get',id='synthetic',include_native_children=True)
        self.assertNotIn('include_native_metadata',calls[0]);self.assertIs(calls[1]['include_native_metadata'],True);self.assertEqual(calls[1]['source_ids'],['selected']);self.assertEqual(calls[1]['id'],'synthetic');self.assertNotIn('include_native_metadata',calls[2]);self.assertIs(calls[2]['include_native_tags'],True);self.assertIs(calls[3]['include_native_hierarchy'],True);self.assertIs(calls[4]['include_native_children'],True)
    def test_urgent_read_requires_opt_in_and_boolean_metadata(self):
        config={'reminders':{'source_ids':['selected'],'helper_path':'/synthetic/helper','advanced_backend':'reminderkit'}}
        result={'protocol_version':1,'item':{'id':'synthetic','source_id':'selected','list_id':'list','native_metadata':{'id':'synthetic','source_id':'selected','list_id':'list','coverage':['urgent'],'urgent':False}}}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=result):
            self.assertIs(NativeReader('reminders').read('get',id='synthetic',include_native_urgent=True)['item']['native_metadata']['urgent'],False)
            result['item']['native_metadata']['urgent']=1
            with self.assertRaises(SafetyError):NativeReader('reminders').read('get',id='synthetic',include_native_urgent=True)
            with self.assertRaises(SafetyError):NativeReader('reminders').read('get',id='synthetic',include_native_urgent=1)
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps({'reminders':{'source_ids':['selected']}})),patch('platform.system',return_value='Darwin'),self.assertRaises(SafetyError):NativeReader('reminders').read('get',id='synthetic',include_native_urgent=True)

    def test_private_metadata_defaults_and_other_service_or_action_refuse(self):
        config={name:{'source_ids':['selected']} for name in ('notes','reminders')}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',side_effect=AssertionError('No helper')):
            for name,action,value in [('reminders','get',True),('reminders','get',1),('notes','get',True),('reminders','query',True)]:
                with self.subTest(name=name,action=action,value=value),self.assertRaises(SafetyError):NativeReader(name).read(action,include_native_metadata=value)
                with self.subTest(name=name,action=action,value=value),self.assertRaises(SafetyError):NativeReader(name).read(action,include_native_tags=value)
                with self.subTest(name=name,action=action,value=value),self.assertRaises(SafetyError):NativeReader(name).read(action,include_native_hierarchy=value)
                with self.subTest(name=name,action=action,value=value),self.assertRaises(SafetyError):NativeReader(name).read(action,include_native_children=value)
    def test_urgent_filter_requires_opt_in_and_verified_matching_evidence(self):
        config={'reminders':{'source_ids':['selected'],'helper_path':'/synthetic/helper','advanced_backend':'reminderkit'}}
        evidence={'scanned':1,'matched':0,'observation_consistency':'separate_provider_reads'}
        result={'protocol_version':1,'items':[],'done':False,'next_offset':1,'pagination_consistency':'live_offset_may_shift','native_urgent_filter':evidence}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=result):
            self.assertEqual(NativeReader('reminders').read('query',urgent=True,limit=1)['items'],[])
            with self.assertRaises(SafetyError):NativeReader('reminders').read('query',urgent=1,limit=1)
            result.pop('native_urgent_filter')
            with self.assertRaises(SafetyError):NativeReader('reminders').read('query',urgent=True,limit=1)
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps({'reminders':{'source_ids':['selected']}})),patch('platform.system',return_value='Darwin'),self.assertRaises(SafetyError):NativeReader('reminders').read('query',urgent=True)

    def test_flag_filter_requires_opt_in_boolean_and_selected_scope(self):
        config={'reminders':{'source_ids':['selected'],'helper_path':'/synthetic/helper','advanced_backend':'reminderkit'}};calls=[]
        def dispatch(request,config):calls.append(request);return {'protocol_version':1,'items':[],'done':False,'next_offset':1,'pagination_consistency':'live_offset_may_shift','native_flag_filter':{'scanned':1,'matched':0,'observation_consistency':'separate_provider_reads'}}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',side_effect=dispatch):
            NativeReader('reminders').read('query',flagged=False,limit=1)
            self.assertIs(calls[0]['flagged'],False);self.assertEqual(calls[0]['source_ids'],['selected'])
            for name,action,value in [('reminders','query',1),('reminders','get',True),('notes','query',True)]:
                with self.subTest(name=name,action=action,value=value),self.assertRaises(SafetyError):NativeReader(name).read(action,flagged=value)
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps({'reminders':{'source_ids':['selected']}})),patch('platform.system',return_value='Darwin'),self.assertRaises(SafetyError):NativeReader('reminders').read('query',flagged=True)

    def test_tag_filter_requires_exact_bounded_scope_and_helper_evidence(self):
        config={'reminders':{'source_ids':['selected'],'helper_path':'/synthetic/helper','advanced_backend':'reminderkit'}}
        evidence={'scanned':1,'matched':0,'observation_consistency':'separate_provider_reads'}
        response={'items':[],'done':False,'next_offset':1,'pagination_consistency':'live_offset_may_shift','native_tag_filter':evidence}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=response) as dispatch:
            NativeReader('reminders').read('query',tag='Project',limit=1)
            self.assertEqual(dispatch.call_args.args[0]['tag'],'Project')
            for name,action,value in [('reminders','query',''),('reminders','query','x'*513),('reminders','query','bad\nname'),('reminders','get','Project'),('notes','query','Project')]:
                with self.subTest(name=name,action=action,value=value),self.assertRaises(SafetyError):NativeReader(name).read(action,tag=value)
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value={'items':[],'done':True}),self.assertRaises(SafetyError):
            NativeReader('reminders').read('query',tag='Project')
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps({'reminders':{'source_ids':['selected']}})),patch('platform.system',return_value='Darwin'),self.assertRaises(SafetyError):
            NativeReader('reminders').read('query',tag='Project')

    def test_old_or_unscoped_helper_cannot_silently_succeed_at_requested_metadata(self):
        config={'reminders':{'source_ids':['selected'],'helper_path':'/synthetic/helper','advanced_backend':'reminderkit'}}
        item={'id':'synthetic','source_id':'selected','list_id':'list'}
        for response in ({'item':item},{'item':{**item,'native_metadata':{'id':'synthetic','source_id':'foreign','list_id':'list','coverage':['children'],'child_ids':[],'children_complete':False}}},{'item':{**item,'native_metadata':{'id':'synthetic','source_id':'selected','list_id':'list','coverage':['children'],'children_complete':False,'children_fetch_status':'unavailable'}}}):
            with self.subTest(response=response),self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=response),self.assertRaises(SafetyError):
                NativeReader('reminders').read('get',id='synthetic',include_native_children=True)

    def test_old_or_unfiltered_helper_cannot_succeed_at_flagged_query(self):
        config={'reminders':{'source_ids':['selected'],'helper_path':'/synthetic/helper','advanced_backend':'reminderkit'}}
        responses=[{'items':[],'done':True}, {'items':[],'done':False,'next_offset':0,'native_flag_filter':{'scanned':0,'matched':0}}, {'items':[],'done':True,'next_offset':0,'native_flag_filter':{'scanned':1,'matched':0}},
                   {'items':[{'id':'x','source_id':'selected','list_id':'list'}],'done':True,'next_offset':1,'native_flag_filter':{'scanned':1,'matched':1}}]
        for response in responses:
            with self.subTest(response=response),self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=response),self.assertRaises(SafetyError):
                NativeReader('reminders').read('query',flagged=True)

    def test_recovery_list_validates_title_only_pages_and_scope(self):
        from copy import deepcopy
        root='11111111-1111-4111-8111-111111111111';config={'reminders':{'source_ids':['selected'],'helper_path':'/synthetic/helper','advanced_backend':'reminderkit'}}
        result={'protocol_version':1,'source_id':'selected','view':'provider_recently_deleted_roots','pagination_consistency':'live_offset_may_shift','data_mutated':False,'done':True,'next_offset':1,'items':[{'id':root,'source_id':'selected','title':'Synthetic'}]}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=result):
            self.assertEqual(NativeReader('reminders').read('recovery_list',source_id='selected'),result)
            for fields in ({'source_id':'foreign'},{'source_id':'selected','limit':21},{'source_id':'selected','offset':-1}):
                with self.assertRaises(SafetyError):NativeReader('reminders').read('recovery_list',**fields)
        for mutate in ('extra','wrong_source','duplicate','bad_offset','not_boolean'):
            bad=deepcopy(result)
            if mutate=='extra':bad['items'][0]['notes']='private body'
            if mutate=='wrong_source':bad['items'][0]['source_id']='foreign'
            if mutate=='duplicate':bad['items'].append(deepcopy(bad['items'][0]));bad['next_offset']=2
            if mutate=='bad_offset':bad['next_offset']=2
            if mutate=='not_boolean':bad['done']=1
            with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=bad),self.subTest(mutate=mutate),self.assertRaises(SafetyError):NativeReader('reminders').read('recovery_list',source_id='selected')

    def test_recovery_tree_validates_single_view_graph_and_scope(self):
        from copy import deepcopy
        root='11111111-1111-4111-8111-111111111111';child='22222222-2222-4222-8222-222222222222'
        config={'reminders':{'source_ids':['selected'],'helper_path':'/synthetic/helper','advanced_backend':'reminderkit'}}
        result={'protocol_version':1,'source_id':'selected','requested_id':child,'root_id':root,'view':'provider_recently_deleted','hierarchy_complete':False,'observation_consistency':'single_provider_view','title_observation_consistency':'separate_object_reads','data_mutated':False,'nodes':[{'id':root,'title':'Synthetic root','parent_id':None,'depth':0,'reported_child_ids':[child],'children_status':'provider_reported'},{'id':child,'title':'Synthetic child','parent_id':root,'depth':1,'reported_child_ids':[],'children_status':'provider_reported'}]}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=result):
            self.assertEqual(NativeReader('reminders').read('recovery_tree',id=child,source_id='selected'),result)
            with self.assertRaises(SafetyError):NativeReader('reminders').read('recovery_tree',id=child,source_id='foreign')
        for mutate in ('missing','duplicate','foreign_parent','complete','unverified','missing_title','title_consistency'):
            bad=deepcopy(result)
            if mutate=='missing':bad['nodes'].pop()
            if mutate=='duplicate':bad['nodes'].append(bad['nodes'][1])
            if mutate=='foreign_parent':bad['nodes'][1]['parent_id']=child
            if mutate=='complete':bad['hierarchy_complete']=True
            if mutate=='unverified':bad['observation_consistency']='separate_provider_reads'
            if mutate=='missing_title':del bad['nodes'][1]['title']
            if mutate=='title_consistency':bad['title_observation_consistency']='single_provider_view'
            with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=bad),self.subTest(mutate=mutate),self.assertRaises(SafetyError):
                NativeReader('reminders').read('recovery_tree',id=child,source_id='selected')

    def test_recovery_lookup_rejects_unselected_or_unverified_results(self):
        config={'reminders':{'source_ids':['selected'],'helper_path':'/synthetic/helper','advanced_backend':'reminderkit'}}
        response={'id':'task','source_id':'selected','found_as_root':True,'found_in_view':True,'parent_id':None,'recovery_root_id':'task','view':'provider_recently_deleted','reported_child_ids':None,'children_status':'unavailable','children_complete':False,'observation_consistency':'separate_provider_reads','data_mutated':False}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=response):
            self.assertTrue(NativeReader('reminders').read('recovery_lookup',id='task',source_id='selected')['found_as_root'])
            with self.assertRaises(SafetyError):NativeReader('reminders').read('recovery_lookup',id='task',source_id='foreign')
            with self.assertRaises(SafetyError):NativeReader('reminders').read('get',id='task',source_id='selected')
        for value in ({'items':[]},{**response,'source_id':'foreign'},{**response,'found_as_root':1},{**response,'data_mutated':True},{**response,'found_in_view':True,'found_as_root':False,'parent_id':None},{**response,'reported_child_ids':['x','x'],'children_status':'provider_reported'},{**response,'found_in_view':False,'found_as_root':False,'reported_child_ids':['x'],'children_status':'provider_reported'}):
            with self.subTest(value=value),self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=value),self.assertRaises(SafetyError):
                NativeReader('reminders').read('recovery_lookup',id='task',source_id='selected')

    def test_model_folder_role_requires_explicit_backend_and_exact_scope(self):
        config={'notes':{'source_ids':['selected','other'],'helper_path':'/synthetic/helper','metadata_backend':'notes-store-readonly','folder_write_backend':'notes-shared'}}
        result={'folder_id':'folder','source_id':'selected','role':'ordinary_non_system','backend':'notes-shared-folder-role-v1','data_mutated':False,'ancestor_count':0}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=result) as dispatch:
            NativeReader('notes').read('model_folder_role',folder_id='folder',source_id='selected')
            self.assertEqual(dispatch.call_args.args[0]['source_ids'],['selected'])
            dispatch.return_value={**result,'role':'system_trash'}
            self.assertEqual(NativeReader('notes').read('model_folder_role',folder_id='folder',source_id='selected')['role'],'system_trash')
            dispatch.return_value=result
            with self.assertRaises(SafetyError):NativeReader('notes').read('model_folder_role',folder_id='folder',source_id='foreign')
        for invalid in ({**result,'role':'special'},{**result,'data_mutated':True},{**result,'source_id':'other'},{**result,'ancestor_count':True}):
            with self.subTest(invalid=invalid),self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=invalid),self.assertRaises(SafetyError):
                NativeReader('notes').read('model_folder_role',folder_id='folder',source_id='selected')
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps({'notes':{'source_ids':['selected']}})),patch('platform.system',return_value='Darwin'),self.assertRaises(SafetyError):
            NativeReader('notes').read('model_folder_role',folder_id='folder',source_id='selected')
        for key in ('metadata_backend','folder_write_backend','helper_path'):
            incomplete={'notes':{k:v for k,v in config['notes'].items() if k!=key}}
            with self.subTest(missing=key),self.env(ICLOUD_NATIVE_CONFIG=json.dumps(incomplete)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',side_effect=AssertionError('No model access')),self.assertRaises(SafetyError):
                NativeReader('notes').read('model_folder_role',folder_id='folder',source_id='selected')

    def test_unknown_advanced_backends_are_not_configuration_authority(self):
        for config in ({'notes':{'advanced_backend':'reminderkit'}},{'reminders':{'advanced_backend':'unknown'}},{'reminders':{'advanced_backend':True}}):
            with self.subTest(config=config),self.assertRaises(ValueError):validate_config(config)

    def test_disabled_unknown_or_unscoped_refused_before_backend(self):
        with self.env(ICLOUD_ENABLE_NOTES='0'), patch('subprocess.run', side_effect=AssertionError('No helper')):
            with self.assertRaises(SafetyError): NativeReader('notes').read('sources')
        with self.env(), patch('subprocess.run', side_effect=AssertionError('No helper')), patch('platform.system', return_value='Darwin'):
            for action in ('get', 'query', 'folders', 'delete', 'request_access'):
                with self.subTest(action=action), self.assertRaises(SafetyError): NativeReader('notes').read(action)

    def test_disabled_global_and_native_modes_refuse_discovery(self):
        for extra in ({'ICLOUD_MAIL_ACCESS': 'disabled'}, {'ICLOUD_MAIL_ACCESS': 'unknown'},
                      {'ICLOUD_NATIVE_CONFIG': json.dumps({'notes': {'access_mode': 'disabled'}})}):
            with self.env(**extra), patch('subprocess.run', side_effect=AssertionError('No helper')):
                with self.assertRaises(SafetyError): NativeReader('notes').read('sources')

    def test_supported_actions_still_require_macos(self):
        with self.env(), patch('platform.system', return_value='Linux'), patch('subprocess.run', side_effect=AssertionError('No helper')):
            with self.assertRaises(SafetyError): NativeReader('notes').read('sources')

    def test_input_scope_cannot_be_overridden_and_boolean_limit_refused(self):
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps({'notes': {'source_ids': ['selected']}})), patch('platform.system', return_value='Darwin'), patch('subprocess.run', side_effect=AssertionError('No helper')):
            for fields in ({'source_ids':['other']}, {'limit':True}, {'offset':-1}, {'limit':101}, {'script':'delete everything'}):
                with self.subTest(fields=fields), self.assertRaises(SafetyError): NativeReader('notes').read('query', **fields)

    def test_helper_input_is_json_and_secret_environment_not_forwarded(self):
        observed = []
        def run(command, **kwargs):
            observed.append((command, kwargs['env'], json.loads(kwargs['input'])))
            kwargs['stdout'].write(json.dumps({'protocol_version':1,'sources':[]}).encode())
            from types import SimpleNamespace
            return SimpleNamespace(returncode=0)
        with self.env(SECRET='private', ICLOUD_NATIVE_CONFIG='{}'), patch('platform.system', return_value='Darwin'), patch('subprocess.run', side_effect=run):
            self.assertEqual(NativeReader('notes').read('sources')['sources'], [])
        command, env, request = observed[0]
        self.assertEqual(command[:3], ['/usr/bin/osascript','-l','JavaScript'])
        self.assertNotIn('SECRET', env)
        self.assertEqual(request, {'protocol_version':1,'action':'sources','source_ids':[]})

    def test_daily_filter_rejects_ambiguous_dates_and_missing_timezone_before_backend(self):
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps({'reminders':{'source_ids':['selected']}})), patch('platform.system', return_value='Darwin'), patch('subprocess.run',side_effect=AssertionError('No helper')):
            for fields in ({'due_date':'tomorrow','timezone':'America/Chicago'},
                           {'due_date':'2026-10-26'}, {'due_date':'2026-02-30','timezone':'UTC'},
                           {'due_date':'2026-10-26','timezone':'invalid'}):
                with self.subTest(fields=fields),self.assertRaises(SafetyError):
                    NativeReader('reminders').read('query',**fields)

    def test_permission_failure_is_actionable_without_leaking_stderr(self):
        from types import SimpleNamespace
        def run(command, **kwargs):
            kwargs['stderr'].write(b'private note title (-1743)')
            return SimpleNamespace(returncode=1)
        with self.env(), patch('platform.system', return_value='Darwin'), patch('subprocess.run', side_effect=run):
            with self.assertRaisesRegex(SafetyError, 'Automation permission') as caught:
                NativeReader('notes').read('sources')
            self.assertNotIn('private note', str(caught.exception))

    def test_response_protocol_errors_and_overflow_refused(self):
        def response(raw):
            def run(command, **kwargs):
                kwargs['stdout'].write(raw)
                from types import SimpleNamespace
                return SimpleNamespace(returncode=0)
            return run
        for raw in (b'not json', b'[]', b'{"protocol_version":2}', b'{"protocol_version":1,"error":"permission_required"}', b'x' * 2_000_001):
            with self.env(), patch('platform.system', return_value='Darwin'), patch('subprocess.run', side_effect=response(raw)):
                with self.assertRaises(SafetyError): NativeReader('notes').read('sources')

    def test_read_tools_register_only_selected_services_and_stale_tools_refuse(self):
        for notes in (False,True):
            for reminders in (False,True):
                with self.env(ICLOUD_ENABLE_NOTES=str(int(notes)),ICLOUD_ENABLE_REMINDERS=str(int(reminders))):
                    importlib.reload(server)
                    tools = {t.name:t for t in asyncio.run(server.mcp.list_tools())}
                    self.assertEqual(sum(n.startswith('notes_') for n in tools),44 if notes else 0)
                    self.assertEqual(sum(n.startswith('reminders_') for n in tools),34 if reminders else 0)
                    if notes:
                        with patch.dict(os.environ, {'ICLOUD_ENABLE_NOTES':'0'}), patch('subprocess.run',side_effect=AssertionError('No helper')):
                            with self.assertRaises(ToolError): tools['notes_sources'].fn()

    def test_config_validation_refuses_unbounded_or_unknown_authority(self):
        for config in ({'notes':{'source_ids':['same','same']}}, {'notes':{'source_ids':'*'}},
                       {'notes':{'folder_write_backend':'notes-shared'}}, {'reminders':{'folder_write_backend':'notes-shared','metadata_backend':'notes-store-readonly'}}, {'notes':{'folder_write_backend':'unknown'}}, {'notes':{'arbitrary_script':'x'}}, {'notes':{'helper_path':'relative'}},
                       {'reminders':{'helper_path':'relative'}}, {'notes':{'access_mode':'all'}}, []):
            with self.subTest(config=config), self.assertRaises(ValueError): validate_config(config)

    def test_implicit_page_defaults_are_sent_to_fixed_protocol(self):
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps({'notes':{'source_ids':['selected']}})), patch('platform.system',return_value='Darwin'), patch.object(NativeReader,'_dispatch',return_value={'items':[],'done':True,'next_offset':0,'pagination_consistency':'live_offset_may_shift'}) as dispatch:
            NativeReader('notes').read('query',folder_id='folder')
            request=dispatch.call_args.args[0]
            self.assertEqual((request['limit'],request['offset']),(50,0))

    def test_notes_folder_pages_require_selected_scope_and_progress(self):
        config={'notes':{'source_ids':['selected']}}
        row={'id':'folder','title':'Synthetic','source_id':'selected','parent_id':'selected','body':'private'}
        page={'folders':[row],'done':True,'next_offset':1,'pagination_consistency':'live_offset_may_shift','private':'private'}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=page) as backend:
            result=NativeReader('notes').read('folders',limit=1)
            self.assertNotIn('body',result['folders'][0]);self.assertNotIn('private',result)
            for value in ({**page,'folders':[{**row,'source_id':'foreign'}]}, {**page,'next_offset':2},
                          {**page,'folders':[],'next_offset':0,'done':False}, {**page,'folders':[{**row,'parent_id':None}]}):
                backend.return_value=value
                with self.subTest(value=value),self.assertRaises(SafetyError):NativeReader('notes').read('folders',limit=1)

    def test_notes_title_page_requires_scope_and_bounded_progress(self):
        config={'notes':{'source_ids':['selected']}}
        row={'id':'note','title':'Synthetic','source_id':'selected','folder_id':'folder','body':'private'}
        page={'items':[row],'done':True,'next_offset':1,'pagination_consistency':'live_offset_may_shift','private':'private'}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=page):
            result=NativeReader('notes').read('query',folder_id='folder',limit=1)
            self.assertNotIn('body',result['items'][0]);self.assertNotIn('private',result)
        invalid=[{**page,'items':[{**row,'source_id':'foreign'}]}, {**page,'items':[{**row,'folder_id':'other'}]},
                 {**page,'next_offset':2}, {**page,'items':[],'done':False,'next_offset':0}, {**page,'done':1},
                 {**page,'pagination_consistency':'stable'}]
        for value in invalid:
            with self.subTest(value=value),self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=value),self.assertRaises(SafetyError):
                NativeReader('notes').read('query',folder_id='folder',limit=1)

    def test_search_finds_table_text_and_keeps_raw_html_internal(self):
        page={'items':[],'table_projection_candidates':[{'id':'note','title':'Synthetic','source_id':'selected',
            'folder_id':'folder','locked':False,'html':'<object><table><tr><td>A &amp; B</td></tr></table></object>','truncated':False}],
            'done':True,'next_offset':1,**BODY_SEARCH_EVIDENCE}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps({'notes':{'source_ids':['selected']}})),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=page):
            result=NativeReader('notes').read('search',text='A & B')
        self.assertEqual(result['items'][0]['excerpt'],'A & B')
        self.assertEqual(result['items'][0]['match_source'],'exported_table_text')
        self.assertNotIn('table_projection_candidates',result)
        self.assertNotIn('html',result['items'][0])

    def test_notes_body_search_rejects_old_or_wrong_search_bounds(self):
        config={'notes':{'source_ids':['selected']}}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch') as backend:
            for value in ({'items':[]}, {'items':[],**BODY_SEARCH_EVIDENCE,'plaintext_search_limit':100000}, {'items':[],**BODY_SEARCH_EVIDENCE,'plaintext_search_unit':'bytes'}):
                backend.return_value=value
                with self.subTest(value=value),self.assertRaises(SafetyError):NativeReader('notes').read('search',text='needle')
            backend.return_value={'items':[],**BODY_SEARCH_EVIDENCE}
            self.assertEqual(NativeReader('notes').read('search',text='needle')['plaintext_search_limit'],5000000)

    def test_notes_search_always_checks_scope_and_page_bound(self):
        config={'notes':{'source_ids':['selected']}}
        row={'id':'note','title':'Synthetic','source_id':'selected','folder_id':'folder','locked':False,'excerpt':'test','content_complete':False}
        candidate={'id':'note','title':'Synthetic','source_id':'selected','folder_id':'folder','locked':False,'html':'<table><tr><td>test</td></tr></table>','truncated':False}
        pages=[{'items':[{**row,'source_id':'foreign'}]}, {'items':[row],'table_projection_candidates':[{**candidate,'folder_id':'other'}]},
               {'items':[row,row],'table_projection_candidates':[]}, {'items':[],'table_projection_candidates':[{**candidate,'source_id':'foreign'}]},
               {'items':[{**row,'locked':True,'excerpt':'private'}]}, {'items':[],'table_projection_candidates':[{**candidate,'locked':True}]},
               {'items':[],'table_projection_candidates':[{k:v for k,v in candidate.items() if k!='locked'}]}]
        for page in pages:
            with self.subTest(page=page),self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value={**page,**BODY_SEARCH_EVIDENCE}),self.assertRaises(SafetyError):
                NativeReader('notes').read('search',text='test',folder_id='folder',limit=1)
        def drift(*args):
            os.environ['ICLOUD_MAIL_ACCESS']='disabled'
            return {'items':[row],**BODY_SEARCH_EVIDENCE}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',side_effect=drift),self.assertRaises(SafetyError):
            NativeReader('notes').read('search',text='test',folder_id='folder')
        leaking={'items':[{**row,'html':'private'}], 'unavailable':[{'id':'note','category':'note_read_unavailable','body':'private'}], 'private_body':'private',**BODY_SEARCH_EVIDENCE}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=leaking):
            result=NativeReader('notes').read('search',text='test',folder_id='folder')
            self.assertNotIn('html',result['items'][0]);self.assertNotIn('body',result['unavailable'][0]);self.assertNotIn('private_body',result)

    def test_notes_attachment_filter_requires_evidence_and_excludes_locked_content(self):
        config={'notes':{'source_ids':['selected']}}
        candidate={'id':'note','title':'Synthetic','source_id':'selected','folder_id':'folder','locked':False,'has_attachments':True,'html':'<table><tr><td>needle</td></tr></table>','truncated':False}
        def page(**extra):return {'items':[],'table_projection_candidates':[dict(candidate)],'attachment_filter':True,**BODY_SEARCH_EVIDENCE,**extra}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=page()) as backend:
            result=NativeReader('notes').read('search',text='needle',has_attachments=True)
            self.assertTrue(result['attachment_filter']);self.assertTrue(result['items'][0]['has_attachments'])
            for value in ({'items':[]}, {**page(),'attachment_filter':False}, {**page(),'table_projection_candidates':[{**candidate,'has_attachments':False}]}, {**page(),'table_projection_candidates':[{**candidate,'locked':True}]}):
                backend.return_value=value
                with self.subTest(value=value),self.assertRaises(SafetyError):NativeReader('notes').read('search',text='needle',has_attachments=True)
            with self.assertRaises(SafetyError):NativeReader('notes').read('search',text='needle',has_attachments=1)

    def test_notes_shared_filter_requires_evidence_and_preserves_table_matches(self):
        config={'notes':{'source_ids':['selected']}}
        candidate={'id':'note','title':'Synthetic','source_id':'selected','folder_id':'folder','locked':False,'shared':True,'html':'<table><tr><td>needle</td></tr></table>','truncated':False}
        page={'items':[],'table_projection_candidates':[candidate],'shared_filter':True,**BODY_SEARCH_EVIDENCE}
        def fresh(**extra):return {**page,'table_projection_candidates':[dict(candidate)],**extra}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=fresh()) as backend:
            result=NativeReader('notes').read('search',text='needle',shared=True)
            self.assertTrue(result['shared_filter']);self.assertTrue(result['items'][0]['shared'])
            for value in (fresh(shared_filter=False),fresh(table_projection_candidates=[{**candidate,'shared':False}]),{'items':[]}):
                backend.return_value=value
                with self.subTest(value=value),self.assertRaises(SafetyError):NativeReader('notes').read('search',text='needle',shared=True)
            with self.assertRaises(SafetyError):NativeReader('notes').read('search',text='needle',shared=1)

    def test_notes_lock_filter_requires_evidence_and_matching_results(self):
        config={'notes':{'source_ids':['selected']}}
        row={'id':'note','title':'Synthetic','source_id':'selected','folder_id':'folder','locked':True,'excerpt':'','content_complete':False}
        page={'items':[row],'table_projection_candidates':[],'match_scope':'title','locked_filter':True}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=page) as backend:
            result=NativeReader('notes').read('search',text='',match_scope='title',locked=True)
            self.assertTrue(result['locked_filter']);self.assertTrue(result['items'][0]['locked'])
            for value in ({'items':[row],'match_scope':'title'}, {**page,'locked_filter':False}, {**page,'items':[{**row,'locked':False}]}):
                backend.return_value=value
                with self.subTest(value=value),self.assertRaises(SafetyError):NativeReader('notes').read('search',text='',match_scope='title',locked=True)
            with self.assertRaises(SafetyError):NativeReader('notes').read('search',text='',match_scope='title',locked=1)

    def test_notes_creation_filter_requires_evidence_and_preserves_projection(self):
        config={'notes':{'source_ids':['selected']}}
        fields={'text':'needle','created_from':'2026-01-01T00:00:00Z','created_before':'2026-01-02T00:00:00Z'}
        evidence={'from':fields['created_from'],'before':fields['created_before'],'semantics':'utc_inclusive_from_exclusive_before'}
        candidate={'id':'note','title':'Synthetic','source_id':'selected','folder_id':'folder','locked':False,'html':'<table><tr><td>needle</td></tr></table>','truncated':False,'created':'2026-01-01T00:00:00.000Z'}
        def page(**extra):return {'items':[],'table_projection_candidates':[dict(candidate)],'created_filter':evidence,**BODY_SEARCH_EVIDENCE,**extra}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=page()) as backend:
            result=NativeReader('notes').read('search',**fields)
            self.assertEqual(result['items'][0]['created'],candidate['created'])
            for invalid in ({'items':[]}, {**page(),'created_filter':None}, {**page(),'table_projection_candidates':[{**candidate,'created':'2026-01-02T00:00:00.000Z'}]}):
                backend.return_value=invalid
                with self.subTest(invalid=invalid),self.assertRaises(SafetyError):NativeReader('notes').read('search',**fields)
            with self.assertRaises(SafetyError):NativeReader('notes').read('search',text='needle',created_from='2026-02-30T00:00:00Z')

    def test_notes_modification_filter_rejects_old_helpers_and_out_of_range_rows(self):
        config={'notes':{'source_ids':['selected']}}
        fields={'text':'table','modified_from':'2026-10-27T12:00:00Z','modified_before':'2026-10-27T13:00:00Z'}
        filter_info={'from':fields['modified_from'],'before':fields['modified_before'],'semantics':'utc_inclusive_from_exclusive_before'}
        candidate={'id':'note','title':'Synthetic','source_id':'selected','folder_id':'folder','locked':False,'html':'<table><tr><td>table</td></tr></table>','truncated':False,'modified':'2026-10-27T12:00:00.000Z'}
        page={'items':[],'table_projection_candidates':[candidate],'modified_filter':filter_info,'done':True,'next_offset':1,**BODY_SEARCH_EVIDENCE}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=page):
            result=NativeReader('notes').read('search',**fields)
            self.assertEqual(result['items'][0]['modified'],candidate['modified'])
        for invalid in ({'items':[]}, {**page,'table_projection_candidates':[{**candidate,'modified':'2026-10-27T13:00:00.000Z'}]}, {**page,'items':[{'modified':None}]}, {**page,'table_projection_candidates':[{**candidate,'source_id':'foreign'}]}):
            with self.subTest(invalid=invalid),self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=invalid),self.assertRaises(SafetyError):
                NativeReader('notes').read('search',**fields)
        def drift(*args):
            os.environ['ICLOUD_MAIL_ACCESS']='disabled'
            return {**page,'table_projection_candidates':[candidate]}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',side_effect=drift),self.assertRaises(SafetyError):
            NativeReader('notes').read('search',**fields)
        for invalid in ({'modified_from':'2026-02-30T00:00:00Z'}, {'modified_from':'2026-10-27T12:00:00+00:00'}, {'modified_from':'2026-10-27T13:00:00Z','modified_before':'2026-10-27T12:00:00Z'}):
            with self.subTest(invalid=invalid),self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',side_effect=AssertionError('No helper')),self.assertRaises(SafetyError):
                NativeReader('notes').read('search',text='table',**invalid)

    def test_title_only_search_rejects_old_helpers_body_and_scope_drift(self):
        config={'notes':{'source_ids':['selected']}}
        row={'id':'note','title':'Synthetic Title','source_id':'selected','folder_id':'folder','excerpt':'','locked':False,'content_complete':False}
        def page(**extra):return {'items':[dict(row)],'table_projection_candidates':[],'match_scope':'title',**extra}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=page()) as dispatch:
            value=NativeReader('notes').read('search',text='Title',folder_id='folder',match_scope='title')
            self.assertEqual(value['items'][0]['id'],'note');self.assertEqual(dispatch.call_args.args[0]['match_scope'],'title')
        leaking={**page(),'private_body':'secret','items':[{**row,'html':'secret'}],'unavailable':[{'id':'note','category':'note_read_unavailable','body':'secret'}]}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=leaking):
            value=NativeReader('notes').read('search',text='Title',folder_id='folder',match_scope='title')
            self.assertNotIn('private_body',value);self.assertNotIn('html',value['items'][0]);self.assertNotIn('body',value['unavailable'][0])
        bounded=page(modified_filter={'from':'2026-10-27T12:00:00Z','before':'2026-10-27T13:00:00Z','semantics':'utc_inclusive_from_exclusive_before'})
        bounded['items'][0]['modified']='2026-10-27T12:30:00.000Z'
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=bounded):
            value=NativeReader('notes').read('search',text='Title',folder_id='folder',match_scope='title',modified_from='2026-10-27T12:00:00Z',modified_before='2026-10-27T13:00:00Z')
            self.assertEqual(value['items'][0]['modified'],'2026-10-27T12:30:00.000Z')
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=page()):
            self.assertEqual(NativeReader('notes').read('search',text='',folder_id='folder',match_scope='title')['items'][0]['id'],'note')
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',side_effect=AssertionError('No helper')),self.assertRaises(SafetyError):
            NativeReader('notes').read('search',text='',folder_id='folder')
        for invalid in ({'items':[]}, {**page(),'table_projection_candidates':[{'html':'private'}]}, {**page(),'items':[{**row,'excerpt':'body'}]}, {**page(),'items':[{**row,'folder_id':'other'}]}):
            with self.subTest(invalid=invalid),self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=invalid),self.assertRaises(SafetyError):
                NativeReader('notes').read('search',text='Title',folder_id='folder',match_scope='title')

    def test_due_range_and_priority_filters_refuse_invalid_values_before_sdk(self):
        cases=[{'due_from':'2026-10-28','due_through':'2026-10-27','timezone':'UTC'},
               {'due_from':'2026-10-27'}, {'due_date':'2026-10-27','due_from':'2026-10-26','timezone':'UTC'},
               {'due_from':'2026-10-27T00:00','timezone':'UTC'}, {'priorities':[True]},
               {'priorities':[1,1]}, {'priorities':[]}, {'priorities':[3]}, {'include_overdue':True}]
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps({'reminders':{'source_ids':['selected']}})),patch('platform.system',return_value='Darwin'),patch('subprocess.run',side_effect=AssertionError('No SDK')):
            for fields in cases:
                with self.subTest(fields=fields),self.assertRaises(SafetyError):NativeReader('reminders').read('query',**fields)
    def test_reminder_lists_require_selected_scope_and_bounded_progress(self):
        config={'reminders':{'source_ids':['selected']}}
        row={'id':'list','title':'Synthetic','source_id':'selected','writable':True,'color':None,'private':'private'}
        page={'lists':[row],'done':True,'next_offset':1,'pagination_consistency':'live_offset_may_shift','private':'private'}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=page) as backend:
            result=NativeReader('reminders').read('lists',limit=1)
            self.assertNotIn('private',result);self.assertNotIn('private',result['lists'][0])
            for value in ({**page,'lists':[{**row,'source_id':'foreign'}]}, {**page,'lists':[{**row,'writable':1}]},
                          {**page,'lists':[{**row,'color':{'red':float('nan'),'green':0,'blue':0,'alpha':1}}]},
                          {**page,'lists':[{**row,'color':{'red':10**400,'green':0,'blue':0,'alpha':1}}]},
                          {**page,'next_offset':2}, {**page,'lists':[],'next_offset':0,'done':False}):
                backend.return_value=value
                with self.subTest(value=value),self.assertRaises(SafetyError):NativeReader('reminders').read('lists',limit=1)

    def test_exact_reminder_requires_selected_source_and_identity(self):
        config={'reminders':{'source_ids':['selected']}}
        item={'id':'task','source_id':'selected','list_id':'list'}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value={'item':item}) as backend:
            self.assertEqual(NativeReader('reminders').read('get',id='task')['item'],item)
            for invalid in ({**item,'source_id':'foreign'}, {**item,'id':'other'}, {**item,'list_id':''}, {k:v for k,v in item.items() if k!='source_id'}):
                backend.return_value={'item':invalid}
                with self.subTest(invalid=invalid),self.assertRaises(SafetyError):NativeReader('reminders').read('get',id='task')

    def test_reminder_pages_require_selected_scope_and_progress(self):
        config={'reminders':{'source_ids':['selected']}}
        row={'id':'task','source_id':'selected','list_id':'list','completed':False}
        page={'items':[row],'done':True,'next_offset':1,'pagination_consistency':'live_offset_may_shift'}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=page) as backend:
            self.assertEqual(NativeReader('reminders').read('query',list_id='list',limit=1)['items'],[row])
            for value in ({**page,'items':[{**row,'source_id':'foreign'}]}, {**page,'items':[{**row,'list_id':'other'}]},
                          {**page,'next_offset':2}, {**page,'items':[],'next_offset':0,'done':False}, {**page,'done':1}):
                backend.return_value=value
                with self.subTest(value=value),self.assertRaises(SafetyError):NativeReader('reminders').read('query',list_id='list',limit=1)

    def test_reminder_pages_enforce_requested_completion_and_priority(self):
        config={'reminders':{'source_ids':['selected']}}
        row={'id':'task','source_id':'selected','list_id':'list','completed':False,'priority':1}
        page={'items':[row],'done':True,'next_offset':1,'pagination_consistency':'live_offset_may_shift'}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=page) as backend:
            self.assertEqual(NativeReader('reminders').read('query',completed=False,priorities=[1])['items'],[row])
            for invalid in ({**row,'completed':True}, {**row,'completed':0}, {**row,'priority':5}, {**row,'priority':True}):
                backend.return_value={**page,'items':[invalid]}
                with self.subTest(invalid=invalid),self.assertRaises(SafetyError):NativeReader('reminders').read('query',completed=False,priorities=[1])

    def test_completion_range_requires_evidence_and_matching_scoped_items(self):
        from datetime import datetime,timezone
        start='2026-10-27T00:00:00Z';end='2026-10-28T00:00:00Z';ts=datetime(2026,10,27,tzinfo=timezone.utc).timestamp()
        evidence={'from':start,'before':end,'semantics':'utc_inclusive_from_exclusive_before'}
        row={'id':'task','source_id':'selected','list_id':'list','completed':True,'completion_date':ts}
        result={'protocol_version':1,'items':[row],'done':True,'next_offset':1,'pagination_consistency':'live_offset_may_shift','completion_filter':evidence}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps({'reminders':{'source_ids':['selected']}})),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=result) as backend:
            self.assertEqual(NativeReader('reminders').read('query',completed_from=start,completed_before=end)['items'],[row])
            for invalid in ({'completed_from':end,'completed_before':start},{'completed_from':start,'completed':False},{'completed_from':'2026-10-27'},{'completed_from':start,'completed_before':start}):
                with self.subTest(invalid=invalid),self.assertRaises(SafetyError):NativeReader('reminders').read('query',**invalid)
            for bad in (dict(result,completion_filter=None),dict(result,items=[dict(row,source_id='foreign')]),dict(result,items=[dict(row,completed=False)]),dict(result,items=[dict(row,completion_date=ts+86400)]),dict(result,items=[dict(row,completion_date=True)]),dict(result,items=[dict(row,completion_date=float('nan'))]),dict(result,items=[dict(row,completion_date=10**400)])):
                backend.return_value=bad
                with self.assertRaises(SafetyError):NativeReader('reminders').read('query',completed_from=start,completed_before=end)

    def test_valid_inclusive_range_and_priorities_are_forwarded_exactly(self):
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps({'reminders':{'source_ids':['selected']}})),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value={'items':[],'done':True,'next_offset':0,'pagination_consistency':'live_offset_may_shift'}) as dispatch:
            NativeReader('reminders').read('query',due_from='2026-10-26',due_through='2026-10-28',timezone='America/Chicago',priorities=[0,1])
        request=dispatch.call_args.args[0]
        self.assertEqual((request['due_from'],request['due_through']),( '2026-10-26','2026-10-28'))
        self.assertEqual(request['priorities'],[0,1])
