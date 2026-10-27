"""Bounded source-checked hierarchy inspection without an Apple store."""
import json,os,unittest
from unittest.mock import patch
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_read import inspect_reminder_hierarchy,inspect_reminder_recovery_hierarchy

class Reader:
    def __init__(self):
        self.calls=[]
        self.tree={'root':(None,['a','b']),'a':('root',['c']),'b':('root',None),'c':('a',[])}
    def read(self,action,**fields):
        self.calls.append(fields['id']);parent,children=self.tree[fields['id']]
        return {'item':{'id':fields['id'],'title':'Task '+fields['id'],'completed':False,'source_id':'source','list_id':'list','native_metadata':{
            'parent_id':parent,'is_subtask':parent is not None,'has_subtasks':None if children is None else bool(children),
            'child_ids':children,'children_fetch_status':'unavailable' if children is None else 'provider_reported'}}}

class HierarchyInspectionTests(unittest.TestCase):
    def setUp(self):
        env={'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'source_ids':['source'],'advanced_backend':'reminderkit','helper_path':'/synthetic/helper'}})}
        context=patch.dict(os.environ,env,clear=True);context.start();self.addCleanup(context.stop);self.reader=Reader()
    def test_parentage_unknown_and_bounded_traversal(self):
        result=inspect_reminder_hierarchy(self.reader,'root',max_nodes=3,max_depth=5)
        self.assertEqual(self.reader.calls,['root','a','b']);self.assertFalse(result['hierarchy_complete'])
        self.assertEqual((result['nodes'][1]['title'],result['nodes'][1]['completed'],result['nodes'][1]['title_truncated']),('Task a',False,False))
        self.assertEqual({(x['id'],x['reason']) for x in result['unexpanded']},{('c','node_limit'),('b','provider_unavailable')})
        self.assertEqual(result['observation_consistency'],'separate_provider_reads')
    def test_depth_limit_and_cycles_refuse(self):
        result=inspect_reminder_hierarchy(self.reader,'root',max_depth=0)
        self.assertEqual(self.reader.calls,['root']);self.assertEqual({x['id'] for x in result['unexpanded']},{'a','b'})
        self.reader.tree['a']=('root',['root'])
        with self.assertRaises(SafetyError):inspect_reminder_hierarchy(self.reader,'root')
    def test_titles_are_bounded_and_invalid_completion_is_refused(self):
        reader=Reader();read=reader.read
        def long_title(*args,**kwargs):
            result=read(*args,**kwargs);result['item']['title']='x'*1001;return result
        reader.read=long_title
        node=inspect_reminder_hierarchy(reader,'root',max_depth=0)['nodes'][0]
        self.assertEqual(len(node['title']),1000);self.assertTrue(node['title_truncated'])
        def invalid_completion(*args,**kwargs):
            result=read(*args,**kwargs);result['item']['completed']=None;return result
        reader.read=invalid_completion
        with self.assertRaises(SafetyError):inspect_reminder_hierarchy(reader,'root')
    def test_scope_parent_and_access_drift_refuse(self):
        self.reader.tree['a']=('other',[])
        with self.assertRaises(SafetyError):inspect_reminder_hierarchy(self.reader,'root')
        reader=Reader();old=reader.read
        def drift(*args,**kwargs):
            result=old(*args,**kwargs);result['item']['list_id']='foreign' if kwargs['id']=='a' else 'list';return result
        reader.read=drift
        with self.assertRaises(SafetyError):inspect_reminder_hierarchy(reader,'root')
        reader=Reader();old=reader.read
        def access_change(*args,**kwargs):
            result=old(*args,**kwargs);os.environ['ICLOUD_MAIL_ACCESS']='disabled';return result
        reader.read=access_change
        with self.assertRaises(SafetyError):inspect_reminder_hierarchy(reader,'root')
    def test_invalid_limits_refused_before_read(self):
        for nodes,depth in [(True,1),(0,1),(21,1),(1,-1),(1,6)]:
            with self.subTest(nodes=nodes,depth=depth),self.assertRaises(SafetyError):inspect_reminder_hierarchy(self.reader,'root',nodes,depth)
        self.assertFalse(self.reader.calls)

class RecoveryReader:
    def __init__(self):self.calls=[];self.parents={'ROOT':None,'A':'ROOT','B':'ROOT','C':'A'};self.children={'ROOT':['A','B'],'A':['C'],'B':None,'C':[]}
    def read(self,action,**fields):
        identity=fields['id'].upper();self.calls.append(identity);parent=self.parents[identity];kids=self.children[identity]
        return {'found_in_view':True,'found_as_root':parent is None,'parent_id':parent,'recovery_root_id':'ROOT','reported_child_ids':kids,'children_status':'unavailable' if kids is None else 'provider_reported'}

class RecoveryHierarchyInspectionTests(HierarchyInspectionTests):
    def test_recovery_child_resolves_and_rechecks_whole_observed_root(self):
        reader=RecoveryReader();result=inspect_reminder_recovery_hierarchy(reader,'a','source',max_nodes=3)
        self.assertEqual(reader.calls,['A','ROOT','A','B']);self.assertEqual(result['recovery_root_id'],'ROOT')
        self.assertFalse(result['hierarchy_complete']);self.assertEqual({(x['id'],x['reason']) for x in result['unexpanded']},{('C','node_limit'),('B','provider_unavailable')})
    def test_recovery_hierarchy_rejects_changed_relationship_and_scope(self):
        reader=RecoveryReader();read=reader.read
        def drift(action,**fields):
            result=read(action,**fields)
            if len(reader.calls)>1 and fields['id'].upper()=='A':result['parent_id']='FOREIGN'
            return result
        reader.read=drift
        with self.assertRaises(SafetyError):inspect_reminder_recovery_hierarchy(reader,'a','source')
    def test_recovery_hierarchy_rejects_cycle_and_bounds(self):
        reader=RecoveryReader();reader.children['A']=['root']
        with self.assertRaises(SafetyError):inspect_reminder_recovery_hierarchy(reader,'root','source')
        with self.assertRaises(SafetyError):inspect_reminder_recovery_hierarchy(reader,'root','source',max_nodes=21)
