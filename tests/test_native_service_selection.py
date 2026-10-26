"""Optional native services never inherit DAV or untrusted launcher switches."""
import itertools
import os
import unittest
from unittest.mock import patch

from icloud_for_agents.core import SafetyError
from icloud_for_agents.local_config import child_environment
from icloud_for_agents.services import configured, enabled


class NativeServiceSelectionTests(unittest.TestCase):
    def test_every_combination_is_independent(self):
        names = ('mail', 'calendar', 'contacts', 'notes', 'reminders')
        for flags in itertools.product((False, True), repeat=5):
            selection = dict(zip(names, flags))
            with self.subTest(selection=selection):
                settings = {'services': selection, 'dav_reads': True, 'account': 'test@example.com',
                            'apple_keychain_service': 'test', 'access_mode': 'read-only'}
                env = child_environment(settings, {})
                self.assertEqual(configured(settings), selection)
                with patch.dict(os.environ, env, clear=True):
                    self.assertEqual({name: enabled(name) for name in names}, selection)

    def test_legacy_dav_never_enables_native_services(self):
        with patch.dict(os.environ, {'ICLOUD_ENABLE_DAV_READ': '1'}, clear=True):
            self.assertTrue(enabled('calendar'))
            self.assertTrue(enabled('contacts'))
            self.assertFalse(enabled('notes'))
            self.assertFalse(enabled('reminders'))

    def test_launcher_does_not_inherit_native_opt_ins(self):
        settings = {'dav_reads': True, 'account': 'test@example.com',
                    'apple_keychain_service': 'test', 'access_mode': 'read-only'}
        env = child_environment(settings, {'ICLOUD_ENABLE_NOTES': '1', 'ICLOUD_ENABLE_REMINDERS': '1'})
        self.assertEqual(env['ICLOUD_ENABLE_NOTES'], '0')
        self.assertEqual(env['ICLOUD_ENABLE_REMINDERS'], '0')

    def test_partial_native_selection_leaves_other_native_service_disabled(self):
        settings = {'dav_reads': False, 'services': {'mail': False, 'calendar': False, 'contacts': False, 'notes': True},
                    'account': 'test@example.com', 'apple_keychain_service': 'test',
                    'access_mode': 'read-only'}
        env = child_environment(settings, {})
        self.assertEqual(env['ICLOUD_ENABLE_NOTES'], '1')
        self.assertEqual(env['ICLOUD_ENABLE_REMINDERS'], '0')

    def test_invalid_native_switches_fail_closed(self):
        for name in ('notes', 'reminders'):
            for value in ('true', '', '2'):
                with patch.dict(os.environ, {'ICLOUD_ENABLE_' + name.upper(): value}, clear=True):
                    with self.assertRaises(SafetyError):
                        enabled(name)
        for value in (1, 'true', None):
            with self.assertRaises(ValueError):
                configured({'services': {'mail': True, 'calendar': False, 'contacts': False,
                                         'notes': value}})
