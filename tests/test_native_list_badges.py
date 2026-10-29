"""Native badge field validation, exact memberships and ongoing authority."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from test_native_list_pins import PinReader
from icloud_for_agents.native_reminders import RemindersCreateAdapter
from icloud_for_agents.native_protocol import mutation_request
from icloud_for_agents.native_access import require_write
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.native_grants import NativeGrants
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError

DEFAULT={'kind':'default'};EMOJI={'kind':'emoji','value':'🧪'};SYMBOL={'kind':'symbol','value':'star.fill'}
class Reader(PinReader):
    def __init__(self):super().__init__();self.badge=copy.deepcopy(DEFAULT)
    def read(self,action,**fields):
        if action=='list_features' and fields.get('include_badge'):return dict(self.features,badge=copy.deepcopy(self.badge),backend='reminderkit-list-features-v4')
        return super().read(action,**fields)
class Adapter(RemindersCreateAdapter):
    backend_id='synthetic-native-list-badge'
    def __init__(self):self.reader=Reader();self.calls=[]
    def apply(self,item):
        require_write('reminders',item);self.calls.append(item['id']);wire=mutation_request('reminders',item)['change'];assert wire['before']['list_features'];self.reader.badge=copy.deepcopy(item['fields']['badge']);return {'id':item['id'],'source_id':item['source_id'],'badge':copy.deepcopy(self.reader.badge)}
class ListBadgeTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.adapter=Adapter();self.store=PlanStore(Path(temp.name)/'runtime');context=patch.dict(os.environ,{'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'access_mode':'read-only','source_ids':['selected'],'advanced_backend':'reminderkit'}})},clear=True);context.start();self.addCleanup(context.stop);self.engine=NativeChanges('reminders','synthetic@example.com',self.adapter,self.store)
    def change(self,value=EMOJI):return {'operation':'set_list_badge','source_id':'selected','list_id':'list','fields':{'badge':copy.deepcopy(value)}}
    def test_once_emoji_symbol_and_default_preserve_membership_and_no_replay(self):
        before=copy.deepcopy(self.adapter.reader.membership)
        for badge in (EMOJI,SYMBOL,DEFAULT):
            plan=self.engine.prepare_once([self.change(badge)]);self.assertEqual(self.engine.execute(plan['plan_id'])['status'],'applied');self.assertEqual(self.adapter.reader.badge,badge)
            with self.assertRaises(SafetyError):self.engine.execute(plan['plan_id'])
        self.assertEqual(before,self.adapter.reader.membership);self.assertEqual(os.environ['ICLOUD_MAIL_ACCESS'],'read-only')
    def test_invalid_extra_noop_source_and_backend_refuse(self):
        for value in (True,None,{},DEFAULT,{'kind':'emoji','value':'hello'},{'kind':'symbol','value':'../../secret'},{'kind':'default','value':'extra'},{'kind':'emoji','value':'🧪','title':'extra'}):
            with self.assertRaises(SafetyError):self.engine.prepare_once([self.change(value)])
        with self.assertRaises(SafetyError):self.engine.prepare_once([dict(self.change(),source_id='other')])
        os.environ['ICLOUD_NATIVE_CONFIG']=json.dumps({'reminders':{'access_mode':'read-only','source_ids':['selected']}})
        with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
        self.assertEqual(self.adapter.calls,[])
    def test_stale_badge_and_membership_refuse_before_start(self):
        for field in ('badge','membership'):
            plan=self.engine.prepare_once([self.change()])
            if field=='badge':self.adapter.reader.badge=SYMBOL
            else:self.adapter.reader.membership['count']+=1
            with self.assertRaises(SafetyError):self.engine.execute(plan['plan_id'])
            self.assertFalse(self.store.path(plan['plan_id'],'.started').exists());self.adapter.reader.badge=DEFAULT;self.adapter.reader.membership['count']=2
    def test_private_ordinary_writable_roles_required(self):
        for key in ('shared','shared_to_me','is_group','grocery_categorization','grocery_shopping_session'):
            self.adapter.reader.features[key]=True
            with self.assertRaises(SafetyError):self.engine.prepare_once([self.change()])
            self.adapter.reader.features[key]=False
    def test_grant_survives_own_changes_and_revocation(self):
        m=NativeGrants(self.engine);scope={'scenario':'Synthetic badges','rules':[{'operation':'set_list_badge','source_id':'selected','container_id':'list','fields':['badge'],'destinations':[]}],'max_batch':1,'max_24h':3,'expires':None};g=m.prepare(scope)['plan_id'];m.activate(g)
        for value in (EMOJI,DEFAULT):
            plan=self.engine.prepare_with_grant(g,[self.change(value)]);self.assertEqual(self.engine.execute(plan['plan_id'])['status'],'applied');self.assertEqual(m.status(g)['status'],'active')
        pending=self.engine.prepare_with_grant(g,[self.change(SYMBOL)]);m.revoke(g)
        with self.assertRaises(SafetyError):self.engine.execute(pending['plan_id'])
    def test_known_returned_id_readback_reconciliation_preserves_failure(self):
        with patch.object(self.adapter,'verify',return_value=False):
            plan=self.engine.prepare_once([self.change()]);self.assertEqual(self.engine.execute(plan['plan_id'])['status'],'failed_or_uncertain')
        original=self.store.path(plan['plan_id'],'.result').read_bytes();self.assertEqual(self.engine.reconcile_known_changes(plan['plan_id'])['status'],'applied_after_readback_reconciliation');self.assertEqual(original,self.store.path(plan['plan_id'],'.result').read_bytes());self.assertEqual(len(self.adapter.calls),1)

    def test_native_reader_requires_selected_version_exact_badge_shape_and_scope(self):
        from icloud_for_agents.native_read import NativeReader
        config=json.loads(os.environ['ICLOUD_NATIVE_CONFIG']);config['reminders']['helper_path']='/synthetic/helper';os.environ['ICLOUD_NATIVE_CONFIG']=json.dumps(config)
        observed=dict(self.adapter.reader.features,badge=EMOJI,backend='reminderkit-list-features-v4')
        with patch.object(NativeReader,'_dispatch',return_value=observed) as dispatch:
            self.assertEqual(NativeReader('reminders').read('list_features',list_id='list',include_badge=True)['badge'],EMOJI);self.assertTrue(dispatch.call_args.args[0]['include_badge'])
            for invalid in (dict(observed,badge={'kind':'unknown'}),dict(observed,source_id='other'),dict(observed,backend='reminderkit-list-features-v3'),dict(observed,unexpected=True)):
                dispatch.return_value=invalid
                with self.assertRaises(SafetyError):NativeReader('reminders').read('list_features',list_id='list',include_badge=True)
        for bad in (1,'yes',None):
            with self.assertRaises(SafetyError):NativeReader('reminders').read('list_features',list_id='list',include_badge=bad)
        with self.assertRaises(SafetyError):NativeReader('reminders').read('query',include_badge=True)
