"""Production adapter exercised only with synthetic stores, IMAP and owner TTY."""
import copy
import fcntl
import io
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.cleanup_access import CleanupAccess, configured
from icloud_for_agents.core import SafetyError, digest
from icloud_for_agents import managed_policy as managed
from icloud_for_agents.policy_approval import approve_local
from icloud_for_agents.service import MailService
from test_mail_safety import FakeIMAP


class OwnerTTY(io.StringIO):
    def __init__(self, answer):super().__init__();self.answer=answer
    def readline(self):return self.answer+'\n'
    def __exit__(self,*args):pass


class ManagedProductionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.account='synthetic@example.com';self.grant='c'*32
        env=patch.dict(os.environ,{'HOME':self.tmp.name,'ICLOUD_MAIL_ACCOUNT':self.account,'ICLOUD_CLEANUP_GRANT_ID':self.grant,'ICLOUD_MAIL_ACCESS':'write-enabled','ICLOUD_APPROVAL_POLICY':'trusted-client','ICLOUD_ENABLE_POLICY_MANAGEMENT':'1','ICLOUD_ENABLE_MAIL':'1'},clear=True)
        env.start();self.addCleanup(env.stop)
        PlanStore().ready()
        self.access=CleanupAccess(self.grant,self.account)
        scope={'grant_id':self.grant,'account':self.account,'operations':['archive','mark_read','trash'],'source_folders':['INBOX'],'destinations':{'archive':'Retained','trash':'Bin'},'max_batch':25,'max_24h':None,'issued':time.time()-1,'expires':None,'rules_reference':'Synthetic reviewed rules'}
        self.access.authority.write_once(self.access.authority.path(self.grant,'.json'),scope)
        managed.initialize_from_base(self.account,self.grant)
        self.manager=managed.context(self.account)[0]
        self.client=FakeIMAP((1,2,3));self.store=PlanStore()
        self.service=MailService(self.account,'synthetic',store=self.store,connector=lambda:self.client)
    def exception(self,uid=1):
        mail=self.service.prepare_trash('INBOX',71,[uid],'Synthetic owner request')
        return managed.prepare_exception(self.service,mail['plan_id'],['starred']),mail
    def execute(self,p,sha=None):
        return managed.execute(self.account,self.store,p['proposal']['plan_id'],sha or p['sha256'],'synthetic exact owner chat confirmation',lambda:self.service)
    def test_trusted_exact_exception_moves_preserving_flags_and_replay_refused(self):
        p,mail=self.exception();flags=self.client.boxes['INBOX'][1][b'FLAGS']
        result=self.execute(p);self.assertEqual(result['status'],'moved');self.assertNotIn(1,self.client.boxes['INBOX'])
        self.assertEqual(set(self.client.boxes['Bin'][10001][b'FLAGS']),set(flags))
        self.assertFalse(self.manager.store.path(p['proposal']['plan_id'],'.approved').exists())
        with self.assertRaises(SafetyError):self.execute(p)
    def test_strict_requires_real_local_tty_interface_then_executes(self):
        with patch.dict(os.environ,{'ICLOUD_APPROVAL_POLICY':'local-receipt'}):
            p,_=self.exception()
            with self.assertRaises(FileNotFoundError):self.execute(p)
            manager=managed.context(self.account)[0]
            phrase='APPROVE POLICY '+p['proposal']['plan_id']+' '+p['sha256']
            approve_local(manager,p['proposal']['plan_id'],lambda:OwnerTTY(phrase))
            self.assertEqual(self.execute(p)['status'],'moved')
    def test_wrong_tty_phrase_produces_no_receipt(self):
        with patch.dict(os.environ,{'ICLOUD_APPROVAL_POLICY':'local-receipt'}):
            p,_=self.exception();manager=managed.context(self.account)[0]
            with self.assertRaises(SafetyError):approve_local(manager,p['proposal']['plan_id'],lambda:OwnerTTY('yes'))
            self.assertFalse(manager.store.path(p['proposal']['plan_id'],'.approved').exists())
    def test_stale_live_snapshot_and_altered_approval_refused_before_write(self):
        p,_=self.exception()
        with self.assertRaises(SafetyError):self.execute(p,sha='0'*64)
        self.client.boxes['INBOX'][1][b'FLAGS']=()
        with self.assertRaises(SafetyError):self.execute(p)
        self.assertIn(1,self.client.boxes['INBOX']);self.assertFalse(self.client.boxes['Bin'])
    def test_default_disabled_and_wrong_account_and_read_only_refused(self):
        p,_=self.exception()
        for env in ({'ICLOUD_ENABLE_POLICY_MANAGEMENT':'0'},{'ICLOUD_MAIL_ACCESS':'read-only'},{'ICLOUD_MAIL_ACCOUNT':'other@example.com'}):
            with patch.dict(os.environ,env),self.assertRaises(SafetyError):self.execute(p)
        self.assertFalse(self.client.boxes['Bin'])
    def test_revocation_and_concurrent_execution_refused(self):
        p,_=self.exception();self.manager.revoke(p['proposal']['plan_id'])
        with self.assertRaises(SafetyError):self.execute(p)
        p,_=self.exception();fd=os.open(self.manager.store.root/'management.lock',os.O_RDWR)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):self.execute(p)
        finally:os.close(fd)
        self.assertFalse(self.client.boxes['Bin'])
    def test_persistent_policy_changes_effective_scope_without_rewriting_base_grant(self):
        replacement=dict(self.manager.current()['policy'],max_batch=50)
        p=self.manager.preview_change(replacement);self.assertEqual(self.execute(p)['status'],'completed')
        self.assertEqual(configured(self.account).scope()['max_batch'],50)
        self.assertEqual(self.access.scope()['max_batch'],25)
        self.access.authority.write_once(self.access.authority.path(self.grant,'.revoked'),{'time':time.time()})
        with self.assertRaises(SafetyError):configured(self.account).scope()
    def test_failed_move_blocks_managed_and_ordinary_operations(self):
        p,_=self.exception()
        with patch('icloud_for_agents.core.safe_move',side_effect=OSError('synthetic uncertain transport')):
            self.assertEqual(self.execute(p)['status'],'failed_or_uncertain')
        with self.assertRaises(SafetyError):configured(self.account).scope()
        with self.assertRaises(SafetyError):self.execute(p)
    def test_extra_messages_changed_plan_and_scope_mismatch_refused(self):
        p,mail=self.exception();target=self.store.path(mail['plan_id'],'.json');value=self.store.read(target)
        value['payload']['items'].append(dict(value['payload']['items'][0],uid=2))
        target.write_text(__import__('json').dumps(value))
        with self.assertRaises(SafetyError):self.execute(p)
        self.assertFalse(self.client.boxes['Bin'])
    def test_deleted_or_permanent_operations_cannot_be_proposed(self):
        p,_=self.exception();policy=self.manager.current()['policy']
        with self.assertRaises(SafetyError):self.manager.preview_change(dict(policy,operations=['purge']))
        with self.assertRaises(SafetyError):self.manager.preview_change(dict(policy,protect_starred=False))

    def test_aggregate_quota_counts_exception_against_shared_cleanup_ledger(self):
        p=self.manager.preview_change(dict(self.manager.current()['policy'],max_batch=1,max_24h=1))
        self.assertEqual(self.execute(p)['status'],'completed')
        first,_=self.exception(1);self.assertEqual(self.execute(first)['status'],'moved')
        second,_=self.exception(2)
        with self.assertRaises(SafetyError):self.execute(second)
        self.assertIn(2,self.client.boxes['INBOX'])

    def test_policy_management_cannot_silently_clear_unfinished_mail(self):
        p,_=self.exception();mail=self.service.prepare_trash('INBOX',71,[2],'Synthetic pending action')
        self.store.write_once(self.store.path(mail['plan_id'],'.started'),{'account':self.account,'started':time.time()})
        with self.assertRaises(SafetyError):self.execute(p)
        self.assertFalse(self.client.boxes['Bin'])

    def test_policy_prepare_validates_scope_before_backend_creation(self):
        import asyncio
        from unittest.mock import Mock
        from fastmcp import FastMCP
        factory=Mock(side_effect=AssertionError('No backend should initialize'))
        registry=FastMCP('Synthetic management')
        managed.register_tools(registry,lambda fn:fn,factory)
        tool=asyncio.run(registry.get_tool('policy_prepare_exception'))
        with self.assertRaises(FileNotFoundError):tool.fn(mail_plan_id='a'*32,deviations=['starred'])
        factory.assert_not_called()

    def test_management_status_exposes_uncertain_outcome_without_retry(self):
        import asyncio
        from fastmcp import FastMCP
        p,_=self.exception()
        self.manager.store.write_once(self.manager.store.path(p['proposal']['plan_id'],'.started'),{'authorization':'synthetic pending write'})
        registry=FastMCP('Synthetic management')
        managed.register_tools(registry,lambda fn:fn,lambda:self.service)
        tool=asyncio.run(registry.get_tool('policy_status'))
        result=tool.fn(proposal_id=p['proposal']['plan_id'])
        self.assertEqual(result['outcome']['status'],'started_or_uncertain')
        self.assertTrue(result['outcome']['do_not_retry'])
