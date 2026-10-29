"""Reviewed observation recovery never replays a Reminder mutation."""
import copy,json,os,unittest
from unittest.mock import patch
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_reminder_recovery import ReminderObservationRecovery
from icloud_for_agents.native_reminder_recovery import LEGACY_ACK
from test_native_reminder_exchange import ReminderExchangeTests, Adapter


class RecoveryAdapter(Adapter):
    def prepare(self,change):
        item=super().prepare(change)
        item['before']['list']={'id':'list','source_id':'selected','writable':True}
        self.current=copy.deepcopy(item['before'])
        return item
    def snapshot(self,item):return copy.deepcopy(self.current)


class ReminderRecoveryTests(ReminderExchangeTests):
    def setUp(self):
        super().setUp();self.adapter=RecoveryAdapter(self.helper);self.engine.adapter=self.adapter
        self.recovery=ReminderObservationRecovery(self.engine.account,self.store,self.adapter)
    def failed(self):
        proposal=self.plan();response={'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True}
        with patch('subprocess.run',side_effect=self.launch(response)):
            self.assertEqual(self.engine.execute(proposal['plan_id'])['status'],'failed_or_uncertain')
        return proposal['plan_id']
    def test_reviewed_acceptance_preserves_original_failure_and_never_replays(self):
        original=self.failed();path=self.store.path(original,'.result');prior=path.read_bytes()
        proposal=self.recovery.prepare(original);result=self.recovery.execute(proposal['plan_id'])
        self.assertEqual(result['recovery_state'],'observed_unchanged_reminder_accepted_after_review')
        self.assertFalse(result['native_data_changed']);self.assertFalse(result['causal_dispatch_proven'])
        self.assertEqual(path.read_bytes(),prior);self.assertTrue(self.store.operation_settled(original))
        self.assertEqual(self.store.status(original)['original_status'],'failed_or_uncertain')
        self.engine.assert_settled()
        with self.assertRaises(SafetyError):self.engine.execute(original)
        with self.assertRaises(SafetyError):self.recovery.prepare(original)
    def test_strict_receipt_is_required_and_bound_to_exact_review(self):
        original=self.failed();os.environ['ICLOUD_APPROVAL_POLICY']='local-receipt'
        proposal=self.recovery.prepare(original)
        with self.assertRaises(FileNotFoundError):self.recovery.execute(proposal['plan_id'])
        self.store.write_once(self.store.path(proposal['plan_id'],'.approved'),{'plan_id':proposal['plan_id'],'sha256':proposal['sha256']})
        self.assertEqual(self.recovery.execute(proposal['plan_id'])['status'],'completed')
        self.assertTrue(self.store.operation_settled(original))
    def test_changed_snapshot_scope_and_missing_terminal_refuse(self):
        original=self.failed();proposal=self.recovery.prepare(original)
        self.adapter.current['item']['notes']='externally changed'
        with self.assertRaises(SafetyError):self.recovery.execute(proposal['plan_id'])
        self.adapter.current['item']['notes']='private synthetic before';self.adapter.backend_id='changed'
        with self.assertRaises(SafetyError):self.recovery.execute(proposal['plan_id'])
        self.adapter.backend_id='synthetic-reminder-helper-v1'
        self.store.path(original,'.native-reminder-response-0').unlink()
        with self.assertRaises(SafetyError):self.recovery.execute(proposal['plan_id'])
        self.assertFalse(self.store.operation_settled(original))
    def test_interrupted_proof_can_finalize_and_withdrawal_retains_guard(self):
        original=self.failed();proposal=self.recovery.prepare(original);self.recovery.abandon(proposal['plan_id'])
        self.assertFalse(self.store.operation_settled(original))
        with self.assertRaises(SafetyError):self.recovery.execute(proposal['plan_id'])
        replacement=self.recovery.prepare(original)
        with patch.object(self.recovery,'proof',side_effect=OSError('Synthetic interruption')),self.assertRaises(OSError):self.recovery.execute(replacement['plan_id'])
        self.assertFalse(self.store.operation_settled(original))
        self.assertEqual(self.recovery.finalize(replacement['plan_id'])['status'],'observed_unchanged_reminder_accepted')
        self.assertEqual(self.recovery.finalize(replacement['plan_id'])['status'],'observed_unchanged_reminder_accepted')
    def test_preexisting_uncertain_case_without_terminal_evidence_cannot_recover(self):
        original=self.failed();self.store.path(original,'.native-reminder-request-0').unlink();self.store.path(original,'.native-reminder-response-0').unlink()
        with self.assertRaises(SafetyError):self.recovery.prepare(original)
        self.assertFalse(self.store.operation_settled(original))

    def legacy(self):
        original=self.failed()
        for suffix in ('.native-reminder-request-0','.native-reminder-response-0'):self.store.path(original,suffix).unlink()
        return original

    def test_legacy_owner_acceptance_requires_separate_acknowledgement_and_preserves_audit(self):
        original=self.legacy();before=self.store.path(original,'.result').read_bytes()
        proposal=self.recovery.prepare(original,legacy_missing_terminal=True)
        for acknowledgement in (None,'yes','I approve'):
            with self.assertRaises(SafetyError):self.recovery.execute(proposal['plan_id'],acknowledgement)
            self.assertFalse(self.store.path(proposal['plan_id'],'.started').exists())
        result=self.recovery.execute(proposal['plan_id'],LEGACY_ACK)
        self.assertEqual(result['owner_acknowledgement'],LEGACY_ACK)
        self.assertTrue(self.store.operation_settled(original));self.engine.assert_settled()
        self.assertEqual(self.store.path(original,'.result').read_bytes(),before)
        for suffix in ('.native-reminder-request-0','.native-reminder-response-0'):self.assertFalse(self.store.path(original,suffix).exists())
        with self.assertRaises(SafetyError):self.engine.execute(original)

    def test_legacy_strict_policy_requires_receipt_as_well_as_acknowledgement(self):
        original=self.legacy();os.environ['ICLOUD_APPROVAL_POLICY']='local-receipt'
        proposal=self.recovery.prepare(original,legacy_missing_terminal=True)
        with self.assertRaises(FileNotFoundError):self.recovery.execute(proposal['plan_id'],LEGACY_ACK)
        self.store.write_once(self.store.path(proposal['plan_id'],'.approved'),{'plan_id':proposal['plan_id'],'sha256':proposal['sha256']})
        self.assertEqual(self.recovery.execute(proposal['plan_id'],LEGACY_ACK)['status'],'completed')
        self.assertTrue(self.store.operation_settled(original))

    def test_legacy_recovery_refuses_partial_exchange_or_changed_snapshot(self):
        original=self.failed();self.store.path(original,'.native-reminder-response-0').unlink()
        with self.assertRaises(SafetyError):self.recovery.prepare(original,legacy_missing_terminal=True)
        self.store.path(original,'.native-reminder-request-0').unlink()
        proposal=self.recovery.prepare(original,legacy_missing_terminal=True)
        self.adapter.current['item']['notes']='changed'
        with self.assertRaises(SafetyError):self.recovery.execute(proposal['plan_id'],LEGACY_ACK)
        self.assertFalse(self.store.operation_settled(original))

    def test_legacy_review_cannot_omit_risk_or_acknowledgement(self):
        original=self.legacy();proposal=self.recovery.prepare(original,legacy_missing_terminal=True)
        plan=self.store.read(self.store.path(proposal['plan_id'],'.json'))
        for field in ('legacy_acknowledgement','request_record_sha256','response_record_sha256'):
            forged=copy.deepcopy(plan);del forged['payload'][field]
            self.assertFalse(self.store._native_reminder_recovery_for(original,forged))
        forged=copy.deepcopy(plan);del forged['payload']['effects']['missing_terminal_evidence']
        self.assertFalse(self.store._native_reminder_recovery_for(original,forged))

    def test_legacy_new_evidence_after_review_blocks_acceptance(self):
        original=self.legacy();proposal=self.recovery.prepare(original,legacy_missing_terminal=True)
        self.store.write_once(self.store.path(original,'.native-exchange-0'),{'unexpected':'synthetic late evidence'})
        with self.assertRaises(SafetyError):self.recovery.execute(proposal['plan_id'],LEGACY_ACK)
        self.assertFalse(self.store.operation_settled(original))
        self.assertFalse(self.store.path(proposal['plan_id'],'.started').exists())

    def test_legacy_interrupted_proof_finalizes_only_persisted_acknowledgement(self):
        original=self.legacy();proposal=self.recovery.prepare(original,legacy_missing_terminal=True)
        with patch.object(self.recovery,'proof',side_effect=OSError('Synthetic interruption')),self.assertRaises(OSError):
            self.recovery.execute(proposal['plan_id'],LEGACY_ACK)
        self.assertFalse(self.store.operation_settled(original))
        self.assertEqual(self.recovery.finalize(proposal['plan_id'])['status'],'observed_unchanged_reminder_accepted')
        self.assertTrue(self.store.operation_settled(original))
    def test_missing_or_forged_request_response_and_unrelated_unfinished_write_block(self):
        original=self.failed();proposal=self.recovery.prepare(original);plan=self.store.read(self.store.path(proposal['plan_id'],'.json'))
        for key in ('original_sha256','original_result_sha256','request_record_sha256','response_record_sha256'):
            forged=copy.deepcopy(plan);forged['payload'][key]='0'*64
            self.assertFalse(self.store._native_reminder_recovery_for(original,forged))
        from icloud_for_agents.core import digest
        other=self.plan();other_plan=self.store.read(self.store.path(other['plan_id'],'.json'))
        self.store.write_once(self.store.path(other['plan_id'],'.started'),{'sha256':digest(other_plan),'account':self.engine.account,'authorization':'trusted-client-policy; not an independent human receipt'})
        with self.assertRaises(SafetyError):self.recovery.execute(proposal['plan_id'])
        self.assertFalse(self.store.operation_settled(original))
    def test_review_cannot_hide_snapshot_limitations(self):
        original=self.failed();proposal=self.recovery.prepare(original)
        plan=self.store.read(self.store.path(proposal['plan_id'],'.json'))
        for key in ('snapshot_coverage','observation_consistency','do_not_retry','causal_dispatch_proven'):
            forged=copy.deepcopy(plan);del forged['payload']['observed'][key]
            self.assertFalse(self.store._native_reminder_recovery_for(original,forged))
        self.assertFalse(self.store.operation_settled(original))
    def test_read_failure_after_consumption_does_not_release_guard(self):
        original=self.failed();proposal=self.recovery.prepare(original)
        with patch.object(self.recovery,'observation',side_effect=[proposal['review']['payload']['observed'],SafetyError('synthetic read failure')]):
            result=self.recovery.execute(proposal['plan_id'])
        self.assertEqual(result['recovery_state'],'not_accepted')
        self.assertFalse(self.store.operation_settled(original))

    def unknown_terminal(self,subtask=False):
        if subtask:
            from types import SimpleNamespace
            from icloud_for_agents.core import NativeObjectNotFound
            self.adapter.operations={'update_reminder','create_subtask'};prepare=self.adapter.prepare
            def prepared(change):
                item=prepare(change);item['child_id']='11111111-1111-4111-8111-111111111111';return item
            self.adapter.prepare=prepared
            def read(action,**fields):
                if action=='get':raise NativeObjectNotFound('Not in synthetic selected scope')
                if action=='recovery_lookup':return {'found_in_view':False}
                raise AssertionError(action)
            self.adapter.reader=SimpleNamespace(read=read)
            proposal=self.engine.prepare_once([{'operation':'create_subtask','source_id':'selected','id':'task','fields':{'title':'Synthetic child'}}])
        else:proposal=self.plan()
        with patch('subprocess.run',side_effect=self.launch({'protocol_version':1,'error':'private_metadata_unavailable'})):
            self.assertEqual(self.engine.execute(proposal['plan_id'])['status'],'failed_or_uncertain')
        return proposal['plan_id']

    def test_recorded_unknown_dispatch_requires_explicit_review_and_retains_original(self):
        original=self.unknown_terminal();before=self.store.path(original,'.result').read_bytes()
        with self.assertRaises(SafetyError):self.recovery.prepare(original)
        proposal=self.recovery.prepare(original,accept_unknown_dispatch=True)
        self.assertTrue(proposal['review']['payload']['accept_unknown_dispatch'])
        self.assertIn('absence of delayed effects',proposal['review']['payload']['effects']['retained_terminal_unknown_dispatch'])
        self.assertEqual(self.recovery.execute(proposal['plan_id'])['status'],'completed');self.engine.assert_settled()
        self.assertEqual(before,self.store.path(original,'.result').read_bytes())
        self.assertIsNone(self.store.read(self.store.path(original,'.native-reminder-response-0'))['mutation_dispatched'])
        with self.assertRaises(SafetyError):self.engine.execute(original)

    def test_unknown_dispatch_strict_policy_still_requires_bound_owner_receipt(self):
        original=self.unknown_terminal();os.environ['ICLOUD_APPROVAL_POLICY']='local-receipt';proposal=self.recovery.prepare(original,accept_unknown_dispatch=True)
        with self.assertRaises(FileNotFoundError):self.recovery.execute(proposal['plan_id'])
        self.store.write_once(self.store.path(proposal['plan_id'],'.approved'),{'plan_id':proposal['plan_id'],'sha256':proposal['sha256']})
        self.assertEqual(self.recovery.execute(proposal['plan_id'])['status'],'completed');self.assertTrue(self.store.operation_settled(original))

    def test_unknown_dispatch_never_borrows_missing_terminal_or_legacy_acknowledgement(self):
        original=self.unknown_terminal()
        for args in ({'accept_unknown_dispatch':1},{'accept_unknown_dispatch':True,'legacy_missing_terminal':True}):
            with self.assertRaises(SafetyError):self.recovery.prepare(original,**args)
        self.store.path(original,'.native-reminder-response-0').unlink()
        with self.assertRaises(SafetyError):self.recovery.prepare(original,accept_unknown_dispatch=True)
        self.assertFalse(self.store.operation_settled(original))

    def test_subtask_unknown_acceptance_checks_candidate_and_keeps_scope_limitations(self):
        original=self.unknown_terminal(subtask=True);proposal=self.recovery.prepare(original,accept_unknown_dispatch=True)
        observed=proposal['review']['payload']['observed'];self.assertFalse(observed['preassigned_child_observation']['absence_globally_proven'])
        self.assertEqual(self.recovery.execute(proposal['plan_id'])['status'],'completed');self.engine.assert_settled()

    def test_subtask_observed_candidate_or_read_failure_prevents_acceptance(self):
        original=self.unknown_terminal(subtask=True)
        self.adapter.reader.read=lambda *args,**kwargs:{'item':{'id':'synthetic'}}
        with self.assertRaises(SafetyError):self.recovery.prepare(original,accept_unknown_dispatch=True)
        def denied(*args,**kwargs):raise SafetyError('Synthetic permission failure')
        self.adapter.reader.read=denied
        with self.assertRaises(SafetyError):self.recovery.prepare(original,accept_unknown_dispatch=True)
        self.assertFalse(self.store.operation_settled(original))

    def test_unknown_review_cannot_omit_terminal_risk_or_forge_candidate_observation(self):
        original=self.unknown_terminal(subtask=True);proposal=self.recovery.prepare(original,accept_unknown_dispatch=True);plan=proposal['review']
        for field in ('accept_unknown_dispatch','request_record_sha256','response_record_sha256'):
            forged=copy.deepcopy(plan);del forged['payload'][field];self.assertFalse(self.store._native_reminder_recovery_for(original,forged))
        forged=copy.deepcopy(plan);del forged['payload']['effects']['retained_terminal_unknown_dispatch'];self.assertFalse(self.store._native_reminder_recovery_for(original,forged))
        forged=copy.deepcopy(plan);forged['payload']['observed']['preassigned_child_observation']['absence_globally_proven']=True;self.assertFalse(self.store._native_reminder_recovery_for(original,forged))
