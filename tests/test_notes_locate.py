"""Bounded exact-ID location without Apple Notes or personal data."""
import json,os,unittest
from unittest.mock import patch
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_read import locate_notes_by_id
class Reader:
    def __init__(self):self.calls=[];self.folders=[{'id':'folder','source_id':'selected','title':'Synthetic'},{'id':'other','source_id':'foreign','title':'Other'}]
    def read(self,action,**kwargs):
        self.calls.append((action,kwargs))
        if action=='folders':return {'folders':self.folders,'next_offset':2,'done':True}
        return {'folder_id':kwargs['folder_id'],'source_id':'selected','exists':True}
class LocateTests(unittest.TestCase):
    def setUp(self):
        env={'ICLOUD_ENABLE_NOTES':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'source_ids':['selected']}})}
        ctx=patch.dict(os.environ,env,clear=True);ctx.start();self.addCleanup(ctx.stop);self.reader=Reader()
    def test_exact_membership_keeps_role_unknown_and_skips_foreign(self):
        result=locate_notes_by_id(self.reader,'note','selected');self.assertEqual(result['folders'],[{'id':'folder','source_id':'selected','title':'Synthetic','role':'unverified'}]);self.assertEqual(len(self.reader.calls),2)
    def test_invalid_pagination_or_membership_refuses(self):
        self.reader.folders.append(self.reader.folders[0])
        with self.assertRaises(SafetyError):locate_notes_by_id(self.reader,'note','selected')
        self.reader=Reader();read=self.reader.read
        def bad(action,**kwargs):
            value=read(action,**kwargs)
            if action=='contains':value['source_id']='foreign'
            return value
        self.reader.read=bad
        with self.assertRaises(SafetyError):locate_notes_by_id(self.reader,'note','selected')
    def test_unselected_source_invalid_limits_and_access_drift_refuse(self):
        for source,limit in [('foreign',10),('selected',21),('selected',True)]:
            with self.subTest(source=source,limit=limit),self.assertRaises(SafetyError):locate_notes_by_id(self.reader,'note',source,limit)
        read=self.reader.read
        def drift(action,**kwargs):
            result=read(action,**kwargs);os.environ['ICLOUD_MAIL_ACCESS']='disabled';return result
        self.reader.read=drift
        with self.assertRaises(SafetyError):locate_notes_by_id(self.reader,'note','selected')
