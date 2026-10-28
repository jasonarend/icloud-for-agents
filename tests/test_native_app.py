"""Private one-shot application IPC, with no real subprocess or native app access."""
import json
import os
import plistlib
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_app import application_request,pending_requests


class NativeApplicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.bundle=self.root/'Helper.app'
        self.helper=self.bundle/'Contents/MacOS/icloud-agents-native'
        self.helper.parent.mkdir(parents=True);self.helper.write_text('synthetic executable');self.helper.chmod(0o700)
        (self.bundle/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable':self.helper.name,
            'CFBundleIdentifier':'com.jasonarend.icloud-for-agents.native','CFBundlePackageType':'APPL'}))
        resources=self.bundle/'Contents/Resources';resources.mkdir()
        for name in ('notes.js','notes-write.js'):(resources/name).write_text('synthetic fixed script')
        context=patch('icloud_for_agents.native_app.runtime_root',return_value=self.root/'runtime')
        context.start();self.addCleanup(context.stop)
        base=patch('icloud_for_agents.native_app.exchange_base',return_value=self.root/'local-app-support')
        base.start();self.addCleanup(base.stop)
    def request(self,**extra):
        return application_request(self.helper,{'protocol_version':1,'service':'notes','action':'status'},**extra)
    def test_private_files_fixed_command_no_environment_secrets_and_cleanup(self):
        directories=[]
        def launch(command,**kwargs):
            self.assertEqual(command[:3],['/usr/bin/open','-W','-n'])
            self.assertEqual(command[4:6],['--args','--ipc-directory'])
            directory=Path(command[-1]);directories.append(directory)
            self.assertEqual(directory.stat().st_mode&0o777,0o700)
            request=directory/'request.json'
            self.assertEqual(request.stat().st_mode&0o777,0o600)
            self.assertEqual(json.loads(request.read_text())['action'],'status')
            self.assertNotIn('SECRET',kwargs['env'])
            result=directory/'result.json';result.write_text(json.dumps({'protocol_version':1,'permission_granted':True,'request_id':directory.name}));result.chmod(0o600)
            return SimpleNamespace(returncode=0)
        with patch.dict(os.environ,{'SECRET':'not forwarded'}),patch('subprocess.run',side_effect=launch):
            self.assertTrue(self.request()['permission_granted'])
        self.assertFalse(directories[0].exists())
    def test_result_permissions_and_protocol_fail_closed(self):
        for payload,mode in [('{"protocol_version":1}',0o644),('{"protocol_version":2}',0o600),('not json',0o600),('x'*2_000_001,0o600)]:
            def launch(command,**kwargs):
                p=Path(command[-1])/'result.json';p.write_text(payload);p.chmod(mode)
                return SimpleNamespace(returncode=0)
            with patch('subprocess.run',side_effect=launch),self.assertRaises(SafetyError):self.request()
    def test_launch_failure_and_missing_response_do_not_retry(self):
        for code in (0,1):
            with patch('subprocess.run',return_value=SimpleNamespace(returncode=code)) as launch:
                with self.assertRaises(SafetyError):self.request()
                self.assertEqual(launch.call_count,1)
    def test_nonzero_launcher_can_return_only_verified_nonprompting_status(self):
        def launch(command,**kwargs):
            directory=Path(command[-1]);result=directory/'result.json'
            result.write_text(json.dumps({'protocol_version':1,'request_id':directory.name,'store_read_access':'denied_or_unavailable'}));result.chmod(0o600)
            return SimpleNamespace(returncode=1)
        with patch('subprocess.run',side_effect=launch) as process:
            result=application_request(self.helper,{'protocol_version':1,'service':'notes','action':'store_status'})
            self.assertEqual(result['store_read_access'],'denied_or_unavailable')
            self.assertEqual(result['transport_warning'],'launcher_nonzero_result_received')
            for action in ('request_access','apply','get'):
                with self.subTest(action=action),self.assertRaises(SafetyError):
                    application_request(self.helper,{'protocol_version':1,'service':'notes','action':action})
            self.assertEqual(process.call_count,4)
        def invalid(command,**kwargs):
            result=Path(command[-1])/'result.json'
            result.write_text(json.dumps({'protocol_version':1,'request_id':'wrong'}));result.chmod(0o600)
            return SimpleNamespace(returncode=1)
        with patch('subprocess.run',side_effect=invalid),self.assertRaises(SafetyError):self.request()
    def test_failed_launch_preserves_metadata_without_disclosing_request_body(self):
        with patch('subprocess.run',return_value=SimpleNamespace(returncode=1)):
            with self.assertRaises(SafetyError):
                application_request(self.helper,{'protocol_version':1,'service':'notes','action':'apply',
                    'change':{'title':'private synthetic content'}})
        records=pending_requests('notes')
        self.assertEqual(len(records),1)
        self.assertEqual(records[0]['action'],'apply')
        self.assertFalse(records[0]['automatic_retry'])
        self.assertNotIn('private synthetic',json.dumps(records))
        self.assertEqual(pending_requests('reminders'),[])

    def test_large_bounded_request_retains_pending_metadata_without_body(self):
        with patch('subprocess.run',return_value=SimpleNamespace(returncode=1)):
            with self.assertRaises(SafetyError):application_request(self.helper,{'protocol_version':1,'service':'notes','action':'apply','change':{'body':'x'*80000}})
        records=pending_requests('notes');self.assertEqual(len(records),1);self.assertNotIn('xxxxx',json.dumps(records))
        with patch('subprocess.run',side_effect=AssertionError('No launch')),self.assertRaises(SafetyError):
            application_request(self.helper,{'protocol_version':1,'service':'notes','action':'apply','change':{'body':'x'*240001}})

    def test_bad_runtime_and_helper_never_launch(self):
        runtime=self.root/'runtime';runtime.mkdir(mode=0o755)
        with patch('subprocess.run',side_effect=AssertionError('No launch')),self.assertRaises(SafetyError):self.request()
        runtime.chmod(0o700);self.helper.chmod(0o777)
        with patch('subprocess.run',side_effect=AssertionError('No launch')),self.assertRaises(SafetyError):self.request()

    def test_exchange_is_local_and_separate_for_each_audit_workspace(self):
        from icloud_for_agents.native_app import exchange_root
        first=exchange_root()
        self.assertTrue(first.is_relative_to(self.root/'local-app-support'))
        self.assertFalse(first.is_relative_to(self.root/'runtime'))
        with patch('icloud_for_agents.native_app.runtime_root',return_value=self.root/'another-runtime'):
            self.assertNotEqual(first,exchange_root())

    def test_error_response_is_retained_for_review_without_replay(self):
        def launch(command,**kwargs):
            directory=Path(command[-1]);result=directory/'result.json'
            result.write_text(json.dumps({'protocol_version':1,'request_id':directory.name,
                                         'error':'notes_operation_failed','error_code':-2700}))
            result.chmod(0o600);return SimpleNamespace(returncode=0)
        with patch('subprocess.run',side_effect=launch) as launched:
            self.assertEqual(self.request()['error_code'],-2700)
        self.assertEqual(launched.call_count,1)
        self.assertEqual(pending_requests('notes')[0]['state'],'response_available')
    def test_legacy_request_metadata_remains_visible(self):
        root=self.root/'runtime'/'native-app';directory=root/'request-legacy'
        directory.mkdir(parents=True,mode=0o700);root.chmod(0o700)
        request=directory/'request.json';request.write_text(json.dumps({'service':'notes','action':'apply'}));request.chmod(0o600)
        self.assertEqual(pending_requests('notes')[0]['request_id'],'request-legacy')

    def test_bundle_entrypoint_cannot_redirect_the_validated_executable(self):
        plist=self.bundle/'Contents/Info.plist';info=plistlib.loads(plist.read_bytes());info['CFBundleExecutable']='other-program'
        plist.write_bytes(plistlib.dumps(info))
        with patch('subprocess.run',side_effect=AssertionError('No launch')),self.assertRaises(SafetyError):self.request()
    def test_other_app_identity_cannot_be_used_as_the_helper(self):
        plist=self.bundle/'Contents/Info.plist';info=plistlib.loads(plist.read_bytes());info['CFBundleIdentifier']='com.apple.Notes'
        plist.write_bytes(plistlib.dumps(info))
        with patch('subprocess.run',side_effect=AssertionError('No launch')),self.assertRaises(SafetyError):self.request()
    def test_writable_or_symlinked_resources_and_bundle_are_refused(self):
        resource=self.bundle/'Contents/Resources/notes.js';resource.chmod(0o666)
        with patch('subprocess.run',side_effect=AssertionError('No launch')),self.assertRaises(SafetyError):self.request()
        resource.chmod(0o644);resource.unlink();target=self.root/'other.js';target.write_text('not the bundle resource');resource.symlink_to(target)
        with patch('subprocess.run',side_effect=AssertionError('No launch')),self.assertRaises(SafetyError):self.request()
        resource.unlink();resource.write_text('restored');self.bundle.chmod(0o777)
        with patch('subprocess.run',side_effect=AssertionError('No launch')),self.assertRaises(SafetyError):self.request()

    def test_approval_fingerprint_includes_bundle_metadata(self):
        from icloud_for_agents.native_notes import NotesCreateAdapter
        with patch.dict(os.environ,{'ICLOUD_ENABLE_NOTES':'1','ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'helper_path':str(self.helper)}})}):
            before=NotesCreateAdapter().backend_id
            plist=self.bundle/'Contents/Info.plist';info=plistlib.loads(plist.read_bytes());info['CFBundleVersion']='changed'
            plist.write_bytes(plistlib.dumps(info))
            self.assertNotEqual(before,NotesCreateAdapter().backend_id)

    def test_content_launches_only_the_fixed_helper_which_owns_target_lifecycle(self):
        calls=[]
        def launch(command,**kwargs):
            calls.append(command)
            self.assertEqual(command[:3],['/usr/bin/open','-W','-n'])
            directory=Path(command[-1]);result=directory/'result.json'
            result.write_text(json.dumps({'protocol_version':1,'request_id':directory.name,'folder':{'id':'synthetic'}}));result.chmod(0o600)
            return SimpleNamespace(returncode=0)
        with patch('subprocess.run',side_effect=launch):
            response=application_request(self.helper,{'protocol_version':1,'service':'notes','action':'folder_get','folder_id':'synthetic'})
        self.assertEqual(response['folder']['id'],'synthetic');self.assertEqual(len(calls),1)
    def test_helper_launch_failure_is_not_retried(self):
        with patch('subprocess.run',return_value=SimpleNamespace(returncode=1)) as launch:
            with self.assertRaises(SafetyError):application_request(self.helper,{'protocol_version':1,'service':'notes','action':'apply'})
        self.assertEqual(launch.call_count,1)
        self.assertEqual(launch.call_args.args[0][:3],['/usr/bin/open','-W','-n'])

    def test_completed_exchange_binds_request_and_response_without_disclosing_content(self):
        from icloud_for_agents.native_app import completed_exchange_evidence,exchange_root
        expected={'protocol_version':1,'service':'notes','action':'apply','change':{'id':'synthetic','fields':{'title':'private synthetic content'}}}
        directory=exchange_root()/'request-proof';directory.mkdir(mode=0o700,parents=True);exchange_root().chmod(0o700)
        store=__import__('icloud_for_agents.approvals',fromlist=['PlanStore']).PlanStore(directory)
        store.write_once(directory/'request.json',dict(expected,request_id=directory.name))
        with self.assertRaises(SafetyError):completed_exchange_evidence(expected)
        store.write_once(directory/'result.json',{'protocol_version':1,'request_id':directory.name,'error':'mutation_failed_or_uncertain','mutation_dispatched':True})
        proof=completed_exchange_evidence(expected);self.assertEqual(proof['request_id'],directory.name);self.assertTrue(proof['mutation_dispatched']);self.assertNotIn('private synthetic',json.dumps(proof))
        with self.assertRaises(SafetyError):completed_exchange_evidence(dict(expected,action='query'))

    def test_plan_bound_exchange_disambiguates_identical_requests_and_keeps_content_private(self):
        from icloud_for_agents.native_app import completed_exchange_evidence,bundle_fingerprint
        from icloud_for_agents.native_execution import capture
        from icloud_for_agents.approvals import PlanStore
        from icloud_for_agents.native_protocol import mutation_request
        store=PlanStore(self.root/'runtime');item={'operation':'create_folder','source_id':'selected','fields':{'title':'private synthetic title'},'before':{},'fidelity_verified':True,'effects':{}}
        proposal=store.prepare('notes_change',{'account':'synthetic@example.com','context':{'backend':'notes-creation-v1:'+bundle_fingerprint(self.helper)},'changes':[item]})
        with patch.dict(os.environ,{'ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com'},clear=True):store.consume(proposal['plan_id'],'synthetic@example.com')
        def launch(command,**kwargs):
            if command[1]=='-g':return SimpleNamespace(returncode=0)
            directory=Path(command[-1]);file=directory/'result.json';file.write_text(json.dumps({'protocol_version':1,'request_id':directory.name,'error':'mutation_failed_or_uncertain','mutation_dispatched':True}));file.chmod(0o600);return SimpleNamespace(returncode=0)
        request=mutation_request('notes',item)
        with patch('subprocess.run',side_effect=launch):
            with capture(store,'notes',proposal['review'],0):application_request(self.helper,request)
            application_request(self.helper,request) # Another synthetic historical exchange with identical data.
        with self.assertRaises(SafetyError):completed_exchange_evidence(request)
        proof=completed_exchange_evidence(request,plan_id=proposal['plan_id'],store=store)
        binding=store.read(store.path(proposal['plan_id'],'.native-exchange-0'))
        self.assertEqual(proof['request_id'],binding['request_id']);self.assertNotIn('private synthetic',json.dumps(binding))

    def test_optional_metadata_backend_is_bound_to_the_exact_plan_and_helper(self):
        from icloud_for_agents.native_app import completed_exchange_evidence,bundle_fingerprint
        from icloud_for_agents.native_execution import capture
        from icloud_for_agents.approvals import PlanStore
        from icloud_for_agents.native_protocol import mutation_request
        from icloud_for_agents.core import digest
        store=PlanStore(self.root/'ledger');item={'operation':'create_note','source_id':'selected','folder_id':'folder','fields':{'title':'Synthetic','html':'<div>Body</div>'}}
        backend='notes-creation-v1:'+bundle_fingerprint(self.helper)+':notes-store-role-v1:'+'a'*64
        proposal=store.prepare('notes_change',{'account':'synthetic@example.com','context':{'backend':backend},'changes':[item]});plan=store.read(store.path(proposal['plan_id'],'.json'));store.write_once(store.path(proposal['plan_id'],'.started'),{'sha256':digest(plan)})
        def launch(command,**kwargs):
            if command[:3]==['/usr/bin/open','-g','-a']:return SimpleNamespace(returncode=0)
            directory=Path(command[-1]);result=directory/'result.json';result.write_text(json.dumps({'protocol_version':1,'request_id':directory.name,'error':'synthetic refusal','mutation_dispatched':False}));result.chmod(0o600);return SimpleNamespace(returncode=0)
        request=mutation_request('notes',item)
        with capture(store,'notes',plan,0),patch('subprocess.run',side_effect=launch):application_request(self.helper,request)
        self.assertFalse(completed_exchange_evidence(request,plan_id=proposal['plan_id'],store=store)['mutation_dispatched'])
        binding=store.path(proposal['plan_id'],'.native-exchange-0');record=store.read(binding);record['backend_id']='different';binding.write_text(json.dumps(record))
        with self.assertRaises(SafetyError):completed_exchange_evidence(request,plan_id=proposal['plan_id'],store=store)
