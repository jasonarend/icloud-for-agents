"""Bounded source-checked hierarchy inspection without an Apple store."""
import json,os,unittest
from unittest.mock import patch
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_read import inspect_reminder_hierarchy

class Reader:
    def __init__(self):
        self.calls=[]
        self.tree={'root':(None,['a','b']),'a':('root',['c']),'b':('root',None),'c':('a',[])}
    def read(self,action,**fields):
        self.calls.append(fields['id']);parent,children=self.tree[fields['id']]
        return {'item':{'id':fields['id'],'source_id':'source','list_id':'list','native_metadata':{
            'parent_id':parent,'is_subtask':parent is not None,'has_subtasks':None if children is None else bool(children),
            'child_ids':children,'children_fetch_status':'unavailable' if children is None else 'provider_reported'}}}

class HierarchyInspectionTests(unittest.TestCase):
    def setUp(self):
        env={'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'source_ids':['source'],'advanced_backend':'reminderkit','helper_path':'/synthetic/helper'}})}
        context=patch.dict(os.environ,env,clear=True);context.start();self.addCleanup(context.stop);self.reader=Reader()
    def test_parentage_unknown_and_bounded_traversal(self):
        result=inspect_reminder_hierarchy(self.reader,'root',max_nodes=3,max_depth=5)
        self.assertEqual(self.reader.calls,['root','a','b']);self.assertFalse(result['hierarchy_complete'])
        self.assertEqual({(x['id'],x['reason']) for x in result['unexpanded']},{('c','node_limit'),('b','provider_unavailable')})
        self.assertEqual(result['observation_consistency'],'separate_provider_reads')
    def test_depth_limit_and_cycles_refuse(self):
        result=inspect_reminder_hierarchy(self.reader,'root',max_depth=0)
        self.assertEqual(self.reader.calls,['root']);self.assertEqual({x['id'] for x in result['unexpanded']},{'a','b'})
        self.reader.tree['a']=('root',['root'])
        with self.assertRaises(SafetyError):inspect_reminder_hierarchy(self.reader,'root')
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
