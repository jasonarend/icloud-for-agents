"""Only completed fixed read timeouts may repeat, with scope/backend revalidation."""
import json,os,plistlib,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_read import NativeReader,native_config

class NativeReadRetryTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name);bundle=self.root/'Helper.app';self.helper=bundle/'Contents/MacOS/helper';self.helper.parent.mkdir(parents=True);self.helper.write_text('synthetic');self.helper.chmod(0o700)
        (bundle/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable':'helper','CFBundleIdentifier':'com.jasonarend.icloud-for-agents.native','CFBundlePackageType':'APPL'}));resources=bundle/'Contents/Resources';resources.mkdir()
        for name in ('notes.js','notes-write.js'):(resources/name).write_text('synthetic')
        self.resource=resources/'notes.js'
        env={'ICLOUD_ENABLE_NOTES':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_RUNTIME_ROOT':str(self.root/'runtime'),'ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'source_ids':['selected'],'helper_path':str(self.helper),'access_mode':'read-only'}})}
        context=patch.dict(os.environ,env,clear=True);context.start();self.addCleanup(context.stop)
        platform=patch('platform.system',return_value='Darwin');platform.start();self.addCleanup(platform.stop)
        self.timeout={'protocol_version':1,'error':'notes_operation_failed','error_code':-1712};self.success={'protocol_version':1,'folder':{'id':'folder','source_id':'selected'}}
    def read(self):return NativeReader('notes').read('folder_get',folder_id='folder')
    def test_one_completed_read_timeout_can_repeat_exact_request_once(self):
        with patch('icloud_for_agents.native_app.application_request',side_effect=[self.timeout,self.success]) as transport:
            result=self.read();self.assertEqual(result['read_retry_count'],1);self.assertEqual(transport.call_count,2)
            self.assertEqual(transport.call_args_list[0].args,transport.call_args_list[1].args)
    def test_second_timeout_is_returned_without_third_attempt(self):
        with patch('icloud_for_agents.native_app.application_request',return_value=self.timeout) as transport:
            with self.assertRaises(SafetyError):self.read()
            self.assertEqual(transport.call_count,2)
    def test_mutations_and_os_requests_never_repeat(self):
        for action in ('apply','request_access'):
            with patch('icloud_for_agents.native_app.application_request',return_value=self.timeout) as transport:
                with self.assertRaises(SafetyError):NativeReader('notes')._dispatch({'protocol_version':1,'action':action,'source_ids':['selected']},native_config('notes'))
                self.assertEqual(transport.call_count,1)
    def test_permission_denials_and_missing_responses_never_repeat(self):
        for code in (-1743,-1744):
            with patch('icloud_for_agents.native_app.application_request',return_value=dict(self.timeout,error_code=code)) as transport:
                with self.assertRaises(SafetyError):self.read()
                self.assertEqual(transport.call_count,1)
        with patch('icloud_for_agents.native_app.application_request',side_effect=SafetyError('Unknown response')) as transport:
            with self.assertRaises(SafetyError):self.read()
            self.assertEqual(transport.call_count,1)
    def test_source_revocation_and_helper_changes_prevent_read_retry(self):
        for change in ('source','helper','global','disabled'):
            with self.subTest(change=change),patch.dict(os.environ,dict(os.environ),clear=True):
                original=self.resource.read_text()
                def response(*args,**kwargs):
                    if change=='source':
                        config=json.loads(os.environ['ICLOUD_NATIVE_CONFIG']);config['notes']['source_ids']=['other'];os.environ['ICLOUD_NATIVE_CONFIG']=json.dumps(config)
                    elif change=='helper':self.resource.write_text('changed fixed helper')
                    elif change=='global':os.environ['ICLOUD_MAIL_ACCESS']='disabled'
                    else:os.environ['ICLOUD_ENABLE_NOTES']='0'
                    return self.timeout
                with patch('icloud_for_agents.native_app.application_request',side_effect=response) as transport:
                    with self.assertRaises(SafetyError):self.read()
                    self.assertEqual(transport.call_count,1)
                self.resource.write_text(original)
