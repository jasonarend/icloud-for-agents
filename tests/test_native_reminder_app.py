"""Bundled Reminders use app identity without losing admitted-write evidence."""
import asyncio
import json
import os
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import NativeObjectNotFound, SafetyError, digest
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.native_read import NativeReader, register_read_tools
from icloud_for_agents.native_reminder_exchange import inspect
from icloud_for_agents.native_reminders import RemindersCreateAdapter
from test_native_reminder_exchange import Adapter


class ReminderAppTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.bundle=self.root/'Helper.app'
        self.helper=self.bundle/'Contents/MacOS/icloud-agents-native'
        self.helper.parent.mkdir(parents=True);self.helper.write_text('synthetic');self.helper.chmod(0o700)
        (self.bundle/'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleExecutable':self.helper.name,'CFBundleIdentifier':'com.jasonarend.icloud-for-agents.native',
            'CFBundlePackageType':'APPL','NSRemindersFullAccessUsageDescription':'Synthetic purpose'}))
        resources=self.bundle/'Contents/Resources';resources.mkdir()
        for name in ('notes.js','notes-write.js'):(resources/name).write_text('synthetic')
        self.store=PlanStore(self.root/'ledger')
        env={'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCESS':'write-enabled',
             'ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_APPROVAL_POLICY':'trusted-client',
             'ICLOUD_RUNTIME_ROOT':str(self.store.root),
             'ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'helper_path':str(self.helper),
                 'source_ids':['selected'],'access_mode':'write-enabled'}})}
        p=patch.dict(os.environ,env,clear=True);p.start();self.addCleanup(p.stop)
        p=patch('icloud_for_agents.native_app.exchange_base',return_value=self.root/'app-support')
        p.start();self.addCleanup(p.stop)
        self.reader=NativeReader('reminders');self.config={'helper_path':str(self.helper)}

    def launcher(self,response,code=0):
        def launch(command,**kwargs):
            self.assertEqual(command[:3],['/usr/bin/open','-W','-n'])
            self.assertEqual(command[3],str(self.bundle))
            self.assertEqual(command[-2],'--ipc-directory')
            self.assertNotIn('input',kwargs)
            directory=Path(command[-1])
            self.last_request_id=directory.name
            request=json.loads((directory/'request.json').read_text())
            self.assertEqual(request['service'],'reminders')
            if response is not None:
                result=directory/'result.json';result.write_text(json.dumps(dict(response,request_id=directory.name)))
                result.chmod(0o600)
            return SimpleNamespace(returncode=code)
        return launch

    def test_status_reads_and_consent_use_app_route_once(self):
        for action in ('status','lists','request_access'):
            with self.subTest(action=action),patch('subprocess.run',side_effect=self.launcher({'protocol_version':1,'access':'full_access'})) as run:
                response=self.reader._dispatch({'protocol_version':1,'action':action,'source_ids':['selected']},self.config)
                self.assertEqual(response['access'],'full_access');self.assertEqual(run.call_count,1)
                self.assertEqual(run.call_args.kwargs['timeout'],75 if action=='request_access' else 40)

    def test_permission_refusal_and_missing_item_do_not_retry_or_fall_back(self):
        for action,code,exception in (('lists','permission_required',SafetyError),('get','not_found_in_scope',NativeObjectNotFound)):
            with self.subTest(action=action),patch('subprocess.run',side_effect=self.launcher({'protocol_version':1,'error':code})) as run:
                with self.assertRaises(exception):self.reader._dispatch({'protocol_version':1,'action':action},self.config)
                self.assertEqual(run.call_count,1)
        with patch('subprocess.run',side_effect=subprocess.TimeoutExpired('open',40)) as run:
            with self.assertRaisesRegex(SafetyError,'inspect pending request status'):
                self.reader._dispatch({'protocol_version':1,'action':'lists'},self.config)
            self.assertEqual(run.call_count,1)

    def test_mutation_outcomes_keep_exact_evidence_and_uncertainty(self):
        cases=[({'protocol_version':1,'result':{'id':'task','source_id':'selected'}},0,'applied',True),
               ({'protocol_version':1,'error':'invalid_mutation','mutation_dispatched':False},0,'refused_without_dispatch',True),
               ({'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True},0,'failed_or_uncertain',True),
               ({'protocol_version':1,'result':{'id':'task','source_id':'selected'}},1,'failed_or_uncertain',False),
               (None,1,'failed_or_uncertain',False),
               ({'protocol_version':2,'result':{'id':'task'}},0,'failed_or_uncertain',False)]
        for index,(response,code,status,terminal) in enumerate(cases):
            with self.subTest(status=status,index=index):
                store=PlanStore(self.root/str(index));engine=NativeChanges('reminders','synthetic@example.com',Adapter(self.helper),store)
                plan=engine.prepare([{'operation':'update_reminder','source_id':'selected','id':'task','fields':{'notes':'Synthetic'}}])
                with patch('subprocess.run',side_effect=self.launcher(response,code)) as run:
                    self.assertEqual(engine.execute(plan['plan_id'])['status'],status)
                self.assertEqual(run.call_count,1)
                self.assertTrue(store.path(plan['plan_id'],'.native-reminder-request-0').exists())
                self.assertEqual(store.path(plan['plan_id'],'.native-reminder-response-0').exists(),terminal)
                if terminal:
                    record=store.read(store.path(plan['plan_id'],'.native-reminder-response-0'))
                    self.assertEqual(record['response_sha256'],digest(dict(response,request_id=self.last_request_id)))
                evidence=inspect(store,store.read(store.path(plan['plan_id'],'.json')))
                self.assertEqual(evidence['terminal_response'],'recorded' if terminal else 'not_recorded')
                if status=='failed_or_uncertain':self.assertFalse(store.operation_settled(plan['plan_id']))
                with self.assertRaises(SafetyError):engine.execute(plan['plan_id'])

    def test_bundle_transport_and_metadata_are_bound_to_backend_identity(self):
        adapter=RemindersCreateAdapter();before=adapter.backend_id
        self.assertTrue(before.startswith('eventkit-app-v2:'))
        plist=self.bundle/'Contents/Info.plist';info=plistlib.loads(plist.read_bytes())
        info['NSRemindersFullAccessUsageDescription']='Changed purpose';plist.write_bytes(plistlib.dumps(info))
        self.assertNotEqual(adapter.backend_id,before)
        (self.bundle/'Contents/Resources/notes.js').chmod(0o777)
        with self.assertRaises(SafetyError):adapter.backend_id

    def test_existing_plan_cannot_cross_changed_bundle_identity(self):
        class BundleAdapter(Adapter):
            @property
            def backend_id(self):return RemindersCreateAdapter().backend_id
        engine=NativeChanges('reminders','synthetic@example.com',BundleAdapter(self.helper),self.store)
        plan=engine.prepare([{'operation':'update_reminder','source_id':'selected','id':'task','fields':{'notes':'Synthetic'}}])
        (self.bundle/'Contents/Resources/notes.js').write_text('changed bundle')
        with patch('subprocess.run',side_effect=AssertionError('No execution')),self.assertRaises(SafetyError):
            engine.execute(plan['plan_id'])
        self.assertFalse(self.store.path(plan['plan_id'],'.started').exists())

    def test_same_executable_bytes_have_different_direct_and_app_identity(self):
        app_identity=RemindersCreateAdapter().backend_id
        standalone=self.root/'standalone';standalone.write_bytes(self.helper.read_bytes());standalone.chmod(0o700)
        config={'reminders':{'helper_path':str(standalone),'source_ids':['selected'],'access_mode':'write-enabled'}}
        with patch.dict(os.environ,{'ICLOUD_NATIVE_CONFIG':json.dumps(config)}):
            direct_identity=RemindersCreateAdapter().backend_id
        self.assertTrue(direct_identity.startswith('eventkit-creation-v1:'))
        self.assertNotEqual(app_identity,direct_identity)

    def test_invalid_app_bundle_cannot_fall_back_to_direct_execution(self):
        (self.bundle/'Contents/Info.plist').unlink()
        with patch('subprocess.run',side_effect=AssertionError('No execution')),self.assertRaises(SafetyError):
            self.reader._dispatch({'protocol_version':1,'action':'status'},self.config)

    def test_strict_public_read_schemas_accept_validated_app_envelope(self):
        source='11111111-1111-4111-8111-111111111111'
        list_id='22222222-2222-4222-8222-222222222222'
        item='33333333-3333-4333-8333-333333333333'
        attachment='44444444-4444-4444-8444-444444444444'
        config={'reminders':{'helper_path':str(self.helper),'source_ids':[source],
                             'access_mode':'read-only','advanced_backend':'reminderkit'}}
        cases=[
            ('default_list',{}, {'protocol_version':1,'state':'available','list':{
                'id':list_id,'title':'Synthetic','source_id':source,'writable':True,'color':None}}),
            ('list_features',{'list_id':list_id}, {'protocol_version':1,'source_id':source,'list_id':list_id,
                'grocery_categorization':False,'grocery_shopping_session':False,'is_group':False,
                'shared':False,'shared_to_me':False,'can_be_shared':True,'sharing_metadata_complete':False,
                'list_type_verified':False,'data_mutated':False,'backend':'reminderkit-list-features-v2'}),
            ('attachment_file',{'id':item,'attachment_id':attachment}, {'protocol_version':1,'id':item,
                'source_id':source,'list_id':list_id,'attachment_id':attachment,'kind':'file',
                'uti':'public.plain-text','file_leaf':'a'*128+'.txt','file_size':12,
                'data_mutated':False,'download_requested':False}),
            ('group_inventory',{'source_id':source}, {'protocol_version':1,'source_id':source,
                'groups':[],'account_order':[list_id],'data_mutated':False,
                'backend':'reminderkit-root-group-inventory-v1'}),
        ]
        with patch.dict(os.environ,{'ICLOUD_NATIVE_CONFIG':json.dumps(config),'ICLOUD_MAIL_ACCESS':'read-only'}),patch('platform.system',return_value='Darwin'):
            server=FastMCP('Synthetic strict read schemas');register_read_tools(server)
            for code in (0,1):
                for action,fields,response in cases:
                    with self.subTest(action=action,code=code),patch('subprocess.run',side_effect=self.launcher(response,code)) as run:
                        result=self.reader.read(action,**fields)
                        self.assertEqual(result,response);self.assertNotIn('request_id',result)
                        self.assertEqual(run.call_count,1)
                    if action!='attachment_file':
                        with patch('subprocess.run',side_effect=self.launcher(response,code)):
                            public=asyncio.run(server.call_tool('reminders_'+action,fields)).structured_content
                        self.assertEqual(public,dict(response,**({'transport_warning':'launcher_nonzero_result_received'} if code else {})))

    def test_public_read_does_not_ignore_mismatched_app_request_identity(self):
        response={'protocol_version':1,'state':'unavailable','list':None}
        launch=self.launcher(response)
        def wrong_nonce(command,**kwargs):
            process=launch(command,**kwargs);directory=Path(command[-1])
            (directory/'result.json').write_text(json.dumps(dict(response,request_id='unrelated-request')))
            return process
        with patch('platform.system',return_value='Darwin'),patch('subprocess.run',side_effect=wrong_nonce) as run:
            with self.assertRaisesRegex(SafetyError,'inspect pending request status'):
                self.reader.read('default_list')
            self.assertEqual(run.call_count,1)

    def test_nonzero_completed_lists_result_is_publicly_marked_without_replay(self):
        response={'protocol_version':1,'lists':[{'id':'list','title':'Synthetic','source_id':'selected',
                    'writable':True,'color':None}],'done':True,'next_offset':1,
                  'pagination_consistency':'live_offset_may_shift'}
        server=FastMCP('Synthetic Reminders');register_read_tools(server)
        with patch('platform.system',return_value='Darwin'),patch('subprocess.run',side_effect=self.launcher(response,1)) as run:
            result=asyncio.run(server.call_tool('reminders_lists',{'limit':1,'offset':0})).structured_content
        self.assertEqual(result,dict(response,transport_warning='launcher_nonzero_result_received'))
        self.assertNotIn('request_id',result);self.assertEqual(run.call_count,1)

    def test_nonzero_read_still_requires_complete_exact_private_result(self):
        for payload in (None,'not json',json.dumps({'protocol_version':1,'request_id':'wrong'}),
                        json.dumps({'protocol_version':2,'request_id':'wrong'})):
            def launch(command,**kwargs):
                if payload is not None:
                    result=Path(command[-1])/'result.json';result.write_text(payload);result.chmod(0o600)
                return SimpleNamespace(returncode=1)
            with self.subTest(payload=payload),patch('subprocess.run',side_effect=launch) as run:
                with self.assertRaises(SafetyError):self.reader._dispatch({'protocol_version':1,'action':'lists'},self.config)
                self.assertEqual(run.call_count,1)

    def test_nonzero_consent_and_unknown_actions_remain_refused(self):
        for action in ('request_access','unknown'):
            with self.subTest(action=action),patch('subprocess.run',side_effect=self.launcher({'protocol_version':1,'granted':True},1)) as run:
                with self.assertRaisesRegex(SafetyError,'Native application launch failed'):
                    self.reader._dispatch({'protocol_version':1,'action':action},self.config)
                self.assertEqual(run.call_count,1)

    def test_nonzero_read_error_and_scope_refusal_are_not_success(self):
        server=FastMCP('Synthetic Reminders');register_read_tools(server)
        invalid=[{'protocol_version':1,'error':'permission_required'},
                 {'protocol_version':1,'lists':[{'id':'list','title':'Synthetic','source_id':'outside',
                    'writable':True,'color':None}],'done':True,'next_offset':1,
                  'pagination_consistency':'live_offset_may_shift'}]
        with patch('platform.system',return_value='Darwin'):
            for response in invalid:
                with self.subTest(response=response),patch('subprocess.run',side_effect=self.launcher(response,1)) as run:
                    with self.assertRaises(ToolError):asyncio.run(server.call_tool('reminders_lists',{'limit':1,'offset':0}))
                    self.assertEqual(run.call_count,1)

    def test_derived_attachment_export_keeps_canonical_descriptor_and_public_warning(self):
        import hashlib
        from icloud_for_agents import native_reminder_files
        source='11111111-1111-4111-8111-111111111111';list_id='22222222-2222-4222-8222-222222222222'
        item='33333333-3333-4333-8333-333333333333';attachment='44444444-4444-4444-8444-444444444444'
        raw=b'Synthetic attachment';leaf=hashlib.sha512(raw).hexdigest()+'.txt'
        root=self.root/'reminder-files';path=root/'Container_v1/Files'/('Account-'+source)/'Attachments'/leaf
        path.parent.mkdir(parents=True);path.write_bytes(raw);path.chmod(0o600)
        config={'reminders':{'helper_path':str(self.helper),'source_ids':[source],
                'access_mode':'read-only','advanced_backend':'reminderkit'}}
        metadata={'id':item,'source_id':source,'list_id':list_id,'coverage':['attachments'],
                  'attachment_metadata_complete':False,'attachments':[{'id':attachment,'kind':'file','uti':'public.plain-text'}]}
        observation={'protocol_version':1,'item':{'id':item,'source_id':source,'list_id':list_id,'native_metadata':metadata}}
        descriptor={'protocol_version':1,'id':item,'source_id':source,'list_id':list_id,'attachment_id':attachment,
                    'kind':'file','uti':'public.plain-text','file_leaf':leaf,'file_size':len(raw),
                    'data_mutated':False,'download_requested':False}
        def launch(command,**kwargs):
            request=json.loads((Path(command[-1])/'request.json').read_text())
            self.assertIn(request['action'],{'get','attachment_file'})
            return self.launcher(observation if request['action']=='get' else descriptor,1)(command,**kwargs)
        with patch.dict(os.environ,{'ICLOUD_NATIVE_CONFIG':json.dumps(config),'ICLOUD_MAIL_ACCESS':'read-only'}),patch('platform.system',return_value='Darwin'),patch.object(native_reminder_files,'group_root',return_value=root),patch('subprocess.run',side_effect=launch) as run:
            server=FastMCP('Synthetic attachment export');register_read_tools(server)
            result=asyncio.run(server.call_tool('reminders_export_attachment',{'id':item,'attachment_id':attachment})).structured_content
        self.assertEqual(result['transport_warning'],'launcher_nonzero_result_received')
        self.assertEqual(result['sha256'],hashlib.sha256(raw).hexdigest());self.assertEqual(run.call_count,4)

    def test_planning_grants_and_execution_return_read_warnings_without_altering_authority(self):
        from icloud_for_agents.native_tools import register_change_tools
        from test_native_grants import Adapter as GrantAdapter
        reader=self.reader
        class ObservingAdapter(GrantAdapter):
            def lists(self):return reader.read('lists',limit=1,offset=0)['lists']
            def prepare(self,change):
                self.lists();return super().prepare(change)
            def snapshot(self,item):
                self.lists();return super().snapshot(item)
            def verify(self,item,outcome):
                self.lists();return super().verify(item,outcome)
        adapter=ObservingAdapter('reminders')
        engine=NativeChanges('reminders','synthetic@example.com',adapter,self.store)
        response={'protocol_version':1,'lists':[{'id':'list','title':'Synthetic','source_id':'selected',
                    'writable':True,'color':None}],'done':True,'next_offset':1,
                  'pagination_consistency':'live_offset_may_shift'}
        change={'operation':'create_reminder','source_id':'selected','list_id':'list','fields':{'title':'Synthetic'}}
        scope={'scenario':'Synthetic read-warning propagation','rules':[{'operation':'create_reminder',
            'source_id':'selected','container_id':'list','fields':['title'],'destinations':[]}],
            'max_batch':1,'max_24h':None,'expires':None}
        server=FastMCP('Synthetic native lifecycle');register_change_tools(server)
        def call(name,arguments):return asyncio.run(server.call_tool(name,arguments)).structured_content
        with patch('platform.system',return_value='Darwin'),patch('icloud_for_agents.native_tools.editor',return_value=engine),patch('subprocess.run',side_effect=self.launcher(response,1)):
            proposal=call('reminders_prepare_changes',{'changes':[change]})
            self.assertEqual(proposal['transport_warning'],'launcher_nonzero_result_received')
            grant=call('reminders_prepare_grant',{'scope':scope})
            self.assertEqual(grant['transport_warning'],'launcher_nonzero_result_received')
            activated=call('reminders_activate_grant',{'grant_id':grant['plan_id']})
            self.assertEqual(activated['transport_warning'],'launcher_nonzero_result_received')
            authorized=call('reminders_prepare_with_grant',{'grant_id':grant['plan_id'],'changes':[change]})
            self.assertEqual(authorized['transport_warning'],'launcher_nonzero_result_received')
            executed=call('reminders_execute_approved',{'plan_id':authorized['plan_id']})
            self.assertEqual(executed['status'],'applied')
            self.assertEqual(executed['transport_warning'],'launcher_nonzero_result_received')
            self.assertNotIn('transport_warning',self.store.read(self.store.path(authorized['plan_id'],'.json')))
            self.assertNotIn('transport_warning',self.store.read(self.store.path(authorized['plan_id'],'.result')))
            status=call('reminders_plan_status',{'plan_id':authorized['plan_id']})
            self.assertNotIn('transport_warning',status)
        self.assertEqual(len(adapter.calls),1)


if __name__=='__main__':unittest.main()
