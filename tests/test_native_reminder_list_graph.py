import copy,json,os,unittest
from unittest.mock import patch
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_read import NativeReader
from icloud_for_agents.native_reminder_list_graph import validate_list_graph
SOURCE='11111111-1111-4111-8111-111111111111';LIST='22222222-2222-4222-8222-222222222222';ROOT='33333333-3333-4333-8333-333333333333';CHILD='44444444-4444-4444-8444-444444444444'

class ListGraphTests(unittest.TestCase):
    def result(self):return {'protocol_version':1,'source_id':SOURCE,'list_id':LIST,'backend':'reminderkit-list-hierarchy-v1','data_mutated':False,'membership_verified':True,'hierarchy_complete':True,'observation_consistency':'separate_provider_reads','coverage':'native_list_and_subtasks_matching_eventkit','nodes':[{'id':CHILD,'parent_id':ROOT,'title':'Synthetic child','completed':False},{'id':ROOT,'parent_id':None,'title':'Synthetic parent','completed':True}]}
    def test_complete_graph_and_empty_selected_list(self):
        result=validate_list_graph(self.result(),LIST,[SOURCE]);self.assertEqual(result['nodes'][0]['id'],ROOT)
        result=self.result();result['nodes']=[];self.assertEqual(validate_list_graph(result,LIST,[SOURCE])['nodes'],[])
    def test_missing_duplicate_cycle_depth_and_bad_types_refuse(self):
        for change in ('missing','duplicate','cycle','bool','unknown','id'):
            result=self.result()
            if change=='missing':result['nodes'].pop()
            if change=='duplicate':result['nodes'].append(copy.deepcopy(result['nodes'][0]))
            if change=='cycle':result['nodes'][1]['parent_id']=CHILD
            if change=='bool':result['nodes'][0]['completed']=1
            if change=='unknown':result['nodes'][0]['extra']='not accepted'
            if change=='id':result['nodes'][0]['id']='not UUID'
            with self.subTest(change=change),self.assertRaises(SafetyError):validate_list_graph(result,LIST,[SOURCE])
        result=self.result();result['nodes']=[{'id':f'{i:08x}-0000-4000-8000-000000000000','parent_id':None if i==0 else f'{i-1:08x}-0000-4000-8000-000000000000','title':'Synthetic','completed':False} for i in range(33)]
        with self.assertRaises(SafetyError):validate_list_graph(result,LIST,[SOURCE])
    def test_scope_backend_membership_and_completeness_are_mandatory(self):
        for key,value in [('source_id','foreign'),('list_id','foreign'),('membership_verified',False),('hierarchy_complete',False),('data_mutated',True),('backend','unknown'),('coverage','hint')]:
            result=self.result();result[key]=value
            with self.subTest(key=key),self.assertRaises(SafetyError):validate_list_graph(result,LIST,[SOURCE])
    def test_native_route_requires_explicit_backend_and_no_mutation_access(self):
        env={'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'source_ids':[SOURCE],'helper_path':'/synthetic/helper'}})}
        with patch.dict(os.environ,env,clear=True),patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=self.result()) as dispatch:
            with self.assertRaises(SafetyError):NativeReader('reminders').read('list_hierarchy',list_id=LIST)
            dispatch.assert_not_called();os.environ['ICLOUD_NATIVE_CONFIG']=json.dumps({'reminders':{'source_ids':[SOURCE],'helper_path':'/synthetic/helper','advanced_backend':'reminderkit'}})
            self.assertEqual(len(NativeReader('reminders').read('list_hierarchy',list_id=LIST)['nodes']),2)
