"""Keep full reviews in plans while bounding the actual fixed helper envelopes."""
import copy
import json
import unittest
from icloud_for_agents.native_protocol import mutation_request
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.core import SafetyError


class NativeProtocolTests(unittest.TestCase):
    def item(self):
        return {'operation':'append_note','source_id':'selected','folder_id':'folder','id':'note',
                'fields':{'title':'Synthetic','html':'<div>'+('n'*20000)+'</div>','expected_text':'n'*20000},
                'before':{'note':{'title':'Synthetic','html':'<div>'+('b'*20000)+'</div>',
                    'modified':'2026-10-26T12:00:00Z','text':'b'*20000,'derived_text':'b'*20000}},
                'effects':{'review':'full before/after data retained in the plan'},'fidelity_verified':True}
    def test_duplicate_projections_stay_in_review_not_ipc(self):
        item=self.item();original=copy.deepcopy(item);request=mutation_request('notes',item)
        self.assertGreater(len(json.dumps(item).encode()),65000)
        self.assertLess(len(json.dumps(request,ensure_ascii=False).encode()),65000)
        self.assertEqual(request['change']['before']['note']['html'],item['before']['note']['html'])
        self.assertNotIn('expected_text',request['change']['fields'])
        self.assertNotIn('derived_text',request['change']['before']['note'])
        self.assertEqual(item,original)
    def test_true_native_envelope_overflow_is_refused_before_plan_creation(self):
        item=self.item();item['before']['note']['html']='b'*50000
        adapter=type('Adapter',(),{'operations':{'append_note'}})()
        engine=NativeChanges('notes','synthetic@example.com',adapter,object())
        with self.assertRaises(SafetyError):engine.check_item(item,{'source_ids':['selected']})
    def test_reminder_existing_object_snapshot_remains_exact(self):
        before={'id':'task','notes':'Original','alarms':[{'absolute_date':12345}]}
        item={'operation':'update_reminder','source_id':'selected','id':'task','fields':{'notes':'New'},
              'before':{'item':before,'list':{'title':'Review-only list metadata'}},'effects':{'review':'scope'}}
        request=mutation_request('reminders',item)
        self.assertEqual(request['change']['before'],{'item':before})
        self.assertNotIn('effects',request['change'])
    def test_existing_reminder_envelope_preserves_optional_native_prewrite_guard(self):
        native={'id':'task','source_id':'selected','list_id':'list','flagged':True,'flag_state':1,
                'tags':[{'id':'tag','name':'Project','native_type':0}],'tags_complete':True,'coverage':['flagged','tags']}
        for operation in ('update_reminder','set_completed','move_reminder','set_recurrence'):
            item={'operation':operation,'source_id':'selected','id':'task','fields':{},
                  'before':{'item':{'id':'task'},'native_metadata':native,'list':{'id':'list'}}}
            with self.subTest(operation=operation):
                request=mutation_request('reminders',item)
                self.assertEqual(request['change']['before'],{'item':{'id':'task'},'native_metadata':native})
                self.assertNotIn('list',request['change']['before'])

    def test_move_keeps_attachment_inventory_and_exact_identity(self):
        item=self.item();item['operation']='move_note';item['fields']={'title':'Synthetic','destination_folder_id':'destination'}
        item['before']['attachments']=[{'id':'asset','note_id':'note','content_identifier':'cid:asset','name':'synthetic.pdf'}]
        request=mutation_request('notes',item)
        self.assertEqual(request['change']['before']['attachments'],item['before']['attachments'])
        self.assertEqual(request['change']['id'],'note')
        self.assertEqual(request['change']['fields']['destination_folder_id'],'destination')

    def test_list_rename_forwards_exact_container_and_membership_before_state(self):
        from icloud_for_agents.native_protocol import mutation_request
        item={'operation':'rename_list','source_id':'selected','list_id':'list','id':'list','fields':{'title':'New'},'before':{'list':{'id':'list'},'siblings':[],'membership':{'sha256':'a'*64,'count':2}},'effects':{'list':'Review only'}}
        request=mutation_request('reminders',item)
        self.assertEqual(request['change']['before'],item['before']);self.assertEqual(request['change']['list_id'],'list');self.assertNotIn('effects',request['change'])

    def test_literal_edit_review_and_query_are_not_interpreted_by_native_helper(self):
        item=self.item();item['operation']='replace_text';item['requested_edit']={'find':'body','replacement':'new','expected_matches':1}
        request=mutation_request('notes',item)
        self.assertEqual(request['change']['operation'],'replace_text')
        self.assertNotIn('requested_edit',request['change'])
        self.assertEqual(request['change']['fields']['html'],item['fields']['html'])
        self.assertEqual(request['change']['before']['note']['html'],item['before']['note']['html'])
