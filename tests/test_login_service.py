import json
import os
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import Mock, patch
from icloud_for_agents import login_service
from icloud_for_agents.local_config import read_settings, child_environment
from icloud_for_agents.tunnel_runtime import RuntimeSetupError

class LoginServiceTests(unittest.TestCase):
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
