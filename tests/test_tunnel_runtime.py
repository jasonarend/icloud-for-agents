import contextlib
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import yaml
from icloud_for_agents import tunnel_runtime as runtime


class DurableRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        (self.root/'local/tunnel').mkdir(parents=True)
        (self.root/'tools/tunnel-client-v0.0.16').mkdir(parents=True)
        (self.root/'.venv/bin').mkdir(parents=True)
        (self.root/'scripts').mkdir()
        (self.root/'.venv/bin/python').touch()
        (self.root/'scripts/run-stdio.py').touch()
        binary=self.root/'tools/tunnel-client-v0.0.16/tunnel-client';binary.touch();binary.chmod(0o700)
        import json
        self.settings={'account':'synthetic@example.com','apple_keychain_service':'test-apple','tunnel_id':'tunnel_synthetic','runtime_keychain_service':runtime.KEYCHAIN_SERVICE,'access_mode':'read-only','dav_reads':True}
        setting=self.root/'local/settings.json';setting.write_text(json.dumps(self.settings));setting.chmod(0o600)
        self.profile=self.root/'local/tunnel/icloud-for-agents.yaml'
        self.data={'config_version':1,'control_plane':{'base_url':'https://api.openai.com','tunnel_id':'tunnel_synthetic','api_key':'env:CONTROL_PLANE_API_KEY'},
                   'mcp':{'commands':[{'channel':'main','command':str(self.root/'.venv/bin/python')+' '+str(self.root/'scripts/run-stdio.py')}]},
                   'health':{'listen_addr':'127.0.0.1:0','url_file':str(self.root/'local/tunnel/health.url')},
                   'admin_ui':{'open_browser':False},'log':{'level':'info','format':'json'}}
        self.save()

    def save(self):
        self.profile.write_text(yaml.safe_dump(self.data));self.profile.chmod(0o600)

    def test_explicit_owner_executable_path_and_default_compatibility(self):
        import json
        default = self.root/'tools/tunnel-client-v0.0.16/tunnel-client'
        self.assertEqual(runtime.launch_plan(self.root)['binary'], default)
        custom = self.root/'reviewed client';custom.touch();custom.chmod(0o700)
        self.settings['tunnel_client_path'] = str(custom)
        settings = self.root/'local/settings.json';settings.write_text(json.dumps(self.settings))
        with patch.dict(os.environ, {'TUNNEL_CLIENT_PATH': '/untrusted/inherited/path'}):
            self.assertEqual(runtime.launch_plan(self.root)['binary'], custom)
        custom.chmod(0o777)
        with self.assertRaises(runtime.RuntimeSetupError):runtime.launch_plan(self.root)
        custom.unlink();custom.symlink_to(default)
        with self.assertRaises(runtime.RuntimeSetupError):runtime.launch_plan(self.root)
        self.settings['tunnel_client_path'] = 'relative/client'
        settings.write_text(json.dumps(self.settings))
        with self.assertRaises(ValueError):runtime.launch_plan(self.root)

    def test_static_preflight_has_no_secret_access(self):
        with patch.object(runtime,'project_root',return_value=self.root),patch.object(runtime,'start') as start,patch('subprocess.run') as run:
            with contextlib.redirect_stdout(io.StringIO()):runtime.main(['--check'])
            start.assert_not_called();run.assert_not_called()

    def test_default_is_check_only(self):
        with patch.object(runtime,'project_root',return_value=self.root),patch.object(runtime,'start') as start:
            with contextlib.redirect_stdout(io.StringIO()):runtime.main([])
            start.assert_not_called()

    def test_rejects_wrong_tunnel_before_key(self):
        self.data['control_plane']['tunnel_id']='wrong';self.save();loader=Mock()
        with self.assertRaises(runtime.RuntimeSetupError):runtime.start(self.root,key_loader=loader)
        loader.assert_not_called()

    def test_rejects_other_credential_destinations(self):
        for host in ('https://evil.test','http://api.openai.com','https://api.openai.com.evil.test'):
            self.data['control_plane']['base_url']=host;self.save()
            with self.assertRaises(runtime.RuntimeSetupError):runtime.launch_plan(self.root)

    def test_rejects_extra_headers_or_public_listener(self):
        self.data['control_plane']['extra_headers']=['Authorization: anything'];self.save()
        with self.assertRaises(runtime.RuntimeSetupError):runtime.launch_plan(self.root)
        del self.data['control_plane']['extra_headers'];self.data['health']['listen_addr']='0.0.0.0:8080';self.save()
        with self.assertRaises(runtime.RuntimeSetupError):runtime.launch_plan(self.root)

    def test_rejects_arbitrary_mcp_child(self):
        self.data['mcp']['commands'][0]['command']='other-program';self.save()
        with self.assertRaises(runtime.RuntimeSetupError):runtime.launch_plan(self.root)

    def test_rejects_plaintext_key_in_profile(self):
        self.data['control_plane']['api_key']='synthetic-forbidden-literal';self.save()
        with self.assertRaises(runtime.RuntimeSetupError):runtime.launch_plan(self.root)

    def test_rejects_profile_permissions_and_symlinks(self):
        self.profile.chmod(0o644)
        with self.assertRaises(runtime.RuntimeSetupError):runtime.launch_plan(self.root)
        self.profile.chmod(0o600);other=self.profile.with_suffix('.other');self.profile.rename(other);self.profile.symlink_to(other)
        with self.assertRaises(runtime.RuntimeSetupError):runtime.launch_plan(self.root)

    def test_keychain_query_uses_separate_exact_item_and_no_secret_arg(self):
        run=Mock(return_value=subprocess.CompletedProcess([],0,stdout=b'fake-runtime-key\n',stderr=b''))
        self.assertEqual(runtime.keychain_runtime_key(run,account='tunnel_synthetic'),'fake-runtime-key')
        argv=run.call_args.args[0]
        self.assertIn(runtime.KEYCHAIN_SERVICE,argv);self.assertIn('tunnel_synthetic',argv)
        self.assertNotIn('fake-runtime-key',argv);self.assertEqual(run.call_args.kwargs['timeout'],120)
        self.assertTrue(run.call_args.kwargs['capture_output'])

    def test_keychain_denial_timeout_empty_and_malformed_do_not_leak(self):
        failures=[Mock(side_effect=subprocess.TimeoutExpired('security',120,output=b'fake-secret')),
                  Mock(return_value=subprocess.CompletedProcess([],1,stdout=b'fake-secret',stderr=b'fake-secret')),
                  Mock(return_value=subprocess.CompletedProcess([],0,stdout=b'',stderr=b'')),
                  Mock(return_value=subprocess.CompletedProcess([],0,stdout=b'fake secret',stderr=b''))]
        for runner in failures:
            with self.assertRaises(runtime.RuntimeSetupError) as caught:runtime.keychain_runtime_key(runner,account='tunnel_synthetic')
            self.assertNotIn('fake-secret',str(caught.exception));self.assertNotIn('fake secret',str(caught.exception))
            self.assertEqual(runner.call_count,1)

    def test_environment_strips_unrelated_and_admin_secrets(self):
        env=runtime.runtime_environment('fake-key',{'HOME':'/tmp','PATH':'/usr/bin','OPENAI_API_KEY':'other','OPENAI_ADMIN_KEY':'admin','ICLOUD_APP_SPECIFIC_PASSWORD':'apple','ICLOUD_ENABLE_DAV_READ':'1','CONTROL_PLANE_BASE_URL':'https://evil.test'})
        self.assertEqual(set(env),{'HOME','PATH','CONTROL_PLANE_API_KEY'})

    def test_single_instance_blocks_before_key_lookup(self):
        fd=runtime.runtime_lock(self.root);loader=Mock()
        try:
            with self.assertRaises(runtime.RuntimeSetupError):runtime.start(self.root,key_loader=loader)
            loader.assert_not_called();self.assertTrue(os.get_inheritable(fd))
        finally:os.close(fd)

    def test_restart_retrieves_saved_item_again_without_new_entry(self):
        loader=Mock(return_value='fake-runtime-key');execute=Mock();output=io.StringIO()
        with contextlib.redirect_stdout(output):
            runtime.start(self.root,key_loader=loader,exec_runner=execute)
            runtime.start(self.root,key_loader=loader,exec_runner=execute)
        self.assertEqual(loader.call_count,2);self.assertEqual(execute.call_count,2)
        self.assertNotIn('fake-runtime-key',output.getvalue())
        for call in execute.call_args_list:
            self.assertNotIn('fake-runtime-key',str(call.args[:2]))
            self.assertEqual(call.args[2]['CONTROL_PLANE_API_KEY'],'fake-runtime-key')

    def test_key_unavailable_never_executes(self):
        execute=Mock();loader=Mock(side_effect=runtime.RuntimeSetupError('Key unavailable'))
        with self.assertRaises(runtime.RuntimeSetupError):runtime.start(self.root,key_loader=loader,exec_runner=execute)
        execute.assert_not_called();loader.assert_called_once()

    def test_exec_failure_releases_lock_and_does_not_print_secret(self):
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            with self.assertRaises(OSError):runtime.start(self.root,key_loader=lambda:'fake-key',exec_runner=Mock(side_effect=OSError('synthetic')))
        fd=runtime.runtime_lock(self.root);os.close(fd)
        self.assertNotIn('fake-key',output.getvalue())

    def test_actual_stdio_wrapper_strips_platform_key_and_dav_flag(self):
        import runpy
        project=Path(__file__).resolve().parents[1]
        with patch.dict(os.environ,{'HOME':'/tmp','PATH':'/usr/bin','CONTROL_PLANE_API_KEY':'fake-platform','OPENAI_ADMIN_KEY':'fake-admin','ICLOUD_ENABLE_DAV_READ':'1'},clear=True):
            with patch('icloud_for_agents.local_config.read_settings',return_value=self.settings), patch('os.execve') as execute:
                runpy.run_path(str(project/'scripts/run-stdio.py'),run_name='__main__')
        env=execute.call_args.args[2]
        self.assertNotIn('CONTROL_PLANE_API_KEY',env);self.assertNotIn('OPENAI_ADMIN_KEY',env)
        self.assertEqual(env['ICLOUD_ENABLE_DAV_READ'],'1')
        self.assertEqual(env['ICLOUD_MAIL_ACCESS'],'read-only')
