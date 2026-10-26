"""Consent requests follow owner policy without elevating application modes."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,PropertyMock
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_permissions import NativePermissions


class NativePermissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=PlanStore(Path(self.tmp.name)/'runtime')
        ctx=patch.dict(os.environ,{'ICLOUD_ENABLE_NOTES':'1','ICLOUD_ENABLE_REMINDERS':'1',
            'ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com',
            'ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_NATIVE_CONFIG':json.dumps({
                'notes':{'access_mode':'read-only','source_ids':[]},'reminders':{'access_mode':'read-only','source_ids':[]}})},clear=True)
        ctx.start();self.addCleanup(ctx.stop)
        for name in ('NotesCreateAdapter','RemindersCreateAdapter'):
            p=patch('icloud_for_agents.native_permissions.'+name+'.backend_id',new_callable=PropertyMock,return_value='synthetic-helper');p.start();self.addCleanup(p.stop)
        self.access=NativePermissions('notes','synthetic@example.com',self.store)
    def test_preview_is_readonly_and_discloses_os_scope(self):
        with patch('subprocess.run',side_effect=AssertionError('No native call')):
            p=self.access.prepare()
        self.assertIn('Notes Automation',p['review']['payload']['effects']['os_scope'])
        self.assertEqual(os.environ['ICLOUD_MAIL_ACCESS'],'read-only')
    def test_approved_request_never_changes_application_access_and_never_replays(self):
        p=self.access.prepare()
        with patch('icloud_for_agents.native_permissions.NativeReader._dispatch',return_value={'permission_state':'granted'}) as dispatch:
            result=self.access.execute(p['plan_id'])
            self.assertEqual(dispatch.call_args.args[0]['action'],'request_access')
        self.assertEqual(result['permission_state'],'granted');self.assertFalse(result['write_access_changed'])
        self.assertFalse(result['data_mutated']);self.assertEqual(os.environ['ICLOUD_MAIL_ACCESS'],'read-only')
        with self.assertRaises(SafetyError):self.access.execute(p['plan_id'])
    def test_strict_request_requires_owner_receipt_before_helper(self):
        os.environ['ICLOUD_APPROVAL_POLICY']='local-receipt';p=self.access.prepare()
        with patch('subprocess.run',side_effect=AssertionError('No native call')),self.assertRaises(FileNotFoundError):self.access.execute(p['plan_id'])
    def test_disabled_stale_tool_and_changed_source_or_mode_refuse(self):
        for key,value in [('ICLOUD_ENABLE_NOTES','0'),('ICLOUD_MAIL_ACCESS','disabled'),
            ('ICLOUD_NATIVE_CONFIG',json.dumps({'notes':{'access_mode':'read-only','source_ids':['other']}}))]:
            p=self.access.prepare()
            with patch.dict(os.environ,{key:value}),patch('subprocess.run',side_effect=AssertionError('No native call')),self.assertRaises(SafetyError):self.access.execute(p['plan_id'])
            self.assertFalse(self.store.path(p['plan_id'],'.started').exists())
    def test_timeout_does_not_repeat_prompt_or_claim_a_grant(self):
        p=self.access.prepare()
        with patch('icloud_for_agents.native_permissions.NativeReader._dispatch',side_effect=SafetyError('Timed out')) as dispatch:
            result=self.access.execute(p['plan_id'])
        self.assertEqual(dispatch.call_count,1);self.assertEqual(result['permission_state'],'unverified')
        self.assertEqual(result['status'],'completed');self.assertTrue(result['do_not_retry'])
    def test_wrong_service_request_cannot_consume_plan(self):
        p=self.access.prepare();other=NativePermissions('reminders','synthetic@example.com',self.store)
        with self.assertRaises(SafetyError):other.execute(p['plan_id'])
        self.assertFalse(self.store.path(p['plan_id'],'.started').exists())
