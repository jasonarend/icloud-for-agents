"""Standing cleanup setup uses synthetic settings and never accesses Mail."""

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.cleanup_access import CleanupAccess
from icloud_for_agents.cleanup_setup import CleanupSetup, configured_root
from icloud_for_agents.core import SafetyError
from icloud_for_agents.local_config import read_settings


class CleanupSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'installation'
        self.root.mkdir()
        (self.root / 'local').mkdir(mode=0o700)
        self.runtime = self.root / 'runtime'
        self.path = self.root / 'local/settings.json'
        self.settings = {'account': 'synthetic@example.com', 'apple_keychain_service': 'synthetic-keychain',
                         'access_mode': 'write-enabled', 'dav_reads': False,
                         'services': {'mail': True, 'calendar': False, 'contacts': False},
                         'approval_policy': 'local-receipt', 'runtime_directory': str(self.runtime)}
        self.write_settings(self.settings)
        self.env = patch.dict(os.environ, {'ICLOUD_MAIL_ACCOUNT': self.settings['account'],
            'ICLOUD_MAIL_ACCESS': 'write-enabled', 'ICLOUD_APPROVAL_POLICY': 'local-receipt',
            'ICLOUD_ENABLE_MAIL': '1', 'ICLOUD_ENABLE_CALENDAR': '0', 'ICLOUD_ENABLE_CONTACTS': '0',
            'ICLOUD_CONFIG_ROOT': str(self.root), 'ICLOUD_RUNTIME_ROOT': str(self.runtime)}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.now = 1800000000.0
        self.store = PlanStore(self.runtime, clock=lambda: self.now)
        self.setup = CleanupSetup(self.root, self.settings['account'], self.store)
        self.request = {'account': self.settings['account'], 'operations': ['archive', 'mark_read', 'trash'],
                        'source_folders': ['INBOX'], 'destinations': {'archive': 'Archive', 'trash': 'Trash'},
                        'max_batch': 20, 'max_24h': 100, 'days': 30, 'rules_reference': 'Synthetic approved cleanup scenario'}

    def write_settings(self, settings):
        self.path.write_text(json.dumps(settings))
        self.path.chmod(0o600)

    def approve(self, proposal):
        self.store.write_once(self.store.path(proposal['plan_id'], '.approved'),
                              {'plan_id': proposal['plan_id'], 'sha256': proposal['sha256'],
                               'authorization': 'local-owner-graphical-review'})

    def activate(self):
        proposal = self.setup.prepare(self.request)
        self.approve(proposal)
        result = self.setup.activate(proposal['plan_id'])
        self.assertEqual(result['status'], 'applied')
        return proposal, result

    def test_preview_is_exact_without_installation_or_settings_changes(self):
        before = self.path.read_bytes()
        proposal = self.setup.prepare(self.request)
        self.assertEqual(proposal['review']['kind'], 'cleanup_grant')
        self.assertEqual(proposal['review']['payload']['scope']['expires'], self.now + 30 * 86400)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse((self.runtime / 'cleanup-authority').exists())
        self.assertNotIn('ICLOUD_CLEANUP_GRANT_ID', os.environ)
        self.assertTrue(proposal['local_receipt_required'])
        self.assertEqual(proposal['review']['payload']['scope']['activation_plan'], proposal['plan_id'])

    def test_graphically_approved_activation_preserves_settings_and_is_not_replayed(self):
        proposal, result = self.activate()
        updated = read_settings(self.root)
        self.assertEqual(updated, dict(self.settings, cleanup_grant_id=result['grant_id']))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        scope = CleanupAccess(result['grant_id'], self.settings['account'], root=self.runtime, clock=lambda: self.now).scope()
        self.assertEqual(scope, proposal['review']['payload']['scope'])
        self.assertEqual(os.environ['ICLOUD_CLEANUP_GRANT_ID'], result['grant_id'])
        self.assertFalse(result['mail_changed'])
        self.assertEqual(result['activation_plan'], proposal['plan_id'])
        self.assertEqual(result['activation_plan_sha256'], proposal['sha256'])
        self.assertTrue(self.store.operation_settled(proposal['plan_id']))
        with self.assertRaises(SafetyError):
            self.setup.activate(proposal['plan_id'])

    def test_missing_receipt_cannot_create_any_grant(self):
        proposal = self.setup.prepare(self.request)
        with self.assertRaises(FileNotFoundError):
            self.setup.activate(proposal['plan_id'])
        self.assertFalse(self.store.path(proposal['plan_id'], '.started').exists())
        self.assertFalse((self.runtime / 'cleanup-authority').exists())
        self.assertNotIn('cleanup_grant_id', read_settings(self.root))

    def test_trusted_client_mode_uses_the_existing_account_authority(self):
        self.settings['approval_policy'] = 'trusted-client'
        self.write_settings(self.settings)
        with patch.dict(os.environ, {'ICLOUD_APPROVAL_POLICY': 'trusted-client'}):
            proposal = self.setup.prepare(self.request)
            self.assertFalse(proposal['local_receipt_required'])
            result = self.setup.activate(proposal['plan_id'])
        self.assertEqual(result['status'], 'applied')
        started = self.store.read(self.store.path(proposal['plan_id'], '.started'))
        self.assertEqual(started['authorization'], 'trusted-client-policy; not an independent human receipt')
        self.assertFalse(self.store.path(proposal['plan_id'], '.approved').exists())

    def test_settings_scope_or_account_drift_refuses_before_activation(self):
        proposal = self.setup.prepare(self.request)
        self.approve(proposal)
        changed = dict(self.settings, folder_overrides={'Archive': 'Other Archive'})
        self.write_settings(changed)
        with self.assertRaises(SafetyError):
            self.setup.activate(proposal['plan_id'])
        self.assertFalse(self.store.path(proposal['plan_id'], '.started').exists())
        self.assertNotIn('cleanup_grant_id', read_settings(self.root))
        self.write_settings(self.settings)
        with patch.dict(os.environ, {'ICLOUD_MAIL_ACCOUNT': 'other@example.com'}), self.assertRaises(SafetyError):
            self.setup.activate(proposal['plan_id'])

    def test_disabled_or_read_only_mail_and_existing_grant_refuse_preview(self):
        for values in ({'ICLOUD_MAIL_ACCESS': 'read-only'}, {'ICLOUD_ENABLE_MAIL': '0'},
                       {'ICLOUD_CLEANUP_GRANT_ID': 'a' * 32}, {'ICLOUD_ENABLE_POLICY_MANAGEMENT': '1'}):
            with self.subTest(values=values), patch.dict(os.environ, values), self.assertRaises(SafetyError):
                self.setup.prepare(self.request)
        self.write_settings(dict(self.settings, cleanup_grant_id='b' * 32))
        with self.assertRaises(SafetyError):
            self.setup.prepare(self.request)

    def test_scope_validation_rejects_unsupported_or_ambiguous_authority(self):
        variants = [dict(self.request, operations=['send']), dict(self.request, source_folders=['*']),
                    dict(self.request, days=True), dict(self.request, days=0), dict(self.request, days=36501),
                    dict(self.request, account='other@example.com'), dict(self.request, max_batch=101),
                    dict(self.request, operations=[{}]), dict(self.request, destinations={'trash': 'INBOX'}),
                    dict(self.request, activation_plan='0' * 32)]
        for request in variants:
            with self.subTest(request=request), self.assertRaises(SafetyError):
                self.setup.prepare(request)
        proposal = self.setup.prepare(dict(self.request, days=None, max_24h=None))
        self.assertIsNone(proposal['review']['payload']['scope']['expires'])
        self.assertIsNone(proposal['review']['payload']['scope']['max_24h'])

    def test_symlinked_settings_or_local_directory_cannot_be_activated(self):
        proposal = self.setup.prepare(self.request)
        self.approve(proposal)
        original = self.path.read_bytes()
        self.path.unlink()
        target = self.root / 'outside.json'
        target.write_bytes(original); target.chmod(0o600)
        self.path.symlink_to(target)
        with self.assertRaises((SafetyError, OSError)):
            self.setup.activate(proposal['plan_id'])
        self.assertFalse(self.store.path(proposal['plan_id'], '.started').exists())
        self.path.unlink(); self.path.write_bytes(original); self.path.chmod(0o600)
        local = self.root / 'local'
        local.rename(self.root / 'actual-local')
        local.symlink_to(self.root / 'actual-local', target_is_directory=True)
        with self.assertRaises((SafetyError, OSError)):
            self.setup.activate(proposal['plan_id'])

    def test_failure_after_grant_write_retains_uncertainty_and_never_replays(self):
        proposal = self.setup.prepare(self.request)
        self.approve(proposal)
        with patch.object(self.setup, '_replace_settings', side_effect=OSError('synthetic disk failure')):
            result = self.setup.activate(proposal['plan_id'])
        self.assertEqual(result['status'], 'failed_or_uncertain')
        self.assertEqual(result['stage'], 'settings_write')
        self.assertFalse(self.store.operation_settled(proposal['plan_id']))
        identity = proposal['review']['payload']['scope']['grant_id']
        self.assertTrue((self.runtime / 'cleanup-authority' / (identity + '.json')).exists())
        self.assertNotIn('cleanup_grant_id', read_settings(self.root))
        with self.assertRaises(SafetyError):
            self.setup.activate(proposal['plan_id'])

    def test_failure_after_settings_replacement_stays_inactive_after_restart(self):
        proposal = self.setup.prepare(self.request)
        self.approve(proposal)
        original = self.setup._replace_settings
        def replace_then_fail(*args):
            original(*args)
            raise OSError('synthetic failure after settings save')
        with patch.object(self.setup, '_replace_settings', side_effect=replace_then_fail):
            result = self.setup.activate(proposal['plan_id'])
        self.assertEqual(result['status'], 'failed_or_uncertain')
        identity = read_settings(self.root)['cleanup_grant_id']
        self.assertNotIn('ICLOUD_CLEANUP_GRANT_ID', os.environ)
        restarted = CleanupAccess(identity, self.settings['account'], root=self.runtime, clock=lambda: self.now)
        with self.assertRaises(SafetyError):
            restarted.scope()
        status = self.setup.status()
        self.assertFalse(status['active'])
        self.assertFalse(status['activation_verified'])
        self.assertTrue(self.setup.revoke(identity)['revoked'])

    def test_crash_before_durable_finish_cannot_activate_after_restart(self):
        proposal = self.setup.prepare(self.request)
        self.approve(proposal)
        with patch.object(self.store, 'finish', side_effect=OSError('synthetic journal failure')), self.assertRaises(OSError):
            self.setup.activate(proposal['plan_id'])
        identity = read_settings(self.root)['cleanup_grant_id']
        self.assertFalse(self.store.path(proposal['plan_id'], '.result').exists())
        self.assertNotIn('ICLOUD_CLEANUP_GRANT_ID', os.environ)
        with self.assertRaises(SafetyError):
            CleanupAccess(identity, self.settings['account'], root=self.runtime, clock=lambda: self.now).scope()

    def test_activation_result_is_committed_before_runtime_authority(self):
        proposal = self.setup.prepare(self.request)
        self.approve(proposal)
        original = self.store.finish
        def finish_before_environment(identity, result):
            self.assertNotIn('ICLOUD_CLEANUP_GRANT_ID', os.environ)
            return original(identity, result)
        with patch.object(self.store, 'finish', side_effect=finish_before_environment):
            result = self.setup.activate(proposal['plan_id'])
        self.assertEqual(result['status'], 'applied')
        self.assertEqual(os.environ['ICLOUD_CLEANUP_GRANT_ID'], result['grant_id'])
        self.assertTrue(self.setup.status()['activation_verified'])

    def test_scope_rechecks_exact_activation_records_before_each_use(self):
        proposal, result = self.activate()
        identity = result['grant_id']
        access = CleanupAccess(identity, self.settings['account'], root=self.runtime, clock=lambda: self.now)
        for suffix, changes in (
                ('.result', {'status': 'failed_or_uncertain'}),
                ('.result', {'activation_plan_sha256': '0' * 64}),
                ('.result', {'scope_sha256': '0' * 64}),
                ('.result', {'grant_id': '0' * 32}),
                ('.result', {'settings_updated': False}),
                ('.started', {'sha256': '0' * 64}),
                ('.started', {'account': 'other@example.com'}),
                ('.started', {'authorization': 'model-approved'}),
                ('.approved', {'sha256': '0' * 64})):
            path = self.store.path(proposal['plan_id'], suffix)
            original = self.store.read(path)
            path.write_text(json.dumps(dict(original, **changes)))
            with self.subTest(suffix=suffix, changes=changes), self.assertRaises(SafetyError):
                access.scope()
            path.write_text(json.dumps(original))
        self.assertEqual(access.scope()['grant_id'], identity)
        authority = access.authority.path(identity, '.json')
        original_scope = access.authority.read(authority)
        authority.write_text(json.dumps(dict(original_scope, activation_plan='0' * 32)))
        with self.assertRaises(SafetyError):
            access.scope()
        authority.write_text(json.dumps(original_scope))
        path = self.store.path(proposal['plan_id'], '.json')
        original = self.store.read(path)
        changed = deepcopy(original)
        changed['payload']['scope']['max_batch'] = 1
        path.write_text(json.dumps(changed))
        with self.assertRaises(SafetyError):
            access.scope()
        path.write_text(json.dumps(original))

    def test_legacy_owner_installed_scope_remains_supported_without_activation_link(self):
        scope = {key: value for key, value in self.request.items() if key != 'days'}
        scope.update(grant_id='a' * 32, issued=self.now, expires=self.now + 86400)
        access = CleanupAccess(scope['grant_id'], self.settings['account'], root=self.runtime, clock=lambda: self.now)
        access.authority.write_once(access.authority.path(scope['grant_id'], '.json'), scope)
        self.assertEqual(access.scope(), scope)
        self.assertFalse(any(self.runtime.glob('*.started')))

    def test_revocation_keeps_the_installed_setting_and_future_batches_fail_closed(self):
        _, result = self.activate()
        identity = result['grant_id']
        self.assertTrue(self.setup.status()['active'])
        before = self.path.read_bytes()
        revoked = self.setup.revoke(identity)
        self.assertTrue(revoked['revoked'])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(os.environ['ICLOUD_CLEANUP_GRANT_ID'], identity)
        self.assertTrue(self.setup.status()['revoked'])
        self.assertFalse(self.setup.status()['active'])
        self.assertTrue(self.setup.revoke(identity)['revoked'])
        with self.assertRaises(SafetyError):
            CleanupAccess(identity, self.settings['account'], root=self.runtime, clock=lambda: self.now).scope()
        with self.assertRaises(SafetyError):
            self.setup.revoke('0' * 32)

    def test_launcher_root_is_explicit_and_not_an_mcp_path_argument(self):
        self.assertEqual(configured_root(), self.root)
        for value in ('relative', '', '/tmp/invalid\nroot'):
            with patch.dict(os.environ, {'ICLOUD_CONFIG_ROOT': value}), self.assertRaises(SafetyError):
                configured_root()
        other = PlanStore(self.root / 'wrong-runtime', clock=lambda: self.now)
        with self.assertRaises(SafetyError):
            CleanupSetup(self.root, self.settings['account'], other).prepare(self.request)
