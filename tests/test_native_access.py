"""Explicit independent application write gates; OS grants cannot elevate them."""
import itertools
import json
import os
import unittest
from unittest.mock import patch

from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_access import access_status, require_write


class NativeAccessTests(unittest.TestCase):
    def test_two_gates_and_source_scope_required_before_any_write(self):
        for name, global_mode, local_mode in itertools.product(('notes','reminders'),
                ('disabled','read-only','write-enabled'), ('disabled','read-only','write-enabled')):
            env = {'ICLOUD_ENABLE_'+name.upper():'1','ICLOUD_MAIL_ACCESS':global_mode,
                   'ICLOUD_NATIVE_CONFIG':json.dumps({name:{'access_mode':local_mode,'source_ids':['selected']}})}
            with self.subTest(name=name,global_mode=global_mode,local_mode=local_mode),patch.dict(os.environ,env,clear=True):
                if global_mode == local_mode == 'write-enabled':
                    self.assertEqual(require_write(name)['effective_access'],'write-enabled')
                else:
                    with self.assertRaises(SafetyError):require_write(name)

    def test_global_write_mode_does_not_elevate_default_native_read_only(self):
        with patch.dict(os.environ,{'ICLOUD_ENABLE_NOTES':'1','ICLOUD_MAIL_ACCESS':'write-enabled'},clear=True):
            self.assertEqual(access_status('notes')['effective_access'],'read-only')
            with self.assertRaises(SafetyError):require_write('notes')

    def test_selected_source_mandatory_even_with_both_write_modes(self):
        with patch.dict(os.environ,{'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCESS':'write-enabled',
                'ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'access_mode':'write-enabled'}})},clear=True):
            with self.assertRaises(SafetyError):require_write('reminders')

    def test_status_is_honest_about_unverified_os_and_unimplemented_writes(self):
        with patch.dict(os.environ,{'ICLOUD_ENABLE_NOTES':'1'},clear=True):
            status=access_status('notes')
            self.assertEqual(status['os_permission'],'not_checked')
            self.assertTrue(status['writes_implemented'])
            self.assertEqual(status['effective_access'],'read-only')
            self.assertTrue(status['standing_grants_implemented'])
            self.assertNotIn('helper_path',status)
