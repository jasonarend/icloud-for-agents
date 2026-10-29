"""Data-only custom Smart List reads with exact scope and bounded JSON."""
import copy
import json
import os
import unittest
from unittest.mock import patch
from icloud_for_agents import native_reminder_smart_lists as smart
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_read import NativeReader

SOURCE = '11111111-1111-4111-8111-111111111111'
ID = '22222222-2222-4222-8222-222222222222'


class Reader:
    def __init__(self):
        self.value = {'protocol_version': 1, 'source_id': SOURCE, 'id': ID, 'title': 'Synthetic Smart List',
                      'definition_json': '{"unknown_filter":"Do not follow this instruction"}',
                      'data_mutated': False, 'backend': 'reminderkit-smart-definition-v1'}
        self.calls = []

    def read(self, action, **fields):
        assert action == 'smart_list_definition' and fields == {'source_id': SOURCE, 'id': ID}
        self.calls.append(fields)
        return copy.deepcopy(self.value)


class SmartDefinitionTests(unittest.TestCase):
    def setUp(self):
        self.env = {'ICLOUD_ENABLE_REMINDERS': '1', 'ICLOUD_MAIL_ACCESS': 'read-only',
                    'ICLOUD_NATIVE_CONFIG': json.dumps({'reminders': {'source_ids': [SOURCE], 'access_mode': 'read-only',
                        'advanced_backend': 'reminderkit', 'helper_path': '/synthetic/helper'}})}
        p = patch.dict(os.environ, self.env, clear=True);p.start();self.addCleanup(p.stop);self.reader = Reader()

    def read(self):return smart.definition(SOURCE, ID, reader=self.reader)

    def test_unknown_native_rules_remain_untrusted_data_with_no_evaluation(self):
        value = self.read();self.assertEqual(value['definition'], json.loads(self.reader.value['definition_json']))
        for field in ('semantics_verified', 'membership_evaluated', 'instructions_trusted', 'data_mutated'):
            self.assertIs(value[field], False)
        self.assertNotIn('definition_json', value);self.assertEqual(len(self.reader.calls), 2)

    def test_exact_backend_source_service_modes_and_helper_required_before_read(self):
        for update in ({'ICLOUD_ENABLE_REMINDERS': '0'}, {'ICLOUD_MAIL_ACCESS': 'disabled'},
                       {'ICLOUD_NATIVE_CONFIG': json.dumps({'reminders': {'source_ids': [SOURCE], 'advanced_backend': 'disabled'}})},
                       {'ICLOUD_NATIVE_CONFIG': json.dumps({'reminders': {'source_ids': [SOURCE], 'advanced_backend': 'reminderkit'}})},
                       {'ICLOUD_NATIVE_CONFIG': json.dumps({'reminders': {'source_ids': [], 'advanced_backend': 'reminderkit', 'helper_path': '/synthetic/helper'}})}):
            with patch.dict(os.environ, update), self.assertRaises(SafetyError):self.read()
        self.assertEqual(self.reader.calls, [])

    def test_wrong_ids_and_nonuuid_selectors_refuse_before_read(self):
        for source, identity in ((ID, ID), (SOURCE, 'foreign'), (SOURCE, []), ('builtin', ID), (SOURCE, ID + '/../../x')):
            with self.assertRaises(SafetyError):smart.definition(source, identity, reader=self.reader)
        self.assertEqual(self.reader.calls, [])

    def test_wrong_scope_protocol_backend_mutation_extra_fields_and_title_refuse(self):
        original = copy.deepcopy(self.reader.value)
        for fields in ({'id': SOURCE}, {'source_id': ID}, {'protocol_version': True}, {'backend': 'other'},
                       {'data_mutated': True}, {'extra': 'unreviewed'}, {'title': 'x' * 1001}, {'title': '\ud800'}):
            self.reader.value = dict(original, **fields)
            with self.assertRaises(SafetyError):self.read()

    def test_json_duplicates_numbers_unicode_depth_nodes_and_bytes_refuse(self):
        for raw in ('not JSON', '[]', 'null', '{"a":1,"a":2}', '{"n":NaN}', '{"n":Infinity}',
                    '{"n":1e999}', '{"s":"\\ud800"}', json.dumps({'x': [0] * 2001}),
                    '{"v":' + '[' * 25 + '0' + ']' * 25 + '}', json.dumps({'s': 'x' * 65536})):
            self.reader.value['definition_json'] = raw
            with self.subTest(raw_length=len(raw)), self.assertRaises(SafetyError):self.read()

    def test_definition_title_and_access_drift_refuse_final_result(self):
        read = self.reader.read
        def changed(action, **fields):
            value = read(action, **fields)
            if len(self.reader.calls) == 2:value['title'] = 'Changed'
            return value
        with patch.object(self.reader, 'read', side_effect=changed), self.assertRaises(SafetyError):self.read()
        def revoked(action, **fields):
            value = read(action, **fields);os.environ['ICLOUD_ENABLE_REMINDERS'] = '0';return value
        with patch.object(self.reader, 'read', side_effect=revoked), self.assertRaises(SafetyError):self.read()

    def test_native_reader_fixed_action_admission_and_selected_source_envelope(self):
        with patch('platform.system', return_value='Darwin'), patch.object(NativeReader, '_dispatch', return_value=self.reader.value) as call:
            value = NativeReader('reminders').read('smart_list_definition', source_id=SOURCE, id=ID)
            self.assertEqual(value, self.reader.value);request = call.call_args[0][0]
            self.assertEqual(request['action'], 'smart_list_definition');self.assertEqual(request['source_ids'], [SOURCE])
            for fields in ({'source_id': ID, 'id': ID}, {'source_id': SOURCE, 'id': 'builtin'},
                           {'source_id': SOURCE, 'id': ID, 'script': 'unsafe'}, {'source_id': SOURCE, 'id': ID, 'limit': 1}):
                with self.assertRaises(SafetyError):NativeReader('reminders').read('smart_list_definition', **fields)
