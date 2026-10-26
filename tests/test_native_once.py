"""Temporary elevation lifecycle with synthetic providers and ordinary approval."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_access import require_write,access_status
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.native_once import planning_access


class Adapter:
    backend_id='synthetic-once';operations={'update'}
    def __init__(self,name):self.name=name;self.value={'title':'Before'};self.calls=0
    def prepare(self,change):
        planning_access(self.name)
        # Planning authority must never admit a native write.
        try:require_write(self.name,change)
        except SafetyError:pass
        else:raise AssertionError('Planning opened write authority')
        return dict(change,before=copy.deepcopy(self.value),fidelity_verified=True,effects={})
    def snapshot(self,item):return copy.deepcopy(self.value)
    def apply(self,item):
        require_write(self.name,item)
        for name,other in [(self.name,dict(item,fields={'title':'Unreviewed'})),('reminders' if self.name=='notes' else 'notes',item)]:
            try:require_write(name,other)
            except SafetyError:pass
            else:raise AssertionError('Authority extended beyond exact item/service')
        self.calls+=1;self.value=copy.deepcopy(item['fields']);return {'id':item['id']}
    def verify(self,item,result):return self.value==item['fields']


class NativeOnceTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.store=PlanStore(Path(temp.name)/'runtime')
        self.account='synthetic@example.com';env={'ICLOUD_ENABLE_NOTES':'1','ICLOUD_ENABLE_REMINDERS':'1',
            'ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_MAIL_ACCOUNT':self.account,'ICLOUD_APPROVAL_POLICY':'trusted-client',
            'ICLOUD_NATIVE_CONFIG':json.dumps({n:{'access_mode':'read-only','source_ids':['selected']} for n in ('notes','reminders')})}
        context=patch.dict(os.environ,env,clear=True);context.start();self.addCleanup(context.stop)
        self.adapter=Adapter('notes');self.engine=NativeChanges('notes',self.account,self.adapter,self.store)
    def changes(self):return [{'operation':'update','source_id':'selected','id':'one','fields':{'title':'After'}}]
    def assert_readonly(self):
        self.assertEqual(access_status('notes')['effective_access'],'read-only')
        with self.assertRaises(SafetyError):require_write('notes',self.changes()[0])
    def test_exact_once_leaves_no_broad_write_window_and_cannot_replay(self):
        original=dict(os.environ);plan=self.engine.prepare_once(self.changes());self.assert_readonly()
        self.assertEqual(plan['review']['payload']['context']['temporary_elevation'],'exact-batch-once')
        self.assertEqual(self.adapter.calls,0)
        result=self.engine.execute(plan['plan_id']);self.assertEqual(result['status'],'applied');self.assertEqual(self.adapter.calls,1)
        self.assertEqual(dict(os.environ),original);self.assert_readonly()
        with self.assertRaises(SafetyError):self.engine.execute(plan['plan_id'])
    def test_restart_preserves_exact_plan_without_persistent_elevation(self):
        plan=self.engine.prepare_once(self.changes());new=NativeChanges('notes',self.account,self.adapter,PlanStore(self.store.root))
        self.assertEqual(new.execute(plan['plan_id'])['status'],'applied');self.assert_readonly()
    def test_default_prepare_still_refuses_readonly(self):
        with self.assertRaises(SafetyError):self.engine.prepare(self.changes())
        self.assert_readonly()
    def test_disabled_unselected_backend_policy_and_mode_changes_refuse_before_consume(self):
        variants=[{'ICLOUD_ENABLE_NOTES':'0'},{'ICLOUD_MAIL_ACCESS':'disabled'},{'ICLOUD_MAIL_ACCESS':'write-enabled'},
                  {'ICLOUD_APPROVAL_POLICY':'local-receipt'},
                  {'ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'access_mode':'read-only','source_ids':['other']}})}]
        for env in variants:
            plan=self.engine.prepare_once(self.changes())
            with patch.dict(os.environ,env),self.assertRaises(SafetyError):self.engine.execute(plan['plan_id'])
            self.assertFalse(self.store.path(plan['plan_id'],'.started').exists())
        plan=self.engine.prepare_once(self.changes());self.adapter.backend_id='changed'
        with self.assertRaises(SafetyError):self.engine.execute(plan['plan_id'])
        self.assertEqual(self.adapter.calls,0)
    def test_strict_receipt_still_required(self):
        os.environ['ICLOUD_APPROVAL_POLICY']='local-receipt';plan=self.engine.prepare_once(self.changes())
        with self.assertRaises(FileNotFoundError):self.engine.execute(plan['plan_id'])
        self.assertEqual(self.adapter.calls,0)
        # Synthetic receipt fixture only, never a human approval performed by an agent.
        self.store.write_once(self.store.path(plan['plan_id'],'.approved'),{'plan_id':plan['plan_id'],'sha256':plan['sha256']})
        self.assertEqual(self.engine.execute(plan['plan_id'])['status'],'applied');self.assert_readonly()
    def test_failure_resets_authority_and_blocks_unknown_effect_replay(self):
        plan=self.engine.prepare_once(self.changes())
        def fail(item):require_write('notes',item);raise RuntimeError('Synthetic uncertain save')
        self.adapter.apply=fail
        self.assertEqual(self.engine.execute(plan['plan_id'])['status'],'failed_or_uncertain');self.assert_readonly()
        other=self.engine.prepare_once(self.changes())
        with self.assertRaises(SafetyError):self.engine.execute(other['plan_id'])
    def test_revocation_between_items_stops_remaining_dispatch(self):
        changes=self.changes()+[dict(self.changes()[0],id='two')];plan=self.engine.prepare_once(changes);original=self.adapter.apply
        def apply(item):
            result=original(item);os.environ['ICLOUD_ENABLE_NOTES']='0';return result
        self.adapter.apply=apply;result=self.engine.execute(plan['plan_id'])
        self.assertEqual(result['status'],'failed_or_uncertain');self.assertEqual(len(result['completed']),1);self.assertEqual(self.adapter.calls,1)
        os.environ['ICLOUD_ENABLE_NOTES']='1';self.assert_readonly()
    def test_both_services_and_expired_plans(self):
        adapter=Adapter('reminders');engine=NativeChanges('reminders',self.account,adapter,self.store)
        plan=engine.prepare_once(self.changes());self.assertEqual(engine.execute(plan['plan_id'])['status'],'applied')
        plan=self.engine.prepare_once(self.changes());self.store.clock=lambda:plan['review']['expires']+1
        with self.assertRaises(SafetyError):self.engine.execute(plan['plan_id'])
        self.assertEqual(self.adapter.calls,0)

    def test_copied_context_cannot_keep_dispatch_authority_after_exit(self):
        from contextvars import copy_context
        from icloud_for_agents.native_once import dispatch
        item=self.changes()[0]
        with dispatch('notes',item):
            context=copy_context();context.run(require_write,'notes',item)
        with self.assertRaises(SafetyError):context.run(require_write,'notes',item)
        self.assert_readonly()
    def test_source_revocation_inside_dispatch_scope_still_refuses(self):
        from icloud_for_agents.native_once import dispatch
        item=self.changes()[0]
        with dispatch('notes',item):
            os.environ['ICLOUD_NATIVE_CONFIG']=json.dumps({'notes':{'access_mode':'read-only','source_ids':['other']}})
            with self.assertRaises(SafetyError):require_write('notes',item)

    def test_cancelled_plan_survives_restart_and_receipt_cannot_reactivate_it(self):
        plan=self.engine.prepare_once(self.changes());self.assertEqual(self.engine.cancel(plan['plan_id'])['status'],'cancelled')
        self.assertEqual(self.engine.cancel(plan['plan_id'])['status'],'cancelled')
        self.store.write_once(self.store.path(plan['plan_id'],'.approved'),{'plan_id':plan['plan_id'],'sha256':plan['sha256']})
        new=NativeChanges('notes',self.account,self.adapter,PlanStore(self.store.root))
        with self.assertRaises(SafetyError):new.execute(plan['plan_id'])
        self.assertEqual(self.adapter.calls,0);self.assert_readonly()
    def test_cancellation_cannot_recall_started_or_other_service_plan(self):
        plan=self.engine.prepare_once(self.changes())
        other=NativeChanges('reminders',self.account,Adapter('reminders'),self.store)
        with self.assertRaises(SafetyError):other.cancel(plan['plan_id'])
        self.engine.execute(plan['plan_id'])
        with self.assertRaises(SafetyError):self.engine.cancel(plan['plan_id'])
    def test_revocation_during_snapshot_is_caught_before_dispatch(self):
        plan=self.engine.prepare_once(self.changes());original=self.adapter.snapshot;calls=0
        def snapshot(item):
            nonlocal calls
            calls+=1
            if calls==2:os.environ['ICLOUD_MAIL_ACCESS']='disabled'
            return original(item)
        self.adapter.snapshot=snapshot;result=self.engine.execute(plan['plan_id'])
        self.assertEqual(result['status'],'failed_or_uncertain');self.assertEqual(self.adapter.calls,0)
