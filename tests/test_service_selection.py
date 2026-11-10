"""Actual FastMCP registration and lazy backend tests for independent services."""
import asyncio
import importlib
import itertools
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock
import unittest
from fastmcp.exceptions import ToolError
from icloud_for_agents import server
from icloud_for_agents.services import configured
from icloud_for_agents.local_config import child_environment


class ServiceSelectionTests(unittest.TestCase):
    def tearDown(self):
        importlib.reload(server)

    def load(self, mail, calendar, contacts, mode='read-only'):
        env={'ICLOUD_ENABLE_MAIL':str(int(mail)),'ICLOUD_ENABLE_CALENDAR':str(int(calendar)),
             'ICLOUD_ENABLE_CONTACTS':str(int(contacts)),'ICLOUD_MAIL_ACCESS':mode}
        context=patch.dict(os.environ,env,clear=True);context.start();self.addCleanup(context.stop)
        importlib.reload(server)
        return asyncio.run(server.mcp.list_tools())

    def test_all_eight_combinations_register_only_selected_tools(self):
        for mail,calendar,contacts in itertools.product([False,True],repeat=3):
            with self.subTest(mail=mail,calendar=calendar,contacts=contacts):
                tools=self.load(mail,calendar,contacts);names={t.name for t in tools}
                self.assertEqual(sum(n.startswith('email_') for n in names),19 if mail else 0)
                self.assertEqual('owner_request_review' in names,mail or calendar or contacts)
                self.assertEqual('owner_review_status' in names,mail or calendar or contacts)
                self.assertEqual(sum(n.startswith('calendar_') for n in names),4 if calendar else 0)
                self.assertEqual(sum(n.startswith('contacts_') for n in names),4 if contacts else 0)
                self.assertEqual('dav_execute_approved' in names,calendar or contacts)
                self.assertIsNone(server._service);self.assertIsNone(server._dav_owner)

    def test_disabled_mail_refuses_direct_backend_and_stale_function_call(self):
        self.load(False,True,False)
        with patch.object(server,'credentials',side_effect=AssertionError('No credential lookup')):
            with self.assertRaises(ToolError):server.email_list_folders()
            with self.assertRaises(Exception):server.service()

    def test_calendar_only_read_never_constructs_mail_or_contacts_backend(self):
        tools={t.name:t for t in self.load(False,True,False)}
        with patch.object(server,'credentials',return_value=('synthetic@example.com','synthetic')),patch.object(server,'MailService',side_effect=AssertionError('No IMAP backend')),patch('icloud_for_agents.dav.DAVReader.collections',return_value=[]) as collections:
            self.assertEqual(tools['calendar_list'].fn(),[])
            collections.assert_called_once_with('cal');self.assertIsNone(server._service)

    def test_contacts_only_read_never_constructs_mail_or_calendar_backend(self):
        tools={t.name:t for t in self.load(False,False,True)}
        with patch.object(server,'credentials',return_value=('synthetic@example.com','synthetic')),patch.object(server,'MailService',side_effect=AssertionError('No IMAP backend')),patch('icloud_for_agents.dav.DAVReader.collections',return_value=[]) as collections:
            self.assertEqual(tools['contacts_addressbooks'].fn(),[])
            collections.assert_called_once_with('card');self.assertIsNone(server._service)

    def test_read_only_and_disabled_modes_block_dav_write_before_credentials(self):
        for mode in ('read-only','disabled'):
            tools={t.name:t for t in self.load(False,True,False,mode)}
            with patch.object(server,'credentials',side_effect=AssertionError('No credentials')),self.assertRaises(ToolError):tools['calendar_prepare_changes'].fn(calendar_url='https://caldav.icloud.com/synthetic/',changes=[])

    def test_calendar_only_cannot_execute_contact_plan_in_write_mode(self):
        tools={t.name:t for t in self.load(False,True,False,'write-enabled')}
        store=Mock();store.proposal.return_value={'kind':'contact_change'}
        with patch.object(server,'PlanStore',return_value=store),patch.object(server,'credentials',side_effect=AssertionError('No credentials')),self.assertRaises(ToolError):tools['dav_execute_approved'].fn(plan_id='a'*32)

    def test_one_time_calendar_plan_works_from_read_only_without_changing_mode(self):
        from icloud_for_agents.approvals import PlanStore
        tools={t.name:t for t in self.load(False,True,False)}
        collection='https://caldav.icloud.com/synthetic/'
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,{'ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com'}):
            store=PlanStore(Path(directory)/'plans')
            server._dav_owner=SimpleNamespace(account='synthetic@example.com',_password='fake-only',store=store)
            with patch.object(server,'PlanStore',return_value=store), patch('icloud_for_agents.dav.DAVReader.known_collection',return_value=collection), patch('icloud_for_agents.dav_changes.DAVChanges._write',return_value={'status':'applied'}) as writer:
                proposal=tools['calendar_prepare_once'].fn(calendar_url=collection,changes=[{'operation':'create','fields':{'summary':'Synthetic','start':'2026-11-09T16:00:00Z','end':'2026-11-09T17:00:00Z'}}])
                self.assertEqual(proposal['review']['payload']['temporary_elevation'],'calendar-exact-batch-once')
                writer.assert_not_called()
                self.assertEqual(tools['dav_execute_approved'].fn(plan_id=proposal['plan_id'])['status'],'applied')
                self.assertEqual(os.environ['ICLOUD_MAIL_ACCESS'],'read-only')
                with self.assertRaises(ToolError):tools['dav_execute_approved'].fn(plan_id=proposal['plan_id'])
                writer.assert_called_once()

    def test_read_only_does_not_execute_normal_or_wrong_service_plans(self):
        tools={t.name:t for t in self.load(False,True,True)}
        store=Mock()
        for candidate in ({'kind':'calendar_change','payload':{'kind':'cal'}},
                          {'kind':'contact_change','payload':{'kind':'card','temporary_elevation':'calendar-exact-batch-once'}},
                          {'kind':'calendar_change','payload':{'kind':'card','temporary_elevation':'calendar-exact-batch-once'}}):
            store.proposal.return_value=candidate
            with self.subTest(candidate=candidate), patch.object(server,'PlanStore',return_value=store), patch.object(server,'credentials',side_effect=AssertionError('No credentials')), self.assertRaises(ToolError):
                tools['dav_execute_approved'].fn(plan_id='a'*32)

    def test_disabled_mode_blocks_one_time_calendar_before_credentials(self):
        tools={t.name:t for t in self.load(False,True,False,'disabled')}
        with patch.object(server,'credentials',side_effect=AssertionError('No credentials')), self.assertRaises(ToolError):
            tools['calendar_prepare_once'].fn(calendar_url='https://caldav.icloud.com/synthetic/',changes=[])

    def test_one_time_calendar_delete_still_requires_local_receipt(self):
        from icloud_for_agents.approvals import PlanStore
        from test_dav_changes import EVENT
        tools={t.name:t for t in self.load(False,True,False)}
        collection='https://caldav.icloud.com/synthetic/'
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,{'ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com'}):
            store=PlanStore(Path(directory)/'plans')
            server._dav_owner=SimpleNamespace(account='synthetic@example.com',_password='fake-only',store=store)
            snapshot={'etag':'"synthetic"','sha256':'synthetic','content':EVENT}
            with patch.object(server,'PlanStore',return_value=store), patch('icloud_for_agents.dav.DAVReader.known_collection',return_value=collection), patch('icloud_for_agents.dav_changes.DAVChanges.resource',return_value=snapshot), patch('icloud_for_agents.dav_changes.DAVChanges._write') as writer:
                proposal=tools['calendar_prepare_once'].fn(calendar_url=collection,changes=[{'operation':'delete','url':collection+'synthetic.ics','irreversible_confirmed':True}])
                self.assertTrue(proposal['local_receipt_required'])
                with self.assertRaises(ToolError):tools['dav_execute_approved'].fn(plan_id=proposal['plan_id'])
                writer.assert_not_called()

    def test_legacy_defaults_and_explicit_switches(self):
        legacy={'account':'synthetic','apple_keychain_service':'apple','access_mode':'read-only','dav_reads':True}
        self.assertEqual(configured(legacy),{'mail':True,'calendar':True,'contacts':True})
        explicit=dict(legacy,services={'mail':False,'calendar':True,'contacts':False})
        env=child_environment(explicit,{'ICLOUD_ENABLE_CONTACTS':'1'})
        self.assertEqual(env['ICLOUD_ENABLE_MAIL'],'0');self.assertEqual(env['ICLOUD_ENABLE_CONTACTS'],'0');self.assertEqual(env['ICLOUD_ENABLE_CALENDAR'],'1')
        for invalid in ({'mail':True}, {'mail':True,'calendar':True,'contacts':1}):
            with self.assertRaises(ValueError):configured(dict(legacy,services=invalid))

    def test_explicit_zero_overrides_legacy_dav_one(self):
        with patch.dict(os.environ,{'ICLOUD_ENABLE_MAIL':'0','ICLOUD_ENABLE_DAV_READ':'1','ICLOUD_ENABLE_CALENDAR':'0','ICLOUD_ENABLE_CONTACTS':'1'},clear=True):
            importlib.reload(server)
            names={t.name for t in asyncio.run(server.mcp.list_tools())}
            self.assertFalse(any(n.startswith('calendar_') for n in names));self.assertTrue('contacts_get' in names)

    def test_disabled_mail_policy_does_not_block_independent_calendar_admission(self):
        import tempfile
        from pathlib import Path
        from icloud_for_agents.approvals import PlanStore
        with tempfile.TemporaryDirectory() as t,patch.dict(os.environ,{'ICLOUD_ENABLE_MAIL':'0','ICLOUD_ENABLE_CALENDAR':'1','ICLOUD_ENABLE_POLICY_MANAGEMENT':'1','ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com'},clear=True):
            store=PlanStore(Path(t)/'runtime')
            proposal=store.prepare('calendar_change',{'account':'synthetic@example.com','changes':[{'operation':'create'}]})
            self.assertEqual(store.consume(proposal['plan_id'],'synthetic@example.com')['kind'],'calendar_change')
