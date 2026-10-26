"""Private one-shot application IPC, with no real subprocess or native app access."""
import json
import os
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
        (self.bundle/'Contents/Info.plist').write_text('synthetic bundle')
        context=patch('icloud_for_agents.native_app.runtime_root',return_value=self.root/'runtime')
        context.start();self.addCleanup(context.stop)
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

    def test_bad_runtime_and_helper_never_launch(self):
        runtime=self.root/'runtime';runtime.mkdir(mode=0o755)
        with patch('subprocess.run',side_effect=AssertionError('No launch')),self.assertRaises(SafetyError):self.request()
        runtime.chmod(0o700);self.helper.chmod(0o777)
        with patch('subprocess.run',side_effect=AssertionError('No launch')),self.assertRaises(SafetyError):self.request()
