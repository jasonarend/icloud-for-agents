"""Client-neutral configuration tests; no client install, secrets or live calls."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from icloud_for_agents.local_config import read_settings, child_environment
from icloud_for_agents.tunnel_runtime import launch_plan, RuntimeSetupError

spec = importlib.util.spec_from_file_location('setup_utility', Path(__file__).resolve().parents[1] / 'scripts/configure-local.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class ClientNeutralSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def test_direct_setup_needs_no_tunnel_or_client_name(self):
        config = setup.configure(self.root, 'synthetic@example.com')
        settings = read_settings(self.root)
        self.assertNotIn('tunnel_id', settings)
        self.assertNotIn('runtime_keychain_service', settings)
        self.assertFalse((self.root / 'local/tunnel').exists())
        self.assertEqual(config['args'], [str(self.root / 'scripts/run-stdio.py')])

    def test_defaults_do_not_grant_writes_dav_or_standing_cleanup(self):
        setup.configure(self.root, 'synthetic@example.com')
        settings = read_settings(self.root)
        env = child_environment(settings, {})
        self.assertEqual(env['ICLOUD_MAIL_ACCESS'], 'read-only')
        self.assertEqual(env['ICLOUD_ENABLE_DAV_READ'], '0')
        self.assertEqual(env['ICLOUD_APPROVAL_POLICY'], 'local-receipt')
        self.assertNotIn('ICLOUD_CLEANUP_GRANT_ID', env)

    def test_optional_tunnel_keeps_legacy_profile_contract(self):
        setup.configure(self.root, 'synthetic@example.com', 'tunnel_synthetic')
        settings = read_settings(self.root)
        self.assertEqual(settings['tunnel_id'], 'tunnel_synthetic')
        self.assertTrue((self.root / 'local/tunnel/icloud-for-agents.yaml').is_file())
        self.assertEqual((self.root / 'local/settings.json').stat().st_mode & 0o777, 0o600)

    def test_dav_and_write_flags_are_explicit(self):
        setup.configure(self.root, 'synthetic@example.com', write_enabled=True, dav_reads=True)
        env = child_environment(read_settings(self.root), {})
        self.assertEqual(env['ICLOUD_MAIL_ACCESS'], 'write-enabled')
        self.assertEqual(env['ICLOUD_ENABLE_DAV_READ'], '1')
        self.assertEqual(env['ICLOUD_APPROVAL_POLICY'], 'local-receipt')

    def test_existing_configuration_is_not_overwritten(self):
        setup.configure(self.root, 'synthetic@example.com')
        before = (self.root / 'local/settings.json').read_bytes()
        with self.assertRaises(ValueError): setup.configure(self.root, 'other@example.com', 'tunnel_other')
        self.assertEqual((self.root / 'local/settings.json').read_bytes(), before)

    def test_half_tunnel_settings_and_unknown_credentials_rejected(self):
        setup.configure(self.root, 'synthetic@example.com')
        target = self.root / 'local/settings.json'; original = json.loads(target.read_text())
        for extra in ({'tunnel_id': 'tunnel_test'}, {'runtime_keychain_service': 'runtime'}, {'password': 'synthetic'}):
            target.write_text(json.dumps(dict(original, **extra)))
            with self.subTest(extra=list(extra)), self.assertRaises(ValueError): read_settings(self.root)

    def test_direct_settings_refuse_optional_tunnel_start(self):
        setup.configure(self.root, 'synthetic@example.com')
        with self.assertRaisesRegex(RuntimeSetupError, 'direct stdio'): launch_plan(self.root)

    def test_stdio_configuration_has_no_secrets_or_client_dependency(self):
        setup.configure(self.root, 'synthetic@example.com')
        with patch('subprocess.run', side_effect=AssertionError('No credential or client command permitted')):
            config = setup.stdio_configuration(self.root)
        self.assertEqual(set(config), {'command', 'args'})
        self.assertNotIn('synthetic@example.com', json.dumps(config))

    def test_inherited_client_policy_and_platform_secrets_are_stripped(self):
        setup.configure(self.root, 'synthetic@example.com')
        env = child_environment(read_settings(self.root), {'CONTROL_PLANE_API_KEY': 'synthetic', 'ICLOUD_APPROVAL_POLICY': 'trusted-client', 'ICLOUD_CLEANUP_GRANT_ID': 'a'*32})
        self.assertNotIn('CONTROL_PLANE_API_KEY', env)
        self.assertNotIn('ICLOUD_CLEANUP_GRANT_ID', env)
        self.assertEqual(env['ICLOUD_APPROVAL_POLICY'], 'local-receipt')

    def test_explicit_calendar_only_and_no_service_setup(self):
        selection={'mail':False,'calendar':True,'contacts':False}
        setup.configure(self.root,'synthetic@example.com',services=selection)
        env=child_environment(read_settings(self.root),{})
        self.assertEqual(env['ICLOUD_ENABLE_MAIL'],'0')
        self.assertEqual(env['ICLOUD_ENABLE_CALENDAR'],'1')
        self.assertEqual(env['ICLOUD_ENABLE_CONTACTS'],'0')

    def test_invalid_service_selection_rolls_back_new_configuration(self):
        with self.assertRaises(ValueError):setup.configure(self.root,'synthetic@example.com',services={'mail':True})
        self.assertFalse((self.root/'local/settings.json').exists())

    def test_runtime_root_is_explicit_private_compatibility_setting(self):
        setup.configure(self.root,'synthetic@example.com')
        path=self.root/'local/settings.json';data=json.loads(path.read_text());data['runtime_directory']=str(self.root/'private-audit')
        path.write_text(json.dumps(data))
        env=child_environment(read_settings(self.root),{'ICLOUD_RUNTIME_ROOT':'/inherited/not-accepted','ICLOUD_ENABLE_POLICY_MANAGEMENT':'1'})
        self.assertEqual(env['ICLOUD_RUNTIME_ROOT'],data['runtime_directory'])
        self.assertEqual(env['ICLOUD_ENABLE_POLICY_MANAGEMENT'],'0')

    def test_policy_feature_is_explicit_owner_setting_and_boolean(self):
        setup.configure(self.root,'synthetic@example.com')
        path=self.root/'local/settings.json';data=json.loads(path.read_text());data['policy_management_enabled']=True;path.write_text(json.dumps(data))
        self.assertEqual(child_environment(read_settings(self.root),{})['ICLOUD_ENABLE_POLICY_MANAGEMENT'],'1')
        data['policy_management_enabled']='true';path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):read_settings(self.root)
