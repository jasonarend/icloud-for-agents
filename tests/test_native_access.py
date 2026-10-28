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

    def test_status_reports_paused_moves_without_reading_or_authorizing_them(self):
        for name,move in (('notes','move_note'),('reminders','move_reminder')):
            env={'ICLOUD_ENABLE_'+name.upper():'1'}
            with self.subTest(service=name),patch.dict(os.environ,env,clear=True),patch('icloud_for_agents.native_read.NativeReader.read') as reader:
                status=access_status(name)
                self.assertIn(move,status['unavailable_write_operations'])
                self.assertNotIn(move,status['implemented_write_operations'])
                self.assertIn('restore_note' if name=='notes' else 'restore_reminder',status['unavailable_write_operations'])
                self.assertFalse(status['operation_listing_is_authorization'])
                self.assertEqual(status['provider_capabilities'],'not_probed')
                self.assertEqual(status['os_permission'],'not_checked')
                reader.assert_not_called()

    def test_notes_move_inventory_requires_explicit_metadata_opt_in(self):
        env={'ICLOUD_ENABLE_NOTES':'1','ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'metadata_backend':'notes-store-readonly'}})}
        with patch.dict(os.environ,env,clear=True):
            status=access_status('notes')
            self.assertIn('move_note',status['implemented_write_operations'])
            self.assertNotIn('move_note',status['unavailable_write_operations'])
            self.assertEqual(status['provider_capabilities'],'not_probed')

    def test_early_removal_inventory_requires_explicit_native_backend(self):
        for backend in ('disabled','reminderkit'):
            env={'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'advanced_backend':backend}})}
            with self.subTest(backend=backend),patch.dict(os.environ,env,clear=True):
                status=access_status('reminders');self.assertEqual('clear_early' in status['implemented_write_operations'],backend=='reminderkit');self.assertEqual('clear_early' in status['unavailable_write_operations'],backend!='reminderkit');self.assertEqual(status['effective_access'],'read-only')
