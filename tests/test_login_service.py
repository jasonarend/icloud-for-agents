import json
import contextlib
import io
import os
from pathlib import Path
import plistlib
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from icloud_for_agents import login_service
from icloud_for_agents.local_config import read_settings, child_environment
from icloud_for_agents.tunnel_runtime import RuntimeSetupError


class Clock:
    def __init__(self):self.now = 0
    def __call__(self):return self.now
    def sleep(self, seconds):self.now += seconds


class LoginServiceTests(unittest.TestCase):
    def installed(self, root):
        target = Path(root)/'Library/LaunchAgents'/(login_service.LABEL+'.plist')
        target.parent.mkdir(parents=True)
        target.write_bytes(plistlib.dumps(login_service.definition(root)))
        target.chmod(0o600)
        return target

    def test_shutdown_without_tunnel_binary_or_credentials(self):
        with tempfile.TemporaryDirectory() as t:
            target = self.installed(t)
            run = Mock(side_effect=[Mock(returncode=0), Mock(returncode=113), Mock(returncode=0)])
            with patch.object(login_service, 'launch_plan', side_effect=AssertionError('No startup preflight')):
                result = login_service.stop(t, home=t, runner=run)
            self.assertTrue(result['stopped']);self.assertTrue(target.exists())
            self.assertEqual(run.call_args_list[0].args[0],
                             ['/bin/launchctl', 'bootout', f'gui/{os.getuid()}/{login_service.LABEL}'])

    def test_uninstall_removes_only_reviewed_plist_after_confirmed_stop(self):
        with tempfile.TemporaryDirectory() as t:
            target = self.installed(t)
            retained = target.parent/'unrelated.plist';retained.write_text('retained')
            run = Mock(side_effect=[Mock(returncode=113), Mock(returncode=113), Mock(returncode=0)])
            self.assertTrue(login_service.uninstall(t, home=t, runner=run)['uninstalled'])
            self.assertFalse(target.exists());self.assertTrue(retained.exists())

    def test_failed_or_unconfirmed_stop_preserves_installation(self):
        for codes in ((1, 1), (0, 0), (0, 1)):
            with self.subTest(codes=codes), tempfile.TemporaryDirectory() as t:
                target = self.installed(t)
                clock = Clock()
                run = Mock(side_effect=lambda command, **kwargs: Mock(returncode=codes[0] if command[1]=='bootout' else codes[1]))
                with self.assertRaises(RuntimeSetupError):
                    login_service.uninstall(t, home=t, runner=run, clock=clock, sleeper=clock.sleep)
                self.assertTrue(target.exists())
                self.assertEqual(sum(call.args[0][1]=='bootout' for call in run.call_args_list),1)

    def test_status_distinguishes_unavailable_from_not_loaded(self):
        with tempfile.TemporaryDirectory() as t:
            self.installed(t)
            for code, expected in ((0, 'loaded'), (113, 'not_loaded'), (1, 'status_unavailable')):
                run = Mock(side_effect=lambda command, **kwargs: Mock(returncode=0 if command[-1]==f'gui/{os.getuid()}' else code))
                self.assertEqual(login_service.status(t, home=t, runner=run)['status'], expected)

    def test_stop_waits_for_delayed_service_removal_without_repeating_request(self):
        with tempfile.TemporaryDirectory() as t:
            target = self.installed(t);clock = Clock()
            run = Mock(side_effect=[Mock(returncode=n) for n in (0,0,0,113,0)])
            result = login_service.stop(t, home=t, runner=run, clock=clock, sleeper=clock.sleep)
            self.assertTrue(result['stopped']);self.assertTrue(target.exists())
            self.assertEqual([call.args[0][1] for call in run.call_args_list],['bootout','print','print','print','print'])
            self.assertEqual(clock.now,2*login_service.STOP_POLL_INTERVAL)
            self.assertTrue(all(call.kwargs['timeout']>0 for call in run.call_args_list))

    def test_nonzero_or_timed_out_stop_can_report_verified_absence_with_warning(self):
        for failure,warning,code in ((Mock(returncode=5),'launchctl_nonzero_stop_result',5),
                                     (subprocess.TimeoutExpired('launchctl',5),'launchctl_stop_timed_out',None),
                                     (OSError('private diagnostic'),'launchctl_stop_request_failed',None)):
            with self.subTest(warning=warning),tempfile.TemporaryDirectory() as t:
                self.installed(t)
                run=Mock(side_effect=[failure,Mock(returncode=113),Mock(returncode=0)])
                result=login_service.stop(t,home=t,runner=run)
                self.assertTrue(result['stopped']);self.assertEqual(result['stop_warning'],warning)
                self.assertEqual(result['stop_returncode'],code)
                self.assertEqual(sum(call.args[0][1]=='bootout' for call in run.call_args_list),1)
                self.assertNotIn('private diagnostic',json.dumps(result))

    def test_missing_gui_domain_never_proves_service_stopped(self):
        with tempfile.TemporaryDirectory() as t:
            target=self.installed(t);clock=Clock()
            run=Mock(return_value=Mock(returncode=113))
            self.assertEqual(login_service.status(t,home=t,runner=run)['status'],'status_unavailable')
            with self.assertRaisesRegex(login_service.ServiceStopError,'status is unavailable'):
                login_service.uninstall(t,home=t,runner=run,clock=clock,sleeper=clock.sleep)
            self.assertTrue(target.exists())
            self.assertEqual(sum(call.args[0][1]=='bootout' for call in run.call_args_list),1)

    def test_status_failure_and_verification_timeouts_are_bounded_and_do_not_remove_plist(self):
        for error in (subprocess.TimeoutExpired('launchctl',1),OSError('private failure')):
            with self.subTest(error=type(error).__name__),tempfile.TemporaryDirectory() as t:
                target=self.installed(t);clock=Clock()
                def run(command,**kwargs):
                    if command[1]=='bootout':return Mock(returncode=0)
                    self.assertLessEqual(kwargs['timeout'],login_service.COMMAND_TIMEOUT)
                    raise error
                runner=Mock(side_effect=run)
                with self.assertRaisesRegex(login_service.ServiceStopError,'status is unavailable'):
                    login_service.uninstall(t,home=t,runner=runner,clock=clock,sleeper=clock.sleep)
                self.assertTrue(target.exists());self.assertLessEqual(clock.now,login_service.STOP_VERIFY_TIMEOUT)
                self.assertEqual(sum(call.args[0][1]=='bootout' for call in runner.call_args_list),1)

    def test_definition_change_during_stop_verification_preserves_replacement(self):
        with tempfile.TemporaryDirectory() as t:
            target=self.installed(t);clock=Clock()
            def run(command,**kwargs):
                if command[1]=='bootout':return Mock(returncode=0)
                target.write_bytes(plistlib.dumps(dict(login_service.definition(t),KeepAlive=True)))
                return Mock(returncode=0)
            runner=Mock(side_effect=run)
            with self.assertRaisesRegex(login_service.ServiceStopError,'definition changed'):
                login_service.uninstall(t,home=t,runner=runner,clock=clock,sleeper=clock.sleep)
            self.assertTrue(target.exists());self.assertTrue(plistlib.loads(target.read_bytes())['KeepAlive'])
            self.assertEqual(runner.call_count,2)

    def test_definition_changed_during_final_absence_check_is_refused(self):
        with tempfile.TemporaryDirectory() as t:
            target=self.installed(t)
            def run(command,**kwargs):
                if command[1]=='bootout':return Mock(returncode=0)
                if command[-1]==f'gui/{os.getuid()}':
                    target.write_bytes(plistlib.dumps(dict(login_service.definition(t),KeepAlive=True)))
                    return Mock(returncode=0)
                return Mock(returncode=113)
            with self.assertRaisesRegex(login_service.ServiceStopError,'definition changed'):
                login_service.stop(t,home=t,runner=Mock(side_effect=run))
            self.assertTrue(target.exists())

    def test_delayed_uninstall_waits_before_removing_only_exact_plist_and_keeps_warning(self):
        with tempfile.TemporaryDirectory() as t:
            target=self.installed(t);clock=Clock();retained=target.parent/'unrelated';retained.write_text('retained')
            codes=iter((5,0,113,0))
            def run(command,**kwargs):
                self.assertTrue(target.exists());return Mock(returncode=next(codes))
            runner=Mock(side_effect=run)
            result=login_service.uninstall(t,home=t,runner=runner,clock=clock,sleeper=clock.sleep)
            self.assertFalse(target.exists());self.assertTrue(retained.exists())
            self.assertEqual(result['stop_warning'],'launchctl_nonzero_stop_result')
            self.assertEqual(result['stop_returncode'],5)

    def test_cli_stop_diagnostic_identifies_verification_failure(self):
        with patch.object(sys,'argv',['install-login-service.py','--stop']),patch.object(login_service,'stop',side_effect=login_service.ServiceStopError('Service stop unconfirmed after the bounded wait: launchd status is unavailable; installation retained')),contextlib.redirect_stderr(io.StringIO()) as errors:
            with self.assertRaises(SystemExit) as result:login_service.main(root='/tmp/synthetic')
        self.assertEqual(result.exception.code,1)
        self.assertIn('Service stop unconfirmed',errors.getvalue());self.assertIn('status is unavailable',errors.getvalue())

    def test_launchd_smoke_skips_only_missing_or_unsupported_gui_domain(self):
        module=runpy.run_path(str(Path(__file__).with_name('login_service_launchd_smoke.py')))
        for code in (113,125):
            with self.subTest(code=code),patch('subprocess.run',return_value=Mock(returncode=code)) as runner:
                result=module['run']()
                self.assertEqual(result['status'],'skipped');self.assertEqual(result['launchctl_returncode'],code)
                self.assertEqual(runner.call_count,1)
        with patch('subprocess.run',return_value=Mock(returncode=1)) as runner,self.assertRaisesRegex(AssertionError,'inspection failed'):
            module['run']()
        self.assertEqual(runner.call_count,1)

    def test_changed_or_symlink_service_is_never_stopped_or_removed(self):
        with tempfile.TemporaryDirectory() as t:
            target = self.installed(t);run = Mock()
            original = target.read_bytes()
            target.write_bytes(plistlib.dumps(dict(login_service.definition(t), KeepAlive=True)))
            with self.assertRaises(RuntimeSetupError):login_service.uninstall(t, home=t, runner=run)
            target.unlink();other = target.parent/'other';other.write_bytes(original);other.chmod(0o600)
            target.symlink_to(other)
            with self.assertRaises(RuntimeSetupError):login_service.stop(t, home=t, runner=run)
            run.assert_not_called();self.assertTrue(other.exists())

    def test_definition_no_identity_secret_or_restart_loop(self):
        d=login_service.definition('/tmp/synthetic bridge')
        self.assertEqual(d['ProgramArguments'][0],str(Path('/tmp/synthetic bridge/.venv/bin/python').resolve()))
        self.assertTrue(d['RunAtLoad']);self.assertFalse(d['KeepAlive'])
        self.assertNotIn('EnvironmentVariables',d)
        self.assertEqual(d['StandardErrorPath'],'/dev/null')

    def test_active_foreground_installs_without_bootstrap_or_credentials(self):
        with tempfile.TemporaryDirectory() as t:
            run=Mock()
            with patch.object(login_service,'launch_plan',return_value={'root':Path(t)}),patch.object(login_service,'runtime_lock',side_effect=RuntimeSetupError('busy')):
                result=login_service.install(t,home=t,runner=run)
            self.assertTrue(result['installed']);self.assertFalse(result['started']);run.assert_not_called()
            data=plistlib.loads(Path(result['path']).read_bytes());self.assertTrue(data['RunAtLoad'])
            self.assertEqual(Path(result['path']).stat().st_mode & 0o777,0o600)

    def test_unlocked_bootstrap_and_existing_service_not_overwritten(self):
        with tempfile.TemporaryDirectory() as t:
            run=Mock(return_value=Mock(returncode=0))
            with patch.object(login_service,'launch_plan',return_value={'root':Path(t)}),patch.object(login_service,'runtime_lock',side_effect=lambda r:os.open('/dev/null',os.O_RDONLY)):
                result=login_service.install(t,home=t,runner=run)
                self.assertTrue(result['started'])
                self.assertEqual(run.call_count,1)
                with self.assertRaises(RuntimeSetupError):login_service.install(t,home=t,runner=run)
                self.assertEqual(run.call_count,1)

    def test_settings_reject_secrets_unknown_mode_and_preserve_write_choice(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'local';p.mkdir();p=p/'settings.json'
            d={'account':'you@example.com','apple_keychain_service':'apple','tunnel_id':'tunnel_test','runtime_keychain_service':'runtime','access_mode':'write-enabled','dav_reads':True}
            p.write_text(json.dumps(d));p.chmod(0o600)
            env=child_environment(read_settings(t),{'HOME':t,'CONTROL_PLANE_API_KEY':'synthetic-secret'})
            self.assertEqual(env['ICLOUD_MAIL_ACCESS'],'write-enabled');self.assertNotIn('CONTROL_PLANE_API_KEY',env)
            d['password']='synthetic';p.write_text(json.dumps(d))
            with self.assertRaises(ValueError):read_settings(t)
            del d['password'];d['access_mode']='anything';p.write_text(json.dumps(d))
            with self.assertRaises(ValueError):read_settings(t)

    def test_activation_refuses_duplicate_and_altered_definition(self):
        with tempfile.TemporaryDirectory() as t:
            run=Mock(return_value=Mock(returncode=0))
            with patch.object(login_service,'launch_plan',return_value={'root':Path(t)}),patch.object(login_service,'runtime_lock',side_effect=RuntimeSetupError('busy')):
                result=login_service.install(t,home=t,runner=run)
                with self.assertRaises(RuntimeSetupError):login_service.activate(t,home=t,runner=run)
                run.assert_not_called()
            with patch.object(login_service,'launch_plan',return_value={'root':Path(t)}),patch.object(login_service,'runtime_lock',side_effect=lambda r:os.open('/dev/null',os.O_RDONLY)):
                self.assertTrue(login_service.activate(t,home=t,runner=run)['started'])
                p=Path(result['path']);d=plistlib.loads(p.read_bytes());d['KeepAlive']=True;p.write_bytes(plistlib.dumps(d))
                with self.assertRaises(RuntimeSetupError):login_service.activate(t,home=t,runner=run)
                self.assertEqual(run.call_count,1)

    def test_exact_folder_mapping_passes_only_configured_roles(self):
        settings={'account':'synthetic','apple_keychain_service':'apple','access_mode':'write-enabled','dav_reads':True,'folder_overrides':{'Archive':'Retained Mail'}}
        env=child_environment(settings,{'ICLOUD_TRASH_FOLDER':'inherited-unsafe'})
        self.assertEqual(env['ICLOUD_ARCHIVE_FOLDER'],'Retained Mail')
        self.assertNotIn('ICLOUD_TRASH_FOLDER',env)
