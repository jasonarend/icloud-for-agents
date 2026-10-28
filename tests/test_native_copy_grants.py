"""A reviewed copy scenario is distinct from generic creation authority."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.native_grants import NativeGrants
from icloud_for_agents.native_note_import import prepare_copy as note_copy
from icloud_for_agents.native_reminder_import import prepare_copy as reminder_copy
from test_native_grants import Adapter

class CopyGrantTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.store=PlanStore(Path(temp.name)/'runtime')
        env={'ICLOUD_ENABLE_NOTES':'1','ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com',
             'ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_NATIVE_CONFIG':json.dumps({name:{'access_mode':'read-only','source_ids':['selected']} for name in ('notes','reminders')})}
        manager=patch.dict(os.environ,env,clear=True);manager.start();self.addCleanup(manager.stop)
        self.engines={name:NativeChanges(name,'synthetic@example.com',Adapter(name),self.store) for name in ('notes','reminders')}
        self.note={'format':'icloud-for-agents-note-observation','version':1,'item':{'id':'old','title':'Synthetic','html':'<div>Synthetic</div><div>Body</div>','locked':False,'truncated':False,'content_complete':False}}
        self.reminder={'format':'icloud-for-agents-reminders-observation','version':1,'list_id':'old-list','page':{'done':True},'items':[{'id':'old','list_id':'old-list','title':'Synthetic','notes':'Text','priority':1,'completed':False}]}
    def scope(self,name,operation='copy_observation',fields=None):
        fields=fields or (['title','body','format'] if name=='notes' else ['title','notes','priority'])
        return {'scenario':'Synthetic scoped copies','rules':[{'operation':operation,'source_id':'selected','container_id':'folder','fields':fields,'destinations':[]}],
                'max_batch':1,'max_24h':1,'expires':None}
    def grant(self,name,operation='copy_observation',fields=None):
        manager=NativeGrants(self.engines[name]);proposal=manager.prepare(self.scope(name,operation,fields))
        if os.environ['ICLOUD_APPROVAL_POLICY']=='local-receipt':
            with self.assertRaises(FileNotFoundError):manager.activate(proposal['plan_id'])
            self.store.write_once(self.store.path(proposal['plan_id'],'.approved'),{'plan_id':proposal['plan_id'],'sha256':proposal['sha256']})
        manager.activate(proposal['plan_id']);return manager,proposal
    def copy(self,name,grant_id,**kwargs):
        engine=self.engines[name]
        return (note_copy(engine,self.note,'selected','folder',accept_omissions=True,grant_id=grant_id) if name=='notes'
                else reminder_copy(engine,self.reminder,'selected','folder',accept_omissions=True,grant_id=grant_id,**kwargs))
    def test_generic_creation_and_copy_scopes_are_not_interchangeable(self):
        for name in ('notes','reminders'):
            op='create_note' if name=='notes' else 'create_reminder';manager,generic=self.grant(name,op)
            with self.assertRaises(SafetyError):self.copy(name,generic['plan_id'])
            manager.revoke(generic['plan_id']);manager,specific=self.grant(name)
            changes=([{'operation':'create_note','source_id':'selected','folder_id':'folder','fields':{'title':'Synthetic','body':'Body','format':'html'}}] if name=='notes'
                     else [{'operation':'create_reminder','source_id':'selected','list_id':'folder','fields':{'title':'Synthetic','notes':'Text','priority':1}}])
            with self.assertRaises(SafetyError):self.engines[name].prepare_with_grant(specific['plan_id'],changes)
            with self.assertRaises(SafetyError):self.engines[name].prepare_with_grant(specific['plan_id'],[dict(changes[0],operation='copy_observation')])
            manager.revoke(specific['plan_id'])
    def test_grants_require_review_and_enforce_revocation_quota_and_replay(self):
        for name in ('notes','reminders'):
            manager,grant=self.grant(name);self.assertIn('lossy copies',grant['review']['payload']['effects']['observation_copy'])
            if name=='reminders':self.assertIn('notifications',grant['review']['payload']['effects']['observation_copy'])
            plan=self.copy(name,grant['plan_id']);self.assertEqual(plan['authorization_path'],'standing-native')
            forged=copy.deepcopy(plan['review']);del forged['payload']['copy_review']
            with self.assertRaises(SafetyError):manager.check_batch(forged,manager.checked(grant['plan_id']))
            self.assertEqual(self.engines[name].execute(plan['plan_id'])['status'],'applied')
            with self.assertRaises(SafetyError):self.engines[name].execute(plan['plan_id'])
            next_plan=self.copy(name,grant['plan_id'])
            with self.assertRaises(SafetyError):self.engines[name].execute(next_plan['plan_id'])
            self.engines[name].cancel(next_plan['plan_id']);manager.revoke(grant['plan_id'])
            with self.assertRaises(SafetyError):self.copy(name,grant['plan_id'])
    def test_strict_grant_activation_and_field_scope(self):
        os.environ['ICLOUD_APPROVAL_POLICY']='local-receipt';manager,grant=self.grant('reminders')
        self.reminder['items'][0]['due']={'year':2026,'month':10,'day':28,'date_only':True,'timezone':None}
        self.reminder['items'][0]['start']=None
        with self.assertRaises(SafetyError):self.copy('reminders',grant['plan_id'],include_dates=True)
        plan=self.copy('reminders',grant['plan_id']);self.assertEqual(self.engines['reminders'].execute(plan['plan_id'])['status'],'applied')
        self.assertFalse(self.store.path(plan['plan_id'],'.approved').exists());manager.revoke(grant['plan_id'])
    def test_revoked_grant_cannot_execute_a_prepared_copy(self):
        for name in ('notes','reminders'):
            manager,grant=self.grant(name);plan=self.copy(name,grant['plan_id']);manager.revoke(grant['plan_id'])
            with self.assertRaises(SafetyError):self.engines[name].execute(plan['plan_id'])
            self.assertEqual(self.engines[name].adapter.calls,[])
    def test_uncertain_copy_blocks_later_grant_copy(self):
        manager,grant=self.grant('reminders');plan=self.copy('reminders',grant['plan_id'])
        manager.consume(plan['plan_id']);self.store.finish(plan['plan_id'],{'status':'failed_or_uncertain','phase':'mutation','attempted':[],'completed':[]})
        next_plan=self.copy('reminders',grant['plan_id'])
        with self.assertRaises(SafetyError):self.engines['reminders'].execute(next_plan['plan_id'])
        self.assertEqual(self.engines['reminders'].adapter.calls,[])
if __name__=='__main__':unittest.main()
