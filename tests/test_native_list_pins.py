"""Pin scope, preflight, preserved membership, grant lifecycle and reconciliation."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from test_native_reminders import Reader
from icloud_for_agents.native_reminders import RemindersCreateAdapter
from icloud_for_agents.native_protocol import mutation_request
from icloud_for_agents.native_access import require_write
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.native_grants import NativeGrants
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError

class PinReader(Reader):
    def __init__(self):super().__init__();self.pinned=False
    def read(self,action,**fields):
        if action=='list_features' and fields.get('include_pinned'):return dict(self.features,pinned=self.pinned,backend='reminderkit-list-features-v3')
        return super().read(action,**fields)

class Adapter(RemindersCreateAdapter):
    backend_id='synthetic-native-list-pin'
    def __init__(self):self.reader=PinReader();self.calls=[]
    def apply(self,item):
        require_write('reminders',item);self.calls.append(item['id']);wire=mutation_request('reminders',item)['change'];assert wire['before']['list_features']
        self.reader.pinned=item['fields']['pinned'];return {'id':item['id'],'source_id':item['source_id'],'pinned':item['fields']['pinned']}

class ListPinTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.adapter=Adapter();self.store=PlanStore(Path(temp.name)/'runtime')
        context=patch.dict(os.environ,{'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'access_mode':'read-only','source_ids':['selected'],'advanced_backend':'reminderkit'}})},clear=True);context.start();self.addCleanup(context.stop);self.engine=NativeChanges('reminders','synthetic@example.com',self.adapter,self.store)
    def change(self,pinned=True):return {'operation':'set_list_pinned','source_id':'selected','list_id':'list','fields':{'pinned':pinned}}
    def test_once_pin_unpin_and_replay_refusal_preserve_membership(self):
        before=copy.deepcopy(self.adapter.reader.membership)
        for pinned in (True,False):
            plan=self.engine.prepare_once([self.change(pinned)]);self.assertEqual(self.engine.execute(plan['plan_id'])['status'],'applied');self.assertIs(self.adapter.reader.pinned,pinned)
            with self.assertRaises(SafetyError):self.engine.execute(plan['plan_id'])
        self.assertEqual(self.adapter.reader.membership,before);self.assertEqual(os.environ['ICLOUD_MAIL_ACCESS'],'read-only')
    def test_noop_extra_fields_nonboolean_backend_and_scope_refused(self):
        for change in (self.change(False),dict(self.change(),source_id='other'),dict(self.change(),list_id='other'),dict(self.change(),fields={'pinned':1}),dict(self.change(),fields={'pinned':True,'title':'Extra'})):
            with self.assertRaises(SafetyError):self.engine.prepare_once([change])
        os.environ['ICLOUD_NATIVE_CONFIG']=json.dumps({'reminders':{'access_mode':'read-only','source_ids':['selected']}})
        with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
        self.assertEqual(self.adapter.calls,[])
    def test_unsafe_list_role_and_readonly_list_refused(self):
        for key in ('shared','shared_to_me','is_group','grocery_categorization','grocery_shopping_session'):
            self.adapter.reader.features[key]=True
            with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
            self.adapter.reader.features[key]=False
        self.adapter.reader.list['writable']=False
        with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
    def test_pin_membership_and_role_drift_refuse_before_dispatch(self):
        for field in ('pin','membership','role'):
            plan=self.engine.prepare_once([self.change()])
            if field=='pin':self.adapter.reader.pinned=True
            elif field=='membership':self.adapter.reader.membership['count']+=1
            else:self.adapter.reader.features['shared']=True
            with self.assertRaises(SafetyError):self.engine.execute(plan['plan_id'])
            self.assertFalse(self.store.path(plan['plan_id'],'.started').exists());self.adapter.reader.pinned=False;self.adapter.reader.membership['count']=2;self.adapter.reader.features['shared']=False
        self.assertEqual(self.adapter.calls,[])
    def test_grant_survives_own_pin_changes_and_revokes_pending_batch(self):
        m=NativeGrants(self.engine);scope={'scenario':'Synthetic pins','rules':[{'operation':'set_list_pinned','source_id':'selected','container_id':'list','fields':['pinned'],'destinations':[]}],'max_batch':1,'max_24h':3,'expires':None};g=m.prepare(scope)['plan_id'];m.activate(g)
        for value in (True,False):
            plan=self.engine.prepare_with_grant(g,[self.change(value)]);self.assertEqual(self.engine.execute(plan['plan_id'])['status'],'applied');self.assertEqual(m.status(g)['status'],'active')
        plan=self.engine.prepare_with_grant(g,[self.change()]);m.revoke(g)
        with self.assertRaises(SafetyError):self.engine.execute(plan['plan_id'])
    def test_known_id_reconciliation_preserves_original_failure_and_never_replays(self):
        verify=self.adapter.verify
        with patch.object(self.adapter,'verify',return_value=False):
            plan=self.engine.prepare_once([self.change()]);self.assertEqual(self.engine.execute(plan['plan_id'])['status'],'failed_or_uncertain')
        original=self.store.path(plan['plan_id'],'.result').read_bytes();result=self.engine.reconcile_known_changes(plan['plan_id']);self.assertEqual(result['status'],'applied_after_readback_reconciliation');self.assertEqual(len(self.adapter.calls),1);self.assertEqual(self.store.path(plan['plan_id'],'.result').read_bytes(),original)
