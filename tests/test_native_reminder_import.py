"""Lossy observation copies remain exact, temporary and separately approved."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_access import require_write
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.native_once import planning_access
from icloud_for_agents.native_reminder_import import prepare_copy

class Adapter:
    backend_id='synthetic-copy';operations={'create_reminder'}
    def __init__(self):self.saved=[]
    def prepare(self,change):
        planning_access('reminders')
        return dict(change,before={'list':'synthetic'},effects={'notifications':'Apple controls delivery'},fidelity_verified=True)
    def snapshot(self,item):return {'list':'synthetic'}
    def apply(self,item):
        require_write('reminders',item);self.saved.append(copy.deepcopy(item['fields']))
        return {'id':'new-'+str(len(self.saved))}
    def verify(self,item,outcome):return outcome['id']=='new-'+str(len(self.saved))

class ReminderImportTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.store=PlanStore(Path(temp.name)/'runtime')
        env={'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com',
             'ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'access_mode':'read-only','source_ids':['selected']}})}
        manager=patch.dict(os.environ,env,clear=True);manager.start();self.addCleanup(manager.stop)
        self.adapter=Adapter();self.engine=NativeChanges('reminders','synthetic@example.com',self.adapter,self.store)
        self.row={'id':'old','list_id':'source-list','title':'Synthetic','notes':'Text','priority':1,'completed':True,
                  'due':{'year':2026,'month':10,'day':28,'date_only':True,'timezone':None},'start':None,'url':'https://example.com'}
        self.page={'format':'icloud-for-agents-reminders-observation','version':1,'list_id':'source-list','items':[self.row],'page':{'done':False}}
    def prepare(self,**options):return prepare_copy(self.engine,self.page,'selected','destination',accept_omissions=True,**options)
    def test_one_time_copy_is_reviewed_and_not_a_restore(self):
        plan=self.prepare(include_dates=True,include_urls=True);payload=plan['review']['payload'];review=payload['copy_review']
        self.assertEqual(review['source_completed_count'],1);self.assertFalse(review['page_done'])
        self.assertIn('incomplete',review['effects'][0]);self.assertIn('duplicate',review['effects'][2])
        self.assertEqual(payload['context']['temporary_elevation'],'exact-batch-once')
        fields=payload['changes'][0]['fields'];self.assertEqual(fields['due'],{'year':2026,'month':10,'day':28})
        self.assertEqual(fields['url'],'https://example.com');self.assertNotIn('completed',fields)
        self.assertEqual(self.adapter.saved,[]);self.assertEqual(self.engine.execute(plan['plan_id'])['status'],'applied')
        self.assertEqual(self.adapter.saved,[fields])
        with self.assertRaises(SafetyError):self.engine.execute(plan['plan_id'])
    def test_receipt_and_guard_remain_in_force(self):
        os.environ['ICLOUD_APPROVAL_POLICY']='local-receipt';plan=self.prepare()
        with self.assertRaises(FileNotFoundError):self.engine.execute(plan['plan_id'])
        self.assertEqual(self.adapter.saved,[])
        self.store.write_once(self.store.path(plan['plan_id'],'.approved'),{'plan_id':plan['plan_id'],'sha256':plan['sha256']})
        self.assertEqual(self.engine.execute(plan['plan_id'])['status'],'applied')
    def test_default_copy_omits_dates_and_urls(self):
        fields=self.prepare()['review']['payload']['changes'][0]['fields']
        self.assertEqual(fields,{'title':'Synthetic','notes':'Text','priority':1})
    def test_two_copies_are_admitted_and_verified_in_one_review(self):
        self.page['items'].append(dict(self.row,id='other',title='Second',completed=False))
        plan=self.prepare();self.assertEqual(len(plan['review']['payload']['changes']),2)
        result=self.engine.execute(plan['plan_id']);self.assertEqual(result['status'],'applied')
        self.assertEqual([x['title'] for x in self.adapter.saved],['Synthetic','Second'])
    def test_prior_uncertain_write_still_blocks_copy(self):
        prior=self.prepare();self.store.consume(prior['plan_id'],self.engine.account)
        self.store.finish(prior['plan_id'],{'status':'failed_or_uncertain','phase':'mutation','attempted':[],'completed':[]})
        copy_plan=self.prepare()
        with self.assertRaises(SafetyError):self.engine.execute(copy_plan['plan_id'])
        self.assertEqual(self.adapter.saved,[])
    def test_invalid_scope_omission_and_partial_time_refuse(self):
        with self.assertRaises(SafetyError):prepare_copy(self.engine,self.page,'selected','destination',accept_omissions=False)
        with self.assertRaises(SafetyError):prepare_copy(self.engine,self.page,'other','destination',accept_omissions=True)
        self.row['due']={'year':2026,'month':10,'day':28,'hour':8,'minute':0,'second':30,'timezone':'UTC','date_only':False}
        with self.assertRaises(SafetyError):self.prepare(include_dates=True)
        self.row['due']['second']=0;self.assertEqual(self.prepare(include_dates=True)['review']['payload']['changes'][0]['fields']['due']['timezone'],'UTC')
        self.row['due']['calendar']='unknown'
        with self.assertRaises(SafetyError):self.prepare(include_dates=True)
    def test_duplicate_rows_and_oversized_pages_refuse(self):
        self.page['items'].append(copy.deepcopy(self.row))
        with self.assertRaises(SafetyError):self.prepare()
        self.page['items']=[dict(self.row,notes='x'*100001)]
        with self.assertRaises(SafetyError):self.prepare()
    def test_real_adapter_validates_selected_fields_and_unsafe_url(self):
        from icloud_for_agents.native_reminders import RemindersCreateAdapter
        helper=self.store.root.parent/'helper';helper.write_text('synthetic')
        os.environ['ICLOUD_NATIVE_CONFIG']=json.dumps({'reminders':{'access_mode':'read-only','source_ids':['selected'],'helper_path':str(helper)}})
        adapter=RemindersCreateAdapter();engine=NativeChanges('reminders',self.engine.account,adapter,self.store)
        with patch.object(adapter,'snapshot',return_value={'id':'destination','source_id':'selected','writable':True}):
            plan=prepare_copy(engine,self.page,'selected','destination',accept_omissions=True,include_dates=True,include_urls=True)
            self.assertEqual(plan['review']['payload']['changes'][0]['operation'],'create_reminder')
            self.row['url']='javascript:alert(1)'
            with self.assertRaises(SafetyError):prepare_copy(engine,self.page,'selected','destination',accept_omissions=True,include_urls=True)
    def test_real_adapter_executes_and_verifies_synthetic_copy(self):
        from icloud_for_agents.native_reminders import RemindersCreateAdapter
        helper=self.store.root.parent/'helper';helper.write_text('synthetic')
        os.environ['ICLOUD_NATIVE_CONFIG']=json.dumps({'reminders':{'access_mode':'read-only','source_ids':['selected'],'helper_path':str(helper)}})
        adapter=RemindersCreateAdapter();engine=NativeChanges('reminders',self.engine.account,adapter,self.store);saved=[]
        def dispatch(request,config):
            self.assertEqual(request['action'],'apply');saved.append(request['change']['fields'])
            return {'result':{'id':'new','source_id':'selected','list_id':'destination'}}
        def read(action,**kwargs):
            self.assertEqual(action,'get');return {'item':{**saved[-1],'id':'new','source_id':'selected','list_id':'destination',
                'completed':False,'due':None,'start':None,'url':None,'location':None,'has_recurrence':False,'recurrence_rules':[]}}
        with patch.object(adapter,'snapshot',return_value={'id':'destination','source_id':'selected','writable':True}),patch.object(adapter.reader,'_dispatch',side_effect=dispatch),patch.object(adapter.reader,'read',side_effect=read):
            plan=prepare_copy(engine,self.page,'selected','destination',accept_omissions=True)
            self.assertEqual(engine.execute(plan['plan_id'])['status'],'applied')
            self.assertEqual(saved,[{'title':'Synthetic','notes':'Text','priority':1}])
if __name__=='__main__':unittest.main()
