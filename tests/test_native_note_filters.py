"""Bounded native metadata filtering over isolated physical-folder title pages."""
import copy
import json
import os
import unittest
from unittest.mock import patch

from icloud_for_agents import native_note_filters as filters
from icloud_for_agents.core import SafetyError
from test_native_note_assets import SOURCE, FOLDER, NOTE

OTHER = NOTE.replace('p20', 'p21')


class Reader:
    def __init__(self):
        self.page = {'items': [{'id': identity, 'folder_id': FOLDER, 'source_id': SOURCE,
                               'title': 'Synthetic title', 'locked': False} for identity in (NOTE, OTHER)],
                     'unavailable': [], 'done': False, 'next_offset': 2, 'scanned': 2}
        self.calls = []

    def read(self, action, **fields):
        assert action == 'search' and fields['folder_id'] == FOLDER and fields['text'] == '' and fields['match_scope'] == 'title'
        self.calls.append(fields)
        return copy.deepcopy(self.page)


class NativeFilterTests(unittest.TestCase):
    def setUp(self):
        env = {'ICLOUD_ENABLE_NOTES': '1', 'ICLOUD_MAIL_ACCESS': 'read-only',
               'ICLOUD_NATIVE_CONFIG': json.dumps({'notes': {'access_mode': 'read-only', 'source_ids': [SOURCE], 'metadata_backend': 'notes-store-readonly'}})}
        p = patch.dict(os.environ, env, clear=True);p.start();self.addCleanup(p.stop)
        self.reader = Reader();self.pins = {NOTE: False, OTHER: True};self.tokens = {NOTE: ['EXACT'], OTHER: ['OTHER']}
        for p in (patch.object(filters, 'metadata', side_effect=self.pin), patch.object(filters, 'inventory', side_effect=self.tags)):
            p.start();self.addCleanup(p.stop)

    def pin(self, folder, identity):
        return {'id': identity, 'folder_id': folder, 'source_id': SOURCE, 'pinned': self.pins[identity]}

    def tags(self, folder, identity, *, reader):
        return {'id': identity, 'folder_id': folder, 'source_id': SOURCE,
                'tags': [{'native_token': token} for token in self.tokens[identity]]}

    def query(self, **fields):
        return filters.query(FOLDER, reader=self.reader, **fields)

    def test_pin_filters_and_title_only_guard_do_not_read_tags_or_content(self):
        with patch.object(filters, 'inventory', side_effect=AssertionError('No content read')):
            for state, expected in ((False, NOTE), (True, OTHER)):
                value = self.query(pinned=state)
                self.assertEqual([row['id'] for row in value['items']], [expected])
                self.assertFalse(value['note_content_read_for_guard']);self.assertFalse(value['content_returned'])
                self.assertFalse(value['data_mutated']);self.assertEqual(value['next_offset'], 2)

    def test_native_tokens_are_exact_and_combined_with_pin_by_and(self):
        self.assertEqual([row['id'] for row in self.query(tag_token='EXACT')['items']], [NOTE])
        self.assertEqual(self.query(tag_token='exact')['items'], [])
        self.assertEqual(self.query(pinned=True, tag_token='EXACT')['items'], [])
        value = self.query(pinned=False, tag_token='EXACT');self.assertEqual(value['items'][0]['matched_native_tag_count'], 1)
        self.assertFalse(value['native_tag_inventory_complete']);self.assertFalse(value['smart_folder_membership_evaluated'])

    def test_empty_filtered_page_retains_underlying_continuation(self):
        value = self.query(tag_token='missing');self.assertEqual(value['items'], [])
        self.assertFalse(value['done']);self.assertEqual(value['scanned'], 2);self.assertEqual(value['next_offset'], 2)

    def test_locked_tags_and_unknown_pin_are_unavailable_not_absent_or_unlocked(self):
        self.reader.page['items'][0]['locked'] = True;self.pins[OTHER] = None
        value = self.query(tag_token='EXACT');self.assertEqual(value['items'], [])
        self.assertIn({'id': NOTE, 'category': 'locked_note_native_tags_unavailable'}, value['unavailable'])
        value = self.query(pinned=False);self.assertIn({'id': OTHER, 'category': 'native_pin_unavailable'}, value['unavailable'])

    def test_metadata_failure_is_explicit_and_does_not_hide_other_results(self):
        def pin(folder, identity):
            if identity == NOTE:raise SafetyError('Synthetic unavailable metadata')
            return self.pin(folder, identity)
        with patch.object(filters, 'metadata', side_effect=pin):value = self.query(pinned=True)
        self.assertEqual([row['id'] for row in value['items']], [OTHER])
        self.assertIn({'id': NOTE, 'category': 'native_metadata_unavailable'}, value['unavailable'])

    def test_invalid_filters_bounds_and_native_folder_ids_refuse_before_dispatch(self):
        for fields in ({}, {'pinned': 0}, {'pinned': 'true'}, {'tag_token': ''}, {'tag_token': '\ud800'},
                       {'tag_token': 'x' * 1001}, {'pinned': False, 'limit': True}, {'pinned': False, 'limit': 11},
                       {'pinned': False, 'offset': -1}, {'pinned': False, 'offset': 100001}):
            with self.assertRaises(SafetyError):self.query(**fields)
        with self.assertRaises(SafetyError):filters.query('foreign', pinned=True, reader=self.reader)
        self.assertEqual(self.reader.calls, [])

    def test_optional_backend_disabled_scope_and_revocation_refuse(self):
        for update in ({'ICLOUD_ENABLE_NOTES': '0'}, {'ICLOUD_MAIL_ACCESS': 'disabled'},
                       {'ICLOUD_NATIVE_CONFIG': json.dumps({'notes': {'source_ids': [SOURCE], 'access_mode': 'read-only'}})}):
            with patch.dict(os.environ, update), self.assertRaises(SafetyError):self.query(pinned=False)
        def revoked(folder, identity):
            os.environ['ICLOUD_ENABLE_NOTES'] = '0';return self.pin(folder, identity)
        with patch.object(filters, 'metadata', side_effect=revoked), self.assertRaises(SafetyError):self.query(pinned=False)

    def test_malformed_duplicate_wrong_scope_and_bad_pagination_refuse(self):
        original = copy.deepcopy(self.reader.page)
        for update in ({'items': [None]}, {'items': [original['items'][0]] * 2},
                       {'items': [dict(original['items'][0], source_id='foreign')]},
                       {'items': [dict(original['items'][0], folder_id='foreign')]},
                       {'next_offset': 100}, {'next_offset': 0}, {'done': 1}, {'scanned': True}, {'scanned': 0}):
            self.reader.page = dict(original, **update)
            with self.assertRaises(SafetyError):self.query(pinned=False)

    def test_changed_page_or_native_observation_refuses_result(self):
        old = self.reader.read
        def changed(action, **fields):
            result = old(action, **fields)
            if len(self.reader.calls) == 2:result['items'][0]['title'] = 'Changed'
            return result
        with patch.object(self.reader, 'read', side_effect=changed), self.assertRaises(SafetyError):self.query(pinned=False)
        calls = []
        def drift(folder, identity):
            calls.append(identity);value = self.pin(folder, identity)
            if len(calls) > 2:value['pinned'] = not value['pinned']
            return value
        with patch.object(filters, 'metadata', side_effect=drift), self.assertRaises(SafetyError):self.query(pinned=False)

    def test_ending_page_and_source_unavailability_remain_explicit(self):
        self.reader.page.update(items=[], done=True, next_offset=0, scanned=0, unavailable=[{'id': NOTE, 'category': 'note_read_unavailable'}])
        value = self.query(pinned=True);self.assertTrue(value['done']);self.assertEqual(value['unavailable'], self.reader.page['unavailable'])
