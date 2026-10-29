"""Digest-only Reminder terminal evidence preserves immutable uncertainty."""
import json, os, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError, digest
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents.native_execution import capture
from icloud_for_agents.native_protocol import mutation_request
from icloud_for_agents.native_read import NativeReader
from icloud_for_agents.native_reminder_exchange import begin, inspect


class Adapter:
    backend_id='synthetic-reminder-helper-v1';operations={'update_reminder'}
    def __init__(self,helper):self.helper=helper
    def prepare(self,change):
        return {**change,'before':{'item':{'id':'task','source_id':'selected','list_id':'list','notes':'private synthetic before'}},'fidelity_verified':True,'effects':{}}
    def snapshot(self,item):return item['before']
    def apply(self,item):return NativeReader('reminders')._dispatch(mutation_request('reminders',item),{'helper_path':str(self.helper)})['result']
    def verify(self,item,result):return result.get('id')==item['id']


class ReminderExchangeTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);root=Path(temp.name)
        self.helper=root/'helper';self.helper.write_text('synthetic');self.helper.chmod(0o700)
        self.store=PlanStore(root/'runtime');self.adapter=Adapter(self.helper)
        env={'ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCESS':'write-enabled','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_APPROVAL_POLICY':'trusted-client',
             'ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'source_ids':['selected'],'access_mode':'write-enabled'}})}
        patcher=patch.dict(os.environ,env,clear=True);patcher.start();self.addCleanup(patcher.stop)
        self.engine=NativeChanges('reminders','synthetic@example.com',self.adapter,self.store)
    def plan(self):
        return self.engine.prepare([{'operation':'update_reminder','source_id':'selected','id':'task','fields':{'notes':'private synthetic after'}}])
    def launch(self,response,code=0):
        def run(command,**kwargs):
            if response is not None:kwargs['stdout'].write(json.dumps(response).encode())
            return SimpleNamespace(returncode=code)
        return run
    def test_terminal_error_is_bound_and_does_not_settle_uncertainty(self):
        plan=self.plan();identity=plan['plan_id']
        response={'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True}
        with patch('subprocess.run',side_effect=self.launch(response)):
            result=self.engine.execute(identity)
        self.assertEqual(result['status'],'failed_or_uncertain')
        request=self.store.read(self.store.path(identity,'.native-reminder-request-0'))
        terminal=self.store.read(self.store.path(identity,'.native-reminder-response-0'))
        self.assertEqual(terminal['request_sha256'],request['request_sha256'])
        self.assertEqual(terminal['response_sha256'],digest(response))
        self.assertTrue(terminal['mutation_dispatched'])
        self.assertNotIn('private synthetic',json.dumps([request,terminal]))
        stored=self.store.read(self.store.path(identity,'.json'))
        self.assertEqual(inspect(self.store,stored),{'terminal_response':'recorded','helper_reported_mutation_dispatched':True})
        self.assertFalse(self.store.operation_settled(identity))
        with self.assertRaises(SafetyError):self.engine.assert_settled()
    def test_success_refusal_and_missing_response_keep_distinct_evidence(self):
        cases=[({'protocol_version':1,'result':{'id':'task','source_id':'selected'}},0,'applied',True),
               ({'protocol_version':1,'error':'invalid_mutation','mutation_dispatched':False},0,'refused_without_dispatch',True),
               (None,1,'failed_or_uncertain',False)]
        for number,(response,code,status,has_response) in enumerate(cases):
            with self.subTest(status=status):
                self.store=PlanStore(self.store.root/str(number));self.engine.store=self.store
                plan=self.plan();identity=plan['plan_id']
                with patch('subprocess.run',side_effect=self.launch(response,code)):
                    self.assertEqual(self.engine.execute(identity)['status'],status)
                self.assertTrue(self.store.path(identity,'.native-reminder-request-0').exists())
                self.assertEqual(self.store.path(identity,'.native-reminder-response-0').exists(),has_response)
                if not has_response:
                    self.assertEqual(inspect(self.store,self.store.read(self.store.path(identity,'.json')))['terminal_response'],'not_recorded')
                    self.assertFalse(self.store.operation_settled(identity))
    def test_mismatched_admitted_payload_refused_before_helper(self):
        proposal=self.plan();identity=proposal['plan_id'];plan=self.store.read(self.store.path(identity,'.json'))
        self.store.consume(identity,self.engine.account)
        request=mutation_request('reminders',plan['payload']['changes'][0]);request['change']['id']='other'
        with capture(self.store,'reminders',plan,0),self.assertRaises(SafetyError):begin(request)
        self.assertFalse(self.store.path(identity,'.native-reminder-request-0').exists())

    def test_changed_or_unbound_terminal_evidence_is_refused(self):
        proposal=self.plan();identity=proposal['plan_id'];plan=self.store.read(self.store.path(identity,'.json'))
        self.assertEqual(inspect(self.store,plan)['terminal_response'],'not_recorded')
        self.store.write_once(self.store.path(identity,'.native-reminder-response-0'),{'unexpected':'synthetic'})
        with self.assertRaises(SafetyError):inspect(self.store,plan)

    def test_unknown_dispatch_type_and_invalid_protocol_do_not_create_false_proof(self):
        for number,(response,expected) in enumerate([
            ({'protocol_version':1,'error':'synthetic','mutation_dispatched':'false'},'recorded'),
            ({'protocol_version':2,'error':'synthetic','mutation_dispatched':False},'not_recorded')]):
            self.store=PlanStore(self.store.root/str(number));self.engine.store=self.store
            proposal=self.plan();identity=proposal['plan_id']
            with patch('subprocess.run',side_effect=self.launch(response)):
                self.assertEqual(self.engine.execute(identity)['status'],'failed_or_uncertain')
            evidence=inspect(self.store,self.store.read(self.store.path(identity,'.json')))
            self.assertEqual(evidence,{'terminal_response':expected,'helper_reported_mutation_dispatched':None})
            self.assertFalse(self.store.operation_settled(identity))

    def test_only_completed_exact_get_not_found_is_a_typed_missing_observation(self):
        from icloud_for_agents.core import NativeObjectNotFound
        reader=NativeReader('reminders');config={'helper_path':str(self.helper)}
        for code in ('permission_required','query_limit','private_metadata_unavailable'):
            with self.subTest(error=code),patch('subprocess.run',side_effect=self.launch({'protocol_version':1,'error':code})):
                try:reader._dispatch({'protocol_version':1,'action':'get','id':'synthetic','source_ids':['selected']},config)
                except SafetyError as exc:self.assertNotIsInstance(exc,NativeObjectNotFound)
                else:self.fail('Failed read was accepted')
        with patch('subprocess.run',side_effect=self.launch({'protocol_version':1,'error':'not_found_in_scope'})),self.assertRaises(NativeObjectNotFound):
            reader._dispatch({'protocol_version':1,'action':'get','id':'synthetic','source_ids':['selected']},config)
    def test_redacted_sdk_codes_are_retained_without_descriptions_or_authority(self):
        plan=self.plan();identity=plan['plan_id'];response={'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True,'native_error_domain':'com.apple.reminderkit','native_error_code':-3004,'localized_description':'Private synthetic account or content','user_info':{'secret':'Never retain'}}
        with patch('subprocess.run',side_effect=self.launch(response)):result=self.engine.execute(identity)
        self.assertEqual(result['status'],'failed_or_uncertain');self.assertIn('[SDK com.apple.reminderkit -3004]',result['safety_reason']);terminal=self.store.read(self.store.path(identity,'.native-reminder-response-0'));self.assertEqual(terminal['native_error_code'],-3004);self.assertNotIn('Private synthetic',json.dumps(terminal));self.assertNotIn('Never retain',json.dumps(terminal));stored=self.store.read(self.store.path(identity,'.json'));self.assertEqual(inspect(self.store,stored)['helper_native_error'],{'domain':'com.apple.reminderkit','code':-3004});self.assertFalse(self.store.operation_settled(identity))
    def test_unknown_domains_boolean_float_and_oversized_codes_remain_digest_only(self):
        from icloud_for_agents.native_reminder_exchange import native_diagnostic
        for fields in ({'native_error_domain':'private@example.com','native_error_code':1},{'native_error_domain':'other','native_error_code':True},{'native_error_domain':'other','native_error_code':1.0},{'native_error_domain':'other','native_error_code':2**80},{'native_error_domain':'other'}):self.assertEqual(native_diagnostic(fields),{})
        self.assertEqual(native_diagnostic({'native_error_domain':'other','native_error_code':0,'description':'private'}),{'native_error_domain':'other','native_error_code':0})
    def test_corrupt_optional_diagnostics_cannot_validate_terminal_evidence(self):
        plan=self.plan();identity=plan['plan_id']
        with patch('subprocess.run',side_effect=self.launch({'protocol_version':1,'error':'mutation_failed_or_uncertain','mutation_dispatched':True})):self.engine.execute(identity)
        path=self.store.path(identity,'.native-reminder-response-0');before=self.store.read(path);stored=self.store.read(self.store.path(identity,'.json'))
        for fields in ({'native_error_domain':'other'},{'native_error_domain':'other','native_error_code':True},{'native_error_domain':'private@example.com','native_error_code':1}):
            path.write_text(json.dumps(dict(before,**fields)))
            with self.assertRaises(SafetyError):inspect(self.store,stored)
