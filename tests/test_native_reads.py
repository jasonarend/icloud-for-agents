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
            calls.append(request);return {'protocol_version':1,'item':{'id':'synthetic','html':'<h1>Title</h1><p><b>Decision</b></p>','truncated':False}}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',side_effect=dispatch):
            result=NativeReader('notes').read('get',folder_id='folder',id='synthetic',representation='markdown')
        self.assertNotIn('representation',calls[0]);self.assertIn('**Decision**',result['item']['markdown']);self.assertFalse(result['item']['markdown_projection']['round_trip_safe']);self.assertIn('html',result['item'])
    def test_markdown_does_not_turn_missing_or_locked_html_into_an_empty_export(self):
        config={'notes':{'source_ids':['selected']}}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value={'protocol_version':1,'item':{'locked':True}}):
            result=NativeReader('notes').read('get',folder_id='folder',id='synthetic',representation='markdown')
        self.assertNotIn('markdown',result['item']);self.assertEqual(result['item']['markdown_projection']['status'],'unavailable')
    def test_representation_type_scope_and_value_refused_before_helper(self):
        config={name:{'source_ids':['selected']} for name in ('notes','reminders')}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps(config)),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',side_effect=AssertionError('No helper')):
            for name,action,value in [('notes','get','unknown'),('notes','get',True),('notes','query','native'),('reminders','get','markdown')]:
                with self.subTest(name=name,action=action,value=value),self.assertRaises(SafetyError):NativeReader(name).read(action,representation=value)

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
                    self.assertEqual(sum(n.startswith('notes_') for n in tools),35 if notes else 0)
                    self.assertEqual(sum(n.startswith('reminders_') for n in tools),26 if reminders else 0)
                    if notes:
                        with patch.dict(os.environ, {'ICLOUD_ENABLE_NOTES':'0'}), patch('subprocess.run',side_effect=AssertionError('No helper')):
                            with self.assertRaises(ToolError): tools['notes_sources'].fn()

    def test_config_validation_refuses_unbounded_or_unknown_authority(self):
        for config in ({'notes':{'source_ids':['same','same']}}, {'notes':{'source_ids':'*'}},
                       {'notes':{'folder_write_backend':'notes-shared'}}, {'reminders':{'folder_write_backend':'notes-shared','metadata_backend':'notes-store-readonly'}}, {'notes':{'folder_write_backend':'unknown'}}, {'notes':{'arbitrary_script':'x'}}, {'notes':{'helper_path':'relative'}},
                       {'reminders':{'helper_path':'relative'}}, {'notes':{'access_mode':'all'}}, []):
            with self.subTest(config=config), self.assertRaises(ValueError): validate_config(config)

    def test_implicit_page_defaults_are_sent_to_fixed_protocol(self):
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps({'notes':{'source_ids':['selected']}})), patch('platform.system',return_value='Darwin'), patch.object(NativeReader,'_dispatch',return_value={'items':[]}) as dispatch:
            NativeReader('notes').read('query',folder_id='folder')
            request=dispatch.call_args.args[0]
            self.assertEqual((request['limit'],request['offset']),(50,0))

    def test_search_finds_table_text_and_keeps_raw_html_internal(self):
        page={'items':[],'table_projection_candidates':[{'id':'note','title':'Synthetic','source_id':'selected',
            'folder_id':'folder','html':'<object><table><tr><td>A &amp; B</td></tr></table></object>','truncated':False}],
            'done':True,'next_offset':1}
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps({'notes':{'source_ids':['selected']}})),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=page):
            result=NativeReader('notes').read('search',text='A & B')
        self.assertEqual(result['items'][0]['excerpt'],'A & B')
        self.assertEqual(result['items'][0]['match_source'],'exported_table_text')
        self.assertNotIn('table_projection_candidates',result)
        self.assertNotIn('html',result['items'][0])

    def test_due_range_and_priority_filters_refuse_invalid_values_before_sdk(self):
        cases=[{'due_from':'2026-10-28','due_through':'2026-10-27','timezone':'UTC'},
               {'due_from':'2026-10-27'}, {'due_date':'2026-10-27','due_from':'2026-10-26','timezone':'UTC'},
               {'due_from':'2026-10-27T00:00','timezone':'UTC'}, {'priorities':[True]},
               {'priorities':[1,1]}, {'priorities':[]}, {'priorities':[3]}, {'include_overdue':True}]
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps({'reminders':{'source_ids':['selected']}})),patch('platform.system',return_value='Darwin'),patch('subprocess.run',side_effect=AssertionError('No SDK')):
            for fields in cases:
                with self.subTest(fields=fields),self.assertRaises(SafetyError):NativeReader('reminders').read('query',**fields)
    def test_valid_inclusive_range_and_priorities_are_forwarded_exactly(self):
        with self.env(ICLOUD_NATIVE_CONFIG=json.dumps({'reminders':{'source_ids':['selected']}})),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value={'items':[]}) as dispatch:
            NativeReader('reminders').read('query',due_from='2026-10-26',due_through='2026-10-28',timezone='America/Chicago',priorities=[0,1])
        request=dispatch.call_args.args[0]
        self.assertEqual((request['due_from'],request['due_through']),( '2026-10-26','2026-10-28'))
        self.assertEqual(request['priorities'],[0,1])
