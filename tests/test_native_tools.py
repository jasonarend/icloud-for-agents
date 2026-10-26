"""Native MCP writes retain mode, service, source and owner gates before backend use."""
import asyncio
import importlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastmcp.exceptions import ToolError
from icloud_for_agents import server
from icloud_for_agents.approvals import PlanStore


class NativeToolTests(unittest.TestCase):
    def tearDown(self):importlib.reload(server)
    def load(self,root,mode='read-only'):
        env={'ICLOUD_ENABLE_MAIL':'0','ICLOUD_ENABLE_CALENDAR':'0','ICLOUD_ENABLE_CONTACTS':'0',
             'ICLOUD_ENABLE_NOTES':'1','ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_MAIL_ACCESS':mode,
             'ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_RUNTIME_ROOT':str(root),
             'ICLOUD_NATIVE_CONFIG':json.dumps({'notes':{'source_ids':['selected'],'access_mode':mode},
                                              'reminders':{'source_ids':['selected'],'access_mode':mode}})}
        ctx=patch.dict(os.environ,env,clear=True);ctx.start();self.addCleanup(ctx.stop)
        importlib.reload(server);return {t.name:t for t in asyncio.run(server.mcp.list_tools())}
    def test_readonly_proposals_refuse_without_sdk_or_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            tools=self.load(Path(directory)/'runtime')
            with patch('subprocess.run',side_effect=AssertionError('No helper')),patch.object(server,'credentials',side_effect=AssertionError('No credentials')):
                for name in ('notes_prepare_changes','reminders_prepare_changes'):
                    with self.assertRaises(ToolError):tools[name].fn(changes=[{'operation':'create_note'}])
            self.assertFalse((Path(directory)/'runtime').exists())
    def test_disabled_service_stale_execute_refuses_before_plan_access(self):
        with tempfile.TemporaryDirectory() as directory:
            tools=self.load(Path(directory)/'runtime','write-enabled')
            os.environ['ICLOUD_ENABLE_NOTES']='0'
            with self.assertRaises(ToolError):tools['notes_execute_approved'].fn(plan_id='a'*32)
            self.assertFalse((Path(directory)/'runtime').exists())
    def test_plan_status_refuses_other_service_owner_and_revoked_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'runtime';tools=self.load(root);store=PlanStore(root)
            for kind,account,sources in [('reminders_change','synthetic@example.com',['selected']),
                                          ('notes_change','other@example.com',['selected']),
                                          ('notes_change','synthetic@example.com',['other'])]:
                plan=store.prepare(kind,{'account':account,'context':{'source_ids':sources},'changes':[]})
                with self.assertRaises(ToolError):tools['notes_plan_status'].fn(plan_id=plan['plan_id'])
    def test_valid_status_needs_no_native_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'runtime';tools=self.load(root);store=PlanStore(root)
            plan=store.prepare('notes_change',{'account':'synthetic@example.com','context':{'source_ids':['selected']},'changes':[]})
            with patch('subprocess.run',side_effect=AssertionError('No helper')):
                status=tools['notes_plan_status'].fn(plan_id=plan['plan_id'])
            self.assertEqual(status['status'],'awaiting_local_approval')
