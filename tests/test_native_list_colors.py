"""Scoped public list colors, native field preservation and reviewed correction."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import test_native_reminders as fixtures
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_colors import validate_color,colors_equal
from icloud_for_agents.native_reminders import RemindersCreateAdapter
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.native_grants import NativeGrants
from icloud_for_agents.native_access import require_write
from icloud_for_agents.approvals import PlanStore

class Adapter(RemindersCreateAdapter):
    backend_id='synthetic-list-color'
    def __init__(self):self.reader=fixtures.Reader();self.reader.list['color']=validate_color('#336699');self.calls=[];self.ignore=False
    def apply(self,item):
        require_write('reminders',item);self.calls.append(item['id'])
        if not self.ignore:self.reader.list['color']=copy.deepcopy(item['fields']['color'])
        return {'id':item['id'],'source_id':item['source_id']}

class ListColorTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.adapter=Adapter();self.store=PlanStore(Path(temporary.name)/'runtime')
        env={'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'access_mode':'read-only','source_ids':['selected']}})}
        context=patch.dict(os.environ,env,clear=True);context.start();self.addCleanup(context.stop);self.engine=NativeChanges('reminders','synthetic@example.com',self.adapter,self.store)
    def change(self,color='#1234AB'):return {'operation':'set_list_color','source_id':'selected','list_id':'list','fields':{'color':color}}
    def test_explicit_colors_and_bounded_conversion(self):
        self.assertEqual(validate_color('#FF0080'),{'red':1.,'green':0.,'blue':128/255,'alpha':1.})
        for color in ('red','#fff','#12345678',None,{'red':True,'green':0,'blue':0,'alpha':1},{'red':float('nan'),'green':0,'blue':0,'alpha':1},{'red':0,'green':0,'blue':0,'alpha':0.25},{'red':-1,'green':0,'blue':0,'alpha':1},{'red':10**1000,'green':0,'blue':0,'alpha':1},{'red':float('inf'),'green':0,'blue':0,'alpha':1},{'red':0,'green':0,'blue':0},dict(validate_color('#000000'),space='other')):
            with self.subTest(color=color),self.assertRaises(SafetyError):validate_color(color)
        self.assertTrue(colors_equal(dict(validate_color('#336699'),red=.2000001),'#336699'));self.assertFalse(colors_equal(dict(validate_color('#336699'),red=.2001),'#336699'))
    def test_once_preserves_identity_title_and_membership_and_refuses_replay(self):
        before=copy.deepcopy(self.adapter.reader.list);membership=copy.deepcopy(self.adapter.reader.membership);plan=self.engine.prepare_once([self.change()]);self.assertEqual(self.engine.execute(plan['plan_id'])['status'],'applied')
        self.assertTrue(colors_equal(self.adapter.reader.list['color'],'#1234AB'));self.assertEqual({k:v for k,v in self.adapter.reader.list.items() if k!='color'},{k:v for k,v in before.items() if k!='color'});self.assertEqual(self.adapter.reader.membership,membership)
        with self.assertRaises(SafetyError):self.engine.execute(plan['plan_id'])
        self.assertEqual(os.environ['ICLOUD_MAIL_ACCESS'],'read-only')
    def test_unknown_baseline_noop_wrong_scope_and_extra_fields_refuse(self):
        with self.assertRaises(SafetyError):self.engine.prepare_once([self.change('#336699')])
        for change in (dict(self.change(),source_id='other'),dict(self.change(),list_id='other'),dict(self.change(),fields={'color':'#ffffff','title':'Sneak'})):
            with self.assertRaises(SafetyError):self.engine.prepare_once([change])
        self.adapter.reader.list['color']=None
        with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
        self.assertEqual(self.adapter.calls,[])
    def test_prewrite_membership_drift_refuses_before_admission(self):
        plan=self.engine.prepare_once([self.change()]);self.adapter.reader.membership['count']=3
        with self.assertRaises(SafetyError):self.engine.execute(plan['plan_id'])
        self.assertFalse(self.store.path(plan['plan_id'],'.started').exists());self.assertEqual(self.adapter.calls,[])
    def test_ongoing_color_authority_survives_own_colors_but_not_revocation(self):
        manager=NativeGrants(self.engine);scope={'scenario':'Synthetic list colors','rules':[{'operation':'set_list_color','source_id':'selected','container_id':'list','fields':['color'],'destinations':[]}],'max_batch':1,'max_24h':3,'expires':None}
        grant=manager.prepare(scope);manager.activate(grant['plan_id'])
        for color in ('#ABCDEF','#336699'):
            plan=self.engine.prepare_with_grant(grant['plan_id'],[self.change(color)]);self.assertEqual(self.engine.execute(plan['plan_id'])['status'],'applied');self.assertEqual(manager.status(grant['plan_id'])['status'],'active')
        plan=self.engine.prepare_with_grant(grant['plan_id'],[self.change()]);manager.revoke(grant['plan_id'])
        with self.assertRaises(SafetyError):self.engine.execute(plan['plan_id'])
    def test_failed_provider_color_has_reviewed_before_correction_without_replay(self):
        self.adapter.ignore=True;plan=self.engine.prepare_once([self.change()]);self.assertEqual(self.engine.execute(plan['plan_id'])['status'],'failed_or_uncertain');original=self.store.path(plan['plan_id'],'.result').read_bytes();self.adapter.ignore=False
        repair=self.engine.prepare_update_correction(plan['plan_id'],'before');self.assertEqual(self.engine.execute(repair['plan_id'])['status'],'applied');self.assertEqual(self.engine.reconcile_correction(repair['plan_id'])['status'],'restored_before_after_explicit_correction');self.assertEqual(self.store.path(plan['plan_id'],'.result').read_bytes(),original)
    def test_correction_cannot_overwrite_external_title_or_membership(self):
        self.adapter.ignore=True;plan=self.engine.prepare_once([self.change()]);self.engine.execute(plan['plan_id']);self.adapter.reader.list['title']='External'
        with self.assertRaises(SafetyError):self.engine.prepare_update_correction(plan['plan_id'],'before')
