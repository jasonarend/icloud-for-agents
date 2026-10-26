"""Local pre-dispatch proofs settle only fully accounted native dispatch prefixes."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.core import SafetyError
try:
    from tests.test_native_changes import Adapter
except ModuleNotFoundError:
    from test_native_changes import Adapter

class NativeStopsTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.store=PlanStore(Path(tmp.name)/'runtime');self.adapter=Adapter()
        context=patch.dict(os.environ,{'ICLOUD_ENABLE_NOTES':'1','ICLOUD_MAIL_ACCESS':'write-enabled','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'source_ids':['selected'],'access_mode':'write-enabled'}})},clear=True);context.start();self.addCleanup(context.stop)
        self.engine=NativeChanges('notes','synthetic@example.com',self.adapter,self.store)
    def prepare(self,ids=('one',)):
        return self.engine.prepare([{'operation':'update','source_id':'selected','id':identity,'fields':{'title':'After'}} for identity in ids])
    def old_failure(self,phase='snapshot_recheck'):
        p=self.prepare();self.store.consume(p['plan_id'],self.engine.account);result={'status':'failed_or_uncertain','phase':phase,'attempted':[],'completed':[],'do_not_retry':True};self.store.finish(p['plan_id'],result);return p,result
    def test_old_read_failure_finalizes_without_provider_call_or_original_rewrite(self):
        p,result=self.old_failure();original=self.store.path(p['plan_id'],'.result').read_bytes()
        self.adapter.apply=lambda _:self.fail('No native dispatch during finalization')
        self.assertEqual(self.engine.finalize_stopped(p['plan_id'])['status'],'stopped_before_dispatch_finalized')
        self.assertEqual(self.store.path(p['plan_id'],'.result').read_bytes(),original);self.assertTrue(self.store.operation_settled(p['plan_id']))
        with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
    def test_mutation_and_verification_failures_cannot_use_local_stop_proof(self):
        for phase in ('mutation','verification'):
            self.store=PlanStore(self.store.root/phase);self.engine.store=self.store
            p,_=self.old_failure(phase)
            with self.assertRaises(SafetyError):self.engine.finalize_stopped(p['plan_id'])
            self.assertFalse(self.store.path(p['plan_id'],'.native-stopped').exists())
    def test_later_attempt_evidence_prevents_stop_finalization(self):
        p,_=self.old_failure();self.store.write_once(self.store.path(p['plan_id'],'.attempt-0'),{'unexpected':'possible dispatch'})
        with self.assertRaises(SafetyError):self.engine.finalize_stopped(p['plan_id'])
    def test_new_snapshot_stop_is_settled_and_remains_consumed(self):
        p=self.prepare();original=self.adapter.snapshot;calls=0
        def snapshot(item):
            nonlocal calls
            calls+=1
            if calls==2:raise SafetyError('Synthetic read timeout')
            return original(item)
        self.adapter.snapshot=snapshot;result=self.engine.execute(p['plan_id']);self.assertEqual(result['status'],'stopped_before_dispatch');self.assertEqual(self.adapter.calls,[])
        self.assertTrue(self.store.operation_settled(p['plan_id']));self.assertEqual(result['pre_dispatch_evidence']['not_dispatched_index'],0)
        with self.assertRaises(SafetyError):self.engine.execute(p['plan_id'])
        self.adapter.snapshot=original;fresh=self.prepare();self.assertEqual(self.engine.execute(fresh['plan_id'])['status'],'applied')
    def test_partial_stop_requires_exact_durable_completed_attempts(self):
        p=self.prepare(('one','two'));original=self.adapter.snapshot;calls=0
        def snapshot(item):
            nonlocal calls
            calls+=1
            if calls==4:raise SafetyError('Synthetic second snapshot failure')
            return original(item)
        self.adapter.snapshot=snapshot;result=self.engine.execute(p['plan_id']);self.assertEqual(result['status'],'stopped_before_dispatch');self.assertEqual(self.adapter.calls,['one'])
        self.assertTrue(self.store.operation_settled(p['plan_id']));self.assertEqual(len(result['pre_dispatch_evidence']['attempt_record_hashes']),1)
        self.store.path(p['plan_id'],'.attempt-0').unlink()
        with self.assertRaises(SafetyError):self.store.status(p['plan_id'])
